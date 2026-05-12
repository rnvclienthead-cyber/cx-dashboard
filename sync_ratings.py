import os
import requests
from datetime import datetime
from sqlalchemy import create_engine, text
import time

DB_URL = os.environ.get("DB_URL", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip() 

def get_wb_cards_mapping_from_db(engine):
    mapping = {}
    try:
        query = text("""
            SELECT DISTINCT nm_id, supplier_article 
            FROM wb_logistics 
            WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL
        """)
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
            for row in result:
                mapping[row[0]] = str(row[1]).strip()
        print(f"✅ Успешно собран маппинг из БД: {len(mapping)} товаров")
        return mapping
    except Exception as e:
        print(f"❌ Ошибка получения маппинга из БД: {e}")
        return {}

def get_public_wb_ratings_with_proxy(nm_ids):
    ratings_data = {}
    
    proxies = {}
    if PROXY_URL:
        proxies = {
            "http": PROXY_URL,
            "https": PROXY_URL
        }
        print("🌐 Используется прокси-сервер.")
    else:
        print("⚠️ ПРОКСИ НЕ ЗАДАН! Запрос пойдет с IP-адреса GitHub, возможна ошибка 404.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/"
    }

    chunk_size = 50
    for i in range(0, len(nm_ids), chunk_size):
        chunk = nm_ids[i:i + chunk_size]
        nm_string = ";".join(map(str, chunk))
        
        # ВОТ ЗДЕСЬ ИСПРАВЛЕНИЕ: Вернули рабочий адрес v1
        endpoints = [
            f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={nm_string}",
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={nm_string}"
        ]
        
        success = False
        last_error = None
        
        for url in endpoints:
            try:
                response = requests.get(url, headers=headers, proxies=proxies, timeout=20)
                response.raise_for_status()
                
                data = response.json().get('data', {}).get('products', [])
                for item in data:
                    nm = item.get('id')
                    ratings_data[nm] = {
                        'average_rating': item.get('reviewRating', 0.0),
                        'review_count': item.get('feedbacks', 0)
                    }
                success = True
                break
                
            except Exception as e:
                last_error = e
                continue
                
        if not success:
             print(f"⚠️ Ошибка для пачки. Последняя ошибка: {last_error}")
             
        time.sleep(2) 
            
    print(f"✅ Успешно получены публичные рейтинги для {len(ratings_data)} товаров.")
    return ratings_data

def sync_ratings_to_supabase():
    if not DB_URL:
        print("🚨 КРИТИЧЕСКАЯ ОШИБКА: Не найдена переменная DB_URL")
        return

    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    mapping = get_wb_cards_mapping_from_db(engine)
    if not mapping:
        print("⚠️ Нет маппинга для синхронизации.")
        return

    nm_ids_list = list(mapping.keys())
    ratings_data = get_public_wb_ratings_with_proxy(nm_ids_list)
    
    if not ratings_data:
        print("⚠️ Нет данных рейтингов для сохранения.")
        return

    upsert_query = text("""
        INSERT INTO wb_ratings (date, supplier_article, nm_id, average_rating, review_count)
        VALUES (:date, :supplier_article, :nm_id, :average_rating, :review_count)
        ON CONFLICT (date, supplier_article) 
        DO UPDATE SET 
            average_rating = EXCLUDED.average_rating,
            review_count = EXCLUDED.review_count,
            created_at = CURRENT_TIMESTAMP;
    """)

    success_count = 0
    with engine.begin() as conn:
        for nm_id, stats in ratings_data.items():
            supplier_article = mapping.get(nm_id)
            if not supplier_article:
                continue
                
            try:
                conn.execute(upsert_query, {
                    "date": current_date,
                    "supplier_article": supplier_article,
                    "nm_id": nm_id,
                    "average_rating": float(stats['average_rating']),
                    "review_count": int(stats['review_count'])
                })
                success_count += 1
            except Exception as e:
                print(f"⚠️ Ошибка записи в БД для {supplier_article}: {e}")

    print(f"🚀 Синхронизация завершена! Записано/обновлено строк: {success_count} за {current_date}")

if __name__ == "__main__":
    sync_ratings_to_supabase()
