import os
import requests

# Берем ваш прокси
PROXY_URL = os.environ.get("PROXY_URL", "").strip() 
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}

# Тестируем всего ОДИН артикул
test_nm = "425397773"

# Актуальный набор заголовков, чтобы максимально походить на браузер
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://www.wildberries.ru",
    "Referer": f"https://www.wildberries.ru/catalog/{test_nm}/detail.aspx"
}

print(f"🔍 Запуск диагностики WB API...")
print(f"🌐 Прокси: {'Подключен' if PROXY_URL else 'ОТСУТСТВУЕТ!'}")

# Пробуем 3 разные ссылки, которые WB использует сейчас
urls_to_test = [
    f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={test_nm}",
    f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={test_nm}",
    # Новая структура API WB (распределенная по корзинам):
    f"https://basket-01.wbbasket.ru/vol{test_nm[:4]}/part{test_nm[:6]}/{test_nm}/info/ru/card.json" 
]

for url in urls_to_test:
    print(f"\n--- Тестируем URL: {url} ---")
    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        print(f"Код ответа: {response.status_code}")
        
        # Печатаем первые 300 символов ответа сервера, чтобы увидеть, что он нам пишет
        print(f"Тело ответа: {response.text[:300]}") 
        
    except Exception as e:
        print(f"Ошибка запроса: {e}")

print("\n🏁 Диагностика завершена.")
