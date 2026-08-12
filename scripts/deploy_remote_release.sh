#!/usr/bin/env bash
set -euo pipefail

INCOMING_DIR="${1:?Укажите каталог входящего релиза}"
BACKEND_ROOT="/root/cx-dashboard"
FRONTEND_ROOT="/var/www/cx-dashboard"
LOCK_FILE="/var/lock/cx-dashboard-deploy.lock"

TARGET_COMMIT="$(tr -d '[:space:]' < "$INCOMING_DIR/target_commit")"
if [[ ! "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Некорректный target_commit" >&2
  exit 2
fi

for required in source.tgz frontend-dist.tgz ancestors.txt; do
  if [[ ! -f "$INCOMING_DIR/$required" ]]; then
    echo "В релизе отсутствует $required" >&2
    exit 2
  fi
done

exec 9>"$LOCK_FILE"
flock -x 9

CURRENT_COMMIT=""
if [[ -f "$BACKEND_ROOT/.deployed_commit" ]]; then
  CURRENT_COMMIT="$(tr -d '[:space:]' < "$BACKEND_ROOT/.deployed_commit")"
fi

# Если другой агент уже выложил более новый или параллельный коммит, этот
# релиз не имеет права затирать его более старым состоянием.
if [[ -n "$CURRENT_COMMIT" ]] && ! grep -Fxq "$CURRENT_COMMIT" "$INCOMING_DIR/ancestors.txt"; then
  echo "ОТКАЗ: сервер уже на $CURRENT_COMMIT, который не является предком $TARGET_COMMIT" >&2
  exit 42
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKEND_ROOT/_deploy_backups/release-${STAMP}-${TARGET_COMMIT:0:12}"
mkdir -p "$BACKUP_DIR"

tar \
  --exclude='./.env' \
  --exclude='./bot_api_key.json' \
  --exclude='./venv' \
  --exclude='./uploads' \
  --exclude='./static' \
  --exclude='./_deploy_backups' \
  --exclude='./_deploy_incoming' \
  --exclude='./frontend/node_modules' \
  --exclude='./frontend/dist' \
  --exclude='./output' \
  --exclude='./tmp' \
  -czf "$BACKUP_DIR/source.tgz" -C "$BACKEND_ROOT" .
tar -czf "$BACKUP_DIR/frontend-dist.tgz" -C "$FRONTEND_ROOT" .

RELEASE_SOURCE="$(mktemp -d /tmp/cx-dashboard-source.XXXXXX)"
RELEASE_FRONTEND="$(mktemp -d /tmp/cx-dashboard-frontend.XXXXXX)"
ROLLBACK_SOURCE="$(mktemp -d /tmp/cx-dashboard-source-rollback.XXXXXX)"
ROLLBACK_FRONTEND="$(mktemp -d /tmp/cx-dashboard-frontend-rollback.XXXXXX)"
cleanup() {
  rm -rf "$RELEASE_SOURCE" "$RELEASE_FRONTEND" "$ROLLBACK_SOURCE" "$ROLLBACK_FRONTEND"
}
trap cleanup EXIT

ensure_static_access() {
  # Nginx раздает /static и /uploads напрямую из /root/cx-dashboard.
  # Ему нужен execute-bit на каждом родительском каталоге, иначе все фото
  # и вложения массово падают с 403 Permission denied.
  chmod 711 "$BACKEND_ROOT"
  [[ -d "$BACKEND_ROOT/static" ]] && chmod 755 "$BACKEND_ROOT/static"
  [[ -d "$BACKEND_ROOT/uploads" ]] && chmod 755 "$BACKEND_ROOT/uploads"
}

sync_source() {
  local source_dir="$1"
  rsync -az --delete \
    --exclude='.env' \
    --exclude='bot_api_key.json' \
    --exclude='venv/' \
    --exclude='uploads/' \
    --exclude='static/' \
    --exclude='_deploy_backups/' \
    --exclude='_deploy_incoming/' \
    --exclude='frontend/node_modules/' \
    --exclude='frontend/dist/' \
    --exclude='output/' \
    --exclude='tmp/' \
    "$source_dir/" "$BACKEND_ROOT/"
}

tar -xzf "$INCOMING_DIR/source.tgz" -C "$RELEASE_SOURCE"
tar -xzf "$INCOMING_DIR/frontend-dist.tgz" -C "$RELEASE_FRONTEND"
sync_source "$RELEASE_SOURCE"
ensure_static_access
rsync -az --delete "$RELEASE_FRONTEND/" "$FRONTEND_ROOT/"

systemctl restart cx-backend

HEALTHY=0
for _attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8001/api/v1/health/db >/dev/null; then
    HEALTHY=1
    break
  fi
  sleep 2
done

if [[ "$HEALTHY" -eq 1 ]] && ! curl -fsS https://cxvo.ru/ >/dev/null; then
  HEALTHY=0
fi

if [[ "$HEALTHY" -ne 1 ]]; then
  echo "Проверка backend/site не прошла, выполняется автоматический откат" >&2
  tar -xzf "$BACKUP_DIR/source.tgz" -C "$ROLLBACK_SOURCE"
  tar -xzf "$BACKUP_DIR/frontend-dist.tgz" -C "$ROLLBACK_FRONTEND"
  sync_source "$ROLLBACK_SOURCE"
  ensure_static_access
  rsync -az --delete "$ROLLBACK_FRONTEND/" "$FRONTEND_ROOT/"
  systemctl restart cx-backend
  exit 1
fi

printf '%s\n' "$TARGET_COMMIT" > "$BACKEND_ROOT/.deployed_commit"

echo "DEPLOY_OK $TARGET_COMMIT"
