#!/bin/bash
# Chrome кабинета WB на VPS. Не трогает Ozon (другой профиль, :98, CDP 9224, VNC 5902).
# Вход один раз: ssh -L 5902:127.0.0.1:5902 root@VPS → VNC → seller.wildberries.ru
set -euo pipefail
DIR=/root/wb-bot-chrome
DISPLAY_NUM=98
CDP_PORT=9224
VNC_PORT=5902
PASSFILE=/root/.vnc/passwd.wb
mkdir -p "$DIR" /var/log/wb-bot /tmp/.X11-unix /root/.vnc
chmod 1777 /tmp/.X11-unix

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
sleep 1
rm -f "$DIR/SingletonLock" "$DIR/SingletonSocket" "$DIR/SingletonCookie"

if [ ! -f "$PASSFILE" ]; then
  x11vnc -storepasswd "wb-login" "$PASSFILE" >/dev/null
  chmod 600 "$PASSFILE"
fi

env DISPLAY=":${DISPLAY_NUM}" \
  Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -ac \
  >/var/log/wb-bot/xvfb.log 2>&1 &
for i in $(seq 1 20); do
  if [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
    break
  fi
  sleep 0.5
done
if [ ! -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
  echo "Xvfb :${DISPLAY_NUM} not ready"
  tail -30 /var/log/wb-bot/xvfb.log || true
  exit 1
fi

: > /var/log/wb-bot/chrome.log
env DISPLAY=":${DISPLAY_NUM}" HOME=/root LANG=ru_RU.UTF-8 LANGUAGE=ru \
  /usr/bin/google-chrome-stable \
    --lang=ru \
    --accept-lang=ru-RU,ru \
    --remote-debugging-port="${CDP_PORT}" \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="${DIR}" \
    --profile-directory=Default \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-background-networking \
    --disable-sync \
    --disable-extensions \
    --disable-component-update \
    --hide-crash-restore-bubble \
    --renderer-process-limit=3 \
    --window-size=1920,1080 \
    about:blank \
    >/var/log/wb-bot/chrome.log 2>&1 &
chrome_pid=$!

ok=0
for i in $(seq 1 90); do
  if ! kill -0 "$chrome_pid" 2>/dev/null; then
    echo "Chrome exited during start"
    tail -80 /var/log/wb-bot/chrome.log || true
    exit 1
  fi
  if curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
      "http://127.0.0.1:${CDP_PORT}/json/version" | grep -q 200; then
    ok=1
    break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "WB CDP not ready"
  tail -80 /var/log/wb-bot/chrome.log || true
  exit 1
fi

kill_match "x11vnc.*rfbport ${VNC_PORT}"
env DISPLAY=":${DISPLAY_NUM}" \
  x11vnc -display ":${DISPLAY_NUM}" -rfbport "${VNC_PORT}" -rfbauth "$PASSFILE" \
  -shared -forever -localhost -noxdamage -ncache 10 \
  >/var/log/wb-bot/x11vnc.log 2>&1 &
sleep 1

curl -s -o /dev/null -w "wb_cdp=%{http_code}\n" --max-time 5 \
  "http://127.0.0.1:${CDP_PORT}/json/version"
echo "wb_chrome_start OK (VNC localhost:${VNC_PORT})"
