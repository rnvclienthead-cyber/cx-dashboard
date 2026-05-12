import os
import requests

PROXY_URL = os.environ.get("PROXY_URL", "").strip() 
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}

print(f"Тестируем прокси: {PROXY_URL}")

try:
    # Пробуем зайти на Яндекс через ваш прокси
    response = requests.get("https://ya.ru", proxies=proxies, timeout=10)
    print(f"✅ Успех! Код ответа Яндекса: {response.status_code}")
except Exception as e:
    print(f"❌ Ошибка прокси: {e}")
