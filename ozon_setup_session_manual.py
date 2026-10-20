"""
Локальный вход в Ozon Seller: открываем Chrome, вы логинитесь руками, сессия сохраняется.

  cd cx-dashboard
  ./venv/bin/python ozon_setup_session_manual.py

1) Откроется окно Chrome
2) Войдите как bot.clientmng@vidovito.team (код с почты — сами)
3) Дойдите до раздела «Отзывы»
4) Скрипт сам сохранит сессию, когда увидит кабинет
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

EMAIL = (os.getenv("OZON_BOT_EMAIL") or "bot.clientmng@vidovito.team").strip()
PROXY = (os.getenv("OZON_BOT_PROXY") or "").strip()
SESSION_FILE = str(ROOT / "ozon_session.json")
PROFILE_DIR = str(ROOT / "ozon_browser_profile")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _ok_url(url: str) -> bool:
    u = (url or "").lower()
    if "seller.ozon.ru/app" in u and "login" not in u and "__rr=" not in u:
        return True
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    print(f"Email сотрудника: {EMAIL}")
    print(f"Профиль: {PROFILE_DIR}")
    print(f"Сессия → {SESSION_FILE}")
    print()
    print(">>> Сейчас откроется Chrome.")
    print(">>> Войдите сами (почта + код), выберите компанию Видовито.")
    print(">>> Откройте раздел Отзывы. Жду до 10 минут...")
    print()

    with sync_playwright() as p:
        kwargs = dict(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--window-size=1440,900",
            ],
            user_agent=UA,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
            ignore_default_args=["--enable-automation"],
        )
        if PROXY:
            kwargs["proxy"] = {"server": PROXY}

        context = p.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page.goto("https://seller.ozon.ru/", wait_until="domcontentloaded", timeout=90000)
        time.sleep(2)

        deadline = time.time() + 600
        last = ""
        while time.time() < deadline:
            url = page.url
            title = page.title()
            if url != last:
                print(f"  url={url}  title={title!r}")
                last = url
            body = ""
            try:
                body = (page.inner_text("body") or "")[:200]
            except Exception:
                pass
            if "нет соединения" in body.lower() or "Antibot" in title:
                print("[!] Антибот. Обновите страницу в окне Chrome или зайдите позже.")
            if _ok_url(url):
                # чуть подождём загрузки кабинета
                time.sleep(3)
                if "reviews" not in url.lower():
                    try:
                        page.goto(
                            "https://seller.ozon.ru/app/reviews",
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        time.sleep(4)
                    except Exception as e:
                        print(f"[!] Не удалось открыть отзывы: {e}")
                context.storage_state(path=SESSION_FILE)
                page.screenshot(path=str(ROOT / "ozon_setup_ok.png"), full_page=True)
                print(f"[OK] Сессия сохранена: {SESSION_FILE}")
                print("[OK] Можно закрывать окно — или оставьте, скрипт закроет сам через 3с")
                time.sleep(3)
                context.close()
                return 0
            time.sleep(2)

        page.screenshot(path=str(ROOT / "ozon_setup_timeout.png"), full_page=True)
        print("[ERR] Таймаут 10 мин — кабинет не открылся")
        context.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
