import os
import requests
from datetime import datetime
from sqlalchemy import create_engine, text

# Загружаем доступы из переменных окружения GitHub Actions
WB_API_KEY = os.environ.get("WB_API_KEY")
DB_URL = os.environ.get("DB_URL")

def get_wb_cards_mapping():
    """Получает связку nmId -> Артикул продавца (vendorCode)"""
    url = "https://content-api.wildberries.ru/content/v2/get/cards/list"
    headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}
    payload = {
        "settings": {
            "cursor": {"limit": 1000},
            "filter": {"withPhoto": -1}
        }
    }
    
    mapping = {}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        cards = response.json().get('cards', [])
        for card in cards:
            nm_id = card.get('nmID')
            vendor_code = card.get('vendorCode')
            if nm_id and vendor_code:
                mapping[nm_id] = vendor_code
        print(f"✅ Успешно получено карточек для маппинга: {len(mapping)}")
        return mapping
    except Exception as e:
        print(f"❌ Ошибка получения карточек: {e}")
        return {}

def get_wb_ratings():
    """Получает текущий рейтинг и количество отзывов по всем nmId"""
    url = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks/products/rating"
    headers = {"Authorization": WB_API_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json().get('data', [])
        print(f"✅ Успешно получены рейтинги для {len(data)} товаров")
        return data
    except Exception as e:
        print(f"❌ Ошибка получения рейтингов: {e}")
        return []

def sync_ratings_to_supabase():
    if not WB_API_KEY or not DB_URL:
        print("🚨 КРИТИЧЕСКАЯ ОШИБКА: Не найдены WB_API_KEY или DB_URL")
        return

    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    mapping = get_wb_cards_mapping()
    ratings_data = get_wb_ratings()
    
    if not mapping or not ratings_data:
        print("⚠️ Нет данных для синхронизации. Завершение работы.")
        return

    # Подготавливаем запрос с UPSERT (обновление при конфликте дат и артикулов)
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
        for item in ratings_data:
            nm_id = item.get('nmId')
            # Забираем из API или маппинга
            valuation = item.get('valuation')
            feedbacks_count = item.get('feedbacksCount')
            
            supplier_article = mapping.get(nm_id)
            
            if not supplier_article:
                continue # Пропускаем, если артикул не удалось связать
                
            try:
                conn.execute(upsert_query, {
                    "date": current_date,
                    "supplier_article": str(supplier_article).strip(),
                    "nm_id": nm_id,
                    "average_rating": float(valuation) if valuation else 0.0,
                    "review_count": int(feedbacks_count) if feedbacks_count else 0
                })
                success_count += 1
            except Exception as e:
                print(f"⚠️ Ошибка записи для {supplier_article}: {e}")

    print(f"🚀 Синхронизация завершена! Записано/обновлено строк: {success_count} за {current_date}")

if __name__ == "__main__":
    sync_ratings_to_supabase()
