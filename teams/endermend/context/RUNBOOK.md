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

## Установка на vm-betta

На `vm-betta` (SSH-пользователь `student`) приложение установлено в
`/home/student/snowvision-deploy/releases/8089509` из отслеживаемых исходников
коммита `8089509`. Существующий Compose-проект — `release-q47f5hj9`;
сохранять это имя при обновлении, чтобы использовать прежний том
`release-q47f5hj9_history`. Секреты остаются в
`/home/student/snowvision-deploy/.env`, их не копировать в Git.
Локальный `compose.override.yaml` сохраняет публикацию web на порту 80;
основной Compose также оставляет `127.0.0.1:5173`. Backend наружу не опубликован.

```sh
ssh -F ~/.ssh/config vm-betta
cd /home/student/snowvision-deploy/releases/8089509
docker compose --env-file ../../.env -p release-q47f5hj9 ps
docker compose --env-file ../../.env -p release-q47f5hj9 exec -T web nginx -t
curl --fail http://127.0.0.1/api/health
```

При следующем обновлении готовить отдельный каталог release из Git-исходников,
сохранять override и имя Compose-проекта. Предыдущий каталог
`releases/confidence-8Ketr1Uw` сохранён. Архивы качества и `.context-local`
в установку не передавались.

## Пользовательские данные

История и последний кадр находятся в Compose volume. Не применять
`docker compose down -v`, не удалять БД, архивы, ключи или оригиналы.
Quality-данные хранятся в `teams/endermend/snowvision/.quality-local/`.
Секреты и сырые журналы не публиковать.

При `dubious ownership` использовать `git -c safe.directory=<checkout>`;
не менять global safe.directory. Не делать force push и не переписывать `main`.
