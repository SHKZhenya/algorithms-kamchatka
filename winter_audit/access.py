"""Evidence-based passability and organizational authority, independent of routing."""
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from snowops.contracts import Contract
from snowops.validation import validate
from pydantic import AwareDatetime, Field, model_validator

Evidence = Literal["observed", "official_report", "derived", "forecast",
                   "assumed", "simulated", "unknown"]
Operation = Literal["deadhead", "plow", "compact_plow", "grader", "blower",
                    "brush", "load", "haul", "excavate", "spread", "sidewalk_clear"]


class RoadCondition(Contract):
    edge_id: str
    source_id: str
    evidence: Evidence
    recorded_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime
    closed: bool | None = None
    operational_regime: str | None = None
    usable_width_m: float | None = Field(default=None, gt=0)
    snowfall_mm_h: float | None = Field(default=None, ge=0)
    snow_depth_cm: float | None = Field(default=None, ge=0)
    ice: bool | None = None
    usable_lanes: int | None = Field(default=None, ge=0)
    snowbanks: bool | None = None
    parked_vehicles: bool | None = None
    completed_operations: tuple[Operation, ...] = ()
    repeat_required: bool | None = None
    last_cleared_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_interval(self):
        if self.valid_from >= self.valid_to or not self.source_id.strip():
            raise ValueError("expected a source and a nonempty validity interval")
        return self


class RoadEvidence(Contract):
    edge_id: str
    source_id: str
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    recorded_at: AwareDatetime
    legal_access: bool | None = None
    winter_access: bool | None = None
    usable_width_m: float | None = Field(default=None, gt=0)
    max_mass_t: float | None = Field(default=None, gt=0)
    max_axle_t: float | None = Field(default=None, gt=0)
    max_height_m: float | None = Field(default=None, gt=0)
    max_width_m: float | None = Field(default=None, gt=0)
    engineering_grade_pct: float | None = None
    verified_operations: tuple[Operation, ...] = ()
    # True only after reviewing whether any additional clearance limits apply.
    limits_reviewed: bool = False

    @model_validator(mode="after")
    def check_interval(self):
        if self.effective_from >= self.effective_to or not self.source_id.strip():
            raise ValueError("expected a source and a nonempty validity interval")
        return self


class Capability(Contract):
    vehicle_type_id: str
    source_id: str
    operation: Operation
    confirmed: bool = False
    mass_t: float | None = Field(default=None, gt=0)
    max_axle_t: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    required_width_m: float | None = Field(default=None, gt=0)
    max_up_pct: float | None = Field(default=None, ge=0)
    max_down_pct: float | None = Field(default=None, ge=0)
    load_state: Literal["loaded", "empty", "unspecified"] = "unspecified"


class Assignment(Contract):
    edge_id: str
    organization_id: str
    source_document_id: str
    operation: Operation
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    recorded_at: AwareDatetime
    verification_status: Literal["confirmed_primary", "reported", "derived", "disputed", "unknown"]
    role: Literal["owner", "organizer", "customer", "executor", "inspector"]


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple[str, ...]

    def permits(self, policy):
        if policy not in {"research", "conservative", "manual"}:
            raise ValueError("unknown uncertainty policy")
        return self.status == "allowed" or (self.status == "unknown" and policy == "research")


def assess(edge_id, capability, start, end, conditions=(), evidence=(), *, as_of=None,
           simulate=False):
    """Only a confirmed negative or explicit scenario restriction can hard-block."""
    if start >= end:
        raise ValueError("passability requires a nonempty time interval")
    known_by = start if as_of is None else as_of
    states = [state for state in conditions if state.edge_id == edge_id
              and state.valid_from < end and start < state.valid_to
              and state.recorded_at <= known_by]
    official = {"observed", "official_report"}
    simulated = {"assumed", "simulated"} if simulate else set()
    for state in states:
        if state.closed and state.evidence in official | simulated:
            reason = "verified_closure" if state.evidence in official else "scenario_closure"
            return Decision("blocked", (reason,))
    rows = [road for road in evidence if road.edge_id == edge_id
            and road.effective_from < end and start < road.effective_to
            and road.recorded_at <= known_by]
    if any(r.legal_access is False or r.winter_access is False for r in rows):
        return Decision("blocked", ("verified_access_restriction",))
    if capability.required_width_m is not None and capability.confirmed:
        for state in states:
            if (state.evidence in official | simulated and state.usable_width_m is not None
                    and state.usable_width_m < capability.required_width_m):
                reason = "verified_width" if state.evidence in official else "scenario_width"
                return Decision("blocked", (reason,))
    unknown = set()
    if not capability.confirmed:
        unknown.add("vehicle_specification")
    covering = [r for r in rows if r.effective_from <= start and end <= r.effective_to]
    if not covering:
        unknown.update(("legal_access", "winter_access", "clearance", "grade"))
    for road in rows:
        if road.legal_access is not True:
            unknown.add("legal_access")
        if road.winter_access is not True:
            unknown.add("winter_access")
        if capability.operation not in road.verified_operations and capability.operation != "deadhead":
            unknown.add("service_site")
        grade = road.engineering_grade_pct
        limit = capability.max_up_pct if grade is not None and grade >= 0 else capability.max_down_pct
        checks = (
            ("width", capability.required_width_m, road.usable_width_m),
            ("mass", capability.mass_t, road.max_mass_t),
            ("axle", capability.max_axle_t, road.max_axle_t),
            ("height", capability.height_m, road.max_height_m),
            ("vehicle_width", capability.width_m, road.max_width_m),
            ("grade", abs(grade) if grade is not None else None, limit),
        )
        for name, value, cap in checks:
            if value is not None and cap is not None:
                if capability.confirmed and value > cap:
                    return Decision("blocked", ("verified_" + name,))
            elif not road.limits_reviewed or name in {"width", "grade"} or cap is not None:
                unknown.add(name)
    if any(value is None for value in (capability.mass_t, capability.max_axle_t,
                                       capability.height_m, capability.width_m)):
        unknown.add("vehicle_clearance_specification")
    if capability.operation != "deadhead" and not covering:
        unknown.add("service_site")
    return Decision("unknown" if unknown else "allowed", tuple(sorted(unknown)))


def has_authority(assignments, edge_id, operation, organization_id, start, end, as_of=None):
    known_by = start if as_of is None else as_of
    rows = [a for a in assignments if a.edge_id == edge_id and a.operation == operation
            and a.role == "executor" and a.effective_from < end and start < a.effective_to
            and a.recorded_at <= known_by]
    if any(a.verification_status == "disputed" for a in rows):
        return False
    return any(a.verification_status == "confirmed_primary" and a.source_document_id.strip()
               and a.organization_id == organization_id
               and a.effective_from <= start and end <= a.effective_to for a in rows)


def replay(graph, snapshot, routes, capabilities, *, conditions=(), evidence=(),
           assignments=(), organization_id=None, simulate=False):
    """Recheck the expanded actions, including return, against temporal layers."""
    math = validate(graph, snapshot, routes)
    issues = []
    task_operations = {t.task_id: t.capability for t in snapshot.tasks}
    for route in routes:
        for index, action in enumerate(route.actions):
            if action.edge_id is None:
                continue
            operation = task_operations.get(action.task_id, "deadhead") if action.mode == "service" else "deadhead"
            capability = capabilities[route.vehicle_id, operation]
            start = snapshot.as_of + timedelta(seconds=action.start_s)
            end = snapshot.as_of + timedelta(seconds=action.end_s)
            decision = assess(action.edge_id, capability, start, end, conditions, evidence,
                              as_of=snapshot.as_of, simulate=simulate)
            authority = operation == "deadhead" or has_authority(
                assignments, action.edge_id, operation, organization_id, start, end, snapshot.as_of)
            if decision.status != "allowed" or not authority:
                issues.append(dict(vehicle_id=route.vehicle_id, action_index=index,
                                   edge_id=action.edge_id, operation=operation,
                                   status=decision.status, reasons=decision.reasons,
                                   service_authority_confirmed=authority))
    mathematical = not math.violations
    operational = mathematical and not issues and not simulate
    return dict(graph_version=graph.version, mathematically_valid=mathematical,
                structurally_valid=all(v.code == "unserved_required_passes" for v in math.violations),
                operationally_confirmed=operational, scenario_only=not operational,
                unserved_tasks=math.unserved_tasks, issues=issues)


def service_tasks(snapshot, capabilities, *, conditions=(), evidence=(), assignments=(),
                  organization_id=None, profile_policy="research", simulate=False):
    """Return eligible jobs and held jobs before invoking the unchanged solvers."""
    eligible, held = [], {}
    for task in snapshot.tasks:
        start = snapshot.as_of + timedelta(seconds=task.earliest_s)
        end = snapshot.as_of + timedelta(seconds=task.deadline_s)
        if organization_id is not None and not has_authority(
                assignments, task.edge_id, task.capability, organization_id, start, end, snapshot.as_of):
            held[task.task_id] = "service_authority_unconfirmed"
            continue
        candidates = [v for v in snapshot.fleet if task.capability in v.capabilities]
        permitted = [v for v in candidates if assess(task.edge_id, capabilities[v.vehicle_id, task.capability],
                     start, end, conditions, evidence, as_of=snapshot.as_of,
                     simulate=simulate).permits(profile_policy)]
        if len(permitted) == len(candidates) and permitted:
            eligible.append(task)
        elif not permitted:
            held[task.task_id] = "service_unavailable_under_policy"
        else:
            raise ValueError("different per-vehicle service eligibility requires separate solver batches")
    return tuple(eligible), held
