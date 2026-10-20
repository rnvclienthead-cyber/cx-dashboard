"""
Одноразовый вход в Ozon Seller под сотрудником и сохранение сессии.

Локально (Mac):
  cd cx-dashboard
  ./venv/bin/python ozon_setup_session.py
  # Когда придёт код на почту:
  echo '123456' > ozon_2fa_code.txt

На сервере:
  PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python3 \\
    /root/cx-dashboard/ozon_setup_session.py
  echo '123456' > /root/cx-dashboard/ozon_2fa_code.txt
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
COMPANY_HINT = (os.getenv("OZON_BOT_COMPANY") or "Видовито").strip()
PROXY = (os.getenv("OZON_BOT_PROXY") or "").strip()  # http://user:pass@host:port
SESSION_FILE = str(ROOT / "ozon_session.json")
CODE_FILE = str(ROOT / "ozon_2fa_code.txt")
PROFILE_DIR = str(ROOT / "ozon_browser_profile")
# На Mac показываем окно браузера; на сервере (без DISPLAY) — через Xvfb тоже False ок
HEADED = os.getenv("OZON_BOT_HEADED", "1").strip() not in ("0", "false", "False")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _wait_code(timeout_sec: int = 300) -> str:
    if os.path.exists(CODE_FILE):
        os.remove(CODE_FILE)
    print(f">>> Ждём код в файле: {CODE_FILE}")
    print(">>> Пример: echo '123456' > /root/cx-dashboard/ozon_2fa_code.txt")
    waited = 0
    while waited < timeout_sec:
        if os.path.exists(CODE_FILE):
            code = Path(CODE_FILE).read_text(encoding="utf-8").strip()
            if code:
                print(f"[OK] Код получен: {code}")
                return code
        time.sleep(3)
        waited += 3
        if waited % 30 == 0:
            print(f"... ждём код ещё ({waited}/{timeout_sec}с)")
    raise TimeoutError("Код не получен вовремя")


def _click_first(page, selectors: list[str], timeout: int = 8000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


def _fill_first(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.fill(value)
                return True
        except Exception:
            continue
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
    except Exception:
        stealth = None

    print(f"[1] Email: {EMAIL}")
    print(f"[1] Сессия → {SESSION_FILE}")
    print(f"[1] Proxy: {PROXY or 'нет'}")
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        launch_kwargs = dict(
            headless=not HEADED,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1440,900",
            ],
            user_agent=UA,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
            ignore_default_args=["--enable-automation"],
        )
        # На Mac/Linux с установленным Chrome — channel=chrome; иначе Playwright Chromium
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(chrome_bin) or os.path.exists("/usr/bin/google-chrome-stable"):
            launch_kwargs["channel"] = "chrome"
        if PROXY:
            launch_kwargs["proxy"] = {"server": PROXY}
        # Постоянный профиль Chrome (куки живут между запусками)
        context = p.chromium.launch_persistent_context(PROFILE_DIR, **launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        if stealth:
            stealth.apply_stealth_sync(page)
        else:
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

        print("[2] Открываем seller.ozon.ru ...")
        page.goto("https://seller.ozon.ru/", wait_until="domcontentloaded", timeout=90000)
        time.sleep(5)
        title = page.title()
        url = page.url
        print(f"    title={title!r} url={url}")

        body_snip = (page.inner_text("body") or "")[:400].replace("\n", " ")
        blocked = (
            "Antibot" in title
            or "challenge" in url.lower()
            or "нет соединения" in body_snip.lower()
            or "fab_chlg" in body_snip.lower()
        )
        if blocked:
            print("[ERR] Антибот / блок IP на входе. Сохраняю screenshot.")
            page.screenshot(path=str(ROOT / "ozon_setup_antibot.png"), full_page=True)
            context.close()
            return 2

        # Кнопка «Войти» на лендинге
        _click_first(
            page,
            [
                "button:has-text('Войти')",
                "a:has-text('Войти')",
                "[data-testid*='login']",
                "text=Войти",
            ],
        )
        time.sleep(3)

        # Переход на «Войти по почте»
        if not _click_first(
            page,
            [
                "text=Войти по почте",
                "a:has-text('Войти по почте')",
                "button:has-text('Войти по почте')",
            ],
        ):
            print("[!] Ссылка «Войти по почте» не найдена сразу — возможно уже на форме")
        time.sleep(2)

        print("[3] Вводим почту...")
        if not _fill_first(
            page,
            [
                "input[type='email']",
                "input[name='email']",
                "input[autocomplete='username']",
                "input[placeholder*='почт' i]",
                "input[type='text']",
            ],
            EMAIL,
        ):
            page.screenshot(path=str(ROOT / "ozon_setup_no_email.png"), full_page=True)
            print("[ERR] Поле почты не найдено")
            context.close()
            return 3

        _click_first(page, ["button:has-text('Войти')", "button[type='submit']"])
        time.sleep(4)

        # Экран кода
        print("[4] Ждём экран кода...")
        body = (page.inner_text("body") or "")
        if "код" not in body.lower() and "code" not in body.lower():
            page.screenshot(path=str(ROOT / "ozon_setup_after_email.png"), full_page=True)
            print(f"[!] Не похоже на экран кода. URL={page.url}")
            print(body[:500])

        code = _wait_code(300)
        filled = _fill_first(
            page,
            [
                "input[autocomplete='one-time-code']",
                "input[type='number']",
                "input[name='code']",
                "input[inputmode='numeric']",
                "input[type='text']",
            ],
            code,
        )
        if not filled:
            page.keyboard.type(code, delay=80)
        time.sleep(1)
        page.keyboard.press("Enter")
        time.sleep(5)

        # Выбор компании
        body = page.inner_text("body") or ""
        if "Выберите компанию" in body or "компани" in body.lower():
            print(f"[5] Выбор компании (~{COMPANY_HINT})...")
            if not _click_first(
                page,
                [
                    f"text={COMPANY_HINT}",
                    f"label:has-text('{COMPANY_HINT}')",
                    f"div:has-text('{COMPANY_HINT}')",
                ],
            ):
                # клик по первой радиокнопке
                _click_first(page, ["input[type='radio']", "[role='radio']"])
            time.sleep(1)
            _click_first(page, ["button:has-text('Далее')", "button:has-text('Продолжить')"])
            time.sleep(5)

        print(f"[6] После входа URL={page.url}")
        page.goto(
            "https://seller.ozon.ru/app/reviews",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        time.sleep(6)
        print(f"[7] Reviews URL={page.url} title={page.title()!r}")

        title = page.title()
        if "Antibot" in title or "нет соединения" in (page.inner_text("body") or "").lower():
            page.screenshot(path=str(ROOT / "ozon_setup_reviews_antibot.png"), full_page=True)
            print("[ERR] Антибот на /app/reviews после логина")
            context.close()
            return 4

        context.storage_state(path=SESSION_FILE)
        page.screenshot(path=str(ROOT / "ozon_setup_ok.png"), full_page=True)
        print(f"[OK] Сессия сохранена: {SESSION_FILE}")
        context.close()
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[FATAL] {e}")
        raise
