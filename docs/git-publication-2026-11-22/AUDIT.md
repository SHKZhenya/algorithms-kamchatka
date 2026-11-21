# Git publication audit — 22 ноября 2026, Камчатка

Источник: локальный Git, свежие fetch/ls-remote обоих репозиториев и GitHub PR API. Статус: проверено непосредственно перед push.

Исходная папка не имела коммитов, remotes или сохранённых веток: unborn main, все файлы untracked. Она сохранена без удаления и перезаписи исходников. Подготовлен отдельный checkout от main Жени `ba1c6d2c0cb226fe8b04fc756f88966171e1bb57` (main продвинулся с `55e5c227` во время аудита: PR #10 уже слит).

## Уже опубликовано

- PR #2 (пилот маршрутизации/погода) и #7 (3D-город) слиты в целевой main. Ветка форка `codex/runnable-pilot-ci` полностью содержится в main; `Neverrie/city-sim` и `Neverrie/city-sim-lab` указывают на `a6a3f9c`.
- В форке main оставался `0922d1b`; он не использован как база новой работы.
- Единственный открытый PR в целевом репозитории на момент повторной проверки — #11, SnowVision другого автора; подходящего открытого PR Neverrie нет. Старый #7 не переоткрывается. Создаётся ровно один новый Draft PR.
- Ядро snowops, входы data/pkc и все 17 файлов city_sim/web уже опубликованы и побайтно совпадают с локальными. Они не изменены.

## Что сохранено

Семь логических коммитов: объектный backend/геоданные и закреплённый внешний движок; динамический снег; отключённый по умолчанию импорт дорожных источников; фасады/доступность техники; муниципальные факты/гипотезы закупки; существующие исследовательские скрипты и компактные результаты; документация/аудит.

Корневой README, учебный проект, файлы других команд, CI и более новый weather_coverage из main сохранены. Локальная версия README с описанием маршрутизации опубликована как docs/ROUTING_PILOT_NOTES.md. Локальные старые погодные исходники и выполненный notebook не заменяют более новые версии main.

Исходные документы (DOC/DOCX/PDF/RAR/XLSX), выгрузки СКДФ с неустановленными правами, сырые ответы API/данные сторонних источников, SQLite, кэши, ZIP, CFD-поля, видео и тяжёлые результаты не включены. Оригиналы остаются в исходной папке. Для OSM сохранены атрибуция и манифесты; реестр 0903 содержит производные фактические записи и явно неподтверждённые географические гипотезы, без исходного архива и полной извлечённой документации.

[Поимённый реестр исключений](EXCLUDED_FILES.json) содержит причины; out/, .state/, __pycache__/ исключены целиком. [Контрольные суммы выбранных исходников](PUBLICATION_MANIFEST.json) фиксируют происхождение. Отчёты и скрипты могут ссылаться на намеренно непубликуемые исходные данные и результаты: полное воспроизведение исследований требует локальных материалов и прав доступа. Это не поставка всех исследовательских артефактов.

## Проверка

- city_sim/tests: 81 тест, OK, без skips после подключения CITY_SIM_DANIL к существующей локальной копии закреплённой модели; модель проверяется штатными SHA256. Она не добавлена в Git, Docker получает её по закреплённому commit.
- integration_tests: 9 тестов ядра, OK.
- tests: 8 тестов стартового проекта, OK.
- prototype: 3 теста, OK.
- Первый backend-прогон без внешней модели: 81 тест, 1 ошибка FileNotFoundError и 1 skip. После настройки окружения полный повтор прошёл.
- В логах присутствуют ResourceWarning о незакрытых SQLite-соединениях и DeprecationWarning Shapely; они не скрывались и код в рамках Git-задачи не исправлялся.
- UI: 17 файлов побайтно совпадают с main, diff city_sim/web пустой.
- Сканирование выбранных файлов на GitHub/AWS/private-key/литеральные ключи не нашло признаков секретов. Крупнейший новый runtime-файл works.json — 1,56 МБ; новые материалы суммарно около 9 МБ до добавления реестра исключений.
- Удалённых путей и конфликтных маркеров нет. Diff whitespace отмечает исходные CRLF в CSV, два пробела в цитируемом поле objects.csv один конечный пробел Markdown и намеренные двойные пробелы Markdown для переноса строк; сохранены как в источниках, программных конфликтов нет.
- Ветка построена непосредственно на актуальном upstream/main, чужие изменения не перезаписывались; merge и force-push не выполнялись.

## Ветки после fetch

```text
* codex/backend-consolidation-20261122   21bd5fe [upstream/main: ahead 6] Preserve research scripts, reports and compact validation results
  main                                   ba1c6d2 [upstream/main] Merge pull request #10 from NDaVS/main
  remotes/origin/HEAD                    -> origin/main
  remotes/origin/Neverrie/city-sim       a6a3f9c Document a clean checkout and first snow calculation
  remotes/origin/Neverrie/city-sim-lab   a6a3f9c Document a clean checkout and first snow calculation
  remotes/origin/SHKZhenya-patch-1       bf67ad7 Create README.md
  remotes/origin/SHKZhenya-patch-2       c9c05ed Add README.md for team 02 with initial details
  remotes/origin/codex/runnable-pilot-ci 1bafbfe Show wind directions across atlas layers
  remotes/origin/main                    0922d1b Update team number in README.md
  remotes/upstream/HEAD                  -> upstream/main
  remotes/upstream/SHKZhenya-patch-1     bf67ad7 Create README.md
  remotes/upstream/SHKZhenya-patch-2     c9c05ed Add README.md for team 02 with initial details
  remotes/upstream/fix-pr-1              da6e305 Merge PR 1 and preserve all CI workflows
  remotes/upstream/gh-pages              3e3daf3 Add files via upload
  remotes/upstream/main                  ba1c6d2 Merge pull request #10 from NDaVS/main
```
