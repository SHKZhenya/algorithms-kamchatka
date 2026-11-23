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

## Серверная установка

Адрес, SSH-пользователь, серверные пути, имя Compose-проекта и тома хранятся
только в локальном `deployment.local.env` рядом с этим документом. Файл исключён
из Git. Для новой рабочей копии заполнить копию
[deployment.example.env](deployment.example.env); существующий локальный файл
не перезаписывать. Не добавлять реальные значения в документы, коммиты и PR.
Это конфигурация подключения, а не журнал передачи контекста; состояние
приложения по-прежнему хранить в отслеживаемых документах.

Из каталога `teams/endermend/context` загрузить доверенную локальную конфигурацию:

```sh
. ./deployment.local.env
ssh -F ~/.ssh/config -L 5173:127.0.0.1:5173 "$DEPLOY_SSH_TARGET"
```

Держать туннель открытым; интерфейс — http://localhost:5173.
После входа использовать значения конфигурации для перехода в текущий release,
проверки Compose и nginx. Локальные переменные автоматически на сервер не
передаются; не копировать файл подключения в release-архив.

При обновлении готовить отдельный release из отслеживаемых исходников.
Сохранять существующие `.env`, override, имя Compose-проекта и том истории;
их точные значения сверять с локальной конфигурацией. Публикацию портов
сохранять по серверному override, backend наружу не публиковать.
В текущем release выполнить команды, подставив существующее имя проекта
и путь к серверному файлу окружения из локальной конфигурации:

```sh
docker compose --env-file <environment-file> -p <existing-project> ps
docker compose --env-file <environment-file> -p <existing-project> exec -T web nginx -t
curl --fail http://127.0.0.1/api/health
```

Не удалять предыдущий release, историю или архивы качества при обновлении.

## Пользовательские данные

История и последний кадр находятся в Compose volume. Не применять
`docker compose down -v`, не удалять БД, архивы, ключи или оригиналы.
Quality-данные хранятся в `teams/endermend/snowvision/.quality-local/`.
Секреты и сырые журналы не публиковать.

При `dubious ownership` использовать `git -c safe.directory=<checkout>`;
не менять global safe.directory. Не делать force push и не переписывать `main`.
