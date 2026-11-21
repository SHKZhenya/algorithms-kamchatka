"""Reconcile procurement locations with an immutable routing graph and export map popups."""
import argparse
import gzip
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from snowops.osm import OSM_ATTRIBUTION, OSM_LICENSE_URL

from .build import ROOT, dump, normalized
from .network import raw_network
from .relief import distance

INPUTS = ROOT / "winter_audit/evidence/contract_scope"
PILOT_TAG = "osm_mosaic"
PILOT_GEO = ROOT / "winter_audit/output/geo"
OUTPUT = ROOT / "winter_audit/output/contract_scope"
CONTRACT = "3410114343626000068"
NOTICES = {"0831": "0138300000426000831", "0902": "0138300000426000902"}
TERMS = {"0831": {"signed_from": "2026-10-28", "signed_end_inclusive": "2026-12-25",
                  "status_snapshot": "Исполнение по копии карточки на 03.11.2026"},
         "0902": {"signed_from": None, "signed_end_inclusive": None,
                  "planned_end_inclusive": "2028-12-31", "application_deadline_msk": "2026-11-30",
                  "status_snapshot": "Приём заявок по последней доступной редакции 18.11.2026"}}
PERIODS = {"0831": "28.10.2026–25.12.2026 по карточке контракта",
           "0902": "От заключения до 31.12.2028 по извещению. Дата заключения неизвестна."}
FIRM_MATCH = {"same_named_object", "common_corridor_limits_differ"}
ROAD_TYPES = {"carriageway", "carriageway_and_bus_turnaround", "access_road"}
CONTRACTOR = "МУП «Спецдорремстрой»"
STATUS = {
    "confirmed": {"label": "Подтверждённая ответственность", "color": "#087652"},
    "preliminary": {"label": "Предварительно сопоставленный объект", "color": "#92500e"},
    "unknown": {"label": "Ответственный неизвестен", "color": "#64748b"},
}
DOCUMENT_KIND = {
    "0831": ("signed_contract", "Подписанный контракт № 0831"),
    "0902": ("procurement_notice", "Извещение № 0902 · приём заявок"),
}
OPERATION_LABELS = {
    "anti_icing": "Противогололёдная обработка",
    "snow_ice_removal": "Очистка снега и льда",
    "routine_cleaning": "Обычная уборка и мойка",
    "drainage_cleaning": "Очистка водоотвода",
    "material_handling": "Погрузка и перевозка материалов",
    "waste_management": "Обращение с отходами",
    "repair": "Ремонт",
}


def operation_kind(line, key, target):
    if 28 <= line <= 31:
        return "anti_icing"
    if line in (26, 27):
        return "repair"
    if target == "waste":
        return "waste_management"
    if "load" in key or "haul" in key:
        return "material_handling"
    return "drainage_cleaning" if target == "drainage" else "routine_cleaning"


def confirmation(status):
    return {"responsibility_status": status, "responsibility_status_label": STATUS[status]["label"]}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def graph_index(directory):
    graph = read(directory / "road_network_enriched.geojson")
    edges, incidence = defaultdict(list), defaultdict(set)
    road = {}
    for feature in graph["features"]:
        prop = feature["properties"]
        physical = prop["physical_road_id"]
        edges[physical].append(prop["edge_id"])
        road.setdefault(physical, feature["geometry"])
        incidence[prop["from_node"]].add(physical)
        incidence[prop["to_node"]].add(physical)
    excluded = {f["properties"]["physical_road_id"] for f in
                read(directory / "excluded_osm_segments.geojson")["features"]}
    return graph["graph_version"], edges, road, incidence, excluded


def osm_inputs():
    nodes, tags, ways, _ = raw_network(ROOT / "data/pkc/city.osm.gz")
    way_sources = dict.fromkeys(ways, PILOT_TAG)
    node_sources = dict.fromkeys(nodes, PILOT_TAG)
    manifest = read(INPUTS / "osm_reference_manifest.json")
    for record in manifest:
        path = INPUTS / record["file"]
        digest = sha256(gzip.decompress(path.read_bytes())).hexdigest()
        if digest != record["raw_sha256"]:
            raise ValueError(f"OSM hash mismatch for {path}")
        more_nodes, more_tags, more_ways, _ = raw_network(path)
        for node, point in more_nodes.items():
            if node not in nodes:
                nodes[node] = point
                node_sources[node] = record["source_id"]
        for node, metadata in more_tags.items():
            tags.setdefault(node, metadata)
        for way, data in more_ways.items():
            if way not in ways:
                ways[way] = data
                way_sources[way] = record["source_id"]
    return nodes, tags, ways, way_sources, node_sources, manifest


def operations(catalog):
    source = read(INPUTS / "winter_operations.json")
    records = []
    rows = read(INPUTS / "nonwinter_operations.json") + source["confirmed_contract_lines"]
    for line, key, target, label in rows:
        kind = operation_kind(line, key, target)
        records.append({
            "obligation_id": f"{CONTRACT}:{line}", "contract_id": CONTRACT,
            "procurement_id": NOTICES["0831"], "contract_line": line,
            "customer": catalog["customer"], "contractor": CONTRACTOR,
            "document_kind": "signed_contract", "contract_calendar_context": "Осень–зима 2026",
            "operation_technology": kind, "operation_technology_label": OPERATION_LABELS[kind],
            "explicit_winter_maintenance": kind in {"anti_icing", "snow_ice_removal"},
            "operation_id": key, "operation": label, "target_class": target,
            "period": PERIODS["0831"], **TERMS["0831"], "document_url": catalog["eis_0831"],
            "evidence_url": catalog["contract_0831"], "source_locator": f"Предмет контракта, позиция {line}",
            "evidence_status": "published_contract_line_mirror",
            "scope_level": "contract_item_class", "territorial_application": "unverified",
            "contract_object_id": None, "approved_segment_ids": [],
        })
    tender = {}
    for category, examples in source["tender_0902"].items():
        tender[category] = [
            {"operation_id": key, "target_class": target, "operation": label,
             "operation_technology": "snow_ice_removal" if category == "additional_examples"
             else ("anti_icing" if key.startswith("deice_") else "routine_cleaning")}
            for key, target, label in examples
        ]
    return sorted(records, key=lambda row: row["contract_line"]), tender


def line_geometry(points):
    return {"type": "LineString", "coordinates": points}


def shapes(obj, osm, indexed):
    nodes, tags, ways, way_sources, node_sources, _ = osm
    _, edges, road, incidence, excluded = indexed
    names = set(map(normalized, obj.get("names", [])))
    avoid = set(obj.get("exclude_classes", ()))
    linked = []
    for way, (refs, metadata) in ways.items():
        if way not in obj.get("service_ways", ()) and (
            not names or "highway" not in metadata or metadata["highway"] in avoid
            or normalized(metadata.get("name", "")) not in names
        ):
            continue
        for a, b in zip(refs, refs[1:]):
            if a == b or a not in nodes or b not in nodes or nodes[a] == nodes[b]:
                continue
            physical = f"osm:{way}:{min(a,b)}:{max(a,b)}"
            link = physical if physical in edges else None
            source = PILOT_TAG if link or physical in excluded else way_sources[way]
            if obj.get("item") in (16, 17, 18) and obj.get("class") in {"access_road", "parking"}:
                method = "alternative_access_candidate"
            elif obj.get("class") == "overpass":
                method = "mapped_structure_by_adjacent_address"
            elif obj.get("anchors") and obj.get("item") in (1, 3, 13):
                method = "mapped_landmarks_partial_bounds"
            else:
                method = "named_axis_legal_bounds_unknown"
            linked.append((road[link] if link else line_geometry([nodes[a], nodes[b]]), {
                "geometry_role": "road_axis_candidate" if link else "reference_line",
                "match_method": method, "physical_road_id": link,
                "directed_edge_ids": sorted(edges[link]) if link else [],
                "reference_segment_id": physical, "excluded_in_pilot": physical in excluded,
                "junction_anchor_segment_ids": sorted(incidence[a] | incidence[b]) if not link else [],
                "geometry_source_id": source, "osm_way_id": way,
            }))
    access_anchors = {key for way in obj.get("anchor_access_ways", ())
                      for node in ways[way][0] for key in incidence[node]}
    for way in obj.get("site_ways", ()):
        refs, metadata = ways[way]
        points = [nodes[node] for node in refs]
        if len(points) < 4 or points[0] != points[-1]:
            raise ValueError(f"expected a closed site way: {way}")
        signed_area = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(points, points[1:]))
        if signed_area < 0:
            points.reverse()
        linked.append(({"type":"Polygon", "coordinates":[points]}, {
            "geometry_role":"site_alternative" if obj["class"] == "parking" else "destination_landmark",
            "match_method":"multiple_parking_candidates" if obj["class"] == "parking" else "mapped_destination_only",
            "geometry_source_id":way_sources[way], "osm_way_id":way,
            "junction_anchor_segment_ids": sorted(access_anchors),
        }))
    for node in obj.get("anchors", ()):
        linked.append(({"type":"Point", "coordinates":nodes[node]}, {
            "geometry_role":"landmark", "match_method":"mapped_osm_landmark", "osm_node_id":node,
            "landmark_name":tags.get(node, {}).get("name"), "geometry_source_id":node_sources[node],
        }))
    if not linked:
        linked.append((None, {"geometry_role":"unlocated", "match_method":"no_unique_geometry"}))
    return linked


def popup_values(group, obj, catalog, obligation_rows):
    kind, document_status = DOCUMENT_KIND[group]
    document = {"document_kind": kind, "document_status_label": document_status,
                "document_status_snapshot": TERMS[group]["status_snapshot"],
                "responsible_organization": None, "winter_responsibility_status": "unknown"}
    if group == "0831":
        class_matches = [row for row in obligation_rows if row["operation_technology"] == "anti_icing"
                         and row["target_class"] == "carriageway" and obj["class"] in ROAD_TYPES]
        operation_msg = class_matches[0]["operation"] + " (позиция 28, применимость к этому объекту уточняется)" if class_matches else (
            "Точная зимняя операция на этом объекте не установлена")
        return {
            **document, "customer": catalog["customer"], "contractor": None,
            "contract_party": CONTRACTOR, "contract_id": CONTRACT,
            "procurement_id": NOTICES[group], "document_url": catalog["eis_0831"],
            "territory_document_url": catalog["notice_0831"], "operation_evidence_url": catalog["contract_0831"],
            "period": PERIODS[group], "operation": operation_msg,
            "operation_technology_label": OPERATION_LABELS["anti_icing"] if class_matches else None,
            "operation_candidate_ids": [row["obligation_id"] for row in class_matches],
            "verified_segment_obligation_ids": [], "territorial_status": "notice_only_annex_unverified",
        }
    return {
        **document, "customer": catalog["customer"], "contractor": None, "contract_id": None,
        "procurement_id": NOTICES[group], "document_url": catalog["notice_0902"],
        "territory_document_url": catalog["notice_0902"], "operation_evidence_url": catalog["notice_0902"],
        "period": PERIODS[group], "operation": (
            "В закупке заявлены противогололёдная обработка и очистка снега. Применимость на участке неизвестна."
            if obj["class"] in ROAD_TYPES else
            "Заявлена влажная уборка стен, пола и лестницы. Дата начала неизвестна."
            if obj["class"] == "overpass" else "Операция на объекте уточняется в заявке и контракте."),
        "operation_technology_label": ("Противогололёдная обработка, очистка снега и льда"
                                       if obj["class"] in ROAD_TYPES else
                                       OPERATION_LABELS["routine_cleaning"] if obj["class"] == "overpass" else None),
        "operation_candidate_ids": [], "verified_segment_obligation_ids": [],
        "territorial_status": "planned_procurement",
    }


def reconciliation(obj, candidates, winter_types):
    relation = obj["relation"]
    base_ids = sorted({props["physical_road_id"] for _, props in candidates if props.get("physical_road_id")})
    if relation in FIRM_MATCH:
        common = sorted({props["reference_segment_id"] for _, props in candidates
                         if props.get("reference_segment_id")})
    else:
        common = []
    road = obj["class"] in ROAD_TYPES and relation in FIRM_MATCH
    return {
        "item_0831": obj["item"], "item_0902": obj.get("match_0902"),
        "object": obj["name"], "comparison": relation,
        "shared_reference_segment_ids": common,
        "shared_pilot_candidate_ids": base_ids if relation in FIRM_MATCH else [],
        "common_winter_operations": [row["operation_id"] for row in winter_types["same_class_examples"]
                                     if row["target_class"] == "carriageway"
                                     and row["operation_technology"] == "anti_icing"] if road else [],
        "additional_0902_operations": [row["operation_id"] for row in winter_types["additional_examples"]
                                      if row["target_class"] == "carriageway"] if road else [],
        "period_0831": TERMS["0831"], "period_0902": TERMS["0902"],
        "period_intersection": "possible_after_0902_signature_before_0831_end" if road else "undetermined",
        "overlap_assessment": "potential_only" if road else "undetermined",
        "confirmed_duplicate_assignment": False,
        "note": obj.get("bounds_note", "Совпадает название объекта. Границы подписанного приложения не получены."),
    }


POPUP = [
    ["name", "Объект"], ["responsibility_status_label", "Статус подтверждения"],
    ["responsible_organization", "Ответственный по документу"],
    ["responsibility_role_label", "Роль"],
    ["responsibility_evidence_scope", "Что подтверждено"],
    ["document_status_label", "Тип и статус документа"],
    ["document_status_snapshot", "Статус по последним доступным данным"],
    ["item", "Позиция"], ["customer", "Заказчик"],
    ["contract_party", "Подрядчик по контракту в целом"], ["contractor", "Подрядчик на объекте"],
    ["period", "Период"], ["operation_technology_label", "Технология"], ["operation", "Операция"],
    ["document_url", "Документ"], ["territory_document_url", "Перечень объектов"],
    ["operation_evidence_url", "Перечень операций"], ["scope_0902", "Сопоставление с № 0902"],
    ["document_status_0902", "Статус № 0902"], ["period_0902", "№ 0902 — период"], ["operation_0902", "№ 0902 — заявленные работы"],
    ["source_0902_url", "№ 0902 — извещение"], ["boundary_precision", "Точность границ"], ["bounds_note", "Что требует сверки"],
    ["physical_road_id", "Физический сегмент пилота"],
]
BOUNDARIES = {
    "road_axis_candidate": "Ось совпала по названию. Граница контракта не проверена.",
    "reference_line": "Справочная линия OSM вне исходного графа. Граница контракта не проверена.",
    "site_alternative": "Один из возможных контуров объекта.",
    "destination_landmark": "Ориентир конечной точки. Трасса проезда неизвестна.",
    "landmark": "Точечный ориентир OSM. Граница объекта неизвестна.",
    "unlocated": "Нет однозначной геометрии.",
}
RELATIONS = {
    "same_named_object": "Совпадает наименование объекта",
    "common_corridor_limits_differ": "Общий коридор, описание границ различается",
    "possible_same_object": "Возможное совпадение, требуется сверка",
    "nearby_distinct_or_unresolved": "Одна зона, точное соответствие не установлено",
    "no_listed_equivalent": "Отдельная позиция в перечне № 0902 не найдена",
    "only_0902": "Объект виден в № 0902",
    "possible_class_overlap_only": "В № 0831 есть работы по классу объекта, адрес требует сверки",
}


def responsibility_map(graph_directory, graph_version, catalog, contract_features):
    """Render responsibility in the frozen pilot, with one documented structure marker."""
    candidates = defaultdict(list)
    for feature in contract_features:
        props = feature["properties"]
        if props["physical_road_id"]:
            candidates[props["physical_road_id"]].append(props)
    seen, features = set(), []
    for feature in read(graph_directory / "road_network_enriched.geojson")["features"]:
        source = feature["properties"]
        segment = source["physical_road_id"]
        if segment in seen:
            continue
        seen.add(segment)
        matches = candidates[segment]
        if matches:
            props = dict(matches[0])
            props["item"] = ", ".join(str(row["item"]) for row in matches)
        else:
            props = {
                **confirmation("unknown"), "winter_responsibility_status": "unknown",
                "name": source["name"] or "Дорога без названия в OSM",
                "responsibility_evidence_scope": "В этом слое нет документов, устанавливающих ответственного за сегмент.",
                "physical_road_id": segment, "geometry_role": "pilot_context",
            }
        features.append({"type": "Feature", "id": segment, "geometry": feature["geometry"], "properties": props})

    overpass = [f for f in contract_features if f["properties"]["object_id"] == "0902:overpass_1"
                and f["geometry"] and f["geometry"]["type"] == "LineString"]
    if overpass:
        anchor = overpass[len(overpass) // 2]["geometry"]["coordinates"][0]
        features.append({
            "type": "Feature", "id": "asset:41:01:0010122:655",
            "geometry": {"type": "Point", "coordinates": anchor},
            "properties": {
                **confirmation("confirmed"), "winter_responsibility_status": "unknown",
                "name": "Надземный переход · Ленинградская, 49/1",
                "document_kind": "property_register", "document_status_label": "Муниципальный реестр имущества",
                "responsible_organization": catalog["customer"],
                "responsibility_role": "operational_management",
                "responsibility_role_label": "Оперативное управление сооружением",
                "responsibility_evidence_scope": "Право оперативного управления подтверждено опубликованной записью. "
                    "Дата актуализации реестра и зимний исполнитель на 20.11.2026 не установлены.",
                "period": "Метаданные опубликованного PDF — 23.05.2019",
                "document_url": catalog["overpass_property_register"],
                "boundary_precision": "Маркер сооружения по OSM, кадастровый № 41:01:0010122:655. Контур не установлен.",
                "geometry_role": "documented_asset_marker",
                "scope_0902": "Переход указан в будущем объёме закупки № 0902.",
                "document_status_0902": DOCUMENT_KIND["0902"][1],
                "source_0902_url": catalog["notice_0902"],
            },
        })
    return {
        "type": "FeatureCollection", "graph_version": graph_version, "as_of": catalog["as_of"],
        "attribution": OSM_ATTRIBUTION, "license_url": OSM_LICENSE_URL, "responsibility_states": STATUS,
        "popup_fields": [{"field": key, "label": label} for key, label in POPUP], "features": features,
    }


def export(graph_directory, output):
    catalog = read(INPUTS / "catalog.json")
    indexed = graph_index(graph_directory)
    graph_version, edges, _, _, _ = indexed
    osm = osm_inputs()
    obligation_rows, winter_types = operations(catalog)
    features, items, comparisons = [], [], []
    for group, objects in (("0831", catalog["objects"]), ("0902", catalog["other_0902"])):
        for obj in objects:
            object_id = f'{group}:{obj["item"]}'
            candidates = shapes(obj, osm, indexed)
            fields = popup_values(group, obj, catalog, obligation_rows)
            if group == "0831" and obj.get("match_0902") is not None:
                other = popup_values("0902", obj, catalog, obligation_rows)
                fields.update(period_0902=other["period"], operation_0902=other["operation"],
                              source_0902_url=other["document_url"], contractor_0902=None,
                              document_status_0902=other["document_status_label"])
            other_status = RELATIONS[obj["relation"]]
            match_0902 = obj.get("match_0902")
            if match_0902 is not None:
                other_status += f"; позиции № 0902: {match_0902}"
            if group == "0831":
                compared = reconciliation(obj, candidates, winter_types)
                comparisons.append(compared)
                if compared["overlap_assessment"] == "potential_only":
                    other_status += ". Возможен общий тип работ. Фактическое дублирование не подтверждено."
            fields.update(confirmation("preliminary" if any(g for g, _ in candidates) else "unknown"))
            fields["responsibility_evidence_scope"] = (
                "Объект сопоставлен по извещению. Назначение исполнителя на эту геометрию не подтверждено."
                if fields["responsibility_status"] == "preliminary" else
                "Объект указан в извещении. Однозначная привязка и ответственный на объекте не установлены."
            )
            base_ids, reference_ids, junction_ids = set(), set(), set()
            base_m, reference_m = 0.0, 0.0
            for i, (geometry, meta) in enumerate(candidates):
                physical = meta.get("physical_road_id")
                if physical:
                    base_ids.add(physical)
                    base_m += distance(*geometry["coordinates"])
                elif key := meta.get("reference_segment_id"):
                    reference_ids.add(key)
                    reference_m += distance(*geometry["coordinates"])
                junction_ids.update(meta.get("junction_anchor_segment_ids", ()))
                precision = BOUNDARIES[meta["geometry_role"]]
                props = {
                    **fields, **meta, "name": obj["name"], "item": obj["item"], "object_id": object_id,
                    "graph_version": graph_version, "object_class": obj["class"],
                    "scope_0902": other_status, "overlap_code": obj["relation"],
                    "boundary_precision": precision, "legal_boundary_accuracy_m": None,
                    "bounds_note": obj.get("bounds_note", "Документ задаёт наименование; метраж и границы не известны."),
                    "signed_territorial_boundary_verified": False,
                    "coord_source_note": "WGS84 OSM, назначение на геометрию не установлено",
                    "physical_road_id": physical,
                }
                features.append({"type": "Feature", "id":f"{object_id}:{i}", "geometry": geometry, "properties": props})
            items.append({
                **fields, "object_id": object_id, "name":obj["name"], "graph_version":graph_version,
                "object_class": obj["class"], "boundary_precision": sorted({BOUNDARIES[f[1]["geometry_role"]]
                                                                           for f in candidates}),
                "physical_road_ids": sorted(base_ids), "directed_edge_count": sum(len(edges[k]) for k in base_ids),
                "geometry_types": sorted({g["type"] for g, _ in candidates if g}),
                "reference_segment_ids": sorted(reference_ids), "junction_anchor_segment_ids": sorted(junction_ids),
                "pilot_candidate_axis_m": round(base_m, 1), "reference_axis_m": round(reference_m, 1),
                "signed_territorial_boundary_verified": False,
            })
    output.mkdir(parents=True, exist_ok=True)
    dump(output / "contract_scope.geojson", {
        "type":"FeatureCollection", "graph_version":graph_version, "as_of":catalog["as_of"],
        "attribution":OSM_ATTRIBUTION, "license_url":OSM_LICENSE_URL,
        "responsibility_states": STATUS,
        "popup_fields":[{"field":k, "label":v} for k,v in POPUP],
        "features":features,
    })
    dump(output / "object_links.json", items)
    dump(output / "contract_obligations.json", obligation_rows)
    dump(output / "overlap_0831_0902.json", {
        "as_of":catalog["as_of"], "graph_version":graph_version, "comparison":comparisons,
        "contract_0831":CONTRACT, "notice_0902":NOTICES["0902"], "operation_classes":winter_types,
        "actual_shared_execution_period":None, "actual_conflict_count":None,
        "confirmed_duplicate_count": 0, "verification_status": "potential_overlaps_only",
        "additional_0902":catalog["other_0902"], "osm_reference_sources":osm[-1],
        "class_only_checks":[
            {"target_class":name, "reason":"В № 0902 есть отдельный класс или адрес. Объектное назначение операции по № 0831 не опубликовано.",
             "common_operation_ids":[row["operation_id"] for row in winter_types["same_class_examples"]
                                     if row["target_class"] == name],
             "operation_technology": "routine_cleaning" if name == "overpass" else "anti_icing",
             "scope_conflict":"unverified", "period_intersection":"possible_after_0902_signature_before_0831_end"}
            for name in ("sidewalk", "bus_stop", "overpass")
        ],
        "period_qualification":"Сроки № 0902 условны; в поздней доступной редакции приём заявок до 30.11.2026.",
    })
    dump(output / "popup_fields.json", {"source":"contract_scope.geojson", "graph_version":graph_version,
        "feature_id":"GeoJSON Feature.id", "null_display":"Не установлено",
        "fields":[{"field":key, "label":label, "link":key.endswith("_url")} for key,label in POPUP],
        "geometry_layers":{"LineString":"line", "Point":"circle", "Polygon":"fill"},
        "responsibility_states": STATUS,
    })
    demo = responsibility_map(graph_directory, graph_version, catalog, features)
    dump(output / "responsibility_map.geojson", demo)
    template = Path(__file__).with_name("responsibility_map.html").read_text(encoding="utf-8")
    data = json.dumps(demo, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    (output / "responsibility_map.html").write_text(template.replace("__RESPONSIBILITY_DATA__", data), encoding="utf-8")
    print("Позиции № 0831:", len(comparisons))
    print("Сопоставления:", dict(Counter(row["comparison"] for row in comparisons)))
    print("Физические сегменты пилота:", len({k for row in items for k in row["physical_road_ids"]}))
    print("Позиции договора:", len(obligation_rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-directory", type=Path, default=PILOT_GEO)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    export(args.graph_directory, args.output)
