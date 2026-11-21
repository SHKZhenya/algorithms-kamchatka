"""Paired 360-task study on fixed workload, with independent replay of each vehicle batch."""
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from snowops.economics import evaluate
from snowops.infrastructure import Infrastructure
from snowops.osm import load_osm
from snowops.solvers import GreedySolver, PoolSolver
from snowops.validation import validate

from .build import ROOT, dump, table
from .contract_scope import read

PROFILES = {
    "truck-plow": {"mass_t": 20.5, "capability": "plow"},
    "compact-plow": {"mass_t": 4.5, "capability": "compact_plow"},
    "grader": {"mass_t": 16.3, "capability": "grader"},
}
VARIANTS = {
    "baseline": set(), "closure": {"closure"}, "grade": {"grade"},
    "mass": {"mass"}, "territories": {"territories"},
    "combined": {"closure", "grade", "mass", "territories"},
}


def base_inputs(count):
    graph = load_osm(ROOT / "data/pkc/city.osm.gz")
    infra = Infrastructure(graph)
    seed = infra.scenario(count, fleet_size=12, shift_s=14400)
    # The underlying generator uses 4 repeating profile slots. Assign exclusive
    # work classes for this experiment and add two heavy units for an 8/3/3 fleet.
    fleet = list(seed.fleet)
    before = Counter(v.profile_id for v in fleet)
    for profile, required in (("truck-plow", 8), ("compact-plow", 3), ("grader", 3)):
        if before[profile] and before[profile] < required:
            exemplar = next(v for v in fleet if v.profile_id == profile)
            for _ in range(required - before[profile]):
                fleet.append(exemplar.model_copy(update={"vehicle_id": f"unit-{len(fleet)+1:02}"}))
    fleet = tuple(v.model_copy(update={"capabilities": (PROFILES[v.profile_id]["capability"],)}) for v in fleet)
    return graph, seed.model_copy(update={"fleet": fleet})


def inputs_for_study(graph):
    output = ROOT / "winter_audit/output"
    links = read(output / "contract_scope/object_links.json")
    by_item = {row["object_id"]: set(row["physical_road_ids"]) for row in links}
    named = set.union(*(segments for key, segments in by_item.items() if key.startswith("0831:")
                       and key != "0831:9"))
    signed_candidates = {e.edge_id for e in graph.edges.values() if e.physical_road_id in named}
    mass_axis = by_item["0831:2"] | by_item["0831:6"]
    mass = {e.edge_id for e in graph.edges.values() if e.physical_road_id in mass_axis}
    # Full closure of the pilot-visible corridor is a sensitivity input. The historic
    # construction notice corroborates the corridor, not this reconstructed extent.
    closure = {e.edge_id for e in graph.edges.values() if e.physical_road_id in by_item["0831:15"]}
    slope_rows = read(output / "geo/road_slopes.geojson")["features"]
    grade = {p["edge_id"]: p["mean_grade_pct"] for f in slope_rows if (p := f["properties"])
             and p["quality"] == "dsm_proxy" and p["length_unrounded_m"] >= 70
             and (p["mean_grade_pct"] >= 10 or p["mean_grade_pct"] <= -8)}
    return signed_candidates, mass, closure, grade


def batches(snapshot, territory_edges, zoned):
    profiles = defaultdict(list)
    for vehicle in snapshot.fleet:
        profiles[vehicle.profile_id].append(vehicle)
    for profile, fleet in profiles.items():
        tasks = [t for t in snapshot.tasks if t.capability == PROFILES[profile]["capability"]]
        if zoned:
            owned = [t for t in tasks if t.edge_id in territory_edges]
            elsewhere = [t for t in tasks if t.edge_id not in territory_edges]
            if owned:
                take = max(1, min(len(fleet)-1, round(len(fleet)*len(owned)/len(tasks))))
                yield profile, "0831_candidate", fleet[:take], owned
                yield profile, "other_scenario", fleet[take:], elsewhere
                continue
        yield profile, "pooled", fleet, tasks


def replay_snapshot(base, tasks, vehicles, closed, tag):
    tasks_by_id = {t.task_id for t in tasks}
    sites = tuple(site.model_copy(update={"task_ids": tuple(t for t in site.task_ids if t in tasks_by_id)})
                  for site in base.priority_sites)
    return base.model_copy(update={"scenario_id": tag, "tasks":tuple(tasks), "fleet":tuple(vehicles),
                                   "closed_edges":tuple(sorted(closed)), "priority_sites":sites})


def run(count, limit, out):
    graph, base = base_inputs(count)
    territory_edges, mass, closure, grade = inputs_for_study(graph)
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "workload.json", base.model_dump(mode="json"))
    dump(out / "scenario_constraints.json", {
        "graph_version": graph.version, "fleet_profiles": PROFILES,
        "closures": sorted(closure), "mass_limit_t": 10, "mass_limit_edges": sorted(mass),
        "grade_slowdowns": {e:{"grade_pct":v,"scenario_cap_kmh":{"truck-plow":8,"grader":7,"compact-plow":10}} for e,v in grade.items()},
        "territory_candidate_edges":sorted(territory_edges),
        "as_of":base.as_of.isoformat(), "these_layers_are_sensitivities":True,
    })
    solvers = {"B0": GreedySolver(), "O1": PoolSolver(trials=3)}
    summary, per_batch = [], []
    for variant, factors in VARIANTS.items():
        for solver_name, solver in solvers.items():
            routes, served, blocked = [], set(), set()
            started = perf_counter()
            evidence = []
            for profile, zone, fleet, tasks in batches(base, territory_edges, "territories" in factors):
                if not tasks:
                    continue
                closed = set()
                if "closure" in factors:
                    closed |= closure
                if "mass" in factors and PROFILES[profile]["mass_t"] > 10:
                    closed |= mass
                network = graph
                if "grade" in factors:
                    cap = {"truck-plow":8, "grader":7, "compact-plow":10}[profile]
                    network = replace(graph, edges={eid: replace(edge, speed_limit_kmh=min(edge.speed_limit_kmh or cap, cap))
                                                   if eid in grade else edge for eid, edge in graph.edges.items()})
                constrained = replay_snapshot(base, tasks, fleet, closed, f"{variant}-{profile}-{zone}")
                solution = solver.solve(network, constrained, limit)
                validation = validate(network, constrained, solution.routes)
                assert all(v.code == "unserved_required_passes" for v in validation.violations), validation
                assert not any(a.edge_id in closed for r in solution.routes for a in r.actions)
                served.update(set(t.task_id for t in tasks) - set(validation.unserved_tasks))
                blocked.update(t.task_id for t in tasks if t.edge_id in closed)
                routes.extend(solution.routes)
                record = {"variant":variant,"solver":solver_name,"profile":profile,"zone":zone,
                          "vehicles":len(fleet),"tasks":len(tasks),"unserved":len(validation.unserved_tasks),
                          "closed_arcs":len(closed),"termination":solution.termination,
                          "pool_size":solution.pool_size,"snapshot_hash":constrained.input_hash}
                evidence.append(record)
                per_batch.append(record)
            combined = validate(graph, base, tuple(routes))
            assert {v.code for v in combined.violations} <= {"unserved_required_passes"}
            assert len(served) == count - len(combined.unserved_tasks)
            costs = evaluate(graph, base.tariff, tuple(routes))
            row = dict(variant=variant, solver=solver_name, graph_version=graph.version, tasks=count,
                       completed=len(served), unserved=count-len(served), directly_blocked=len(blocked),
                       critical_missed=sum(t.priority == 0 and t.task_id not in served for t in base.tasks),
                       vehicles_used=costs["vehicles_used"], deadhead_km=costs["deadhead_km"],
                       return_km=costs["return_km"], service_km=costs["service_km"],
                       vehicle_seconds=costs["vehicle_seconds"], cost_scenario_rub=costs["C_var_rub"],
                       runtime_s=round(perf_counter()-started,3))
            summary.append(row)
            dump(out / f"{variant}-{solver_name}.json", {"summary":row,"batches":evidence,
                "unserved_tasks":combined.unserved_tasks,
                "routes":[route.model_dump(mode="json") for route in routes]})
            print(row, flush=True)
    table(out / "comparison.csv", summary)
    table(out / "batches.csv", per_batch)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=360)
    parser.add_argument("--limit", type=float, default=9)
    parser.add_argument("--output", type=Path, default=ROOT / "winter_audit/output/field_experiment")
    args = parser.parse_args()
    run(args.count, args.limit, args.output)
