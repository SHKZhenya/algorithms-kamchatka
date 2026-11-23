# Проверки и локальный контекст

Команды выполнять из checkout, если не указано иначе. Рабочая ветка —
`endermend`; `origin` — `SHKZhenya/algorithms-kamchatka`, `fork` —
`endermend/algorithms-kamchatka`. Процедура commit → push → PR → CI
определена в [AGENTS.md](../AGENTS.md) и применяется ко всем завершённым задачам.
Официальный CLI — `/home/User/.local/bin/gh`; не читать и не выводить токены.

## Проверка приложения

Из `teams/endermend/snowvision`:

```sh
docker compose -p snowvision build web
docker compose -p snowvision exec -T snowvision python -m unittest -v
docker compose -p snowvision exec -T web nginx -t
```

Полный изолированный цикл сборки и тестов из checkout:

```sh
python3 tools/endermend-context/context.py verify
```

Если рабочий стек не запущен, использовать отдельный Compose project и
`run --rm --no-deps snowvision python -m unittest -v`; не подменять недоступный Engine
имитацией проверки. Для запуска рабочего сервиса и env-файла следовать
[README приложения](../snowvision/README.md). Единственный CI workflow —
`.github/workflows/endermend-ci.yml`.

## Журнал и checkpoints

Материальные решения и результаты записывать через `context.py record "событие"`.
Локальный `core.hooksPath=.githooks`; перед настройкой проверить прежние hooks.
Post-commit создаёт checkpoint, SHA-256 manifest и событие в игнорируемом
`.context-local`, не меняя tracked files и не выполняя commit или сетевых запросов.
Сбой checkpoint не отменяет commit: исправить причину и выполнить `context.py capture`.
Сводка ограничена 16 КБ; сокращать повторы, а не увеличивать лимит.

`LATEST.md/latest.json` — последний снимок; `checkpoints/` — история,
`events.jsonl` — решения и результаты, `checks/` — сжатые логи проверок.
Архивы `chat/` обновляются только для явно выбранной владельцем сессии через
`archive-chat <JSONL>` или действующую привязку `session-path`. Не выбирать
случайный последний чат. Маскирование секретов не разрешает публикацию сырых журналов.
На файловой системе без рабочих chmod `.context-local` ссылается на приватный
каталог в `~/.local/state/snowvision-context`; архивы должны сохранять закрытые права.

При ошибке dubious ownership использовать `git -c safe.directory=<checkout>`,
не устанавливать глобальный `safe.directory=*`. Не делать force push,
не переписывать main и не применять `docker compose down -v`.
