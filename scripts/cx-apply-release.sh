#!/usr/bin/env bash
# Приём выкладки CX с раннера GitHub.
# Не трогает .env, ключи, фото, загрузки и виртуальное окружение.
# Фронт копируется без --delete: старые файлы сайта остаются, телефон не зависает.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_ROOT="/root/cx-dashboard"
FRONTEND_ROOT="/var/www/cx-dashboard"
LOCK_FILE="/var/lock/cx-dashboard-deploy.lock"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKEND_ROOT/_deploy_backups/github-${STAMP}"

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "Нет собранного сайта: frontend/dist/index.html" >&2
  exit 2
fi
if [[ ! -x "$BACKEND_ROOT/venv/bin/python" ]]; then
  echo "На сервере нет $BACKEND_ROOT/venv" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Уже идёт другая выкладка" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
tar --exclude='__pycache__' --exclude='*.pyc' -czf "$BACKUP_DIR/backend.tgz" -C "$BACKEND_ROOT" backend
find "$BACKEND_ROOT" -maxdepth 1 -name '*.py' -exec cp {} "$BACKUP_DIR/" \;
if [[ -f "$BACKEND_ROOT/requirements.txt" ]]; then
  cp -a "$BACKEND_ROOT/requirements.txt" "$BACKUP_DIR/requirements.txt"
fi
if [[ -d "$BACKEND_ROOT/scripts" ]]; then
  tar --exclude='*.log' -czf "$BACKUP_DIR/scripts.tgz" -C "$BACKEND_ROOT" scripts
fi
if [[ -f "$FRONTEND_ROOT/index.html" ]]; then
  cp -a "$FRONTEND_ROOT/index.html" "$BACKUP_DIR/index.html"
fi

ROLLED=0
rollback() {
  if [[ "$ROLLED" -eq 1 ]]; then
    return
  fi
  ROLLED=1
  echo "Откат кода" >&2
  tar -xzf "$BACKUP_DIR/backend.tgz" -C "$BACKEND_ROOT"
  find "$BACKUP_DIR" -maxdepth 1 -name '*.py' -exec cp {} "$BACKEND_ROOT/" \;
  if [[ -f "$BACKUP_DIR/requirements.txt" ]]; then
    cp -a "$BACKUP_DIR/requirements.txt" "$BACKEND_ROOT/requirements.txt"
  fi
  if [[ -f "$BACKUP_DIR/scripts.tgz" ]]; then
    tar -xzf "$BACKUP_DIR/scripts.tgz" -C "$BACKEND_ROOT"
  fi
  if [[ -f "$BACKUP_DIR/index.html" ]]; then
    cp -a "$BACKUP_DIR/index.html" "$FRONTEND_ROOT/index.html"
  fi
  chmod 711 "$BACKEND_ROOT"
}

rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'uploads/' \
  --exclude '.ozon_pvz_cache.json' \
  --exclude '.ozon_map_points.json' \
  "$ROOT/backend/" "$BACKEND_ROOT/backend/"
find "$ROOT" -maxdepth 1 -name '*.py' ! -name '_tmp_*' -exec cp {} "$BACKEND_ROOT/" \;
if [[ -f "$ROOT/requirements.txt" ]]; then
  cp -a "$ROOT/requirements.txt" "$BACKEND_ROOT/requirements.txt"
fi
mkdir -p "$BACKEND_ROOT/scripts"
rsync -a --exclude '*.log' --exclude '_tmp_*' "$ROOT/scripts/" "$BACKEND_ROOT/scripts/"
chmod 711 "$BACKEND_ROOT"

if ! (cd "$BACKEND_ROOT" && PYTHONPATH="$BACKEND_ROOT" ./venv/bin/python -c "from backend.main import app" >/dev/null); then
  echo "Импорт backend.main не собрался, сайт не перезапускаю" >&2
  rollback
  exit 1
fi

if ! (cd "$BACKEND_ROOT" && PYTHONPATH="$BACKEND_ROOT" ./venv/bin/python -m backend.schema); then
  echo "Схема базы не применилась, сайт не перезапускаю" >&2
  rollback
  exit 1
fi

rsync -a "$ROOT/frontend/dist/" "$FRONTEND_ROOT/"
chmod 711 "$BACKEND_ROOT"
systemctl restart cx-backend

HEALTHY=0
for _try in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/docs || true)"
  if [[ "$code" == "200" ]]; then
    HEALTHY=1
    break
  fi
  sleep 3
done

if [[ "$HEALTHY" -ne 1 ]]; then
  echo "После перезапуска API не ответил, откатываю" >&2
  rollback
  systemctl restart cx-backend || true
  exit 1
fi

chmod 711 "$BACKEND_ROOT"
if systemctl cat cx-bg.service >/dev/null 2>&1; then
  systemctl restart cx-bg
fi

echo "DEPLOY_OK"
