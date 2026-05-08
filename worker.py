import requests
import sys
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import os
import time
import io
import uuid
import boto3
from PIL import Image

# --- НАСТРОЙКИ ОСНОВНЫЕ ---
WB_API_KEY = os.getenv("WB_API_KEY", "").strip()
DB_URL = os.getenv("DB_URL", "").strip()

engine = create_engine(DB_URL)
headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

# --- НАСТРОЙКИ S3 ХРАНИЛИЩА (Яндекс Cloud, Timeweb, Selectel) ---
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net").strip()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "").strip()
BUCKET_NAME = os.getenv("BUCKET_NAME", "cx-dashboard-media").strip()

# Инициализация клиента S3 (если ключи заданы)
s3_client = None
if S3_ACCESS_KEY and S3_SECRET_KEY:
    s3_client = boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name='ru-central1'
    )

# =========================================
# УТИЛИТЫ И РАБОТА С МЕДИА
# =========================================
def safe_str(val):
    if val is None or str(val).lower() in ['nan', 'none', 'null', '']: return None
    return str(val).strip()

def create_and_upload_thumbnail(wb_image_url):
    if not wb_image_url or not wb_image_url.startswith('http'): return wb_image_url
        
    # Проверка: видит ли воркер ключи вообще?
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print("    ⚠️ ПРОПУСК: Воркер не видит ключи S3_ACCESS_KEY или S3_SECRET_KEY из GitHub Secrets!")
        return wb_image_url

    try:
        response = requests.get(wb_image_url, timeout=15)
        response.raise_for_status()
        
        image = Image.open(io.BytesIO(response.content))
        if image.mode in ("RGBA", "P"): image = image.convert("RGB")
            
        # Настройки высокого качества
        image.thumbnail((1000, 1000))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", optimize=True, quality=90)
        buffer.seek(0)
        
        file_name = f"previews/{uuid.uuid4().hex}.jpg"
        s3_client.upload_fileobj(buffer, BUCKET_NAME, file_name, ExtraArgs={'ContentType': 'image/jpeg'})
        
        s3_url = f"https://{BUCKET_NAME}.storage.yandexcloud.net/{file_name}"
        # Возвращаем связку для базы
        return f"{s3_url}|{wb_image_url}"
    except Exception as e:
        print(f" Ошибка: {e}")
        return wb_image_url
        
    except Exception as e:
        print(f"    ❌ Ошибка обработки фото {wb_image_url}: {e}")
        return wb_image_url # В случае сбоя возвращаем оригинал WB

# =========================================
# 1. СИНХРОНИЗАЦИЯ ПРЕТЕНЗИЙ
# =========================================
def fetch_wb_claims():
    print("⏳ Запрашиваем ВСЕ претензии с WB...")
    
    # 🌟 УМНЫЙ КЭШ: Достаем уже обработанные фото из базы, чтобы не сжимать их повторно
    existing_photos_map = {}
    try:
        with engine.connect() as conn:
            existing_df = pd.read_sql("SELECT srid, photos FROM wb_claims WHERE photos IS NOT NULL AND photos != ''", conn)
            existing_photos_map = dict(zip(existing_df['srid'].astype(str), existing_df['photos'].astype(str)))
    except Exception as e:
        print(f"Информация: кэш фото не загружен ({e})")

    claims_url = "https://returns-api.wildberries.ru/api/v1/claims"
    all_claims = []
    
    for archive in ["false", "true"]:
        params = {"limit": 100, "offset": 0, "is_archive": archive}
        error_counter = 0
        max_errors = 5
        
        while True:
            try:
                response = requests.get(claims_url, headers=headers, params=params, timeout=30)
                
                if response.status_code == 429:
                    print(f"⚠️ WB просит подождать (Лимит 429). Спим 30 секунд...")
                    time.sleep(30)
                    continue
                    
                response.raise_for_status()
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
                    
                    # === ОБРАБОТКА МЕДИА ===
                    final_photos = ""
                    # Если фото уже сжаты и есть в БД - просто берем их
                    if srid in existing_photos_map and existing_photos_map[srid] not in ['nan', 'None']:
                        final_photos = existing_photos_map[srid]
                    else:
                        # Собираем ссылки на фото от WB
                        raw_photos = item.get("photos", [])
                        photo_urls = []
                        if isinstance(raw_photos, list):
                            for p in raw_photos:
                                if isinstance(p, dict) and p.get("url"): photo_urls.append(p.get("url"))
                                elif isinstance(p, dict) and p.get("fullSize"): photo_urls.append(p.get("fullSize"))
                                elif isinstance(p, str): photo_urls.append(p)
                        
                        # Сжимаем и грузим
                        if photo_urls:
                            compressed_urls = [create_and_upload_thumbnail(u) for u in photo_urls if u]
                            final_photos = " ".join(compressed_urls)
                    
                    # Собираем ссылки на видео (их не сжимаем)
                    raw_videos = item.get("video") or item.get("videos") or []
                    video_urls = []
                    if isinstance(raw_videos, str): video_urls = [raw_videos]
                    elif isinstance(raw_videos, list): video_urls = [v for v in raw_videos if isinstance(v, str)]
                    final_videos = " ".join(video_urls)
                    
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
                        "last_sync": datetime.now(),
                        "photos": final_photos,      # Новое поле!
                        "video_paths": final_videos  # Новое поле!
                    })
                
                print(f"📦 Скачано претензий: {len(all_claims)}...")
                
                if len(batch) < 100: break
                params["offset"] += 100
                error_counter = 0 
                time.sleep(2)
                
            except Exception as e:
                error_counter += 1
                print(f"⚠️ Системная ошибка претензий ({error_counter}/{max_errors}): {e}")
                if error_counter >= max_errors:
                    print("🚨 Слишком много ошибок подряд. Принудительно останавливаем.")
                    break
                time.sleep(10)
                
    df = pd.DataFrame(all_claims)
    if not df.empty:
        df = df.drop_duplicates(subset=['srid'], keep='last')
    return df

def sync_claims_to_db(df):
    if df is None or df.empty:
        print("⚠️ Претензий для сохранения не найдено.")
        return
        
    df.to_sql('temp_wb_claims', engine, if_exists='replace', index=False)
    # Добавлены поля photos и video_paths в UPSERT
    upsert_sql = """
    INSERT INTO wb_claims (srid, claim_id, created_dt, supplier_article, nm_id, user_comment, status, status_ex, claim_type, is_archive, last_sync, photos, video_paths)
    SELECT DISTINCT ON (srid) srid, claim_id, CAST(created_dt AS TIMESTAMP), supplier_article, nm_id, user_comment, status, status_ex, claim_type, CAST(is_archive AS BOOLEAN), CAST(last_sync AS TIMESTAMP), photos, video_paths
    FROM temp_wb_claims ORDER BY srid
    ON CONFLICT (srid) DO UPDATE SET 
        status = EXCLUDED.status,
        status_ex = EXCLUDED.status_ex,
        is_archive = EXCLUDED.is_archive,
        last_sync = EXCLUDED.last_sync,
        photos = CASE WHEN EXCLUDED.photos != '' THEN EXCLUDED.photos ELSE wb_claims.photos END,
        video_paths = CASE WHEN EXCLUDED.video_paths != '' THEN EXCLUDED.video_paths ELSE wb_claims.video_paths END;
    """
    with engine.begin() as conn:
        conn.execute(text(upsert_sql))
        conn.execute(text("DROP TABLE temp_wb_claims;"))
    print(f"📥 UPSERT: Синхронизируем {len(df)} претензий...")

# =========================================
# 2. СИНХРОНИЗАЦИЯ ЛОГИСТИКИ (ПРОДАЖИ)
# =========================================
def get_logistics_start_date(doc_type):
    query = text(f"SELECT MAX(last_change_date) FROM wb_logistics WHERE doc_type = '{doc_type}'")
    with engine.connect() as conn:
        result = conn.execute(query).scalar()
        if result:
            return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
    return "2025-10-01T00:00:00"

def sync_chunk_to_db(df):
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
    request_count = 0
    
    while True:
        params = {"dateFrom": current_from, "flag": 0}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            request_count += 1
            
            if response.status_code == 429:
                print("⚠️ WB Лимит 429: Ждем 65 сек...")
                time.sleep(65)
                continue
                
            response.raise_for_status()
            res = response.json()
            if not res or not isinstance(res, list): 
                print(f"✅ {doc_type} успешно загружены!")
                break
            
            chunk_data = []
            for item in res:
                srid = safe_str(item.get("srid") or item.get("saleID"))
                if not srid: continue
                
                chunk_data.append({
                    "srid": srid, 
                    "doc_type": doc_type,
                    "dt": item.get("date"),
                    "supplier_article": safe_str(item.get("supplierArticle") or item.get("saName")),
                    "nm_id": item.get("nmId"),
                    "warehouse_name": item.get("warehouseName"),
                    "category": item.get("category"),
                    "subject": item.get("subject"),
                    "brand": item.get("brand"),
                    "is_cancel": item.get("isCancel", False),
                    "last_change_date": item.get("lastChangeDate"),
                    "income_id": item.get("incomeID")
                })
            
            df_chunk = pd.DataFrame(chunk_data)
            if not df_chunk.empty:
                df_chunk = df_chunk.drop_duplicates(subset=['srid'], keep='last')
                sync_chunk_to_db(df_chunk)
                
            print(f"📥 Сохранена пачка {doc_type} ({len(df_chunk)} строк). Текущая дата: {current_from}")
            
            last_change = res[-1].get("lastChangeDate")
            if not last_change or last_change == current_from: 
                print(f"✅ {doc_type} успешно загружены!")
                break
                
            current_from = last_change
            
            if request_count >= 9:
                time.sleep(62)
            else:
                time.sleep(3)
            
        except Exception as e:
            print(f"⚠️ Ошибка логистики: {e}. Повтор через 30 сек...")
            time.sleep(30)

# =========================================
# 3. ORDERS (ЗАКАЗЫ)
# =========================================
def get_orders_start_date():
    query = text("SELECT MAX(last_change_date) FROM wb_orders")
    with engine.connect() as conn:
        result = conn.execute(query).scalar()
        if result:
            return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
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
    request_count = 0 
    
    while True:
        params = {"dateFrom": current_from, "flag": 0}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            request_count += 1
            
            if response.status_code == 429:
                print("⚠️ WB Лимит 429: Включаем режим '1 запрос в минуту'. Ждем 65 сек...")
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
            
            if not last_change or last_change == current_from: 
                print("✅ Все заказы успешно загружены!")
                break
                
            current_from = last_change
            
            if request_count >= 9:
                time.sleep(62)
            else:
                time.sleep(3)
            
        except Exception as e:
            print(f"⚠️ Ошибка заказов: {e}. Повтор через 30 секунд...")
            time.sleep(30)

# =========================================
# ЗАПУСК
# =========================================
if __name__ == "__main__":
    print("🚀 СТАРТ ЧИСТОГО ВОРКЕРА")
    try:
        # 1. Качаем претензии
        sync_claims_to_db(fetch_wb_claims())
        
        # 2. Качаем продажи
        sales_url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
        fetch_and_save_logistics(sales_url, "SALE")
        
        # 3. Качаем заказы
        orders_url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
        fetch_and_save_orders(orders_url)
        
        print("🏁 СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА УСПЕШНО")
    except Exception as e:
        print(f"💥 ОШИБКА: {e}")
        sys.exit(1)
