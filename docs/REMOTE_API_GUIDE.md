# Получение расчётов снега через удалённый API

Сервис работает на компьютере владельца проекта. Клиенту достаточно HTTP-клиента или Python 3, все расчёты выполняет сервер. Компьютер и сервис должны быть включены.

- Базовый адрес — `http://82.162.61.44:8008`
- Интерактивная документация — [Swagger UI](http://82.162.61.44:8008/docs)
- Машиночитаемая схема — [OpenAPI JSON](http://82.162.61.44:8008/openapi.json)

22 ноября 2026 года проверены публичные OpenAPI, health и каталог сценариев. API отвечает без токена. Проверка health вернула `snow_solver_connected: true` и `terrain_loaded: true`. Поле `solver_connected` относится к отдельному внешнему решателю ветра. Контракт результатов ниже сверен с локальным кодом сервера. Примеры запуска расчёта в ходе подготовки этой инструкции не выполнялись.

## Быстро получить готовый расчёт

Если владелец передал `run_id` завершённого расчёта, сразу запросите его результат. `run_id` — строка из 64 шестнадцатеричных символов.

```sh
BASE='http://82.162.61.44:8008'
RUN_ID='ВСТАВЬТЕ_RUN_ID'

# Статус, метрики, ранжирование дорог и сведения об источниках
curl --fail --max-time 60 "$BASE/api/v1/operations/city/runs/$RUN_ID" -o result.json

# Полные численные поля и временная серия, архив gzip с JSON
curl --fail --max-time 300 "$BASE/api/v1/operations/city/runs/$RUN_ID/replay" -o replay.json.gz

# Глубина снега в конкретной точке в конце расчёта
curl --fail "$BASE/api/v1/operations/city/runs/$RUN_ID/sample?lon=158.642&lat=53.0363"
```

Публичного метода списка всех городских расчётов в проверенной схеме нет. Для существующего результата получите `run_id` у владельца. Также можно отправить тот же сценарий через POST — при совпадении входов и версий данных сервер вернёт сохранённый расчёт.

## Запустить свой расчёт

Сначала получите доступные сценарии и границы модели.

```sh
curl --fail http://82.162.61.44:8008/api/v1/health
curl --fail http://82.162.61.44:8008/api/v1/operations/city/catalog
```

На дату проверки каталог содержит `december-2025`, `january-2026`, `march-2025`. Список `openweather` пуст. Границы модели в порядке запад, юг, восток, север — `[158.48, 52.91, 158.8, 53.16]`.

Пример исторического расчёта на небольшом контуре. Координаты задаются парами `[долгота, широта]` в WGS84.

```sh
curl --fail --max-time 60 \
  -H 'Content-Type: application/json' \
  -d '{"source":"reanalysis","event_id":"march-2025","zone":[[158.641,53.036],[158.643,53.036],[158.642,53.037]]}' \
  http://82.162.61.44:8008/api/v1/operations/city/runs
```

Ответ HTTP 202 содержит `run_id` и `status`. Возможные статусы — `pending`, `running`, `completed`, `failed`. При `running` доступны `completed_tiles` и `total_tiles`. Опросите `GET /api/v1/operations/city/runs/{run_id}` каждые 2–5 секунд до завершения. При `failed` прочитайте поле `error`. Длительный расчёт может занять несколько минут.

## Готовый Python-клиент

Сохраните код как `fetch_snow.py` и выполните `python3 fetch_snow.py`. Используется стандартная библиотека Python. Пример запускает исторический расчёт небольшого участка, ждёт до 30 минут и сохраняет итоговые данные в текущей папке. Для скачивания уже существующего результата замените `EXISTING_RUN_ID = None` на строку с его ID.

```python
import gzip
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://82.162.61.44:8008"
EXISTING_RUN_ID = None
PAYLOAD = {
    "source": "reanalysis",
    "event_id": "march-2025",
    "zone": [[158.641, 53.036], [158.643, 53.036], [158.642, 53.037]],
}


def request(path, payload=None, binary=False):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = Request(BASE + path, data=body, headers=headers)
    try:
        with urlopen(req, timeout=300 if binary else 60) as response:
            data = response.read()
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    return data if binary else json.loads(data)


endpoint = "/api/v1/operations/city/runs"
run_id = EXISTING_RUN_ID
if run_id is None:
    run_id = request(endpoint, PAYLOAD)["run_id"]
print("run_id =", run_id, flush=True)
path = f"{endpoint}/{run_id}"
deadline = time.monotonic() + 1800
while True:
    result = request(path)
    status = result["status"]
    if status == "completed":
        break
    if status == "failed":
        raise RuntimeError(result.get("error", "Расчёт завершился ошибкой"))
    if time.monotonic() >= deadline:
        raise TimeoutError(f"Ожидание истекло, сохраните run_id: {run_id}")
    print(status, result.get("completed_tiles"), result.get("total_tiles"), flush=True)
    time.sleep(3)

Path("result.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
archive = request(path + "/replay", binary=True)
Path("replay.json.gz").write_bytes(archive)
replay = json.loads(gzip.decompress(archive))
print("Метрики:", result["metrics"])
print("Поля:", list(replay["grids"]))
sample = request(path + "/sample?lon=158.642&lat=53.0363")
print("Снег в точке, см:", sample["snow_cm"])
```

Тайм-аут клиента прекращает ожидание. Фоновый расчёт на сервере может продолжаться, его статус можно получить позже по сохранённому `run_id`.

## Параметры POST

| Поле | Значение и назначение |
| --- | --- |
| `source` | Явно задайте `reanalysis`, `manual`, `live`, `atlas_history` или `openweather` |
| `event_id` | Для `reanalysis` обязателен ID из `catalog.storms` |
| `zone` | Необязательный простой полигон из 3–64 пар `[lon, lat]`, внутри модели |
| `district_id` | Альтернатива `zone`, ID района должен быть известен серверу |
| `valid_at` | Для `live`, `atlas_history`, `openweather` обязателен ISO 8601 с часовым поясом, например `+12:00`. Выбирайте доступный срок источника |
| `hours` | 1–6, по умолчанию 3. Продолжительность для `manual` и `live` |
| `wind_from_deg` | Для `manual`, от 0 включительно до 360 исключительно, по умолчанию 270. Направление, откуда дует ветер |
| `wind_speed_ms` | Для `manual`, 0–70 м/с, по умолчанию 8 |
| `snowfall_cm_h` | Для `manual`, 0–30 см/ч свежего снега, по умолчанию 2 |
| `weather_revision` | Для `live` можно передать ревизию выбранного прогноза, чтобы обнаружить его обновление |
| `snow_to_liquid_ratio` | Для OpenWeather, 2–30, по умолчанию 10 |

Без `zone` и `district_id` запрос охватывает весь город. `zone` и `district_id` одновременно задавать нельзя. Контур должен быть без самопересечений, с ненулевой площадью и размерами не более 0,10° по долготе и 0,065° по широте. Для исторического эпизода длительность определяется самим эпизодом.

Пример ручного сценария на том же участке.

```json
{
  "source": "manual",
  "wind_from_deg": 270,
  "wind_speed_ms": 8,
  "snowfall_cm_h": 2,
  "hours": 3,
  "zone": [[158.641, 53.036], [158.643, 53.036], [158.642, 53.037]]
}
```

Каталог погодных источников доступен через `GET /api/v1/operations/atlas`, свежий прогноз через `GET /api/v1/operations/atlas/live`. При выборе свежего прогноза весь запрошенный интервал должен помещаться в доступные сроки.

## Что забирать из результата

| Метод | Данные |
| --- | --- |
| `GET /api/v1/operations/city/runs/{run_id}` | Статус, затем `metrics`, `ranking`, `provenance`, `bounds`, `input`, визуальные слои и временная серия |
| `GET /api/v1/operations/city/runs/{run_id}/replay` | Полный gzip JSON, включая численные `grids`, входы тайлов и временные поля |
| `GET /api/v1/operations/city/runs/{run_id}/surface` | Сетка для отображения с `longitudes`, `latitudes`, `ground_m`, `snow_cm`, максимум 132 × 132 точки |
| `GET /api/v1/operations/city/runs/{run_id}/sample?lon=158.642&lat=53.0363` | Значение исходной расчётной ячейки в указанной точке |

В `sample` поле `snow_cm` задаёт расчётный свежий снег в сантиметрах. Дополнительно возвращаются `weather_snow_cm`, `opposite_snow_cm`, `priority`, размеры ячейки и `sample_distance_m`. Вне области или в маске значение может быть `null`. Для поиска ближайшей доступной ячейки можно добавить `nearby_m=30` (диапазон 0–30 м), фактическое смещение будет в `sample_distance_m`.

`sample` и `surface` принимают `frame` — индекс сохранённого кадра от 0 до 64. Без него возвращается итоговое поле. Выбирайте индекс по фактическому числу сохранённых кадров. Выход за длину серии в текущей реализации возвращает итоговое поле. Кадры накопления могут сохраняться с шагом 12 часов, индекс кадра сам по себе не задаёт час эпизода.

В `replay.grids` находятся двумерные массивы, в том числе `snow_cm`, `weather_snow`, `opposite_snow_cm`, `combined`. `combined` — индекс приоритета. Строки идут с севера на юг, столбцы с запада на восток. Границы берутся из `bounds = [west, south, east, north]`. Центр ячейки столбца `i` и строки `j` имеет координаты `lon = west + (i + 0.5) * (east - west) / cols`, `lat = north - (j + 0.5) * (north - south) / rows`. Значение `null` означает отсутствие расчётных данных.

Для коротких серий архив содержит `timeline_grids`. Для длинных — `timeline_binary` с `encoding: "base64+zlib+float32le"`, `shape` и списком `frames`. Каждый кадр декодируется base64 → zlib → плоский массив float32 little-endian, затем преобразуется в `shape` по строкам. В бинарном массиве маска представлена NaN. Поле `wind_frames` содержит погодные сроки и ветер.

Снег в результатах моделирует новый слой за окно сценария. Для сопоставления с общим покровом нужны начальная глубина и история изменений. Сервер помечает результат `field_validated: false`.

## Геоданные и ошибки

Для отображения дорог и зданий можно запросить GeoJSON по bbox в порядке `west,south,east,north`.

```sh
curl --fail 'http://82.162.61.44:8008/api/v1/city/roads?bbox=158.63,53.02,158.66,53.05&network=city' -o roads.geojson
curl --fail 'http://82.162.61.44:8008/api/v1/city/buildings?bbox=158.63,53.02,158.66,53.05' -o buildings.geojson
```

| Ответ | Действие клиента |
| --- | --- |
| `404` | Проверьте `run_id` и дождитесь завершения перед скачиванием полей |
| `409` | Прогноз изменился, получите свежие сроки и ревизию |
| `422` | Прочитайте `detail`, исправьте параметры, дату или контур |
| `429` | Очередь расчётов заполнена, повторите позже с паузой |
| `503` | Серверу недоступны модель, погодные данные или поставщик прогноза |

Для интеграции из другого веб-приложения проверьте CORS и ограничения браузера на HTTP-запросы со страницы HTTPS. В проверенном локальном приложении CORS middleware отсутствует. Python, curl и серверный HTTP-клиент могут обращаться к API напрямую.
