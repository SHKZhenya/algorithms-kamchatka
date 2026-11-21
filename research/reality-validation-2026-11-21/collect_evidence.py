"""Assemble source-linked candidates. Coordinates identify the asset or search area."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"
FIELDS = ("evidence_id", "event_id", "time_start", "time_end", "lon", "lat", "accuracy_m", "asset_or_road",
          "side_of_building", "observed_type", "observed_value", "unit", "after_plow", "evidence_url",
          "evidence_grade", "weather_source", "match_status", "location_basis", "uncertainty_notes")
KAM = "https://kam24.ru/news/main/"
AIF = "https://kamchatka.aif.ru/society/"
UGMS = "https://kammeteo.ru/kamsneg2012.pdf"
RIA = "https://riamediabank.ru/media/"
REPORT = "https://kamchatkamedia.ru/news/2368112/"
STATION = (158.6552, 52.9897, 100, "МГ-1, Рябиковская, 64Б, WMO 32583")

rows = []


def add(id_, event, dates, place, kind, url, grade, status, notes, coords=None, value="", unit="",
        after="unknown", basis=""):
    lon, lat, accuracy = coords or (None, None, None)
    rows.append(dict(zip(FIELDS, (id_, event, *dates, lon, lat, accuracy, place, "unknown", kind, value, unit,
                                 after, url, grade, "ECMWF IFS / Open-Meteo" if event in {"december-2025",
                                 "january-2026", "march-2025"} else "", status, basis, notes))))


D = "december-2025"
J = "january-2026"
M = "march-2025"
D_DAY = ("2025-12-09T00:00+12:00", "2025-12-09T09:59+12:00")
J_DAY = ("2026-01-15T00:00+12:00", "2026-01-15T23:59+12:00")
J_LATE = ("2026-01-13T00:00+12:00", "2026-01-19T20:08+12:00")
K_DEC = KAM + "20251209/123506.html"
A_DEC = AIF + "centr-petropavlovska-kamchatskogo-skovali-probki-iz-za-ciklona"
STA = "Паспорт станции 32583 (Погода и климат), адрес МГ-1 сверён с Камчатским УГМС"
add("D09-01", D, D_DAY, "Ленинская улица", "slush", K_DEC, "C", "unknown",
    "Сообщение очевидца, участок и час неизвестны", basis="Имя улицы в публикации")
add("D09-02", D, D_DAY, "4-й километр", "bus_stuck", K_DEC, "C", "unknown",
    "Радиус поиска по району, место происшествия неизвестно", (158.6524654, 53.0468673, 450),
    basis="OSM node 3861784858")
add("D09-03", D, D_DAY, "Сапун-гора", "bus_stuck", K_DEC, "C", "unknown",
    "Радиус поиска вокруг остановки, подъём и сторона неизвестны. В статье есть встроенный ролик.",
    (158.6618301, 52.9948629, 400), basis="OSM node 1425986609")
aif_day = ("2025-12-09T00:00+12:00", "2025-12-09T11:12+12:00")
add("D09-04", D, aif_day, "Ленинградская у новой гостиницы", "congestion", A_DEC, "C", "unknown",
    "Гостиница и место столкновения не подтверждены", basis="Имя улицы в публикации")
add("D09-05", D, aif_day, "Ленинский район", "transport_disruption", A_DEC, "C", "unknown",
    "Районное сообщение, затор и расписание не измеряют накопление")
add("D09-06", D, aif_day, "Центр города", "congestion_photo", A_DEC, "C", "unknown",
    "Фотография в репортаже, точка и время съёмки не установлены")
add("D09-07", D, D_DAY, "Сапун-гора или другой участок", "bus_stuck_video",
    "https://vkvideo.ru/video-63591687_456241345", "C", "unknown",
    "Встроенный источник Кам 24, точка и время съёмки не верифицированы")
add("D10-01", D, ("2025-12-09", "2025-12-10"), "Городские магистрали", "operation_report",
    "https://nia-kamchatka.ru/news/authority/8065.html", "C", "unknown", "Приоритеты и уборка, без GPS и замеров")
add("J13-01", J, ("2026-01-13", "2026-01-16"), STATION[3], "total_precipitation_mm", UGMS, "A",
    "weather_context", "131,6 мм всех фаз за период. Границы измерительного окна по часам не опубликованы.",
    STATION[:3], 131.6, "mm", basis=STA)
add("J16-01", J, ("2026-01-13", "2026-01-16"), STATION[3], "total_snow_cm", UGMS, "A",
    "incompatible_quantity", "Площадка станции. Точный срок рейки и протокол замера отсутствуют в сводке.",
    STATION[:3], 170, "cm", basis=STA)
add("J16-02", J, ("2026-01-15", "2026-01-18"), STATION[3], "total_snow_cm",
    "https://www.pogodaiklimat.ru/news/25913/", "C", "possible_duplicate",
    "Публикация сообщает 171 см к концу недели. Возможно, та же станция и тот же эпизод, срок и первичная телеграмма неизвестны.",
    STATION[:3], 171, "cm", basis=STA)
add("J16-03", J, ("2026-01-16", "2026-01-16"), "Метеостанция Начики, вне города", "total_snow_cm",
    "https://www.pogodaiklimat.ru/news/25913/", "C", "outside_domain", "Станционный замер в пересказе, без срока UTC.", value=223, unit="cm")
add("J16-04", J, ("2026-01-16", "2026-01-19"), "Метеостанция Сосновка, вне города", "total_snow_cm",
    "https://www.pogodaiklimat.ru/news/25913/", "C", "outside_domain", "Станционный замер в пересказе, точный срок неизвестен.",
    value=204, unit="cm")
add("J16-05", J, ("2026-01-16", "2026-01-19"), "Метеостанция Пущино, вне города", "total_snow_cm",
    "https://www.pogodaiklimat.ru/news/25913/", "C", "outside_domain", "Станционный замер в пересказе, точный срок неизвестен.",
    value=194, unit="cm")
add("J19-01", J, ("2026-01-13", "2026-01-16"), "Петропавловск-Камчатский", "unverified_snow_cm", REPORT, "D",
    "unknown", "Редакция указывает до 133 см, без точки, инструкции измерения и определения слоя.", value=133, unit="cm")
add("M06-01", "march-2026-followup", ("2025-12-01", "2026-03-06"), "Горные склоны Петропавловска", "snow_layer_cm",
    "https://kammeteo.ru/060326.pdf", "C", "incompatible_quantity",
    "Около метра смёрзшегося слоя декабря, описанного в марте. Отдельная точка и дата отбора неизвестны.", value=100, unit="cm")
add("M16-01", "march-2026-followup", ("2026-03-16", "2026-03-16"), "Зеркальная, 58", "snowbank_cm",
    AIF + "zhitel-petropavlovska-slomal-ryobra-upav-s-2-metrovoy-gory-snega-v-podezd", "C",
    "outside_replay", "Оценка высоты в публикации, снег с дороги и крыш. Съёмка и измерение см не подтверждены.",
    (158.65116, 53.04946, 40), 200, "cm", after="yes", basis="OSM way 99383153")
add("M24-01", M, ("2025-03-23", "2025-03-24"), "Петропавловск-Камчатский", "total_precipitation_mm",
    KAM + "20250324/115258.html", "C", "weather_context", "63 мм за 24 часа к утру 24 марта, час и площадка не указаны.",
    value=63, unit="mm")
add("J15-01", J, J_DAY, "Проспект 50 лет Октября, 4/2", "buried_car_photo",
    "https://www.rbc.ru/life/news/696a09209a794762f3e75d3e", "C", "spatial_context",
    "Фото Пирагиса от 15 января. Адрес из ТЗ подтверждён как существующий, исходный кадр с часом, точкой машины и стороной фасада не привязан.",
    (158.64384, 53.05285, 80), basis="OSM ways 106049024 и 106049027, адресный кандидат")
for i, number, coords, osm in [(1, 52, (158.65307, 53.04839, 55), "99383117 / 611979148"),
                               (2, 54, (158.65244, 53.04884, 40), "99383104"),
                               (3, 56, (158.65181, 53.04915, 40), "99383130"),
                               (4, 58, (158.65116, 53.04946, 40), "99383153")]:
    add(f"J21-0{i}", J, ("2026-01-21", "2026-01-21"), f"Зеркальная, {number}", "unremoved_snow",
        KAM + "20260121/124684.html", "C", "outside_replay",
        "Описание группы домов одним источником. Снег с декабря, неодинаковая уборка, фасад не локализован.",
        coords, basis=f"OSM way {osm}")
for id_, caption, after in [("9086843", "Занесённые машины", "unknown"), ("9086833", "Уборка снега", "yes"),
                             ("9086832", "Снегопад", "unknown"), ("9086827", "Снегоуборочная машина", "yes"),
                             ("9086824", "Снегопад", "unknown"), ("9086823", "Заваленные подъезды", "unknown"),
                             ("9086822", "Пешеходы во время снегопада", "unknown"),
                             ("9086821", "Откапывают автомобиль", "yes"), ("9086820", "Снегопад", "unknown")]:
    add("RIA-"+id_, J, J_DAY, "Петропавловск-Камчатский", "visual_candidate", RIA+id_+".html", "C", "unknown",
        f"{caption}. Карточка в медиабанке от 15 января. Улица, час и масштаб не установлены.", after=after)
for id_, place, kind, notes in [
        ("KM-01", "Краевой театр драмы и комедии", "snow_scene", "Узнаваемый объект. Нужен точный срок, положение и оценка уборки."),
        ("KM-02", "Центральная площадь, памятник Ленину", "snow_scene", "Узнаваемый объект. Точный час неизвестен."),
        ("KM-03", "Городская проезжая часть", "passage_visible", "На фото выгрузка хлеба с дороги. Уборка и точный адрес неизвестны."),
        ("KM-04", "Городской двор", "snow_scene", "Двор после пурги. Адрес и время не указаны.")]:
    add(id_, J, J_LATE, place, kind, REPORT, "C", "unknown", notes, basis="Подпись автора фоторепортажа")
add("TJ-01", J, ("2026-01-27", "2026-01-27"), "Городская очищенная дорога", "cleared_visual_candidate",
    "https://t-j.ru/kamchatka-v-snegu/", "C", "outside_replay",
    "Проезд и парковочные карманы после расчистки. Для контроля до уборки не годится.", after="yes")

with (OUT / "observations.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
features = []
for row in rows:
    lon, lat = (row[k] for k in ("lon", "lat"))
    props = {key: value for key, value in row.items() if key not in {"lon", "lat"}}
    features.append({"type": "Feature", "id": row["evidence_id"],
                     "geometry": None if lon is None else {"type": "Point", "coordinates": [lon, lat]},
                     "properties": props})
(OUT / "observations.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                                     ensure_ascii=False, indent=2)+"\n")
with (OUT / "annotation-template.csv").open("w", newline="") as handle:
    csv.writer(handle).writerow((*FIELDS, "observed_at_local", "observed_time_accuracy_min", "road_segment_id",
                                 "asset_id", "previous_snow_present", "original_media_timestamp",
                                 "source_owner", "measurement_method", "verification_status"))
