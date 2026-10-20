#!/bin/bash
# Запуск тика бота Ozon (launchd каждые 4 часа, пока Mac включён).
set -euo pipefail
ROOT="/Users/user/Documents/ПРОЕКТЫ/local/cx-dashboard"
LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR"
cd "$ROOT"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH"
exec "$ROOT/venv/bin/python" -u "$ROOT/ozon_bot_tick.py" >> "$LOG_DIR/ozon-bot-tick.log" 2>&1
