# Алгоритмы Камчатки

Вычислительный пилот планирует проходы плугов на ориентированном графе центральной части Петропавловска-Камчатского. На одном снимке заданий CLI сравнивает приоритетный жадный алгоритм B0 и O1 на SciPy/HiGHS, независимо проверяет маршруты и экспортирует JSON, GeoJSON и CSV.

## Запуск

Проверено с Python 3.13.12 на macOS arm64. Установка зависимостей нужна один раз. Снимок карты включён в репозиторий, эксперимент работает офлайн.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-core.txt
python -m unittest discover -s tests -v
python -m snowops demo --output out/pkc-first --seed 42 --time-limit-s 30
python -m snowops validate out/pkc-first/base_snowfall/scenario.json out/pkc-first/base_snowfall/O1.plan.json
```

Для быстрого запуска добавьте `--case base_snowfall`. `metrics.csv` содержит парное сравнение, `run_environment.json` — времена и версии, `graph_audit.json` — происхождение и параметры импорта. В каталогах сценариев сохранены вход, планы и слои для карты. `scenario.schema.json` и `plan.schema.json` генерируются из моделей.

Измените параметры в копии `scenario.json` и пересчитайте через `python -m snowops solve <сценарий.json> --output out/custom --method O1`. Проверку выгруженного плана выполняет `validate`, завершение с кодом 1 означает хотя бы одно нарушение. Негородские данные можно указать через `--osm <файл.osm.gz>` перед подкомандой `solve`. Генератор `demo` привязан к выбранным улицам Петропавловска-Камчатского.

Происхождение геометрии — © OpenStreetMap contributors, ODbL. Техника, сроки, затраты и проходимость при отсутствии тегов заданы сценарно. [Лицензия OSM](https://www.openstreetmap.org/copyright)

[Результаты](docs/EXPERIMENT_REPORT.md) · [Контракт интеграции](docs/API_CONTRACT.md) · [Аудит решателей](docs/SOLVER_BENCHMARK.md) · [Происхождение данных](docs/DATA_PROVENANCE.md)

## Масштабный эксперимент и ранги инфраструктуры

```bash
python -m snowops.study --only scale --output out/pkc-stage2 --limit 18
python -m snowops.study --only sensitivity --output out/pkc-stage2-sensitivity --limit 12
python -m snowops.study --only depots --output out/pkc-depots
python -m snowops.study --only operational-depot --output out/pkc-stage2-depot --limit 15
```

Для отдельной конфигурации используйте `--policy <файл.json>`. Ранги объектов, сроки и ручные записи задаются в [политике инфраструктуры](data/pkc/infrastructure-policy.json). Зафиксированные входы и планы остаются рядом с каждой таблицей. Сопоставление с HGS-CARP и ArcRoute и результаты повторной оценки приведены в [отчёте второго этапа](docs/SCALE_AND_SLA_REPORT.md).

## 3D-карта города

Рельеф, здания, дорожный граф и погодные сценарии с экспортом в аэродинамический решатель. [Запуск 3D-города](README_CITY_SIM.md)
