"""
Одноразовый скрипт для входа в ЯМ с 2FA и сохранения сессии.
Запускать: python3 /tmp/ym_setup_session.py
"""
import os, sys, re, time
sys.path.insert(0, '/root/cx-dashboard')
from dotenv import load_dotenv
load_dotenv('/root/cx-dashboard/.env')

login    = os.getenv("YM_LOGIN", "").strip()
password = os.getenv("YM_PASSWORD", "").strip()
YM_BUSINESS_ID  = os.getenv("YM_BUSINESS_ID", "").strip()
YM_CAMPAIGN_ID  = os.getenv("YM_CAMPAIGN_ID", "").strip()

SESSION_FILE = "/root/cx-dashboard/ym_session.json"
CODE_FILE    = "/root/cx-dashboard/ym_2fa_code.txt"

bid  = int(YM_BUSINESS_ID)
path = f"/business/{bid}/reviews-new?campaignId={YM_CAMPAIGN_ID}&activeTab=products&ratingDrawer=opened"
ua   = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# Удалим старый код если есть
if os.path.exists(CODE_FILE):
    os.remove(CODE_FILE)

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"])
    context = browser.new_context(user_agent=ua, locale="ru-RU", timezone_id="Europe/Moscow")
    page = context.new_page()

    print("[1] Открываем страницу логина...")
    page.goto("https://passport.yandex.ru/auth", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    print("[2] Вводим логин...")
    page.wait_for_selector("input[type='text'], input[name='login']", timeout=15000)
    page.fill("input[type='text'], input[name='login']", login)
    btn = page.query_selector("button[type='submit']") or page.query_selector("button:has-text('Войти')") or page.query_selector("button:has-text('Продолжить')")
    if btn: btn.click()
    else: page.keyboard.press("Enter")
    time.sleep(3)

    print("[3] Вводим пароль...")
    page.wait_for_selector("input[type='password']", timeout=15000)
    page.fill("input[type='password']", password)
    page.keyboard.press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    time.sleep(4)

    # Проверяем — нужен ли 2FA
    current_url = page.url
    print(f"[4] Текущий URL: {current_url}")

    if "passport.yandex" in current_url:
        body = page.inner_text("body")
        print(f"[!] Яндекс требует подтверждение. Текст страницы:\n{body[:500]}")
        print(f"\n>>> Напишите код из SMS/push-уведомления в файл: {CODE_FILE}")
        print(">>> Ожидаем код (до 3 минут)...")

        # Ждём файл с кодом
        waited = 0
        code = None
        while waited < 180:
            if os.path.exists(CODE_FILE):
                with open(CODE_FILE) as f:
                    code = f.read().strip()
                if code:
                    print(f"[5] Получили код: {code}")
                    break
            time.sleep(3)
            waited += 3

        if not code:
            print("TIMEOUT: код не получен")
            browser.close()
            sys.exit(1)

        # Вводим код
        inp = page.query_selector("input[type='number'], input[name='code'], input[autocomplete='one-time-code']")
        if inp:
            inp.fill(code)
            inp.press("Enter")
        else:
            # Пробуем ввести по одной цифре
            page.keyboard.type(code)
            page.keyboard.press("Enter")

        page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(4)
        print(f"[6] После кода URL: {page.url}")

    # Идём в кабинет ЯМ
    print("[7] Переходим в кабинет ЯМ...")
    page.goto(f"https://partner.market.yandex.ru{path}", wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)

    content = page.content()
    sk_m = re.search(r'"sk":"(u[a-f0-9]+)"', content)
    if not sk_m:
        snippet = re.sub(r'<[^>]+>', ' ', content)[:300]
        print(f"ОШИБКА: sk не найден. Страница: {snippet}")
        browser.close()
        sys.exit(1)

    # Сохраняем сессию
    context.storage_state(path=SESSION_FILE)
    print(f"[OK] Сессия сохранена в {SESSION_FILE}")
    print(f"[OK] sk={sk_m.group(1)[:20]}...")
    browser.close()

