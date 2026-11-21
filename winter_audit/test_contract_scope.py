"""Regression checks for independently linked scope, obligations and map layers."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from .contract_scope import CONTRACT, PILOT_GEO, export, graph_index, read


class ContractScopeTests(TestCase):
    def test_export_keeps_evidence_and_geometry_separate(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            export(PILOT_GEO, output)
            geo = read(output / "contract_scope.geojson")
            items = read(output / "object_links.json")
            obligations = read(output / "contract_obligations.json")
            reviewed = read(output / "overlap_0831_0902.json")["comparison"]
            demo = read(output / "responsibility_map.geojson")
            self.assertNotIn("__RESPONSIBILITY_DATA__", (output / "responsibility_map.html").read_text())

        version, edges, geometry, _, _ = graph_index(PILOT_GEO)
        self.assertEqual(geo["graph_version"], version)
        self.assertEqual([item["object_id"] for item in items if item["object_id"].startswith("0831:")],
                         [f"0831:{i}" for i in range(1, 19)])
        self.assertEqual([item["contract_line"] for item in obligations], list(range(1, 44)))
        by_line = {row["contract_line"]: row for row in obligations}
        self.assertEqual([r["contract_line"] for r in obligations if r["explicit_winter_maintenance"]],
                         [28, 29, 30, 31])
        for line in (2, 3, 33, 37, 38, 39):
            self.assertEqual(by_line[line]["operation_technology"], "routine_cleaning")
        self.assertEqual(by_line[40]["operation_technology"], "material_handling")
        self.assertEqual(by_line[42]["operation_technology"], "waste_management")
        self.assertTrue(all(row["contract_id"] == CONTRACT and not row["approved_segment_ids"] for row in obligations))
        self.assertEqual(len({f["id"] for f in geo["features"]}), len(geo["features"]))
        for feature in geo["features"]:
            props = feature["properties"]
            self.assertFalse(props["signed_territorial_boundary_verified"])
            self.assertFalse(props["verified_segment_obligation_ids"])
            self.assertIsNone(props["contractor"])
            self.assertIsNone(props["responsible_organization"])
            self.assertEqual(props["responsibility_status"], "preliminary" if feature["geometry"] else "unknown")
            if props["procurement_id"].endswith("0902"):
                self.assertEqual(props["document_kind"], "procurement_notice")
                self.assertNotIn("contract_party", props)
            else:
                self.assertEqual(props["document_kind"], "signed_contract")
                self.assertTrue(props["contract_party"])
            if segment := props["physical_road_id"]:
                self.assertEqual(feature["geometry"], geometry[segment])
                self.assertEqual(set(props["directed_edge_ids"]), set(edges[segment]))
            self.assertTrue(set(props.get("junction_anchor_segment_ids", ())) <= edges.keys())
        self.assertFalse(next(row for row in items if row["object_id"] == "0831:14")["geometry_types"])
        by_item = {row["item_0831"]: row for row in reviewed}
        self.assertEqual(by_item[4]["item_0902"], 4)
        self.assertTrue(by_item[4]["shared_pilot_candidate_ids"])
        self.assertEqual(by_item[9]["comparison"], "no_listed_equivalent")
        self.assertEqual(by_item[17]["comparison"], "nearby_distinct_or_unresolved")
        self.assertTrue(all(not row["confirmed_duplicate_assignment"] for row in reviewed))
        self.assertEqual(by_item[4]["overlap_assessment"], "potential_only")
        self.assertEqual(set(demo["responsibility_states"]), {"confirmed", "preliminary", "unknown"})
        pilot = [f for f in demo["features"] if f["geometry"]["type"] == "LineString"]
        self.assertEqual({f["id"] for f in pilot}, geometry.keys())
        self.assertEqual(len(pilot), len(geometry))
        self.assertEqual(sum(f["properties"]["responsibility_status"] == "preliminary" for f in pilot), 311)
        confirmed = [f for f in demo["features"] if f["properties"]["responsibility_status"] == "confirmed"]
        self.assertEqual([f["id"] for f in confirmed], ["asset:41:01:0010122:655"])
        self.assertTrue(all(f["properties"]["winter_responsibility_status"] == "unknown" for f in demo["features"]))
