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

## Актуальный приоритет

Диспетчеру на главном экране нужны факт снегопада в городе, погода со
штормовым предупреждением и три камеры с наибольшим вкладом в оценку.
Задание и реализация находятся в [PR #14](https://github.com/SHKZhenya/algorithms-kamchatka/pull/14).
Экран выводит статус по пригодным наблюдениям, компактную погоду и бюллетень,
а также до трёх камер из расчёта backend. Состояние UX-пилота и ограничения —
в [PILOT_NOTES.md](PILOT_NOTES.md).
Текущее состояние и проверки хранить в CURRENT, порядок публикации — в AGENTS.

Локальный `.context-local/LATEST.md`, если доступен, содержит SHA снимка,
который нужно сверить с HEAD. Старые checkpoints и архивы чатов не являются
активными поручениями. Путь `.context-local/session-path` может относиться
к прежнему чату: не перепривязывать его автоматически и не искать чужие сессии.
