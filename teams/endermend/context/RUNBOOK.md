# Проверки и локальный контекст

Рабочая ветка `endermend`; единственная цель push — `fork`,
`endermend/algorithms-kamchatka`. Не отправлять изменения в `origin`
(`SHKZhenya/algorithms-kamchatka`) и не создавать там PR. Для изменений
обязателен цикл commit → push в `fork` → проверка CI. PR создавать только
по прямой просьбе пользователя. Правила — в [AGENTS](../AGENTS.md).

## Проверки

Из `teams/endermend/snowvision`:

```sh
docker compose build
docker compose up -d --wait
docker compose exec -T snowvision python -m unittest -v
docker compose exec -T web nginx -t
curl --fail http://localhost:5173/api/health
```

Из корня `python3 tools/endermend-context/context.py verify` запускает
контекстные тесты и изолированные backend build/test. Единственный CI —
`.github/workflows/endermend-ci.yml`.

## Пользовательские данные

История и последний кадр находятся в Compose volume. Не применять
`docker compose down -v`, не удалять БД, архивы, ключи или оригиналы.
Quality-данные хранятся в `teams/endermend/snowvision/.quality-local/`.
Секреты и сырые журналы не публиковать.

Локальный hook пишет checkpoint только в игнорируемый `.context-local`.
Сводка ограничена 16 КБ; сокращать повторы, не увеличивать лимит. Архив чата
создавать только для сессии, явно выбранной владельцем. На ФС без надёжного
`chmod` private archive размещается в `~/.local/state/snowvision-context`.

При `dubious ownership` использовать `git -c safe.directory=<checkout>`;
не менять global safe.directory. Не делать force push и не переписывать `main`.
