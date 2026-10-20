#!/usr/bin/env bash
# Проверка перед выкладкой. Запускать с Mac из корня проекта: ./scripts/preflight.sh
#
# Ловит ровно те поломки, что были в сентябре:
#   1. тесты бэкенда   — логика прав, доотправок, чатов, автоответов;
#   2. тесты фронта    — связь с сервером, Telegram-мини-апп, очереди;
#   3. сборка фронта   — синтаксис Vue/JS, иначе белый экран на сайте;
#   4. импорт backend  — забытый файл → сервис не поднимется (инцидент 10.09).
#
# Любой красный шаг = НЕ выкладывать. Скрипт останавливается на первом сбое.
set -u
cd "$(dirname "$0")/.." || exit 2

PY=./venv/bin/python
fail() { echo ""; echo "❌ ПРОВАЛ: $1 — не выкладывать."; exit 1; }

echo "▶ 1/4 тесты бэкенда"
$PY -m pytest backend/tests -q -p no:cacheprovider || fail "тесты бэкенда"

echo "▶ 2/4 тесты фронта"
( cd frontend && node --test 'tests/*.test.js' >/tmp/preflight_front.log 2>&1 ) \
  && grep -qE "fail 0$" /tmp/preflight_front.log \
  || { tail -15 /tmp/preflight_front.log; fail "тесты фронта"; }
echo "  $(grep -E 'tests [0-9]+$|pass [0-9]+$|fail [0-9]+$' /tmp/preflight_front.log | tr -s ' \n' ' ')"

echo "▶ 3/4 сборка фронта"
( cd frontend && npm run build >/tmp/preflight_build.log 2>&1 ) \
  || { tail -20 /tmp/preflight_build.log; fail "сборка фронта"; }

echo "▶ 4/4 импорт backend (поднимется ли сервис)"
PYTHONPATH=. $PY -c "from backend.main import app" || fail "импорт backend.main"

echo ""
echo "✅ Всё зелёное. Можно выкладывать по docs/04_ДЕПЛОЙ.md."
