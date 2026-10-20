#!/bin/bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export OZON_BOT_CDP="http://127.0.0.1:9222"
export OZON_BOT_VPS="root@72.56.240.22"
export OZON_BOT_LOG_DIR="/var/log/ozon-bot"
# Быстрее дотягиваем комменты/фото возвратов (карточки кабинета)
export OZON_BOT_RETURNS_ENRICH_LIMIT="${OZON_BOT_RETURNS_ENRICH_LIMIT:-120}"
LOG=/var/log/ozon-bot/tick.log
mkdir -p /var/log/ozon-bot

# Расписание задаёт systemd timer (каждые 5 минут).
# Окно здесь не режем — иначе ночные прогоны не стартуют.

systemctl start ozon-vpn.service
if ! curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:9222/json/version | grep -q 200; then
  /root/cx-dashboard/scripts/ozon_chrome_start.sh >> "$LOG" 2>&1 || true
fi

cd /root/cx-dashboard
{
  echo "======== $(date -Is) tick start ========"
  /root/cx-dashboard/venv/bin/python -u /root/cx-dashboard/ozon_bot_tick.py
  echo "======== $(date -Is) tick end ========"
} >> "$LOG" 2>&1
