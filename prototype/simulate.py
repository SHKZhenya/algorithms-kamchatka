"""Reproducible synthetic comparison of plowing policies."""
import argparse
import csv
from pathlib import Path
from time import perf_counter

import numpy as np


DT = 0.25
STEPS = 32
LOOKAHEAD = 8
CANDIDATES = 128
NODES = 12
DEPOT = 0
EDGES = [(node, (node + offset) % NODES)
         for node in range(NODES) for offset in (1, -1)]
DEST = np.array([end for _, end in EDGES])
AREA = np.linspace(700, 1400, len(EDGES))
DRY = np.linspace(750, 1100, len(EDGES))
CORRIDORS = ([0, 2, 4], [1, 23, 21])
POLICIES = ("B0", "B1", "B1-W", "B2")
RISK_LIMIT = 1.25
CAPACITY_LIMIT = 0.65
SAFETY_BUDGET = 1.0
MOBILITY_BUDGET = 0.25 * STEPS * DT


def step(state, snowfall, mode, service):
    """Advance weather and apply completed passes. Return state and net input."""
    snow, ice, bank = np.moveaxis(state, -2, 0)
    snow = snow + DT * snowfall
    freeze_rate, melt_rate = {
        "B0": (0, 0), "B1": (0, 0), "B1-W": (0, 0),
        "B2": (0.075, 0.045), "truth": (0.095, 0.065),
    }[mode]
    freeze = np.minimum(snow, DT * freeze_rate * snow)
    snow -= freeze
    melt = np.minimum(snow, DT * melt_rate * snow)
    snow -= melt
    ice = ice + freeze
    eta = 0.78
    removal_fraction = 0.72 if mode == "truth" else 0.8
    removed = removal_fraction * snow * service
    snow -= removed
    bank = bank + eta * AREA * removed
    net = np.sum(AREA * (DT * snowfall - melt - (1 - eta) * removed), axis=-1)
    return np.stack((snow, ice, bank), axis=-2), net


def metrics(state, visibility_factor):
    snow, ice, bank = np.moveaxis(state, -2, 0)
    proxy = snow / 2.5 + ice / 0.8
    risk_hours = DT * np.maximum(proxy - RISK_LIMIT, 0).mean(axis=-1)
    capacity = DRY * visibility_factor * np.exp(-0.12 * snow - 0.14 * ice
                                               - 0.08 * bank / AREA)
    ratios = np.stack([capacity[..., path].min(axis=-1) / DRY[path].min()
                       for path in CORRIDORS], axis=-1)
    return risk_hours, DT * (ratios < CAPACITY_LIMIT), ratios


def candidate_routes(position, horizon, start, decision_seed):
    """Each interval consumes a whole traversal or wait on this toy graph."""
    rng = np.random.default_rng(decision_seed)
    current = np.repeat(position[None, :], CANDIDATES, axis=0)
    paths = np.empty((horizon, CANDIDATES, len(position)), dtype=int)
    for lag in range(horizon):
        remaining = STEPS - start - lag - 1
        # Codes 0..4 mean wait, two deadheads and two service traversals.
        next_nodes = (current[..., None] + [0, 1, -1, 1, -1]) % NODES
        valid = np.minimum(next_nodes, NODES - next_nodes) <= remaining
        scores = rng.random(valid.shape)
        scores[0, :, 0] = 2  # Include a low-cost wait/return candidate.
        scores[0, :, 1:3] += 0.1
        action = np.where(valid, scores, -1).argmax(axis=-1)
        paths[lag] = np.where(action == 0, -1,
                              4 * current + (action - 1) % 2 + 2 * (action >= 3))
        current = np.take_along_axis(next_nodes, action[..., None], axis=-1)[..., 0]
    return paths


def decode(actions):
    edge = 2 * (actions.clip(min=0) // 4) + actions.clip(min=0) % 2
    service = (actions >= 0) & (actions % 4 >= 2)
    serviced = ((edge[..., None] == np.arange(len(EDGES)))
                & service[..., None]).any(axis=-2)
    cost = np.where(service, 1.0, 0.55 * (actions >= 0)).sum(axis=-1)
    return edge, serviced, cost


def plan(policy, position, state, snowfall, visibility, start, seed, risk_used, bad_used):
    paths = candidate_routes(position, len(snowfall), start, [seed, start])
    _, service, cost = decode(paths)
    predicted = np.broadcast_to(state, (CANDIDATES, *state.shape))
    risk_sum = np.full(CANDIDATES, risk_used)
    bad_sum = np.tile(bad_used, (CANDIDATES, 1))
    for lag, active in enumerate(service):
        predicted, _ = step(predicted, snowfall[lag], policy, active)
        risk, bad, _ = metrics(predicted, visibility[lag])
        risk_sum += risk
        bad_sum += bad
    excess_risk = (risk_sum - SAFETY_BUDGET).clip(min=0)
    excess_bad = (bad_sum - MOBILITY_BUDGET).clip(min=0).max(axis=-1)
    best = np.lexsort((cost.sum(axis=0), excess_bad, excess_risk))[0]
    return paths[:, best]


def build_event(seed, forecast_bias, missing):
    rng = np.random.default_rng(seed)
    time = DT * np.arange(STEPS)
    peak = rng.uniform(2.0, 4.4)
    snowfall = rng.uniform(1.2, 2.4) * np.exp(-0.5 * ((time - peak) / 1.3) ** 2)
    snowfall = snowfall[:, None] * rng.uniform(0.65, 1.45, len(EDGES))
    snowfall *= 1 + 0.2 * np.sin(time[:, None] * 2 + np.arange(len(EDGES)))
    visibility = np.clip(1 - 0.12 * snowfall.mean(axis=1), 0.65, 1.0)
    initial = np.stack((rng.uniform(0.2, 2.0, len(EDGES)),
                        rng.uniform(0, 0.18, len(EDGES)), np.zeros(len(EDGES))))
    truth_padded = np.pad(snowfall, ((0, 1), (0, 0)), mode="edge")
    # Emulate an archived forecast per issue time with biased/smoothed inputs.
    # In operation these inputs would come from a weather provider.
    forecast = forecast_bias * (0.6 * snowfall + 0.4 * truth_padded[1:])
    forecast = (forecast[None, :, :]
                * rng.uniform(0.85, 1.15, (STEPS, STEPS, 1)))
    mask = rng.random((STEPS, len(EDGES))) < missing
    mask[0] = False
    noise = rng.normal(0, 0.08, (STEPS, 2, len(EDGES)))
    return initial, snowfall, visibility, forecast, mask, noise


def run(policy, seed, event):
    state, snowfall, visibility, forecast, mask, noise = event
    observed = state.copy()
    position = np.full(3, DEPOT)
    risk_used = 0.0
    bad_used = np.zeros(len(CORRIDORS))
    spent = 0.0
    solve_seconds = 0.0
    trace = []
    for tick in range(STEPS):
        if policy != "B0" or tick == 0:
            measurement = state.copy()
            measurement[:2] = np.maximum(measurement[:2] + noise[tick], 0)
            observed = np.where(mask[tick], observed, measurement)
            stop = STEPS if policy == "B0" else min(tick + LOOKAHEAD, STEPS)
            forcing = forecast[tick, tick:stop] if policy in ("B1-W", "B2") else np.zeros_like(snowfall[tick:stop])
            # Give every method the same current visibility, held on its horizon.
            current_visibility = visibility[max(0, tick - 1)] if tick else 1.0
            before = perf_counter()
            route = plan(policy, position, observed, forcing,
                         np.full(stop - tick, current_visibility), tick, seed,
                         risk_used, bad_used)
            solve_seconds += perf_counter() - before

        action = route[tick] if policy == "B0" else route[0]
        edge, service, cost = decode(action)
        for machine, edge_idx in enumerate(edge):
            if action[machine] >= 0:
                assert EDGES[edge_idx][0] == position[machine]
                position[machine] = DEST[edge_idx]
        state, _ = step(state, snowfall[tick], "truth", service)
        risk, bad, ratios = metrics(state, visibility[tick])
        # All adaptive methods get the same estimator and accounting rule.
        # True metrics below are reserved for evaluation.
        if policy != "B0":
            observed, _ = step(observed, np.zeros(len(EDGES)), "B1", service)
            risk_estimate, bad_estimate, _ = metrics(observed, current_visibility)
            risk_used += risk_estimate
            bad_used += bad_estimate
        spent += cost
        trace.append((float(risk), *bad, *ratios, float(spent)))
    assert np.all(position == DEPOT)
    true = np.array(trace)
    corridor_hours = true[:, 1:3].sum(axis=0)
    total_risk = true[:, 0].sum()
    return {
        "cost_units": spent,
        "risk_excess_hours": total_risk,
        "corridor_1_below_pct": 100 * corridor_hours[0] / (DT * STEPS),
        "corridor_2_below_pct": 100 * corridor_hours[1] / (DT * STEPS),
        "safety_slack_hours": max(0, total_risk - SAFETY_BUDGET),
        "mobility_slack_hours": max(0, corridor_hours.max() - MOBILITY_BUDGET),
        "solve_seconds": solve_seconds,
    }, true


def forecast_errors(event):
    """Open-loop H1 diagnostic with equal observations and no planned passes."""
    state, snowfall, _, forecast, mask, noise = event
    observed = state.copy()
    measured_errors = []
    no_service = np.zeros(len(EDGES), dtype=bool)
    for tick in range(STEPS - LOOKAHEAD + 1):
        measurement = state.copy()
        measurement[:2] = np.maximum(measurement[:2] + noise[tick], 0)
        observed = np.where(mask[tick], observed, measurement)
        future = state
        predictions = {policy: observed for policy in POLICIES[1:]}
        for lag in range(LOOKAHEAD):
            future, _ = step(future, snowfall[tick + lag], "truth", no_service)
            for policy, estimate in predictions.items():
                forcing = 0 if policy == "B1" else forecast[tick, tick + lag]
                predictions[policy], _ = step(estimate, forcing, policy, no_service)
            if lag + 1 in (2, 4, 8):
                for policy, estimate in predictions.items():
                    snow_error, ice_error = np.abs(estimate[:2] - future[:2]).mean(axis=-1)
                    measured_errors.append((policy, int(60 * DT * (lag + 1)),
                                            snow_error, ice_error))
        state, _ = step(state, snowfall[tick], "truth", no_service)

    for policy in POLICIES[1:]:
        for horizon in (30, 60, 120):
            errors = [(snow, ice) for p, h, snow, ice in measured_errors
                      if p == policy and h == horizon]
            snow_mae, ice_mae = np.mean(errors, axis=0)
            yield dict(policy=policy, horizon_minutes=horizon,
                       snow_mae_kg_m2=snow_mae, ice_mae_kg_m2=ice_mae)


def save_plots(output, rows, traces):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    time = DT * np.arange(1, STEPS + 1)
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(8, 6), layout="constrained")
    for policy, trace in traces.items():
        axes[0].plot(time, trace[:, 3], label=policy)
        axes[1].plot(time, trace[:, 0].cumsum(), label=policy)
    axes[0].axhline(CAPACITY_LIMIT, color="black", linestyle=":")
    axes[1].axhline(SAFETY_BUDGET, color="black", linestyle=":")
    axes[0].set(ylabel="Capacity / dry reference", title="Control event · corridor 1")
    axes[1].set(xlabel="Hours into event", ylabel="Cumulative risk proxy excess (h)")
    axes[0].legend(ncols=4)
    fig.savefig(output / "event_trace.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
    for policy in POLICIES:
        selected = [row for row in rows if row["policy"] == policy]
        ax.scatter([row["cost_units"] for row in selected],
                   [row["risk_excess_hours"] for row in selected], label=policy)
    ax.axhline(SAFETY_BUDGET, color="black", linestyle=":")
    ax.set(xlabel="Variable cost (scenario units)", ylabel="Risk proxy excess (h)",
           title="Cost and modelled safety across synthetic scenarios")
    ax.legend(ncols=4)
    fig.savefig(output / "cost_and_proxy.png", dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, forecast_rows, traces = [], [], {}
    stresses = (("control", 1, 0), ("underforecast", 0.65, 0),
                ("overforecast", 1.35, 0), ("missing", 1, 0.35),
                ("combined", 0.65, 0.35))
    for seed in (101, 203, 307, 409):
        for scenario, bias, missing in stresses:
            event = build_event(seed, bias, missing)
            forecast_rows.extend(dict(seed=seed, scenario=scenario, **row)
                                 for row in forecast_errors(event))
            for policy in POLICIES:
                result, trace = run(policy, seed, event)
                rows.append(dict(seed=seed, scenario=scenario, policy=policy, **result))
                if seed == 101 and scenario == "control":
                    traces[policy] = trace
    for name, records in (("metrics.csv", rows), ("forecast_mae.csv", forecast_rows)):
        with (args.output / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    save_plots(args.output, rows, traces)


if __name__ == "__main__":
    main()
