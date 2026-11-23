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

Для frontend запустить `docker compose build web`: Dockerfile выполняет
Vitest, TypeScript и Vite. После изменения backend пересобрать сервис
`docker compose build snowvision` и затем повторить backend-тесты.
Единственный CI — `.github/workflows/endermend-ci.yml`.

## Пользовательские данные

История и последний кадр находятся в Compose volume. Не применять
`docker compose down -v`, не удалять БД, архивы, ключи или оригиналы.
Quality-данные хранятся в `teams/endermend/snowvision/.quality-local/`.
Секреты и сырые журналы не публиковать.

При `dubious ownership` использовать `git -c safe.directory=<checkout>`;
не менять global safe.directory. Не делать force push и не переписывать `main`.
