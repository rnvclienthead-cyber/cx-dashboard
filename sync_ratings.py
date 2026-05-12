import os
import requests
from datetime import datetime
from sqlalchemy import create_engine, text
import time

# Очистка токенов от случайных пробелов
WB_API_KEY = os.environ.get("WB_API_KEY", "").strip()
DB_URL = os.environ.get("DB_URL", "").strip()

def get_wb_cards_mapping_from_db(engine):
    """Получает связку nmId -> Артикул продавца из вашей БД (из таблицы логистики)"""
    mapping = {}
    try:
        # Ищем уникальные связки артикулов и nm_id в таблице wb_logistics
        query = text("""
            SELECT DISTINCT nm_id, supplier_article 
            FROM wb_logistics 
            WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
            for row in result:
                # row[0] это nm_id, row[1] это supplier_article
                mapping[row[0]] = str(row[1]).strip()
                
        print(f"✅ Успешно собран маппинг из БД: {len(mapping)} товаров")
        return mapping
    except Exception as e:
        print(f"❌ Ошибка получения маппинга из БД: {e}")
        return {}

def get_public_wb_ratings(nm_ids):
    """Получает рейтинги через публичное API сайта WB (Самый надежный способ в 2026 году)"""
    ratings_data = {}
    
    # Разбиваем список nm_id на пачки по 50 штук (лимит запроса)
    chunk_size = 50
    for i in range(0, len(nm_ids), chunk_size):
        chunk = nm_ids[i:i + chunk_size]
        # Соединяем nm_id через точку с запятой для ссылки
        nm_string = ";".join(map(str, chunk))
        
        # Обращаемся к публичному эндпоинту карточек WB
        url = f"https://card.wb.ru/cards/v1/detail?appType=1&dest=-1257786&nm={nm_string}"
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json().get('data', {}).get('products', [])
            
            for item in data:
                nm = item.get('id')
                # WB отдает рейтинг в reviewRating и количество в feedbacks
                ratings_data[nm] = {
                    'average_rating': item.get('reviewRating', 0.0),
                    'review_count': item.get('feedbacks', 0)
                }
            time.sleep(1) # Пауза между пачками, чтобы не спамить
        except Exception as e:
            print(f"⚠️ Ошибка получения публичного рейтинга для пачки: {e}")
            
    print(f"✅ Получены рейтинги для {len(ratings_data)} товаров из публичного API")
    return ratings_data

def sync_ratings_to_supabase():
    if not WB_API_KEY or not DB_URL:
        print("🚨 КРИТИЧЕСКАЯ ОШИБКА: Не найдены переменные WB_API_KEY или DB_URL")
        return

    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    # Шаг 1: Достаем маппинг артикулов прямо из базы (передаем engine)
    mapping = get_wb_cards_mapping_from_db(engine)
    if not mapping:
        print("⚠️ Нет маппинга. Синхронизация остановлена.")
        return

    # Шаг 2: Достаем рейтинги через публичное API
    nm_ids_list = list(mapping.keys())
    ratings_data = get_public_wb_ratings(nm_ids_list)
    
    if not ratings_data:
        print("⚠️ Нет данных рейтингов. Синхронизация остановлена.")
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
                    "supplier_article": str(supplier_article).strip(),
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
