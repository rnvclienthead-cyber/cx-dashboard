#!/bin/bash
set -euo pipefail
DIR=/root/ozon-bot-chrome
DISPLAY_NUM=99
CDP_PORT=9222

kill_match() {
  local pat="$1"
  local pids
  pids=$(pgrep -f "$pat" || true)
  for pid in $pids; do
    if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ]; then
      continue
    fi
    if tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -qE "ozon_chrome_(start|stop).sh"; then
      continue
    fi
    kill "$pid" 2>/dev/null || true
  done
}

kill_match "google-chrome-stable.*user-data-dir=${DIR}"
kill_match "chrome.*user-data-dir=${DIR}"
kill_match "Xvfb :${DISPLAY_NUM}"
kill_match "socat TCP-LISTEN:${CDP_PORT}"
kill_match "socat TCP-LISTEN:9223"
kill_match "socat TCP-LISTEN:5901"
kill_match "x11vnc.*rfbport 5901"
echo "ozon_chrome_stop OK"
