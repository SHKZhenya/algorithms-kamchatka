# Аудит зимней дорожной сети Петропавловска-Камчатского

Пакет содержит граф OSM, высотный слой, атрибуты и флаги для независимых переключателей существующего интерфейса, доказательную базу, вычислительный адаптер и эксперименты B0/O1. Область работы — три тайла исследовательского графа программы «Алгоритмы Камчатки».

| Начать отсюда | Содержание |
|---|---|
| [AGENT_HANDOFF.md](../AGENT_HANDOFF.md) | Статус приёмки и передача работы |
| [Аудит графа](docs/GRAPH_AUDIT.md) | Покрытие и поведение импортёра |
| [Рельеф](docs/RELIEF_AUDIT.md) | Методика DSM и неопределённость |
| [Ответственность](docs/RESPONSIBILITY_REPORT.md) | Источники и статусы |
| [549 проездов](docs/549_PASSAGES_REPORT.md) | Протокол проверки и пробелы |
| [Интеграция](docs/INTEGRATION_GUIDE.md) | B0/O1, валидатор, ограничения по времени |
| [Эксперимент](output/experiments/experiment_report.md) | Числа и интерпретация |
| [Мини-кейсы](output/cases/README.md) | Карты и данные для проверки |

## Воспроизвести

Из корня распакованного архива. Показаны команды с текущим архивным тайлом и готовыми реестрами источников.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-core.txt -r winter_audit/requirements.txt
python -m winter_audit.build --dem winter_audit/evidence/originals/pkc-glo30.tif --output winter_audit/output
python -m winter_audit.maps winter_audit/output
python -m winter_audit.verify winter_audit/output
python -m unittest winter_audit.test_access
python -m unittest discover -s tests
```

Альтернативный запуск на отдельном тайле использует тот же URL из `sources.csv`; сверить SHA-256 `e75792a5d29120cfac3fa1a843dd4bc7e7d3eb373a6cd649faf61e7ab608d359`. Для карты нужен Matplotlib. Модель допускает неизвестные участки только с выбранной исследовательской политикой. При воспроизведении сроки решателей могут зависеть от CPU; проверяйте валидность, покрытие и сценарные различия.
