"""Replay both tracks, record exact artifacts and evaluate optional field labels."""
import argparse
from collections import Counter
from hashlib import file_digest
import json
import math
from pathlib import Path

from pydantic import BaseModel, Field

from .api import make_app
from .operations import PERIODS, inspection_network, weather_benchmark, weather_controls, weather_replay
from .snow_experiment import SnowStudy, SnowStudyRequest
from .weather import WeatherArchive


class RoadCheck(BaseModel):
    district_id: str
    valid_at: str
    inspection_needed: bool
    evidence: str = Field(min_length=1)


class SnowCheck(BaseModel):
    run_id: str
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    fresh_snow_cm: float = Field(ge=0)
    evidence: str = Field(min_length=1)


class FieldChecks(BaseModel):
    road_checks: list[RoadCheck] = Field(default_factory=list)
    snow_checks: list[SnowCheck] = Field(default_factory=list)


def validate_field(observed, weather, snow):
    # Match exact saved district/term; equivalent ISO offsets are normalized.
    from datetime import datetime

    signals = {(r["district_id"], datetime.fromisoformat(r["valid_at"])): r
               for item in weather for r in item["rows"]}
    truth = observed.road_checks
    keys = [(r.district_id, datetime.fromisoformat(r.valid_at)) for r in truth]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate field observations for the same district and term")
    matches = [(obj, signals[key]) for obj, key in zip(truth, keys) if key in signals]
    validation = {}
    for name, key in (("regional", "status"), ("city", "baseline_status")):
        counter = Counter({field: 0 for field in ("tp", "fp", "tn", "fn", "abstained")})
        for obj, row in matches:
            status = row[key]
            if status in {"missing", "abstain", "abstained"}:
                counter["abstained"] += 1
                continue
            predicted = status == "inspect"
            counter[("t" if predicted == obj.inspection_needed else "f") + ("p" if predicted else "n")] += 1
        tp, fp, fn = (counter[field] for field in ("tp", "fp", "fn"))
        evaluated = len(matches) - counter["abstained"]
        validation[name] = {**counter, "evaluated": evaluated,
                            "unmatched": len(truth) - len(matches),
                            "coverage": evaluated/len(truth) if truth else None,
                            "precision": tp/(tp+fp) if tp+fp else None,
                            "recall": tp/(tp+fn) if tp+fn else None}
    results = {r["run_id"]: r for r in snow}
    comparisons = []
    for obj in observed.snow_checks:
        result = results.get(obj.run_id)
        if not result:
            continue
        w, s, e, n = result["bounds"]
        if not (w <= obj.longitude < e and s < obj.latitude <= n):
            continue
        i = math.floor((obj.longitude-w)/(e-w)*result["cols"])
        j = math.floor((n-obj.latitude)/(n-s)*result["rows"])
        modeled = result["grids"]["snow_cm"][j][i]
        if modeled is not None:
            comparisons.append((abs(modeled-obj.fresh_snow_cm),
                                abs(result["resolved_weather"]["snowfall_cm"]-obj.fresh_snow_cm)))
    return {"matching_road_labels": len(matches), "submitted_road_labels": len(truth), "road_metrics": validation,
            "matching_snow_measurements": len(comparisons), "submitted_snow_measurements": len(observed.snow_checks),
            "snow_model_mae_cm": sum(x[0] for x in comparisons)/len(comparisons) if comparisons else None,
            "snow_uniform_mae_cm": sum(x[1] for x in comparisons)/len(comparisons) if comparisons else None}


def reproduce(output, weather_path, danil_path, labels):
    output.mkdir(parents=True, exist_ok=True)
    city = make_app(db_path=output / ".build.sqlite").state.city
    archive = WeatherArchive(weather_path)
    adapter = SnowStudy(city, archive, danil_path)
    frozen = [weather_replay(archive, moment) for moment in PERIODS.values()]
    requests = [SnowStudyRequest(wind_from_deg=270), SnowStudyRequest(wind_from_deg=90),
                SnowStudyRequest(wind_speed_ms=0), SnowStudyRequest(snowfall_cm_h=0)]
    chosen = next((x["sample"] for x in frozen[0]["rows"] if x["district_id"] == "center"
                   and x["sample"] and x["sample"]["snow_mm_3h"] is not None), None)
    if chosen:
        requests.append(SnowStudyRequest(forecast_sample_id=chosen["sample_id"]))
    snow = [adapter.run(r) for r in requests]
    reopened = SnowStudy(city, archive, danil_path).run(requests[0])
    summary = {
        "reproducible_run_id": snow[0]["run_id"] == reopened["run_id"],
        "weather": [w["metrics"] for w in frozen],
        "road_network_m_by_district": inspection_network(city)["length_m_by_district"],
        "weather_controls": weather_controls(),
        "weather_benchmark": weather_benchmark(),
        "snow": [{k: item[k] for k in ("run_id", "resolved_weather", "model", "cfd_coverage",
                                        "metrics", "directional_control", "building_height_sources")} for item in snow],
        "field_validation": validate_field(labels, frozen, snow) if labels else None,
    }
    for filename, data in [("summary", summary),
                           *[(f"weather-{period}", x) for period, x in zip(PERIODS, frozen)],
                           *[(f"snow-{i+1}", x) for i, x in enumerate(snow)]]:
        (output / f"{filename}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    manifest = {}
    names = ["summary", *[f"weather-{p}" for p in PERIODS], *[f"snow-{i+1}" for i in range(len(snow))]]
    for name in names:
        path = output / f"{name}.json"
        with path.open("rb") as handle:
            manifest[path.name] = file_digest(handle, "sha256").hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/city-operations"))
    parser.add_argument("--weather", type=Path, default=Path("weather_coverage/heatmaps"))
    parser.add_argument("--danil", type=Path, default=Path("city_sim/.state/danil"))
    parser.add_argument("--observations", type=Path, help="field observations JSON with evidence for each row")
    args = parser.parse_args()
    labels = FieldChecks.model_validate_json(args.observations.read_text()) if args.observations else None
    reproduce(args.output, args.weather, args.danil, labels)
