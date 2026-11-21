# Аудит решателей

Проверка 19.11.2026 на macOS arm64, Python 3.13.12, Apple clang 21.0.0. Каждый smoke-test проверяет конкретную возможность на задаче инструмента. Сырые целевые значения между инструментами несопоставимы.

| Кандидат, ревизия, лицензия | Нативно | Обвязка / ограничения | Фактический smoke-test |
|---|---|---|---|
| ArcRoute `13aa09c04a553bb66ea133ad97b46e66cffabb2c`, MIT по метаданным | Иерархический directed CARP, спрос/ёмкость, ILS, EA, ACO, MILP | Формат NPZ с переходами и классами обслуживания. Смены/окна каждой машины, повторные проходы и сохранение геометрии потребуют адаптера и отдельной проверки. `pip --dry-run` отклонил Python 3.13.12, метаданные требуют 3.10–3.12 | 16 upstream ILS тестов, включая решение экземпляра с 6 задачами, прошли при запуске из исходников с локальными Torch/NumPy. Полная поддерживаемая установка не проверена. [Ревизия и README](https://github.com/HySonLab/ArcRoute/tree/13aa09c04a553bb66ea133ad97b46e66cffabb2c) |
| HGS-CARP `08fde6a5672c3814dca1b4450aa9e5968b1d273a`, GPL-3.0 | CARP, смешанные задачи на вершинах, рёбрах и дугах, отдельная сборка для поворотных штрафов | Нужен экспорт из нашего directed graph и независимое восстановление времени, смен и задач. Абсолютные запреты и окна обслуживания на нашем графе не подтверждены | Штатный `gdb1.dat` дал 316 и 5 маршрутов за лимит 4 CPU-с. `DI-NEARP-n80-Q4k-TP.dat` с `-DTURN_PENALTIES` дал 6 296 и 2 маршрута за лимит 3 CPU-с. [Ревизия и примеры](https://github.com/vidalt/HGS-CARP/tree/08fde6a5672c3814dca1b4450aa9e5968b1d273a) |
| HGS-TDCARP `9638127242a9ad36ecaf9335c8df540cad6575fc`, GPL-3.0 | CARP на времязависимых путях, отдельный этап построения входа `.tdsp` | Нужны временные профили и транслятор расходов и ограничений. Погодный ряд сам по себе их не даёт. Повороты и индивидуальные окна в пилоте не проверены | `C01.tdsp` дал 2 448,788181 и 9 маршрутов за лимит 3 CPU-с. [Ревизия и README](https://github.com/vidalt/HGS-TDCARP/tree/9638127242a9ad36ecaf9335c8df540cad6575fc) |
| PyVRP `0.14.1`, MIT | Точечный VRP, грузоподъёмность, окна, профили машин, депо, несколько рейсов с перезагрузкой | Для вывоза нужно связать рейсы с балансом снега, доступностью погрузчика, занятостью постов и разрешёнными площадками. Модель дорожных дуг и поворотов остаётся внешней | Два пункта подбора по 4 условные единицы, одна машина ёмкостью 4. Получен один допустимый маршрут из 2 рейсов, стоимость 6. [PyVRP](https://github.com/PyVRP/PyVRP) · [Wheel 0.14.1](https://pypi.org/project/pyvrp/0.14.1/) |
| OR-Tools `9.15.6755`, Apache-2.0 | CP-SAT с отношениями предшествования и ограничениями ресурсов, отдельный Routing Solver | Нужно формулировать модель синхронизации и схему связей с графом. Специализированный CARP требуется моделировать | CP-SAT на двух операциях с предшествованием: старт второй операции в 3, оптимальное завершение в 7, один worker. [OR-Tools](https://developers.google.com/optimization) · [Wheel 9.15.6755](https://pypi.org/project/ortools/9.15.6755/) |
| SciPy `1.17.1` / HiGHS `1.12.0`, BSD-3-Clause / MIT | Бинарная MILP с линейными ограничениями, лимит времени и разрыв в модели | Задачи и пути формирует ядро, строгие окна проверяются при генерации маршрутов, независимая проверка после расчёта | Малый MILP выбрал более дешёвую из двух альтернатив с целевой 1. Затем выполнен парный эксперимент на OSM. [SciPy milp](https://docs.scipy.org/doc/scipy-1.17.1/reference/generated/scipy.optimize.milp.html) · [HiGHS](https://github.com/ERGO-Code/HiGHS) |

VROOM (BSD-2-Clause) поддерживает точечный VRP с внешними матрицами. OSRM (BSD-2-Clause) и Valhalla (MIT) рассматриваются как движки переезда, профили для плугов требуют настройки и проверки. Эти три альтернативы просмотрены по upstream, версии не закреплялись, сборки не запускались. Локальной NetworkX достаточно для текущей величины снимка. [VROOM](https://github.com/VROOM-Project/vroom) · [OSRM](https://github.com/Project-OSRM/osrm-backend) · [Valhalla](https://github.com/valhalla/valhalla)

## Воспроизведение smoke-тестов

Для HGS выполнены команды в каталоге `Program` на указанных ревизиях. Выход и создаваемые upstream файлы BKS должны находиться в отдельной рабочей копии.

```bash
# HGS-CARP
make -j4
./gencarp ../Instances/CARP/gdb1.dat -type 30 -sol /tmp/pkc-carp.sol -seed 1 -t 4
make -B -j4 CCFLAGS='-O3 -DTURN_PENALTIES'
./gencarp ../Instances/MCGRP-TP/DI-NEARP-n80-Q4k-TP.dat -type 34 -sol /tmp/pkc-turn.sol -seed 1 -t 3

# HGS-TDCARP
make -j4
./gencarp ../Instances/TDCARP-withSP/Type_M/C01.tdsp -sol /tmp/pkc-tdcarp.sol -t 3 -seed 1

# ArcRoute из корня исходников с доступными numpy и torch
python3 -m unittest discover -s tests -p test_ils.py -v
```

Для PyVRP использованы `Model.add_location`, `add_depot`, `add_client(pickup=4)`, `add_vehicle_type(capacity=4, reload_depots=[depot], max_reloads=1)`. Локации в позициях 0, 1 и 2, полная матрица `|i-j|` для расстояния и времени. Решение вызвано с `MaxIterations(100)` и `seed=1`. Для OR-Tools модель `start_haul >= start_loader + 3` минимизировала `start_haul + 4` при целочисленных временах начала 0–10.
