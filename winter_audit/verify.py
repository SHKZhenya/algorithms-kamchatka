"""Verify the exported IDs, directed relief, provenance, and route constraints."""
import argparse
import csv
import gzip
import json
from pathlib import Path


def verify(root):
    def geo(layer):
        data = json.loads((root / "geo" / f"{layer}.geojson").read_text())
        assert data["type"] == "FeatureCollection" and data["features"], layer
        return data["features"]

    def rows(table):
        with (root / "tables" / f"{table}.csv").open(encoding="utf-8", newline="") as f:
            data = list(csv.DictReader(f))
        assert data and all(None not in r and None not in r.values() for r in data), table
        return data

    summary = json.loads((root / "audit_metrics.json").read_text())
    roads = {f["properties"]["edge_id"]: f for f in geo("road_network_enriched")}
    assert len(roads) == summary["osm_audit"]["edges"]
    physical = {r["physical_road_id"]: r for r in rows("physical_segments")}
    assert len(physical) == summary["physical_segments"]
    check = {e for segment in physical.values() for e in json.loads(segment["directed_edge_ids"])}
    assert check == roads.keys()
    slopes = {f["properties"]["edge_id"]: f for f in geo("road_slopes")}
    assert slopes.keys() == roads.keys()
    sources = {r["source_id"] for r in rows("sources")}
    for key, road in roads.items():
        slope = slopes[key]
        assert road["geometry"] == slope["geometry"]
        p, s = road["properties"], slope["properties"]
        assert p["physical_road_id"] in physical
        assert p["graph_version"] == s["graph_version"] == summary["graph_version"]
        assert p["source_id"] in sources and s["source_id"] in sources
        for lon, lat in road["geometry"]["coordinates"]:
            assert 158 < lon < 159 and 53 < lat < 54
        reverse = f'osm:{p["osm_way_id"]}:{p["to_node"]}:{p["from_node"]}'
        if reverse not in roads or s["mean_grade_pct"] is None:
            continue
        rev = slopes[reverse]["properties"]
        assert abs(s["mean_grade_pct"]+rev["mean_grade_pct"]) <= .001
        assert abs(s["ascent_m"]-rev["descent_m"]) <= .002
    assert all(p["properties"]["legal_scope_confirmed"] is False for p in geo("responsibility_segments"))
    organizations = {row["organization_id"] for row in rows("organizations")}
    territories = {row["territory_id"] for row in rows("contract_territories")}
    for row in rows("responsibility_assignments"):
        assert row["source_document_id"] in sources
        assert row["territory_id"] in territories and row["organization_id"] in organizations
        assert row["verification_status"] != "confirmed_primary"
    for layer in ("road_restrictions", "data_quality_flags", "infrastructure_objects", "pedestrian_connections"):
        assert all(f["properties"]["source_id"] in sources for f in geo(layer))
    ex = root / "experiments"
    closure = json.loads((ex / "winter_condition_example.json").read_text())[0]["edge_id"]
    assert closure in roads
    for solver in ("B0", "O1"):
        closed = json.loads((ex / f"W-{solver}.plan.json").read_text())
        base = json.loads((ex / f"G0-{solver}.plan.json").read_text())
        assert any(a["edge_id"] == closure for r in base["solver"]["routes"] for a in r["actions"])
        assert all(a["edge_id"] != closure for r in closed["solver"]["routes"] for a in r["actions"])
        assert base["validation"]["completed_passes"] == closed["validation"]["completed_passes"] == 28
    validations = json.loads((ex / "validation_report.json").read_text())
    assert all(v["mathematically_valid"] and not v["operationally_confirmed"]
               for v in validations.values())
    with gzip.open(ex / "vehicle_accessibility.csv.gz", "rt", encoding="utf-8") as file:
        accessibility = csv.DictReader(file)
        count = sum(1 for row in accessibility if row["status"] == "unknown"
                    and row["edge_id"] in roads and row["conservative_allowed"] == "False")
    assert count == len(roads)*len(rows("vehicle_capabilities"))
    print(f"Verified {len(roads)} arcs, {len(physical)} segments, {count} accessibility records and 8 plans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    verify(parser.parse_args().output)
