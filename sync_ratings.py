import os
import requests
from datetime import datetime
from sqlalchemy import create_engine, text
import time

WB_API_KEY = os.environ.get("WB_API_KEY", "").strip()
DB_URL = os.environ.get("DB_URL", "").strip()

def get_wb_cards_mapping_from_db(engine):
    """Получает связку nmId -> Артикул продавца из вашей БД (из таблицы логистики)"""
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

def get_ratings_via_feedbacks_api():
    """Считает рейтинг математически, выкачивая отзывы через официальное API (токен Вопросы и отзывы)"""
    ratings_data = {}
    
    # Скачиваем и отвеченные, и неотвеченные отзывы для полноты картины
    for is_answered in ["true", "false"]:
        skip = 0
        take = 5000 # Максимальный лимит страницы по API
        
        while True:
            url = f"https://feedbacks-api.wildberries.ru/api/v1/feedbacks?isAnswered={is_answered}&take={take}&skip={skip}"
            headers = {"Authorization": WB_API_KEY}
            
            try:
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 401:
                    print("❌ ОШИБКА 401: Токен недействителен или нет доступа к 'Вопросам и отзывам'")
                    return {}
                    
                # Защита от лимитов (если запросов слишком много)
                if response.status_code == 429:
                    print("⚠️ WB Лимит 429. Ждем 10 секунд...")
                    time.sleep(10)
                    continue
                    
                response.raise_for_status()
                data = response.json().get('data', {})
                feedbacks = data.get('feedbacks', [])
                
                if not feedbacks:
                    break # Отзывы закончились
                
                # Собираем оценки
                for fb in feedbacks:
                    # Учитываем возможные изменения структуры JSON от WB
                    nm = fb.get('productDetails', {}).get('nmId') or fb.get('nmId')
                    stars = fb.get('productValuation', 0)
                    
                    if nm and stars:
                        if nm not in ratings_data:
                            ratings_data[nm] = {'sum': 0, 'count': 0}
                        ratings_data[nm]['sum'] += stars
                        ratings_data[nm]['count'] += 1
                
                print(f"📦 Скачана страница: {len(feedbacks)} отзывов (isAnswered={is_answered}, skip={skip})...")
                
                # Если пришло меньше лимита, значит это последняя страница
                if len(feedbacks) < take:
                    break 
                    
                skip += take
                time.sleep(1) # Бережем лимиты WB
                
            except Exception as e:
                print(f"⚠️ Ошибка получения отзывов на skip={skip}: {e}")
                break
                
    # Превращаем сумму звезд в чистый средний рейтинг
    final_ratings = {}
    for nm, stats in ratings_data.items():
        if stats['count'] > 0:
            final_ratings[nm] = {
                'average_rating': round(stats['sum'] / stats['count'], 2),
                'review_count': stats['count']
            }
            
    print(f"✅ Успешно высчитан рейтинг для {len(final_ratings)} товаров на основе реальных отзывов")
    return final_ratings

def sync_ratings_to_supabase():
    if not WB_API_KEY or not DB_URL:
        print("🚨 КРИТИЧЕСКАЯ ОШИБКА: Не найдены переменные WB_API_KEY или DB_URL")
        return

    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    mapping = get_wb_cards_mapping_from_db(engine)
    if not mapping:
        print("⚠️ Нет маппинга. Синхронизация остановлена.")
        return

    # Запускаем официальный сбор
    ratings_data = get_ratings_via_feedbacks_api()
    
    if not ratings_data:
        print("⚠️ Нет данных рейтингов. Синхронизация остановлена.")
        return

    # Обновляем базу данных (UPSERT)
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
