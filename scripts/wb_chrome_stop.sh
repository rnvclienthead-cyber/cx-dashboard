#!/bin/bash
set -euo pipefail
DIR=/root/wb-bot-chrome
DISPLAY_NUM=98
CDP_PORT=9224
VNC_PORT=5902

kill_match() {
  local pat="$1"
  local pids
  pids=$(pgrep -f "$pat" || true)
  for pid in $pids; do
    if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ]; then
      continue
    fi
    if tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -qE "wb_chrome_(start|stop).sh"; then
      continue
    fi
    kill "$pid" 2>/dev/null || true
  done
}

kill_match "google-chrome-stable.*user-data-dir=${DIR}"
kill_match "chrome.*user-data-dir=${DIR}"
kill_match "Xvfb :${DISPLAY_NUM}"
kill_match "x11vnc.*rfbport ${VNC_PORT}"
echo "wb_chrome_stop OK"
