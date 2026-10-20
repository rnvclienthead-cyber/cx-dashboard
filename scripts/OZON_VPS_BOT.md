# Ozon cabinet bot on VPS (VPN-isolated)

## Services
- `ozon-vpn.service` — AmneziaWG only inside netns `ozonvpn`
- `ozon-chrome.service` — Chrome + CDP + VNC inside that netns
- `ozon-bot-tick.timer` — every 2h during Moscow 09:00–19:00 (UTC 06,08,10,12,14)

API worker (`ozon_worker.py` cron) is unchanged.

## VPN config (do not commit / do not paste in chat)
`/etc/amnezia/amneziawg/ozon-bot.conf` → runtime copy without DNS: `ozonbot0.conf`

Upload:
```bash
scp NEW.conf root@72.56.240.22:/etc/amnezia/amneziawg/ozon-bot.conf
ssh root@72.56.240.22 'chmod 600 /etc/amnezia/amneziawg/ozon-bot.conf; systemctl restart ozon-vpn ozon-chrome'
```

## One-time seller login (required)
Mac profile cookies do not transfer. Login once via VNC:

```bash
ssh -L 5901:127.0.0.1:5901 root@72.56.240.22
```

Then open VNC client to `127.0.0.1:5901`, password: `ozon-login`.
Sign in to seller.ozon.ru in the Chrome window, open Reviews, keep session.

Force test tick:
```bash
ssh root@72.56.240.22 'OZON_BOT_FORCE=1 /root/cx-dashboard/scripts/ozon_bot_tick_vps.sh; tail -50 /var/log/ozon-bot/tick.log'
```

## Logs
- `/var/log/ozon-bot/tick.log`
- `/var/log/ozon-bot/chrome.log`
