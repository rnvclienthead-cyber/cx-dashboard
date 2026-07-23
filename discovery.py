import requests
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")

WB_API_KEY = os.getenv("WB_API_KEY", "").strip()
URL_SUPABASE = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

engine_supabase = create_engine(URL_SUPABASE)
engine_local = create_engine(URL_LOCAL_VPS)
headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

def run_discovery():
    print("🚀 СТАРТ СКРИПТА-РАЗВЕДЧИКА РЕЙТИНГОВ")
    
    # 1. Берем номенклатуры
    mapping = {}
    try:
        with engine_local.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT nm_id, supplier_article FROM wb_logistics WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL")).fetchall()
            mapping = {int(r[0]): str(r[1]).strip() for r in result}
    except Exception as e:
        print(f"Ошибка БД: {e}"); return

    url = "https://seller-analytics-api.wildberries.ru/api/analytics/v1/item-rating"
    nm_ids_list = list(mapping.keys())
    
    # Берем данные только за вчерашний день
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    all_raw_cards = []

    for i in range(0, len(nm_ids_list), 50):
        chunk = nm_ids_list[i:i + 50]
        payload = {
            "currentPeriod": {"start": target_date, "end": target_date},
            "nmIds": chunk,
            "orderBy": {"field": "feedbackCount", "mode": "desc"},
            "isNotIncludeNMsWithoutSales": False,
            "limit": 100, "offset": 0
        }
        
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                cards = res.json().get("data", {}).get("cards", [])
                all_raw_cards.extend(cards)
                print(f"✅ Скачано {len(cards)} карточек...")
        except Exception as e:
            print(f"❌ Ошибка запроса: {e}")

    if not all_raw_cards:
        print("📭 Нет данных.")
        return

    # 2. РАСПЛЮЩИВАЕМ JSON! Все вложенные ключи станут колонками (например: 'valuation.value')
    df_raw = pd.json_normalize(all_raw_cards)
    
    # Добавляем для удобства понятные колонки
    df_raw['sys_date'] = target_date
    df_raw['sys_article'] = df_raw.get('nmId', df_raw.get('nm_id')).map(mapping)

    # 3. Сохраняем в новую таблицу-черновик
    for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
        try:
            # if_exists='replace' создаст таблицу заново с нужным набором колонок
            df_raw.to_sql('wb_ratings_raw', eng, if_exists='replace', index=False)
            print(f"📥 Сырые данные ({len(df_raw)} строк, {len(df_raw.columns)} колонок) сохранены в таблицу wb_ratings_raw в {name}")
        except Exception as e:
            print(f"❌ Ошибка записи в {name}: {e}")

if __name__ == "__main__":
    run_discovery()