import requests
import time
import sys
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import os
import time

# --- НАСТРОЙКИ ---
WB_API_KEY = os.getenv("WB_API_KEY", "").strip()
DB_URL = os.getenv("DB_URL", "").strip()

engine = create_engine(DB_URL)
headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

# =========================================
# УТИЛИТЫ
# =========================================
def safe_str(val):
    if val is None or str(val).lower() in ['nan', 'none', 'null', '']: return None
    return str(val).strip()

# =========================================
# 1. СИНХРОНИЗАЦИЯ ПРЕТЕНЗИЙ (ИСПРАВЛЕННАЯ С ЛИМИТАМИ)
# =========================================
def fetch_wb_claims():
    print("⏳ Запрашиваем ВСЕ претензии с WB...")
    claims_url = "https://returns-api.wildberries.ru/api/v1/claims"
    all_claims = []
    
    for archive in ["false", "true"]:
        params = {"limit": 100, "offset": 0, "is_archive": archive}
        while True:
            try:
                response = requests.get(claims_url, headers=headers, params=params, timeout=30)
                
                # СПАСИТЕЛЬНЫЙ БЛОК: Обработка лимитов WB
                if response.status_code == 429:
                    print(f"⚠️ WB просит подождать (Лимит 429). Спим 30 секунд...")
                    time.sleep(30)
                    continue
                    
                res = response.json()
                batch = res.get("claims", [])
                if not batch: break
                
                for item in batch:
                    srid = safe_str(item.get("srid"))
                    if not srid: continue
                    
                    raw_status = str(item.get("status", "")).strip()
                    status_map = {'1': 'На рассмотрении', '2': 'Одобрено', '3': 'Отказ'}
                    mapped = status_map.get(raw_status, raw_status)
                    
                    status_ex = item.get("status_description", "")
                    if mapped in ['Архивная', 'None', '', 'Активная']:
                        ex_lower = str(status_ex).lower()
                        if 'возврат' in ex_lower or 'одобрено' in ex_lower: mapped = 'Одобрено'
                        elif 'отклон' in ex_lower or 'отказ' in ex_lower: mapped = 'Отказ'
                        elif 'рассмотр' in ex_lower: mapped = 'На рассмотрении'
                    
                    all_claims.append({
                        "srid": srid,
                        "claim_id": safe_str(item.get("id")),
                        "created_dt": item.get("dt"),
                        "supplier_article": safe_str(item.get("supplier_article") or item.get("article") or item.get("sa_name")),
                        "nm_id": safe_str(item.get("nm_id")),
                        "user_comment": item.get("user_comment"),
                        "status": mapped,
                        "status_ex": status_ex,
                        "claim_type": item.get("claim_type"),
                        "is_archive": archive == "true",
                        "last_sync": datetime.now()
                    })
                
                print(f"📦 Скачано претензий: {len(all_claims)}...")
                if len(batch) < 100: break
                params["offset"] += 100
                time.sleep(2) # Базовая пауза, чтобы не злить WB
                
            except Exception as e:
                print(f"⚠️ Системная ошибка претензий: {e}. Пробуем еще раз через 10 сек...")
                time.sleep(10)
                
    df = pd.DataFrame(all_claims)
    if not df.empty:
        df = df.drop_duplicates(subset=['srid'], keep='last')
    return df

def sync_claims_to_db(df):
    """Сохраняет скачанные претензии в базу данных"""
    if df is None or df.empty:
        print("⚠️ Претензий для сохранения не найдено.")
        return
        
    df.to_sql('temp_wb_claims', engine, if_exists='replace', index=False)
    upsert_sql = """
    INSERT INTO wb_claims (srid, claim_id, created_dt, supplier_article, nm_id, user_comment, status, status_ex, claim_type, is_archive, last_sync)
    SELECT DISTINCT ON (srid) srid, claim_id, CAST(created_dt AS TIMESTAMP), supplier_article, nm_id, user_comment, status, status_ex, claim_type, CAST(is_archive AS BOOLEAN), CAST(last_sync AS TIMESTAMP)
    FROM temp_wb_claims ORDER BY srid
    ON CONFLICT (srid) DO UPDATE SET 
        status = EXCLUDED.status,
        status_ex = EXCLUDED.status_ex,
        is_archive = EXCLUDED.is_archive,
        last_sync = EXCLUDED.last_sync;
    """
    with engine.begin() as conn:
        conn.execute(text(upsert_sql))
        conn.execute(text("DROP TABLE temp_wb_claims;"))
    print(f"📥 UPSERT: Синхронизируем {len(df)} претензий...")

# =========================================
# 2. СИНХРОНИЗАЦИЯ ЛОГИСТИКИ (С ПОТОКОВЫМ СОХРАНЕНИЕМ)
# =========================================
def get_logistics_start_date(doc_type):
    query = text(f"SELECT MAX(last_change_date) FROM wb_logistics WHERE doc_type = '{doc_type}'")
    with engine.connect() as conn:
        result = conn.execute(query).scalar()
        if result:
            return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
    # Если база пустая, качаем строго с 1 октября 2025!
    return "2025-10-01T00:00:00"

def sync_chunk_to_db(df):
    """Мгновенно сохраняет пачку данных в БД"""
    if df.empty: return
    df.to_sql('temp_wb_logistics', engine, if_exists='replace', index=False)
    upsert_sql = """
    INSERT INTO wb_logistics (srid, doc_type, dt, supplier_article, nm_id, warehouse_name, category, subject, brand, is_cancel, last_change_date, income_id)
    SELECT DISTINCT ON (srid) srid, doc_type, CAST(dt AS TIMESTAMP), supplier_article, nm_id, warehouse_name, category, subject, brand, CAST(is_cancel AS BOOLEAN), CAST(last_change_date AS TIMESTAMP), income_id
    FROM temp_wb_logistics ORDER BY srid, last_change_date DESC
    ON CONFLICT (srid) DO UPDATE SET 
        is_cancel = EXCLUDED.is_cancel, 
        nm_id = COALESCE(EXCLUDED.nm_id, wb_logistics.nm_id),
        last_change_date = EXCLUDED.last_change_date,
        income_id = COALESCE(EXCLUDED.income_id, wb_logistics.income_id);
    """
    with engine.begin() as conn:
        conn.execute(text(upsert_sql))
        conn.execute(text("DROP TABLE temp_wb_logistics;"))

def fetch_and_save_logistics(url, doc_type):
    date_from = get_logistics_start_date(doc_type)
    print(f"⏳ Качаем логистику {doc_type} начиная с {date_from}...")
    
    current_from = date_from
    total_saved = 0

    error_counter = 0
    max_errors = 5
    
    while True:
        try:
        # Обязательно timeout=30
        response = requests.get(claims_url, headers=headers, params=params, timeout=30)
            
            # СПАСИТЕЛЬНЫЙ БЛОК: Обработка лимитов WB
            if response.status_code == 429:
                print(f"⚠️ WB просит подождать (Лимит 429). Спим 30 секунд...")
                time.sleep(30)
                continue
                
            response.raise_for_status() # Проверка на ошибки 401, 500 и т.д.
            res = response.json()
            batch = res.get("claims", [])
            
            if not batch: 
                break # Данные закончились
            
            for item in batch:
                srid = str(item.get("srid", "")).strip()
                if not srid or srid == "None": continue
                
                raw_status = str(item.get("status", "")).strip()
                status_map = {'1': 'На рассмотрении', '2': 'Одобрено', '3': 'Отказ'}
                mapped = status_map.get(raw_status, raw_status)
                
                status_ex = item.get("status_description", "")
                if mapped in ['Архивная', 'None', '', 'Активная']:
                    ex_lower = str(status_ex).lower()
                    if 'возврат' in ex_lower or 'одобрено' in ex_lower: mapped = 'Одобрено'
                    elif 'отклон' in ex_lower or 'отказ' in ex_lower: mapped = 'Отказ'
                    elif 'рассмотр' in ex_lower: mapped = 'На рассмотрении'
                
                all_claims.append({
                    "srid": srid,
                    "claim_id": str(item.get("id", "")).strip(),
                    "created_dt": item.get("dt"),
                    "supplier_article": str(item.get("supplier_article") or item.get("article") or item.get("sa_name") or ""),
                    "nm_id": str(item.get("nm_id", "")),
                    "user_comment": item.get("user_comment", ""),
                    "status": mapped,
                    "status_ex": status_ex,
                    "claim_type": item.get("claim_type"),
                    "is_archive": archive == "true",
                    "last_sync": datetime.now()
                })
            
            print(f"📦 Скачано претензий: {len(all_claims)}...")
            
            # Если WB отдал меньше 100 строк, значит это последняя страница
            if len(batch) < 100: 
                break
                
            params["offset"] += 100
            error_counter = 0 # Сбрасываем счетчик ошибок при успешном ответе
            time.sleep(2) # Базовая пауза, чтобы не злить WB
            
        except Exception as e:
            error_counter += 1
            print(f"⚠️ Системная ошибка претензий ({error_counter}/{max_errors}): {e}")
            if error_counter >= max_errors:
                print("🚨 Слишком много ошибок подряд. Принудительно останавливаем скачивание.")
                break # Выходим из цикла, чтобы скрипт не висел 2 часа
            time.sleep(10)

# =========================================
# 3. Orders
# =========================================

def get_orders_start_date():
    query = text("SELECT MAX(last_change_date) FROM wb_orders")
    with engine.connect() as conn:
        result = conn.execute(query).scalar()
        if result:
            # Если база уже не пустая, берем последнюю дату минус 5 дней (для защиты от "потеряшек")
            return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
            
    # Если база пустая (первый запуск), стартуем строго с 1 апреля 2026 года
    return "2026-04-01T00:00:00"

def sync_orders_chunk_to_db(df):
    if df.empty: return
    df.to_sql('temp_wb_orders', engine, if_exists='replace', index=False)
    upsert_sql = """
    INSERT INTO wb_orders (srid, dt, supplier_article, cancel_dt, last_change_date)
    SELECT DISTINCT ON (srid) srid, CAST(dt AS TIMESTAMP), supplier_article, 
           CAST(cancel_dt AS TIMESTAMP), CAST(last_change_date AS TIMESTAMP)
    FROM temp_wb_orders ORDER BY srid, last_change_date DESC
    ON CONFLICT (srid) DO UPDATE SET 
        cancel_dt = EXCLUDED.cancel_dt,
        last_change_date = EXCLUDED.last_change_date;
    """
    with engine.begin() as conn:
        conn.execute(text(upsert_sql))
        conn.execute(text("DROP TABLE temp_wb_orders;"))

def fetch_and_save_orders(url):
    date_from = get_orders_start_date()
    print(f"⏳ Качаем ЗАКАЗЫ начиная с {date_from}...")
    current_from = date_from
    
    # Счетчик для контроля "всплеска" (до 10 запросов)
    request_count = 0 
    
    while True:
        params = {"dateFrom": current_from, "flag": 0}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            request_count += 1
            
            # Если WB всё равно ругается, включаем принудительную долгую паузу
            if response.status_code == 429:
                print("⚠️ WB Лимит 429: Превышен всплеск. Включаем режим '1 запрос в минуту'. Ждем 65 сек...")
                time.sleep(65)
                continue
                
            res = response.json()
            if not res or not isinstance(res, list): 
                print("✅ Все заказы успешно загружены!")
                break
            
            chunk_data = []
            for item in res:
                srid = safe_str(item.get("srid"))
                if not srid: continue
                
                cancel_dt = item.get("cancelDate")
                is_cancel = item.get("isCancel", False)
                # Защита от кривых дат отмены WB вида "0001-01-01"
                if not is_cancel or str(cancel_dt).startswith("0001"):
                    cancel_dt = None

                chunk_data.append({
                    "srid": srid, 
                    "dt": item.get("date"),
                    "supplier_article": safe_str(item.get("supplierArticle") or item.get("saName")),
                    "cancel_dt": cancel_dt,
                    "last_change_date": item.get("lastChangeDate")
                })
            
            df_chunk = pd.DataFrame(chunk_data).drop_duplicates(subset=['srid'], keep='last')
            sync_orders_chunk_to_db(df_chunk)
            print(f"📥 Сохранена пачка заказов ({len(df_chunk)} строк). Текущая дата: {current_from}")
            
            last_change = res[-1].get("lastChangeDate")
            
            # Если WB отдал всё и новых дат нет — выходим
            if not last_change or last_change == current_from: 
                print("✅ Все заказы успешно загружены!")
                break
                
            current_from = last_change
            
            # УМНАЯ ОБРАБОТКА ЛИМИТОВ ИЗ ВАШЕГО СКРИНШОТА
            # Первые 8-9 запросов (всплеск) делаем быстро. Затем переходим на 1 запрос в минуту.
            if request_count >= 9:
                print("⏳ Ожидание 62 секунды для соблюдения жесткого лимита Wildberries (1 мин)...")
                time.sleep(62)
            else:
                print("⏳ Ожидание 3 секунды (используем лимит всплеска)...")
                time.sleep(3)
            
        except Exception as e:
            print(f"⚠️ Системная ошибка скачивания заказов: {e}")
            print("⏳ Повторная попытка через 30 секунд...")
            time.sleep(30)

# =========================================
# ЗАПУСК
# =========================================
if __name__ == "__main__":
    print("🚀 СТАРТ ЧИСТОГО ВОРКЕРА")
    try:
        # 1. Сначала качаем и сохраняем претензии
        sync_claims_to_db(fetch_wb_claims())
        
        # 2. Затем потоково качаем продажи (Сразу сохраняются в базу!)
        sales_url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
        fetch_and_save_logistics(sales_url, "SALE")
        
        # 3. Затем потоково качаем заказы
        orders_url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
        fetch_and_save_orders(orders_url)
        
        print("🏁 СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА УСПЕШНО")
    except Exception as e:
        print(f"💥 ОШИБКА: {e}")
        sys.exit(1)
