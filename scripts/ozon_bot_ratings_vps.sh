#!/bin/bash
# Ночной снимок рейтингов Ozon из кабинета. Не трогает дневной сбор отзывов.
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export OZON_BOT_CDP="http://127.0.0.1:9222"
export OZON_BOT_VPS="root@72.56.240.22"
LOG=/var/log/ozon-bot/ratings.log
mkdir -p /var/log/ozon-bot

systemctl start ozon-vpn.service
if ! curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:9222/json/version | grep -q 200; then
  /root/cx-dashboard/scripts/ozon_chrome_start.sh >> "$LOG" 2>&1 || true
fi

cd /root/cx-dashboard
{
  echo "======== $(date -Is) ratings start ========"
  /root/cx-dashboard/venv/bin/python -u /root/cx-dashboard/ozon_bot_ratings.py
  echo "======== $(date -Is) ratings end ========"
} >> "$LOG" 2>&1
