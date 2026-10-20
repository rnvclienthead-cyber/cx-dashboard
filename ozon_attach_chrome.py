"""
Вариант без антибота Playwright: обычный Chrome + подключение по CDP.

Шаг 1 — в отдельном Terminal на Mac запустите Chrome:
  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
    --remote-debugging-port=9222 \\
    --user-data-dir=\"$HOME/ozon-bot-chrome\"

Шаг 2 — в этом окне войдите в seller.ozon.ru как bot.clientmng@...
         и откройте «Отзывы».

Шаг 3 — здесь:
  ./venv/bin/python ozon_attach_chrome.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SESSION_FILE = str(ROOT / "ozon_session.json")
CDP = "http://127.0.0.1:9222"


def main() -> int:
    from playwright.sync_api import sync_playwright

    print(f"Подключаюсь к Chrome на {CDP} ...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            print("[ERR] Не вижу Chrome с отладкой.")
            print("Сначала в Terminal:")
            print(
                '  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\\n'
                '    --remote-debugging-port=9222 \\\n'
                '    --user-data-dir="$HOME/ozon-bot-chrome"'
            )
            print(f"Детали: {e}")
            return 1

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "ozon" in (pg.url or "").lower() or "seller" in (pg.url or "").lower():
                    page = pg
                    context = ctx
                    break
            if page:
                break
        if page is None:
            page = context.pages[0] if context.pages else context.new_page()

        print(f"Текущая вкладка: {page.url}")
        print(f"Title: {page.title()!r}")

        body = (page.inner_text("body") or "")[:300]
        if "нет соединения" in body.lower():
            print("[ERR] На вкладке антибот. Обновите страницу в Chrome или зайдите заново.")
            return 2

        if "seller.ozon.ru/app" not in page.url:
            print("Открываю /app/reviews ...")
            page.goto(
                "https://seller.ozon.ru/app/reviews",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            time.sleep(4)

        print(f"После перехода: {page.url} / {page.title()!r}")
        if "нет соединения" in (page.inner_text("body") or "").lower():
            print("[ERR] Антибот на отзывах")
            return 3

        context.storage_state(path=SESSION_FILE)
        page.screenshot(path=str(ROOT / "ozon_attach_ok.png"), full_page=True)
        print(f"[OK] Сессия сохранена: {SESSION_FILE}")
        print("Chrome можно оставить открытым — для синка отзывов тоже удобно через CDP.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
