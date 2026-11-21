from datetime import datetime, timedelta, timezone
from unittest import TestCase

from winter_audit.access import Assignment, Capability, RoadCondition, RoadEvidence, assess, has_authority

WHEN = datetime(2026, 11, 20, tzinfo=timezone.utc)
LATER = WHEN + timedelta(hours=1)


class WinterEvidenceTests(TestCase):
    def setUp(self):
        self.vehicle = Capability(vehicle_type_id="test", operation="deadhead", source_id="spec",
            confirmed=True, mass_t=8, max_axle_t=5, height_m=3, width_m=2.2, required_width_m=3,
            max_up_pct=11, max_down_pct=8)
        self.road = RoadEvidence(edge_id="edge", source_id="survey", recorded_at=WHEN,
            effective_from=WHEN, effective_to=LATER, winter_access=True, legal_access=True,
            usable_width_m=3.5, max_mass_t=10, limits_reviewed=True, engineering_grade_pct=9)

    def test_confirmed_transit_and_direction_specific_grade(self):
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, evidence=[self.road]).status, "allowed")
        downhill = self.road.model_copy(update={"engineering_grade_pct": -9})
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, evidence=[downhill]).status, "blocked")

    def test_unknown_clearance_remains_unknown(self):
        decision = assess("edge", self.vehicle, WHEN, LATER)
        self.assertEqual(decision.status, "unknown")
        self.assertTrue(decision.permits("research"))
        self.assertFalse(decision.permits("conservative"))
        self.assertFalse(decision.permits("manual"))

    def test_short_official_closure_overlaps_but_expires(self):
        closed = RoadCondition(edge_id="edge", source_id="notice", evidence="official_report",
            recorded_at=WHEN, valid_from=WHEN, valid_to=LATER, closed=True)
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, [closed]).status, "blocked")
        self.assertEqual(assess("edge", self.vehicle, LATER, LATER+timedelta(seconds=1), [closed]).status, "unknown")
        forecast = closed.model_copy(update={"evidence":"forecast"})
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, [forecast]).status, "unknown")
        assumed = closed.model_copy(update={"evidence":"assumed"})
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, [assumed]).status, "unknown")
        self.assertEqual(assess("edge", self.vehicle, WHEN, LATER, [assumed], simulate=True).status, "blocked")

    def test_ownership_does_not_authorize_service(self):
        assignment = Assignment(edge_id="edge", organization_id="a", source_document_id="document",
            operation="plow", role="owner", recorded_at=WHEN, effective_from=WHEN,
            effective_to=LATER, verification_status="confirmed_primary")
        self.assertFalse(has_authority([assignment], "edge", "plow", "a", WHEN, LATER))
        executor = assignment.model_copy(update={"role":"executor"})
        self.assertTrue(has_authority([executor], "edge", "plow", "a", WHEN, LATER))
        conflict = executor.model_copy(update={"organization_id":"b", "verification_status":"disputed"})
        self.assertFalse(has_authority([executor, conflict], "edge", "plow", "a", WHEN, LATER))
