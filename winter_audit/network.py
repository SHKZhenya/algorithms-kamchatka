"""Measure the frozen importer and maintain an independent evidence overlay."""
import gzip
from collections import Counter, defaultdict
from itertools import combinations
from math import cos, floor, radians
from xml.etree import ElementTree as ET

import networkx as nx

from snowops.osm import MOTOR_KEYS, ROAD_CLASSES, tags, way_filter
from snowops.routing import Routing
from types import SimpleNamespace

ATTRIBUTES = ("highway", "name", "ref", "oneway", "junction", "access", "vehicle",
              "motor_vehicle", "motorcar", "hgv", "maxweight", "maxaxleload", "maxheight",
              "maxwidth", "maxlength", "maxspeed", "maxspeed:forward", "maxspeed:backward",
              "surface", "smoothness", "width", "est_width", "lanes", "lanes:forward",
              "lanes:backward", "bridge", "tunnel", "layer", "turn:lanes", "incline",
              "winter_service", "seasonal", "service", "lit", "segregated", "lanes:conditional",
              "barrier", "construction", "disused", "demolished", "temporary")
PEDESTRIAN = {"footway", "path", "steps", "pedestrian", "crossing"}


def raw_network(path):
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    nodes, node_tags, ways, relations = {}, {}, {}, {}
    for item in root:
        if item.tag == "bounds":
            continue
        key = int(item.attrib["id"])
        metadata = tags(item)
        if item.tag == "node":
            nodes[key] = float(item.attrib["lon"]), float(item.attrib["lat"])
            if metadata:
                node_tags[key] = metadata
        elif item.tag == "way":
            ways[key] = (tuple(int(n.attrib["ref"]) for n in item.findall("nd")), metadata)
        elif item.tag == "relation" and metadata.get("type", "").startswith("restriction"):
            relations[key] = ([(m.get("type"), int(m.get("ref")), m.get("role"))
                               for m in item.findall("member")], metadata)
    return nodes, node_tags, ways, relations


def structure(metadata):
    return (any(metadata.get(key, "no") != "no" for key in ("bridge", "tunnel"))
            or metadata.get("layer", "0") != "0" or metadata.get("covered") == "yes")


def level(metadata):
    if "layer" in metadata:
        return metadata["layer"]
    if metadata.get("bridge", "no") != "no":
        return "1"
    if metadata.get("tunnel", "no") != "no":
        return "-1"
    return "0"


def metric(point):
    return (point[0]-158.6)*111195*cos(radians(53.05)), (point[1]-53.05)*111195


def intersection(a, b, c, d):
    """Return parametric coordinates for intersection of nonparallel segments."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    ex, ey = d[0]-c[0], d[1]-c[1]
    det = dx*ey - dy*ex
    if abs(det) < 1e-7:
        return None
    qx, qy = c[0]-a[0], c[1]-a[1]
    t, u = (qx*ey-qy*ex)/det, (qx*dy-qy*dx)/det
    return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None


def topology(graph, ways):
    outgoing, incoming, segments = defaultdict(list), defaultdict(list), {}
    counts = Counter()
    junctions = nx.DiGraph()
    for edge in graph.edges.values():
        segments[edge.physical_road_id] = edge
        junctions.add_edge(edge.from_node, edge.to_node)
        incoming[edge.to_node].append(edge)
        outgoing[edge.from_node].append(edge)
        counts[edge.physical_road_id] += 1
    transitions = Routing(graph, SimpleNamespace(fleet=(), forbidden_turns=(), closed_edges=())).network
    scc = sorted(nx.strongly_connected_components(transitions), key=lambda c: (-len(c), min(c)))
    wcc = sorted((len(c) for c in nx.weakly_connected_components(junctions)), reverse=True)
    flags = defaultdict(set)
    for edge_id in transitions:
        edge = graph.edges[edge_id]
        if transitions.out_degree(edge_id) == 0:
            flags[edge.physical_road_id].add("no_legal_successor")
        if transitions.in_degree(edge_id) == 0:
            flags[edge.physical_road_id].add("no_legal_predecessor")
        for next_id in transitions.successors(edge_id):
            if graph.edges[next_id].to_node == edge.from_node:
                flags[edge.physical_road_id].add("u_turn_requires_vehicle_review")
        if edge_id not in scc[0]:
            flags[edge.physical_road_id].add("outside_largest_transition_scc")
    coords = {n: metric(p) for n, p in graph.nodes.items()}
    grid, by_node_pair, by_coordinate_pair = defaultdict(list), defaultdict(list), defaultdict(list)
    bounds = graph.audit["tile_bounds"]
    for key, edge in segments.items():
        a, b = coords[edge.from_node], coords[edge.to_node]
        by_node_pair[tuple(sorted((edge.from_node, edge.to_node)))].append(key)
        by_coordinate_pair[tuple(sorted((a, b)))].append(key)
        for i in range(floor(min(a[0], b[0])/80), floor(max(a[0], b[0])/80)+1):
            for j in range(floor(min(a[1], b[1])/80), floor(max(a[1], b[1])/80)+1):
                grid[i, j].append(key)
        if edge.length_m <= 2 or edge.length_m >= 500:
            flags[key].add("length_outlier")
        metadata = ways[edge.way_id][1]
        if structure(metadata):
            flags[key].add("structure_needs_engineering_data")
        direction = metadata.get("oneway")
        if direction is None and metadata.get("junction") in {"roundabout", "circular"}:
            direction = "yes"
        expected = 1 if direction in {"yes", "1", "true", "-1"} else 2
        if counts[key] != expected:
            flags[key].add("direction_count_mismatch")
        if expected == 1:
            refs = ways[edge.way_id][0]
            pair = (edge.from_node, edge.to_node)
            if direction == "-1":
                pair = pair[::-1]
            if pair not in zip(refs, refs[1:]):
                flags[key].add("direction_order_mismatch")
        for n in (edge.from_node, edge.to_node):
            if len({e.from_node for e in incoming[n]} | {e.to_node for e in outgoing[n]}) == 1:
                flags[key].add("dead_end_candidate")
                lon, lat = graph.nodes[n]
                if any(min(abs(lon-w), abs(lon-e), abs(lat-s), abs(lat-north)) <= .0004
                       for w, s, e, north in bounds if w <= lon <= e and s <= lat <= north):
                    flags[key].add("tile_boundary_dead_end")
    for groups in (by_node_pair, by_coordinate_pair):
        for keys in groups.values():
            if len(keys) > 1:
                for key in keys:
                    flags[key].add("parallel_coincident_candidate")
    pairs, checked = [], set()
    for keys in grid.values():
        for first, second in combinations(keys, 2):
            if (first, second) in checked:
                continue
            checked.add((first, second))
            a, b = segments[first], segments[second]
            if a.way_id == b.way_id:
                continue
            shared = {a.from_node, a.to_node} & {b.from_node, b.to_node}
            ma, mb = ways[a.way_id][1], ways[b.way_id][1]
            different = level(ma) != level(mb)
            if shared:
                reason = "different_level_shared_node_review" if different else None
            else:
                crossing = intersection(coords[a.from_node], coords[a.to_node],
                                        coords[b.from_node], coords[b.to_node])
                interior = crossing and any(.001 < q < .999 for q in crossing)
                reason = ("grade_separation_no_connection" if different else
                          "same_level_crossing_unjoined") if interior else None
            if reason:
                pairs.append(dict(segment_id_a=first, segment_id_b=second, flag=reason))
                flags[first].add(reason)
                flags[second].add(reason)
    return segments, flags, pairs, dict(weak_node_components=wcc,
        turn_scc_sizes=[len(c) for c in scc], turn_transition_count=transitions.number_of_edges(),
        flag_counts=dict(Counter(flag for items in flags.values() for flag in items)))


def omitted_candidates(graph, raw_nodes, raw_node_tags, ways, relations):
    unsupported_from = defaultdict(list)
    for key in graph.audit["unsupported_relation_ids"]:
        members, _ = relations[key]
        from_ways = [ref for kind, ref, role in members if kind == "way" and role == "from"]
        for ref in from_ways or [ref for _, ref, role in members if role in {"via", "to"}]:
            unsupported_from[ref].append(key)
    seen = {edge.physical_road_id for edge in graph.edges.values()}
    boxes = graph.audit["tile_bounds"]
    for way, (refs, md) in ways.items():
        hw = md.get("highway")
        if (hw not in ROAD_CLASSES | {"service", "track", "unclassified", "trunk", "trunk_link",
                                       "motorway", "motorway_link", "construction", "proposed", "road"}
                and md.get("route") != "ferry"
                and not any(k in md for k in ("disused:highway", "abandoned:highway", "demolished:highway"))):
            continue
        for a, b in zip(refs, refs[1:]):
            key = f"osm:{way}:{min(a,b)}:{max(a,b)}"
            if key in seen or a == b or a not in raw_nodes or b not in raw_nodes:
                continue
            coords = raw_nodes[a], raw_nodes[b]
            if not any(all(w <= lon <= e and s <= lat <= n for lon, lat in coords)
                       for w, s, e, n in boxes):
                continue
            endpoint_tags = {n: {k: v for k, v in raw_node_tags.get(n, {}).items()
                                 if k == "barrier" or k in MOTOR_KEYS or k.startswith(("max", "incline"))}
                             for n in (a, b)}
            yield dict(physical_road_id=key, osm_way_id=way, name=md.get("name"),
                       highway=hw, source_id="osm_mosaic", reason=(
                           "unsupported_turn_relation" if way in unsupported_from else way_filter(md)
                           or ("node_restriction" if any(endpoint_tags.values()) else "zero_length_or_duplicate")),
                       relation_ids=unsupported_from.get(way, []), osm_tags=md,
                       endpoint_tags={n: m for n, m in endpoint_tags.items() if m},
                       existence_status="unresolved" if hw in {"construction", "proposed"}
                       or any(k in md for k in ("disused:highway", "abandoned:highway", "demolished:highway"))
                       else "mapped_unverified", coordinates=coords)
