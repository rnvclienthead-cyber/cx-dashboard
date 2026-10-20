#!/bin/bash
set -euo pipefail
NS=ozonvpn
DIR=/root/ozon-bot-chrome
DISPLAY_NUM=99
CDP_PORT=9222
NS_IP=10.200.200.2
CDP_EXT_PORT=9223
mkdir -p "$DIR" /var/log/ozon-bot /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

systemctl start ozon-vpn.service

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
kill_match "socat TCP-LISTEN:${CDP_EXT_PORT}"
sleep 1
rm -f "$DIR/SingletonLock" "$DIR/SingletonSocket" "$DIR/SingletonCookie"

ip netns exec "$NS" env DISPLAY=":${DISPLAY_NUM}" \
  Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -ac \
  >/var/log/ozon-bot/xvfb.log 2>&1 &
sleep 1

# Chrome CDP only on loopback inside ns (Chrome security)
ip netns exec "$NS" env DISPLAY=":${DISPLAY_NUM}" HOME=/root \
  /usr/bin/google-chrome-stable \
    --remote-debugging-port="${CDP_PORT}" \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="${DIR}" \
    --profile-directory=Default \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --window-size=1920,1080 \
    about:blank \
    >/var/log/ozon-bot/chrome.log 2>&1 &

ok=0
for i in $(seq 1 40); do
  if ip netns exec "$NS" curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
      "http://127.0.0.1:${CDP_PORT}/json/version" | grep -q 200; then
    ok=1
    break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "CDP not ready inside netns"
  tail -50 /var/log/ozon-bot/chrome.log || true
  exit 1
fi

# Expose CDP to host via veth IP (inside ns)
ip netns exec "$NS" socat \
  TCP-LISTEN:${CDP_EXT_PORT},bind=${NS_IP},fork,reuseaddr \
  TCP:127.0.0.1:${CDP_PORT} \
  >/var/log/ozon-bot/cdp-ns-proxy.log 2>&1 &

# Also mirror to host localhost:9222 for tick scripts
socat TCP-LISTEN:${CDP_PORT},bind=127.0.0.1,fork,reuseaddr \
  TCP:${NS_IP}:${CDP_EXT_PORT} \
  >/var/log/ozon-bot/cdp-host-proxy.log 2>&1 &
sleep 1

curl -s -o /dev/null -w "cdp_host=%{http_code}\n" --max-time 5 \
  "http://127.0.0.1:${CDP_PORT}/json/version"

curl -s -X PUT "http://127.0.0.1:${CDP_PORT}/json/new?https://seller.ozon.ru/app/reviews" >/dev/null || true

# VNC to Xvfb for one-time seller login (SSH tunnel: ssh -L 5901:127.0.0.1:5901 root@VPS)
kill_match "x11vnc.*rfbport 5901"
ip netns exec "$NS" env DISPLAY=":${DISPLAY_NUM}" \
  x11vnc -display ":${DISPLAY_NUM}" -rfbport 5901 -rfbauth /root/.vnc/passwd.ozon \
  -shared -forever -listen "${NS_IP}" -noxdamage -ncache 10 \
  >/var/log/ozon-bot/x11vnc.log 2>&1 &
sleep 1

kill_match "socat TCP-LISTEN:5901"
socat TCP-LISTEN:5901,bind=127.0.0.1,fork,reuseaddr \
  TCP:${NS_IP}:5901 \
  >/var/log/ozon-bot/vnc-proxy.log 2>&1 &

echo "ozon_chrome_start OK (VNC localhost:5901)"
