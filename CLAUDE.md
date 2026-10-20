# Инструкции проекта

Перед работой прочитай и соблюдай `AGENTS.md`.

Деплой CX Dashboard после переноса репозитория в организацию RNV-Development — пуш в `main` (файл `.github/workflows/deploy.yml`, раннер `cxvo`). Пароли и `.env` остаются на сервере. Ручная выкладка с Mac — если GitHub-путь не используется.

Порядок (пункт 1): фронт `rsync` без `--delete` и **без** restart API; бэкенд — `rsync` и **один** `systemctl restart cx-backend`, затем `chmod 711`.

Перед выкладкой: `./scripts/preflight.sh` (тесты + сборка + импорт backend). Красный шаг — не выкладывать.

```
npm run build   → только локально
rsync dist/     → /var/www/cx-dashboard/   (без --delete, без restart)
rsync backend   → /root/cx-dashboard/backend/
systemctl restart cx-backend              (только если меняли бэкенд)
chmod 711 /root/cx-dashboard
```

Не использовать `./scripts/deploy_committed_release.sh`.

На сервере сохранять:

- `/root/cx-dashboard/.env`
- `/root/cx-dashboard/bot_api_key.json`
- `/root/cx-dashboard/static/`
- `/root/cx-dashboard/uploads/`
