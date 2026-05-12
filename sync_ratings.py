import os
# ИМПОРТИРУЕМ request ИЗ НОВОЙ БИБЛИОТЕКИ
from curl_cffi import requests 

PROXY_URL = os.environ.get("PROXY_URL", "").strip() 
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}

# Ваша ссылка __internal
test_url = "https://www.wildberries.ru/__internal/u-card/cards/v4/detail?appType=1&curr=rub&dest=123589415&spp=30&hide_vflags=4294967296&ab_testing=false&lang=ru&nm=425421147"

headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/catalog/425421147/detail.aspx"
}

print("🔍 Запуск диагностики с подменой отпечатка Chrome (curl_cffi)...")
print(f"🌐 Прокси: {'Подключен' if PROXY_URL else 'ОТСУТСТВУЕТ!'}")

try:
    # Параметр impersonate="chrome120" делает нас 100% невидимыми для защиты
    response = requests.get(
        test_url, 
        headers=headers, 
        proxies=proxies, 
        timeout=15,
        impersonate="chrome120"
    )
    print(f"Код ответа: {response.status_code}")
    
    if response.status_code == 200:
        print("✅ БИНГО! Антибот успешно пройден. Ответ сервера:")
        print(response.text[:700])
    else:
        print("❌ Ошибка сервера. Тело ответа:")
        print(response.text[:500])
        
except Exception as e:
    print(f"💥 Ошибка соединения: {e}")
