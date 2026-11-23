# Текущий контекст

Следующее задание: [UI/UX пилота для диспетчера](UI_UX_PILOT_TASK.md).

Проект: SnowVision, мониторинг начала снегопада в Петропавловске-Камчатском.
Upstream: SHKZhenya/algorithms-kamchatka. Форк: endermend/algorithms-kamchatka.
Ветка: endermend. Предыдущий PR #11 в upstream принят.
Прямой push upstream запрещён правами аккаунта, отправлять в remote fork.

Рабочая папка: /mnt/disk_d/Development/kam4atka/algorithms-kamchatka.
Сервис: teams/endermend/snowvision. Команда ранее называлась «салага».
Стек: FastAPI, SQLite, httpx, OpenCV, React/TS/Vite, nginx, Docker Compose.
Порт: http://localhost:5173. Compose project production: snowvision.

- [BACKEND.md](BACKEND.md) — FastAPI, камеры, анализ кадров, SQLite и доставка.
- [FRONTEND.md](FRONTEND.md) — React, снимки, журнал сигналов и прогноз.
- [task.md](../../../task.md) — план улучшения анализа кадров и проверки локального прогноза начала снегопада.

23 ноября 2026. `quality` собирает данные; `quality-model` запускает MobileNetV3
на CPU без сети. Протокол и команды — task.md. Проверено: 75 backend-тестов,
20 frontend-тестов, TypeScript/Vite и 2 теста контекста.

Офлайн-кандидат по движущимся деталям: SPTV 2/2, TSBOW 6/7; на TSBOW 9 ложных
ответов против 4 у OpenCV. Ночное окно неустойчиво к сдвигу 100 мс. Для допуска
нужны независимые ночные/дорожные клипы; для прогноза — городская разметка и
метеовыпуски, известные на момент выпуска.

История восстановления и резервных копий описана в START_HERE.md
и ../RECOVERY.md. Оригиналы recovered сохраняются отдельно. Коммит 1f24c3e
сохранён в ветке `endermend-original-1f24c3e`.

Порядок работы с локальными снимками и журналом описан в RUNBOOK.md.

UI 1869745 прошёл CI. Реорганизация и quality сохраняются отдельным коммитом.
Шесть навыков и их применение указаны в ../AGENTS.md.
