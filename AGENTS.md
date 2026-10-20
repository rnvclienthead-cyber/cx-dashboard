# Обязательные правила для всех AI-агентов

## Деплой CX Dashboard

1. Перед любым деплоем полностью прочитать `docs/04_ДЕПЛОЙ.md`. Выкладывать только по явной просьбе.
1a. Перед выкладкой прогнать `./scripts/preflight.sh` (тесты бэка+фронта, сборка фронта, импорт `backend.main`). Любой красный шаг — не выкладывать.
2. Штатный способ после переноса репозитория в организацию RNV-Development — пуш в `main`. Выкладка: `.github/workflows/deploy.yml` и раннер `cxvo` на сервере. Секрета GitHub нет, пароли и `.env` остаются на сервере. Фронт на сайт копируется без `--delete`. Ручная выкладка с Mac — только если этот путь не используется. Не выкладывать из `git archive`: в снимке нет ключей сервера.
3. **Порядок (пункт 1, согласован):**
   - Фронт: `npm run build` локально → `rsync` `frontend/dist/` в `/var/www/cx-dashboard/` **без `--delete`**. Службу API **не** перезапускать.
   - Бэкенд: `rsync`/`scp` в `/root/cx-dashboard/backend/` → **один** `systemctl restart cx-backend` (не весь VPS). Затем `chmod 711 /root/cx-dashboard`.
4. **Не использовать** `scripts/deploy_committed_release.sh` и сборку из `git archive`. В git нет ключей карты и адресов — такая выкладка уже ломала сайт.
5. Не выкладывать заведомо старую копию поверх более новой на сервере. Если сомневаешься — остановиться и спросить.
6. Не затирать серверные постоянные файлы: `/root/cx-dashboard/.env`, `/root/cx-dashboard/bot_api_key.json`, каталоги `/root/cx-dashboard/static/` и `/root/cx-dashboard/uploads/`.
7. Никогда не собирать frontend на сервере и не восстанавливать локальный `frontend/src` с сервера.
8. **Полный комплект бэкенда.** Если `main.py` вызывает новую функцию — вместе выкладывать файл, где она определена (часто `backend/routers/cx_responses.py`, схема — `backend/schema.py`). Нельзя заливать только `main.py` и рестартить. Перед рестартом при сомнении: `python -m backend.schema` — упало → не рестартить.
9. После `systemctl restart cx-backend`: `chmod 711 /root/cx-dashboard`, затем ждать, пока `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/docs` станет `200`. `systemctl is-active` недостаточно: мастер uvicorn может быть «active», воркеры мертвы, порт 8001 пустой → nginx 502 HTML → у людей «Unexpected token '<'» и пустая страница.
10. Перед рестартом на сервере: `PYTHONPATH=/root/cx-dashboard ./venv/bin/python -c "from backend.main import app"`. Если падает — не рестартить.
