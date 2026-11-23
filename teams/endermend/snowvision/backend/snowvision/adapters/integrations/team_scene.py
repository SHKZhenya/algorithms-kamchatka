# Adapted from kamchatka-cam/monitoring/scene.py by Eazyan.
# This self-contained copy is maintained within SnowVision.
"""Versioned model contract and deterministic rules shared by live and replay."""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

Snow = Literal["yes", "no", "uncertain"]
View = Literal["good", "limited", "unusable"]


class SceneDetails(BaseModel):
    blowing_snow: Snow
    snowdrift_on_road: Snow
    slush_on_road: Snow
    road_obstruction: Literal["none", "partial", "major", "uncertain"]
    air_view: View
    ground_view: View
    road_view: View
    roadside_view: View


class Features(BaseModel):
    falling_snow: Snow
    intensity: Literal["none", "light", "moderate", "heavy", "uncertain"]
    visibility: Literal["good", "reduced", "poor", "uncertain"]
    view_quality: Literal["good", "limited", "unusable"]
    snow_on_ground: Snow
    large_snowdrifts: Snow
    road_coverage: Literal["clear", "partial", "mostly_covered", "not_visible", "uncertain"]
    evidence: str = Field(min_length=1, max_length=500)


class Scene(Features):
    schema_version: Literal["1", "2"]
    model: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=64)
    format_valid: bool
    details: SceneDetails | None = None
    snow_source: Literal["scene", "snow"] | None = None


def russian_conclusion(scene):
    if not scene.get("format_valid"):
        return "Анализ кадра не прошёл проверку."
    snow = {
        "no": "Падающего снега не видно",
        "yes": {
            "light": "Виден слабый снегопад", "moderate": "Виден умеренный снегопад", "heavy": "Виден сильный снегопад",
        }.get(scene.get("intensity"), "В кадре виден снегопад"),
        "uncertain": "По кадру не удалось определить, идёт ли снег",
    }[scene["falling_snow"]]
    phrases = [snow]
    for field, labels in {
        "snow_on_ground": {"yes": "на земле виден снег", "no": "снега на видимой земле не видно"},
        "road_coverage": {"clear": "видимая проезжая часть выглядит чистой",
                          "partial": "снег частично покрывает проезжую часть",
                          "mostly_covered": "снег покрывает большую часть видимой дороги"},
        "large_snowdrifts": {"yes": "в кадре виден крупный снежный вал"},
    }.items():
        value = labels.get(scene.get(field))
        if value:
            phrases.append(value)
    details = scene.get("details")
    if details and details["air_view"] == "limited":
        phrases.append("обзор в кадре ограничен")
    elif scene.get("visibility") in {"poor", "reduced"}:
        phrases.append("видимость снижена")
    return ". ".join(phrase[0].upper() + phrase[1:] for phrase in phrases) + "."


def unknown():
    return dict(falling_snow="uncertain", intensity="uncertain", visibility="uncertain",
                view_quality="unusable", snow_on_ground="uncertain", large_snowdrifts="uncertain",
                road_coverage="uncertain", evidence="Получить проверяемую оценку кадра не удалось.")


def usable_sequence(rows, freshness):
    sequence = []
    for row in rows:
        if sequence and sequence[0]["observed_at"] - row["observed_at"] > timedelta(seconds=freshness):
            break
        features = row.get("scene")
        if not features or not features["format_valid"] or features["view_quality"] == "unusable":
            break
        sequence.append(row)
    return sequence


RULES = {
    "snowfall": ("Наблюдается устойчивый снегопад.", "Сильный снегопад и ограниченный обзор. Срочно проверить участок и уточнить, работает ли техника."),
    "coverage": ("Снег устойчиво закрывает большую часть видимой проезжей части.", "Снег закрывает проезжую часть, видимость снижена. Срочно проверить участок."),
    "drift": ("В зоне дороги устойчиво виден крупный снежный вал. Проверить влияние на проезд.", ""),
}


def event_signals(rows, road, freshness):
    recent = [row["scene"] for row in usable_sequence(rows, freshness)]
    if not recent:
        return {}
    snow = [scene["falling_snow"] for scene in recent]
    severe = road and all(
        scene["falling_snow"] == "yes" and scene["intensity"] == "heavy"
        and (scene["visibility"] in {"poor", "reduced"}
             or (scene.get("details") or {}).get("air_view") == "limited") for scene in recent[:2]
    ) and len(recent) >= 2
    result = {}
    if severe or snow[:3] == ["yes"] * 3:
        result["snowfall"] = "urgent" if severe else "watch"
    elif snow[:3] == ["no"] * 3:
        result["snowfall"] = "end"
    if road and len(recent) >= 3:
        coverage = [scene["road_coverage"] for scene in recent]
        if coverage[:3] == ["mostly_covered"] * 3:
            result["coverage"] = "urgent" if severe else "watch"
        elif all(value in {"clear", "partial"} for value in coverage[:3]):
            result["coverage"] = "end"
        drifts = [scene["large_snowdrifts"] for scene in recent[:3]]
        if drifts == ["yes"] * 3 and all(
            scene["road_coverage"] not in {"uncertain", "not_visible"} for scene in recent[:3]
        ):
            result["drift"] = "watch"
        elif drifts == ["no"] * 3:
            result["drift"] = "end"
    return result
