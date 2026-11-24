# Начало работы со SnowVision

Checkout: `/mnt/disk_d/Development/kam4atka/algorithms-kamchatka`.
Приложение: `teams/endermend/snowvision`, FastAPI/SQLite + React/TypeScript/Vite
в Docker Compose. Локальный вход — http://localhost:5173, время продукта — Камчатка UTC+12.

## Что прочитать

1. Корневой [AGENTS.md](../../../AGENTS.md) и [правила команды](../AGENTS.md).
2. [CURRENT.md](CURRENT.md), [DECISIONS.md](DECISIONS.md), [RUNBOOK.md](RUNBOOK.md).
3. [BACKEND.md](BACKEND.md) или [FRONTEND.md](FRONTEND.md) по затронутому слою;
   для API читать оба.
4. По текущей задаче — [UI/UX пилота](UI_UX_PILOT_TASK.md). По качеству
   распознавания и прогноза — [task.md](../../../task.md).

Проверить status, HEAD и remotes. При простом возобновлении после чтения
ждать поручения пользователя; наличие плана не запускает его автоматически.

## Состояние и продолжение

Главный экран пилота реализован; требования — в [UI/UX задании](UI_UX_PILOT_TASK.md),
непроверенные полевые условия — в [PILOT_NOTES.md](PILOT_NOTES.md).
Текущее состояние и результаты проверок хранить в [CURRENT.md](CURRENT.md),
порядок публикации — в [правилах команды](../AGENTS.md).
Новую работу начинать только по конкретному поручению пользователя.
