#!/usr/bin/env bash
# Автоимпорт выгрузки WB «В доставке» из ~/Downloads.
# Как только появляется новый delivery*.xlsx — сам заливает на сервер.
set -euo pipefail

DOWNLOADS="${HOME}/Downloads"
STATE_DIR="${HOME}/.cx-dashboard"
STATE_FILE="${STATE_DIR}/wb_dbw_last_import"
LOG_FILE="${HOME}/Library/Logs/wb-dbw-auto-import.log"
SERVER="root@72.56.240.22"
REMOTE_PATH="/tmp/delivery_auto.xlsx"

mkdir -p "$STATE_DIR"
touch "$LOG_FILE"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"; }

# самый свежий delivery*.xlsx (игнорируем временные ~$)
latest="$(
  find "$DOWNLOADS" -maxdepth 1 -type f \( -name 'delivery.xlsx' -o -name 'delivery *.xlsx' -o -name 'delivery*.xlsx' \) \
    ! -name '~$*' -print0 2>/dev/null \
  | xargs -0 stat -f '%m %N' 2>/dev/null \
  | sort -rn \
  | head -1 \
  | cut -d' ' -f2-
)"

if [[ -z "${latest}" ]]; then
  exit 0
fi

mtime="$(stat -f '%m' "$latest")"
size="$(stat -f '%z' "$latest")"
# файл ещё качается — подождём следующего тика
if [[ "$size" -lt 1000 ]]; then
  exit 0
fi

last_mtime=0
last_path=""
if [[ -f "$STATE_FILE" ]]; then
  # format: mtime|path
  IFS='|' read -r last_mtime last_path < "$STATE_FILE" || true
fi

if [[ "$mtime" == "$last_mtime" && "$latest" == "$last_path" ]]; then
  exit 0
fi

log "Найден новый файл: $latest (mtime=$mtime size=$size)"
scp -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new \
  "$latest" "${SERVER}:${REMOTE_PATH}" >>"$LOG_FILE" 2>&1

# ждём освобождения lock до ~3 минут
remote_out="$(
  ssh -o ConnectTimeout=15 "$SERVER" bash -s <<EOF
set -e
cd /root/cx-dashboard
for i in \$(seq 1 18); do
  out=\$(PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python3 -m backend.wb_dbw_delivery import ${REMOTE_PATH} 2>&1 || true)
  echo "\$out"
  if echo "\$out" | grep -q "импорт\|✅"; then
    exit 0
  fi
  if echo "\$out" | grep -qiE "Traceback|Error|❌"; then
    exit 1
  fi
  # lock занят — ждём
  sleep 10
done
echo "timeout waiting for lock"
exit 1
EOF
)"

log "$remote_out"
echo "${mtime}|${latest}" > "$STATE_FILE"
log "Готово: таблица актуализирована из $(basename "$latest")"
