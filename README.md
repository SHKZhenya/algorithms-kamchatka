# SnowVision

SnowVision следит за началом снегопада в Петропавловске-Камчатском по данным
городских камер. Панель показывает наблюдения, источник сигнала, оповещения и
почасовой прогноз.

## Запуск

Нужен Docker Compose. Сервис и frontend описаны в
[`teams/endermend/snowvision/compose.yaml`](teams/endermend/snowvision/compose.yaml).

```sh
cd teams/endermend/snowvision
docker compose -p snowvision up --build -d --wait
```

Веб-интерфейс: <http://localhost:5173>. Локальные настройки поместите в
`teams/endermend/snowvision/.env`; пример переменных — в `.env.example`.
Для остановки используйте `docker compose -p snowvision down`. Не добавляйте
`-v`, если нужно сохранить историю и последний кадр.

## Разработка

- Архитектура и локальная проверка: [`teams/endermend/snowvision/README.md`](teams/endermend/snowvision/README.md).
- Главный экран пилота: [`UI_UX_PILOT_TASK.md`](teams/endermend/context/UI_UX_PILOT_TASK.md).
- Ограничения и план сбора данных: [`task.md`](task.md).
- Контекст backend/frontend и правила продолжения работы: [`teams/endermend/context/`](teams/endermend/context/).
- Инструкции Codex: [`AGENTS.md`](AGENTS.md) и [`teams/endermend/AGENTS.md`](teams/endermend/AGENTS.md).

CI сборка находится в `.github/workflows/endermend-ci.yml`.
