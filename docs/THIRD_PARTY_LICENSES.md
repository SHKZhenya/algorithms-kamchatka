# Внешние компоненты и данные

| Компонент | Используемая версия и лицензия |
|---|---|
| NetworkX | 3.6.1, BSD-3-Clause. [Исходники](https://github.com/networkx/networkx) |
| NumPy | 2.4.4, BSD-3-Clause для проекта и дополнительные уведомления в wheel. [Исходники](https://github.com/numpy/numpy) |
| Pydantic / pydantic-core | 2.13.4 / 2.46.4, MIT. [Исходники](https://github.com/pydantic/pydantic) |
| SciPy / HiGHS | 1.17.1 / 1.12.0, BSD-3-Clause / MIT. У платформенных колёс могут быть дополнительные уведомления для библиотек, поставляемых в сборке. [SciPy](https://github.com/scipy/scipy) · [HiGHS](https://github.com/ERGO-Code/HiGHS) |
| OpenStreetMap | © OpenStreetMap contributors. ODbL 1.0 для исходных данных карты. URL, время и контрольная сумма в `data/pkc/manifest.json`. Атрибуция включена в GeoJSON. [Условия OpenStreetMap](https://www.openstreetmap.org/copyright) |

Внешние CARP/VRP движки запускались из временных каталогов и отсутствуют в runtime-зависимостях. Версии и лицензии ведутся в [аудите](SOLVER_BENCHMARK.md). Перед поставкой проверяются уведомления конкретных wheels и условия распространения данных/производной базы OSM. Для включения HGS нужна отдельная оценка условий GPL-3.0 с учётом фактической схемы поставки.
