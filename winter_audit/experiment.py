"""Project whole-shift restrictions into the existing B0/O1 input contract."""
from collections import Counter
from datetime import timedelta
from dataclasses import replace

from snowops.pilot import scenario_factory
from snowops.reporting import geojson, make_plan
from snowops.routing import check_references
from snowops.solvers import GreedySolver, PoolSolver

from .access import Capability, RoadCondition, assess, replay


def common_closures(graph, snapshot, capabilities, conditions=(), evidence=(),
                    policy="research", simulate=False):
    """Compile a shared transit graph; mixed class-specific closures cannot be projected."""
    shared = []
    for vehicle in snapshot.fleet:
        start = snapshot.as_of + timedelta(seconds=vehicle.shift_start_s)
        end = snapshot.as_of + timedelta(seconds=vehicle.shift_end_s)
        profile = capabilities[vehicle.vehicle_id, "deadhead"]
        closed = {edge_id for edge_id in graph.edges
                  if not assess(edge_id, profile, start, end, conditions, evidence,
                                as_of=snapshot.as_of, simulate=simulate).permits(policy)}
        shared.append(closed | set(snapshot.closed_edges))
    if any(edges != shared[0] for edges in shared[1:]):
        raise ValueError("different per-vehicle transit graphs require separate solver batches")
    return tuple(sorted(shared[0]))


def run(graph, output, dump, limit, slopes):
    scenario = scenario_factory(graph, shift_s=2400, critical_deadline_s=1500)
    scenario = scenario.model_copy(update={"respect_osm_speed_limits": True})
    caps = {(vehicle.vehicle_id, op): Capability(vehicle_type_id="demo-plow",
            operation=op, source_id="scenario") for vehicle in scenario.fleet for op in ("deadhead", "plow")}
    solvers = {"B0": GreedySolver(), "O1": PoolSolver()}
    results, audits, comparison, definitions = [], {}, [], []
    baselines, original_steps = {}, Counter()
    enriched = replace(graph, edges={
        edge_id: replace(edge, slope_pct=slopes.get(edge_id))
        for edge_id, edge in graph.edges.items()
    })
    cases = [("G0", (), False), ("G1", (), False), ("G2", (), False)]
    immutable_targets = {task.edge_id for task in scenario.tasks}
    for variant, conditions, simulate in cases:
        active_graph = enriched if variant in {"G2", "W"} else graph
        closures = common_closures(active_graph, scenario, caps, conditions, simulate=simulate)
        snapshot = scenario.model_copy(update={"scenario_id": f"winter-{variant}",
                    "closed_edges": closures, "constraints_version": f"winter-audit-1-{variant}"})
        check_references(active_graph, snapshot)
        dump(output / f"{variant}.scenario.json", snapshot.model_dump(mode="json"))
        definitions.append(dict(variant=variant, graph_version=graph.version,
            directed_available_research=len(graph.edges)-len(closures),
            directed_deployable_confirmed=0, fixed_tasks=len(snapshot.tasks),
            tasks_on_closed_edges=sum(t.edge_id in closures for t in snapshot.tasks),
            tasks_unknown_winter=sum(t.edge_id not in closures for t in snapshot.tasks),
            status="scenario_only" if simulate else "executed",
            dsm_annotation_applied=variant in {"G2", "W"}))
        for name, solver in solvers.items():
            result = solver.solve(active_graph, snapshot, limit)
            plan = make_plan(active_graph, snapshot, result)
            key = f"{variant}-{name}"
            dump(output / f"{key}.plan.json", plan.model_dump(mode="json"))
            dump(output / f"{key}.routes.geojson", geojson(active_graph, plan))
            audit = replay(active_graph, snapshot, result.routes, caps, conditions=conditions, simulate=simulate)
            assert not any(item["status"] == "blocked" for item in audit["issues"])
            audits[key] = audit
            sequences = {route.vehicle_id: tuple((a.edge_id, a.mode, a.task_id) for a in route.actions)
                         for route in result.routes}
            if variant == "G0":
                baselines[name] = sequences
                original_steps.update(a.edge_id for route in result.routes for a in route.actions
                                      if a.mode in {"deadhead", "return"})
            universe = set(sequences) | set(baselines[name])
            changed = sum(sequences.get(key) != baselines[name].get(key) for key in universe)
            metrics = plan.metrics
            results.append(dict(variant=variant, solver=name, graph_version=graph.version,
                input_hash=snapshot.input_hash, required_passes=plan.validation.required_passes,
                completed_passes=plan.validation.completed_passes, unserved=len(plan.validation.unserved_tasks),
                late_actions=len(plan.validation.late_tasks),
                scenario_missed_or_late=len(set(plan.validation.unserved_tasks) | set(plan.validation.late_tasks)),
                legal_deadline_breaches=None, changed_routes=changed, reference_route_union=len(universe),
                changed_share=round(changed / len(universe), 4) if universe else 0,
                total_km=round(metrics.deadhead_km+metrics.return_km+metrics.service_km, 3),
                service_km=metrics.service_km, deadhead_with_return_km=round(metrics.deadhead_km+metrics.return_km, 3),
                vehicle_s=metrics.vehicle_seconds, variable_scenario_rub=metrics.C_var_rub,
                unverified_actions=len(audit["issues"]), mathematical_valid=audit["mathematically_valid"],
                operationally_confirmed=audit["operationally_confirmed"], termination=result.termination))
        if variant == "G2":
            target = next(edge for edge, _ in original_steps.most_common() if edge not in immutable_targets)
            closure = RoadCondition(edge_id=target, source_id="scenario", evidence="assumed",
                recorded_at=scenario.as_of, valid_from=scenario.as_of,
                valid_to=scenario.as_of+timedelta(hours=1), closed=True)
            cases.append(("W", (closure,), True))
            dump(output / "winter_condition_example.json", [closure.model_dump(mode="json")])
    definitions.append(dict(variant="G3", graph_version=graph.version,
        directed_available_research=len(graph.edges), directed_deployable_confirmed=0,
        fixed_tasks=len(scenario.tasks), tasks_on_closed_edges=None, tasks_unknown_winter=len(scenario.tasks),
        status="blocked_no_confirmed_current_service_scope"))
    critical = [dict(edge_id=edge, traversals=count) for edge, count in original_steps.most_common()]
    return definitions, results, audits, critical
