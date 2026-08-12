# Инструкции проекта

Перед работой прочитай и соблюдай `AGENTS.md`.

Для деплоя CX Dashboard запрещены ручные частичные выкладки. Используй только:

```bash
./scripts/deploy_committed_release.sh
```

Скрипт выкладывает полный закоммиченный `HEAD` и защищает сервер от отката более старым параллельным релизом.
При этом он обязан сохранять серверные постоянные артефакты, которые не лежат в git:

- `/root/cx-dashboard/.env`
- `/root/cx-dashboard/bot_api_key.json`
- `/root/cx-dashboard/static/`
- `/root/cx-dashboard/uploads/`

Также после релиза нельзя ломать доступ nginx к `/static` и `/uploads`.
