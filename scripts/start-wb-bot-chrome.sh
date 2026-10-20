#!/bin/bash
# Отдельный Chrome для бота WB (не смешивать с Ozon :9222).
set -e
DIR="$HOME/wb-bot-chrome"
mkdir -p "$DIR"
rm -f "$DIR/SingletonLock" "$DIR/SingletonSocket" "$DIR/SingletonCookie"
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9224 \
  --user-data-dir="$DIR" \
  --profile-directory=Default \
  --no-first-run \
  --no-default-browser-check \
  "https://seller.wildberries.ru/suppliers-returns"
