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
import gspread
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКИ ОСНОВНЫЕ ---
WB_API_KEY = os.getenv("WB_API_KEY", "").strip()

# Строки подключения к обеим базам данных
URL_SUPABASE = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

# Создаем два движка
engine_supabase = create_engine(URL_SUPABASE)
engine_local = create_engine(URL_LOCAL_VPS)

headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

# --- НАСТРОЙКИ ДЛЯ ИНВОЙСОВ ---
PATH_TO_GOOGLE_CREDS = "/root/my_project/bot_api_key.json" 
SPREADSHEET_ID_INVOICES = os.getenv("SPREADSHEET_ID_INVOICES")

if not os.path.exists(PATH_TO_GOOGLE_CREDS):
    print(f"❌ ФАЙЛ НЕ НАЙДЕН ПО ПУТИ: {PATH_TO_GOOGLE_CREDS}")

# --- НАСТРОЙКИ S3 ХРАНИЛИЩА ---
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net").strip()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "").strip()
BUCKET_NAME = os.getenv("BUCKET_NAME", "cx-dashboard-media").strip()

s3_client = None
if S3_ACCESS_KEY and S3_SECRET_KEY:
    s3_client = boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name='ru-central1'
    )

def safe_str(val):
    if val is None or str(val).lower() in ['nan', 'none', 'null', '']: return None
    return str(val).strip()

def create_and_upload_thumbnail(wb_image_url):
    if not wb_image_url or not wb_image_url.startswith('http'): return wb_image_url
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        return wb_image_url

    try:
        response = requests.get(wb_image_url, timeout=15)
        response.raise_for_status()
        
        image = Image.open(io.BytesIO(response.content))
        if image.mode in ("RGBA", "P"): image = image.convert("RGB")
            
        image.thumbnail((1000, 1000))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", optimize=True, quality=90)
        buffer.seek(0)
        
        file_name = f"previews/{uuid.uuid4().hex}.jpg"
        s3_client.upload_fileobj(buffer, BUCKET_NAME, file_name, ExtraArgs={'ContentType': 'image/jpeg'})
        
        s3_url = f"https://{BUCKET_NAME}.storage.yandexcloud.net/{file_name}"
        return f"{s3_url}|{wb_image_url}"
    except Exception as e:
        print(f" Ошибка обработки фото {wb_image_url}: {e}")
        return wb_image_url

# =========================================
# 1. СИНХРОНИЗАЦИЯ ПРЕТЕНЗИЙ
# =========================================
def fetch_wb_claims():
    print("⏳ Запрашиваем ВСЕ претензии с WB...")
    existing_photos_map = {}
    try:
        # Кэш фото читаем из локальной базы, так как это быстрее
        with engine_local.connect() as conn:
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
                    
                    final_photos = ""
                    if srid in existing_photos_map and existing_photos_map[srid] not in ['nan', 'None']:
                        final_photos = existing_photos_map[srid]
                    else:
                        raw_photos = item.get("photos", [])
                        photo_urls = []
                        if isinstance(raw_photos, list):
                            for p in raw_photos:
                                if isinstance(p, dict) and p.get("url"): photo_urls.append(p.get("url"))
                                elif isinstance(p, dict) and p.get("fullSize"): photo_urls.append(p.get("fullSize"))
                                elif isinstance(p, str): photo_urls.append(p)
                        
                        if photo_urls:
                            compressed_urls = [create_and_upload_thumbnail(u) for u in photo_urls if u]
                            final_photos = " ".join(compressed_urls)
                    
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
                        "photos": final_photos,
                        "video_paths": final_videos
                    })
                
                if len(batch) < 100: break
                params["offset"] += 100
                error_counter = 0 
                time.sleep(2)
                
            except Exception as e:
                error_counter += 1
                print(f"⚠️ Системная ошибка претензий ({error_counter}/{max_errors}): {e}")
                if error_counter >= max_errors: break
                time.sleep(10)
                
    df = pd.DataFrame(all_claims)
    if not df.empty:
        df = df.drop_duplicates(subset=['srid'], keep='last')
    return df

def sync_claims_to_db(df):
    if df is None or df.empty: return
        
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
    for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
        try:
            df.to_sql('temp_wb_claims', eng, if_exists='replace', index=False)
            with eng.begin() as conn:
                conn.execute(text(upsert_sql))
                conn.execute(text("DROP TABLE temp_wb_claims;"))
            print(f"📥 [DUAL WRITE] Претензии синхронизированы в {name}")
        except Exception as e:
            print(f"❌ Ошибка записи претензий в {name}: {e}")

# =========================================
# 2. СИНХРОНИЗАЦИЯ ЛОГИСТИКИ (ПРОДАЖИ)
# =========================================
def get_logistics_start_date(doc_type):
    query = text(f"SELECT MAX(last_change_date) FROM wb_logistics WHERE doc_type = '{doc_type}'")
    try:
        with engine_local.connect() as conn:
            result = conn.execute(query).scalar()
            if result:
                return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
    except: pass
    return "2025-10-01T00:00:00"

def sync_chunk_to_db(df):
    if df.empty: return
    upsert_sql = """
    INSERT INTO wb_logistics (srid, doc_type, dt, supplier_article, nm_id, warehouse_name, category, subject, brand, is_cancel, last_change_date, income_id, last_sync)
    SELECT DISTINCT ON (srid) srid, doc_type, CAST(dt AS TIMESTAMP), supplier_article, nm_id, warehouse_name, category, subject, brand, CAST(is_cancel AS BOOLEAN), CAST(last_change_date AS TIMESTAMP), income_id, CAST(last_sync AS TIMESTAMP)
    FROM temp_wb_logistics ORDER BY srid, last_change_date DESC
    ON CONFLICT (srid) DO UPDATE SET 
        is_cancel = EXCLUDED.is_cancel, 
        nm_id = COALESCE(EXCLUDED.nm_id, wb_logistics.nm_id),
        last_change_date = EXCLUDED.last_change_date,
        income_id = COALESCE(EXCLUDED.income_id, wb_logistics.income_id),
        last_sync = EXCLUDED.last_sync;
    """
    for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
        try:
            df.to_sql('temp_wb_logistics', eng, if_exists='replace', index=False)
            with eng.begin() as conn:
                conn.execute(text(upsert_sql))
                conn.execute(text("DROP TABLE temp_wb_logistics;"))
        except Exception as e:
            print(f"❌ Ошибка записи логистики в {name}: {e}")

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
                time.sleep(65)
                continue
                
            response.raise_for_status()
            res = response.json()
            if not res or not isinstance(res, list): break
            
            chunk_data = []
            for item in res:
                srid = safe_str(item.get("srid") or item.get("saleID"))
                if not srid: continue
                chunk_data.append({
                    "srid": srid, "doc_type": doc_type, "dt": item.get("date"),
                    "supplier_article": safe_str(item.get("supplierArticle") or item.get("saName")),
                    "nm_id": item.get("nmId"), "warehouse_name": item.get("warehouseName"),
                    "category": item.get("category"), "subject": item.get("subject"),
                    "brand": item.get("brand"), "is_cancel": item.get("isCancel", False),
                    "last_change_date": item.get("lastChangeDate"), "income_id": item.get("incomeID")
                })
            
            df_chunk = pd.DataFrame(chunk_data)
            if not df_chunk.empty:
                df_chunk = df_chunk.drop_duplicates(subset=['srid'], keep='last')
                df_chunk['last_sync'] = pd.Timestamp.now()
                sync_chunk_to_db(df_chunk)
                
            print(f"📥 Сохранена пачка {doc_type} ({len(df_chunk)} строк).")
            last_change = res[-1].get("lastChangeDate")
            if not last_change or last_change == current_from: break
            current_from = last_change
            time.sleep(62 if request_count >= 9 else 3)
        except Exception as e:
            time.sleep(30)

# =========================================
# 3. ORDERS (ЗАКАЗЫ)
# =========================================
def get_orders_start_date():
    query = text("SELECT MAX(last_change_date) FROM wb_orders")
    try:
        with engine_local.connect() as conn:
            result = conn.execute(query).scalar()
            if result:
                return (result - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
    except: pass
    return "2026-04-01T00:00:00"

def sync_orders_chunk_to_db(df):
    if df.empty: return
    upsert_sql = """
    INSERT INTO wb_orders (srid, dt, supplier_article, cancel_dt, last_change_date, last_sync)
    SELECT DISTINCT ON (srid) srid, CAST(dt AS TIMESTAMP), supplier_article, 
        CAST(cancel_dt AS TIMESTAMP), CAST(last_change_date AS TIMESTAMP), CAST(last_sync AS TIMESTAMP)
    FROM temp_wb_orders ORDER BY srid, last_change_date DESC
    ON CONFLICT (srid) DO UPDATE SET 
        cancel_dt = EXCLUDED.cancel_dt,
        last_change_date = EXCLUDED.last_change_date,
        last_sync = EXCLUDED.last_sync;
    """
    for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
        try:
            df.to_sql('temp_wb_orders', eng, if_exists='replace', index=False)
            with eng.begin() as conn:
                conn.execute(text(upsert_sql))
                conn.execute(text("DROP TABLE temp_wb_orders;"))
        except Exception as e:
            print(f"❌ Ошибка записи заказов в {name}: {e}")

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
                time.sleep(65)
                continue
                
            res = response.json()
            if not res or not isinstance(res, list): break
            
            chunk_data = []
            for item in res:
                srid = safe_str(item.get("srid"))
                if not srid: continue
                cancel_dt = item.get("cancelDate")
                if item.get("isCancel", False) is False or str(cancel_dt).startswith("0001"):
                    cancel_dt = None

                chunk_data.append({
                    "srid": srid, "dt": item.get("date"),
                    "supplier_article": safe_str(item.get("supplierArticle") or item.get("saName")),
                    "cancel_dt": cancel_dt, "last_change_date": item.get("lastChangeDate")
                })
            
            df_chunk = pd.DataFrame(chunk_data).drop_duplicates(subset=['srid'], keep='last')
            df_chunk['last_sync'] = pd.Timestamp.now()
            sync_orders_chunk_to_db(df_chunk)
            print(f"📥 Сохранена пачка заказов ({len(df_chunk)} строк).")
            
            last_change = res[-1].get("lastChangeDate")
            if not last_change or last_change == current_from: break
            current_from = last_change
            time.sleep(62 if request_count >= 9 else 3)
        except Exception as e:
            time.sleep(30)

def sync_invoices():
    print("⏳ Синхронизация инвойсов из Google Sheets...")
    try:
        gc = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS)
        sheet = gc.open_by_key(SPREADSHEET_ID_INVOICES).get_worksheet(0)
        
        df = pd.DataFrame(sheet.get_all_records())
        if df.empty: return

        df.columns = df.columns.str.strip().str.lower()
        supply_col = next((c for c in df.columns if 'supplyid' in c or 'поставк' in c), None)
        article_col = next((c for c in df.columns if 'артикул' in c and 'wb' not in c), None)
        col_e = df.columns[4] if len(df.columns) > 4 else None

        if not all([supply_col, article_col, col_e]): return

        df[col_e] = df[col_e].astype(str).str.strip()
        bad_invoice_vals = ['nan', 'none', '', '0', '0.0', '-', '---', 'ошибка', 'undefined']

        df_clean = df[
            (~df[col_e].str.lower().isin(bad_invoice_vals)) & 
            (df[supply_col].astype(str).str.strip() != '') & 
            (df[article_col].astype(str).str.strip() != '')
        ].copy()

        if df_clean.empty: return
        df_to_process = df_clean.tail(100)

        df_to_process['s_id'] = df_to_process[supply_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        df_to_process['a_id'] = df_to_process[article_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        df_to_process['i_id'] = df_to_process[col_e]

        sql = text("""
            INSERT INTO wb_invoices (supply_id, supplier_article, invoice_num, last_sync)
            VALUES (:sid, :art, :inv, CURRENT_TIMESTAMP)
            ON CONFLICT (supply_id, supplier_article) 
            DO UPDATE SET invoice_num = EXCLUDED.invoice_num, last_sync = EXCLUDED.last_sync
        """)
        
        for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
            try:
                with eng.begin() as conn:
                    for _, row in df_to_process.iterrows():
                        conn.execute(sql, {"sid": row['s_id'], "art": row['a_id'], "inv": row['i_id']})
                print(f"✅ Инвойсы синхронизированы в {name}")
            except Exception as e:
                print(f"❌ Ошибка инвойсов в {name}: {e}")
    except Exception as e:
        print(f"🚨 Ошибка в блоке инвойсов: {e}")

def sync_assortment_matrix():
    print("⏳ Синхронизация ассортиментной матрицы из Google Sheets...")
    try:
        gc = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS)
        sheet = gc.open_by_key(SPREADSHEET_ID_INVOICES).get_worksheet(1)
        
        df = pd.DataFrame(sheet.get_all_records())
        if df.empty: return

        df.columns = df.columns.str.strip().str.lower()
        article_col = next((c for c in df.columns if 'артикул' in c and 'wb' not in c), None)
        name_ru_col = next((c for c in df.columns if 'наименование' in c and 'кит' not in c), None)
        name_cn_col = next((c for c in df.columns if 'китайск' in c or ('наименование' in c and 'кит' in c)), None)
        manuf_col = next((c for c in df.columns if 'завод' in c or 'производитель' in c), None)

        if not article_col: return

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS wb_assortment (
            supplier_article TEXT PRIMARY KEY, name_ru TEXT, name_cn TEXT, manufacturer TEXT, last_sync TIMESTAMP
        );
        """
        upsert_sql = text("""
            INSERT INTO wb_assortment (supplier_article, name_ru, name_cn, manufacturer, last_sync)
            VALUES (:art, :n_ru, :n_cn, :manuf, CURRENT_TIMESTAMP)
            ON CONFLICT (supplier_article) 
            DO UPDATE SET name_ru = EXCLUDED.name_ru, name_cn = EXCLUDED.name_cn, manufacturer = EXCLUDED.manufacturer, last_sync = EXCLUDED.last_sync
        """)
        
        for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
            try:
                with eng.begin() as conn:
                    conn.execute(text(create_table_sql))
                    for _, row in df.iterrows():
                        art = str(row.get(article_col, '')).strip()
                        if not art or art.lower() in ['nan', 'none', '']: continue
                        n_ru = str(row.get(name_ru_col, '')).strip() if name_ru_col else ''
                        n_cn = str(row.get(name_cn_col, '')).strip() if name_cn_col else ''
                        manuf = str(row.get(manuf_col, '')).strip() if manuf_col else ''
                        conn.execute(upsert_sql, {"art": art, "n_ru": n_ru, "n_cn": n_cn, "manuf": manuf})
                print(f"✅ Ассортиментная матрица синхронизирована в {name}")
            except Exception as e:
                print(f"❌ Ошибка матрицы в {name}: {e}")
    except Exception as e:
        print(f"🚨 Ошибка синхронизации матрицы: {e}")

if __name__ == "__main__":
    print("🚀 СТАРТ ЧИСТОГО ВОРКЕРА (РЕЖИМ ДВОЙНОЙ ЗАПИСИ)")
    try:
        sync_invoices()
        sync_assortment_matrix()
        sync_claims_to_db(fetch_wb_claims())
        
        sales_url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
        fetch_and_save_logistics(sales_url, "SALE")
        
        orders_url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
        fetch_and_save_orders(orders_url)
        
        print("🏁 СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА УСПЕШНО НА ВСЕХ БАЗАХ ДАННЫХ")
    except Exception as e:
        print(f"💥 ОШИБКА: {e}")
        sys.exit(1)