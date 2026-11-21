"""Build the winter evidence package from frozen OSM and one Copernicus tile."""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from shutil import copy2

from snowops.osm import OSM_ATTRIBUTION, OSM_LICENSE_URL, load_osm
from snowops.infrastructure import Infrastructure

from .access import assess, Capability
from .experiment import run
from .network import ATTRIBUTES, PEDESTRIAN, omitted_candidates, raw_network, structure, topology
from .relief import Surface

ROOT = Path(__file__).resolve().parents[1]


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":"))+"\n", encoding="utf-8")


def table(path, rows):
    rows = list(rows)
    if not rows:
        raise ValueError(f"refusing empty dataset {path}")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, tuple))
                             else v for k, v in row.items()})


def feature(kind, coordinates, properties):
    return dict(type="Feature", geometry=dict(type=kind, coordinates=coordinates), properties=properties)


def layer(path, features, graph, **metadata):
    dump(path, dict(type="FeatureCollection", features=list(features), graph_version=graph.version,
                    attribution=OSM_ATTRIBUTION, license_url=OSM_LICENSE_URL, **metadata))


def normalized(value):
    return " ".join(sorted(re.findall(r"[\w-]+", value.casefold().replace("ё", "е"))))


def build(out, dem, limit):
    geo, tables, ex = (out / folder for folder in ("geo", "tables", "experiments"))
    for folder in (geo, tables, ex):
        folder.mkdir(parents=True, exist_ok=True)
    evidence_dir = ROOT / "winter_audit/evidence"
    for registry in sorted(evidence_dir.glob("*.csv")):
        copy2(registry, tables / registry.name)
    copy2(evidence_dir / "frontend_layers.json", out / "frontend_layers.json")
    inputs = ROOT / "data/pkc"
    osm = inputs / "city.osm.gz"
    graph = load_osm(osm)
    nodes, node_tags, ways, relations = raw_network(osm)
    assert graph.audit["nodes"] == 5375 and len(graph.edges) == 10567
    surface = Surface(dem)
    segments, flags, crossings, top = topology(graph, ways)
    tracked = [key for key in ATTRIBUTES if key not in {"barrier", "temporary"}]
    attr_rows = [dict(attribute=key, directed_osm_tag_count=(n := sum(
                 key in ways[e.way_id][1] for e in graph.edges.values())),
                 pct=round(100*n/len(graph.edges), 3),
                 graph_version=graph.version, source_id="osm_mosaic") for key in tracked]
    table(tables / "attribute_coverage.csv", attr_rows)
    table(tables / "topology_crossings.csv", crossings)
    omitted = list({r["physical_road_id"]: r for r in omitted_candidates(
        graph, nodes, node_tags, ways, relations)}.values())
    table(tables / "excluded_osm_segments.csv", omitted)
    layer(geo / "excluded_osm_segments.geojson",
          (feature("LineString", row["coordinates"], {k:v for k,v in row.items() if k != "coordinates"})
           for row in omitted), graph)
    table(tables / "turn_pairs.csv", (dict(incoming=a, outgoing=b, source_id="osm_mosaic",
          graph_version=graph.version, verification_status="derived") for a, b in sorted(graph.forbidden_turns)))
    table(tables / "turn_relations.csv", (dict(osm_relation_id=key, members=members, osm_tags=md,
          excluded_as_unsupported=key in graph.audit["unsupported_relation_ids"], source_id="osm_mosaic")
          for key, (members, md) in relations.items()))
    by_segment = defaultdict(list)
    for e in graph.edges.values():
        by_segment[e.physical_road_id].append(e.edge_id)
    slope_features, road_features, directed, rank = [], [], [], []
    slopecount = Counter()
    for e in graph.edges.values():
        md = ways[e.way_id][1]
        points = [graph.nodes[e.from_node], graph.nodes[e.to_node]]
        z = surface.profile(*points, structure(md))
        slopecount[z["quality"]] += 1
        prop = dict(edge_id=e.edge_id, physical_road_id=e.physical_road_id, osm_way_id=e.way_id,
                    length_m=e.length_m, from_node=e.from_node, to_node=e.to_node, name=e.name,
                    road_class=e.road_class, speed_limit_kmh=e.speed_limit_kmh, source_id="osm_mosaic",
                    source_locator=f"way/{e.way_id}", graph_version=graph.version,
                    existence_status="mapped_unverified", responsibility_status="unknown",
                    operational_status="unknown", usable_width_m=None)
        directed.append(prop)
        slope_features.append(feature("LineString", points, {**z, "edge_id": e.edge_id,
            "physical_road_id": e.physical_road_id, "name": e.name, "graph_version": graph.version}))
        if z["quality"] == "dsm_proxy" and z["length_unrounded_m"] >= 80:
            if abs(z["mean_grade_pct"]) >= 8:
                flags[e.physical_road_id].add("dsm_grade_review")
            rank.append({**z, "edge_id": e.edge_id, "name": e.name, "ranking_direction":
                         "up" if z["mean_grade_pct"] >= 0 else "down"})
        road_features.append(feature("LineString", points, {**prop, "osm_tags": md,
                                                    "relief_quality": z["quality"],
                                                    "mean_grade_pct": z["mean_grade_pct"]}))
    rank.sort(key=lambda r: (r["ranking_direction"], -abs(r["mean_grade_pct"]), r["edge_id"]))
    table(tables / "slope_candidates.csv", rank)
    table(tables / "directed_edges.csv", directed)
    layer(geo / "road_network_enriched.geojson", road_features, graph)
    layer(geo / "road_slopes.geojson", slope_features, graph, dem_source_id="glo30")
    physical, unresolved, quality, restriction, matches, match_features = [], [], [], [], [], []
    with (ROOT / "winter_audit/evidence/municipal_roads_447.csv").open(encoding="utf-8") as file:
        municipal = defaultdict(list)
        for row in csv.DictReader(file):
            municipal[normalized(row["name"])].append(row["road_id"])
    for key, e in sorted(segments.items()):
        md = ways[e.way_id][1]
        points = [graph.nodes[e.from_node], graph.nodes[e.to_node]]
        row = dict(physical_road_id=key, osm_way_id=e.way_id, name=e.name, road_class=e.road_class,
                   source_id="osm_mosaic", graph_version=graph.version, length_m=e.length_m,
                   directed_edge_ids=by_segment[key], coordinates=points, existence_status="mapped_unverified",
                   osm_width_raw=md.get("width"), surface_raw=md.get("surface"), lanes_raw=md.get("lanes"))
        physical.append(row)
        issues = set(flags[key])
        if "width" not in md:
            issues.add("missing_osm_width")
        unresolved.append(dict(physical_road_id=key, graph_version=graph.version, source_id="osm_mosaic",
                               existence_status="mapped_unverified", responsibility_status="unknown",
                               passability_status="unknown", flags=sorted(issues)))
        quality.append(feature("LineString", points, unresolved[-1]))
        candidates = municipal.get(normalized(e.name), [])
        if normalized(e.name) == "ключевская улица":
            candidates = candidates + municipal.get("ключевская", [])
        refs = md.get("ref", "").replace(" ", "").replace("–", "-").replace("А", "A")
        if "A-401" in refs or "А-401" in md.get("ref", ""):
            candidates = candidates + ["federal:A-401"]
        for candidate in candidates:
            source = "federal_666" if candidate == "federal:A-401" else "municipal_447"
            match = dict(physical_road_id=key, road_reference_id=candidate, source_id=source,
                         graph_version=graph.version, match_method="osm_ref" if source == "federal_666"
                         else "normalized_exact_name", verification_status="derived",
                         legal_scope_confirmed=False)
            matches.append(match)
            match_features.append(feature("LineString", points, match))
        keys = ("oneway", "access", "vehicle", "motor_vehicle", "motorcar", "hgv", "maxspeed",
                "maxweight", "maxheight", "maxwidth", "maxaxleload", "incline", "winter_service",
                "bridge", "tunnel", "layer", "turn:lanes", "seasonal")
        for k, value in md.items():
            if k.split(":")[0] in keys or k.endswith(":conditional"):
                restriction.append(dict(physical_road_id=key, key=k, raw_value=value,
                   source_id="osm_mosaic", verification_status="mapped_unverified", graph_version=graph.version,
                   coordinates=points))
    table(tables / "physical_segments.csv", physical)
    table(tables / "unresolved_objects.csv", unresolved)
    table(tables / "road_restrictions.csv", restriction)
    table(tables / "road_match_candidates.csv", matches)
    layer(geo / "road_restrictions.geojson",
          (feature("LineString", r["coordinates"], {k:v for k,v in r.items() if k != "coordinates"})
           for r in restriction), graph)
    layer(geo / "data_quality_flags.geojson", quality, graph)
    layer(geo / "responsibility_segments.geojson", match_features, graph,
          scope="mapped_reference_candidates_only")
    export_infrastructure(graph, nodes, node_tags, ways, geo, tables)
    slopes = {f["properties"]["edge_id"]: f["properties"]["mean_grade_pct"]
              for f in slope_features}
    definitions, impact, validations, critical = run(graph, ex, dump, limit, slopes)
    table(ex / "graph_comparison.csv", definitions)
    table(ex / "routing_impact.csv", impact)
    table(ex / "critical_transit_edges.csv", critical)
    dump(ex / "validation_report.json", validations)
    export_access(graph, ex)
    top["flag_counts_after_relief"] = dict(Counter(f for v in flags.values() for f in v))
    high_priority = {"different_level_shared_node_review", "same_level_crossing_unjoined",
                     "parallel_coincident_candidate", "length_outlier", "no_legal_successor",
                     "no_legal_predecessor", "direction_count_mismatch", "direction_order_mismatch",
                     "structure_needs_engineering_data", "dsm_grade_review"}
    summary = dict(signed_contracts_missing_current_status_or_annex=1,
                   graph_version=graph.version, osm_audit=graph.audit, topology=top,
                   physical_segments=len(physical), physical_segments_high_priority=sum(
                       bool(v & high_priority) for v in flags.values()), priority_flags=sorted(high_priority),
                   excluded_segments=len(omitted), excluded_reasons=dict(Counter(r["reason"] for r in omitted)),
                   dem_sha256=surface.sha256, dsm_endpoint_samples=sum(
                       f["properties"]["dsm_start_m"] is not None and f["properties"]["dsm_end_m"] is not None
                       for f in slope_features), slope_quality=dict(slopecount),
                   municipal_reference_matches=sum(r["source_id"] == "municipal_447" for r in matches),
                   federal_reference_matches=sum(r["source_id"] == "federal_666" for r in matches),
                   current_confirmed_assignments=0, disputed_assignments=0,
                   unknown_physical_segments=len(physical), confirmed_current_territorial_contracts=0)
    dump(out / "audit_metrics.json", summary)

def export_infrastructure(graph, nodes, node_tags, ways, geo, tables):
    index = Infrastructure(graph)
    sites = {site.site_id: site for site in index.sites}
    points = [feature("Point", f.lon_lat, dict(object_id=f.site_id, kind=f.kind, name=f.name,
                 geometry_basis="osm_node_or_centroid", source_id="osm_mosaic", verification_status="mapped_unverified"))
              for f in graph.facilities]
    for nid, md in sorted(node_tags.items()):
        if md.get("highway") == "bus_stop" or md.get("public_transport") in {"platform", "stop_position"}:
            points.append(feature("Point", nodes[nid], dict(object_id=f"osm:node:{nid}:transit",
                                  kind=md.get("public_transport", "bus_stop"), name=md.get("name"),
                                  source_id="osm_mosaic", verification_status="mapped_unverified")))
    layer(geo / "infrastructure_objects.geojson", points, graph)
    buildings = []
    for wid, (refs, md) in sorted(ways.items()):
        kind = md.get("highway")
        if kind not in PEDESTRIAN or not set(refs) <= nodes.keys():
            continue
        for i, (a, b) in enumerate(zip(refs, refs[1:])):
            if a != b and any(all(w <= nodes[n][0] <= e and s <= nodes[n][1] <= north for n in (a,b))
                              for w,s,e,north in graph.audit["tile_bounds"]):
                buildings.append(feature("LineString", [nodes[a], nodes[b]], dict(
                    connection_id=f"osm:ped:{wid}:{i}", kind=kind, from_node=a, to_node=b,
                    source_id="osm_mosaic", verification_status="mapped_unverified",
                    access=md.get("access"), foot=md.get("foot"), surface=md.get("surface"))))
    layer(geo / "pedestrian_connections.geojson", buildings, graph)
    table(tables / "infrastructure_dependencies.csv",
          (dict(object_id=sid, target_edge_id=e, dependency_type="transport",
                basis="existing snowops proximity and incoming-arc heuristic",
                evidence_status="derived_candidate", legal_obligation=False, snap_m=site.snap_m,
                source_id="osm_mosaic", graph_version=graph.version)
           for sid, site in sites.items() for e in site.approach_edges))


def export_access(graph, output):
    labels = {"rotary": ("blower",), "grader": ("grader",), "kdm": ("plow", "spread"),
              "mtz": ("compact_plow", "brush"), "loader": ("load",),
              "dump_truck": ("haul",), "excavator": ("excavate",), "spreader": ("spread",),
              "compact": ("sidewalk_clear", "compact_plow")}
    type_rows, cap_rows = [], []
    for key, operations in labels.items():
        type_rows.append(dict(vehicle_type_id=key, city_model=None, confirmed_city_fleet=False,
                             source_id="project_tz"))
        for op in ("deadhead", *operations):
            states = ("loaded", "empty") if key == "dump_truck" else ("unspecified",)
            for state in states:
                row = Capability(vehicle_type_id=key, source_id="project_tz", operation=op, load_state=state)
                cap_rows.append({**row.model_dump(), "length_m": None, "turn_radius_m": None,
                                 "working_width_m": None, "surface_types": None, "crew_count": None,
                                 "empty_mass_t": None, "fuel_type": None, "fuel_l_h": None,
                                 "work_kmh": None, "transit_kmh": None})
    table(output.parent / "tables/vehicle_types.csv", type_rows)
    table(output.parent / "tables/vehicle_capabilities.csv", cap_rows)
    from snowops.pilot import AS_OF
    from datetime import timedelta
    with (output / "vehicle_accessibility.csv.gz").open("wb") as raw:
        import gzip
        from io import TextIOWrapper
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as zipped:
            with TextIOWrapper(zipped, encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(("graph_version", "edge_id", "vehicle_type_id", "operation", "load_state",
                                 "status", "reason", "source_id", "conservative_allowed", "research_allowed"))
                for row in cap_rows:
                    cap = Capability(**{k:row[k] for k in Capability.model_fields})
                    for eid in graph.edges:
                        decision = assess(eid, cap, AS_OF, AS_OF+timedelta(seconds=1))
                        writer.writerow((graph.version, eid, cap.vehicle_type_id, cap.operation,
                                         cap.load_state, decision.status, "|".join(decision.reasons),
                                         cap.source_id, decision.permits("conservative"),
                                         decision.permits("research")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dem", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=float, default=12)
    args = parser.parse_args()
    build(args.output, args.dem, args.limit)
