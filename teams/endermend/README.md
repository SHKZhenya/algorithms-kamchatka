# Команда endermend

Проект SnowVision находится в snowvision/. Команда ранее называлась «салага».

```bash
cd teams/endermend/snowvision
docker compose up --build -d --wait
docker compose exec -T snowvision python -m unittest -v
```

GitHub Actions использует .github/workflows/endermend-ci.yml в корне репозитория.
Шаблон команды хранится в teams/endermend/.github/workflows/ci.yml.
При изменениях обновляйте обе копии. CI запускается при push в endermend,
на pull request и вручную после появления workflow в основной ветке.
Frontend-тесты и TypeScript-сборка выполняются внутри Docker build.
