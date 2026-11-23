# Endermend workflow

Before work, read context/CURRENT.md, context/DECISIONS.md and context/RUNBOOK.md.
Read context/BACKEND.md or context/FRONTEND.md for the affected layer, both for API contract changes.
Keep these handoff files current after material decisions and verification.
Use the evidence-driven loop in RUNBOOK. Never invent successful checks.
Use tools/endermend-context/context.py record for material decisions/check outcomes.
The local post-commit hook captures an ignored checkpoint. Do not recursively commit it.
Never publish .context-local, credentials, runtime databases or raw conversation logs.

## Навыки проекта

Использовать навыки по задаче; перед первым применением читать их SKILL.md.
Имена ниже соответствуют каталогу доступных навыков Codex. Системные навыки
и навыки подключённых плагинов повторно не копировать в репозиторий.

| Навык | Когда полезен |
| --- | --- |
| `super-powers` | Новые функции, сложные баги и рефакторинг: проследить существующее поведение, внести согласованное изменение и проверить результат. |
| `cloud-environment:cloud-desktop` | Проверка приложения в браузере: формы, навигация, адаптивность и пользовательские сценарии. |
| `skill-creator` | Создание навыков под архитектуру и повторяющиеся задачи проекта. |
| `imagegen` | Иллюстрации, фоны и другие растровые изображения для интерфейса. |
| `sites:sites` | Создание и публикация сайтов через Sites, когда пользователь выбирает этот способ разработки. |
| `skill-installer` | Установка дополнительных навыков из каталога или GitHub. |

SnowVision по умолчанию остаётся приложением FastAPI/React в Docker Compose.
Наличие навыка Sites не означает перенос приложения или публикацию через Sites.
Если нужный навык отсутствует в каталоге, проверить его установку или подключение
соответствующего плагина; не заменять отсутствующий навык выдуманными инструкциями.
