import requests # type: ignore
import sys
import pandas as pd # type: ignore
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text # type: ignore
import os
import time
import io
import uuid
import boto3 # type: ignore
import gspread # type: ignore
from PIL import Image # type: ignore
from dotenv import load_dotenv # type: ignore

load_dotenv()

# --- НАСТРОЙКИ ОСНОВНЫЕ ---
WB_API_KEY = os.getenv("WB_API_KEY", "").strip()

URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

engine_local = create_engine(URL_LOCAL_VPS)

headers = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

# --- ФУНКЦИЯ СИСТЕМНОГО ЛОГИРОВАНИЯ ---
def log_to_system(action, status, details=""):
    """Пишет логи воркера напрямую в БД для дашборда автоматизации"""
    try:
        with engine_local.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:action, :status, :details)"),
                {"action": action, "status": status, "details": str(details)[:2000]}
            )
    except Exception as e:
        print(f"Не удалось записать лог: {e}")

# --- НАСТРОЙКИ ДЛЯ ИНВОЙСОВ ---
PATH_TO_GOOGLE_CREDS = os.getenv("GOOGLE_CREDS_PATH", "/root/cx-dashboard/bot_api_key.json") 
SPREADSHEET_ID_INVOICES = os.getenv("SPREADSHEET_ID_INVOICES")
SPREADSHEET_ID_FINANCES = os.getenv("SPREADSHEET_ID_FINANCES")

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
    log_to_system("Синхронизатор Претензий", "INFO", "Старт загрузки претензий с Wildberries")
    
    existing_photos_map = {}
    try:
        with engine_local.connect() as conn:
            existing_df = pd.read_sql("SELECT srid, photos FROM wb_claims WHERE photos IS NOT NULL AND photos != ''", conn)
            existing_photos_map = dict(zip(existing_df['srid'].astype(str), existing_df['photos'].astype(str)))
    except Exception:
        pass

    # 🔗 ЖЕЛЕЗНОЕ СОПОСТАВЛЕНИЕ: Загружаем внутренний справочник связок nm_id -> артикул из логистики
    art_mapping = {}
    try:
        with engine_local.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT nm_id, supplier_article FROM wb_logistics WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL AND supplier_article != ''")).fetchall()
            art_mapping = {str(r[0]).strip(): str(r[1]).strip() for r in res}
        print(f"📦 Загружен внутренний справочник артикулов: {len(art_mapping)} связок.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки справочника артикулов: {e}")

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
                    time.sleep(30); continue
                    
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
                    
                    order_date_raw = item.get("order_dt") or item.get("orderDate") or item.get("dt_order")
                    claim_date_raw = item.get("dt") or item.get("claim_date") or item.get("created_dt")

                    # Извлекаем и чистим nm_id и пришедший артикул
                    nm_id_val = safe_str(item.get("nm_id") or item.get("nmId"))
                    api_article = safe_str(item.get("supplier_article") or item.get("article") or item.get("saName") or item.get("sa_name"))
                    
                    # 🔥 УМНОЕ ПЕРЕСТРАХОВАНИЕ: Если WB прислал пустоту, вытаскиваем артикул по nm_id из нашего кэша
                    if (not api_article or api_article == 'Без артикула') and nm_id_val and nm_id_val in art_mapping:
                        api_article = art_mapping[nm_id_val]
                        
                    if not api_article:
                        api_article = 'Без артикула'

                    all_claims.append({
                        "srid": srid,
                        "claim_id": safe_str(item.get("id")),
                        "created_dt": item.get("dt"),
                        "supplier_article": api_article,
                        "nm_id": nm_id_val,
                        "user_comment": item.get("user_comment"),
                        "status": mapped,
                        "status_ex": status_ex,
                        "claim_type": item.get("claim_type"),
                        "is_archive": archive == "true",
                        "last_sync": datetime.now(),
                        "photos": final_photos,
                        "video_paths": final_videos,
                        "order_date": order_date_raw,
                        "claim_date": claim_date_raw
                    })
                
                if len(batch) < 100: break
                params["offset"] += 100
                error_counter = 0; time.sleep(2)
            except Exception as e:
                error_counter += 1
                if error_counter >= max_errors: break
                time.sleep(10)
                
    df = pd.DataFrame(all_claims)
    if not df.empty: df = df.drop_duplicates(subset=['srid'], keep='last')
    return df

def sync_claims_to_db(df):
    if df is None or df.empty: return
    
    # Обновленный SQL-запрос с учетом новых колонок order_date и claim_date
    upsert_sql = """
    INSERT INTO wb_claims (srid, claim_id, created_dt, supplier_article, nm_id, user_comment, status, status_ex, claim_type, is_archive, last_sync, photos, video_paths, order_date, claim_date)
    SELECT DISTINCT ON (srid) srid, claim_id, CAST(created_dt AS TIMESTAMP), supplier_article, nm_id, user_comment, status, status_ex, claim_type, CAST(is_archive AS BOOLEAN), CAST(last_sync AS TIMESTAMP), photos, video_paths, CAST(order_date AS TIMESTAMP), CAST(claim_date AS TIMESTAMP)
    FROM temp_wb_claims ORDER BY srid
    ON CONFLICT (srid) DO UPDATE SET 
        status = EXCLUDED.status, status_ex = EXCLUDED.status_ex, is_archive = EXCLUDED.is_archive, last_sync = EXCLUDED.last_sync,
        photos = CASE WHEN EXCLUDED.photos != '' THEN EXCLUDED.photos ELSE wb_claims.photos END,
        video_paths = CASE WHEN EXCLUDED.video_paths != '' THEN EXCLUDED.video_paths ELSE wb_claims.video_paths END,
        order_date = COALESCE(EXCLUDED.order_date, wb_claims.order_date),
        claim_date = COALESCE(EXCLUDED.claim_date, wb_claims.claim_date);
    """
    try:
        df.to_sql('temp_wb_claims', engine_local, if_exists='replace', index=False)
        with engine_local.begin() as conn:
            conn.execute(text(upsert_sql)); conn.execute(text("DROP TABLE temp_wb_claims;"))
        log_to_system("Синхронизатор Претензий", "SUCCESS", f"Успешно обработано и сохранено {len(df)} возвратов")
    except Exception as e:
        print(f"❌ Ошибка записи претензий: {e}")
        log_to_system("Синхронизатор Претензий", "ERROR", f"Ошибка записи в БД: {e}")

# =========================================
# 1,5. СИНХРОНИЗАЦИЯ ОТЗЫВОВ И ВОПРОСОВ (VOC - Voice of Customer)
# =========================================
def fetch_feedbacks_archive():
    print("⏳ Запуск сбора архивных отзывов (Voice of Customer)...")
    log_to_system("Синхронизатор VOC", "INFO", "Старт парсинга архива отзывов")
    
    headers = {
        "Authorization": os.getenv("WB_API_KEY"), 
        "Content-Type": "application/json"
    }
    
    upsert_sql = text("""
        INSERT INTO wb_feedbacks (id, supplier_article, created_date, valuation, text, answer_text, state)
        VALUES (:id, :sku, :created, :val, :text, :ans, 'archive')
        ON CONFLICT (id) 
        DO UPDATE SET 
            valuation = EXCLUDED.valuation,
            answer_text = EXCLUDED.answer_text
    """)
    
    skip = 0
    take = 5000 
    total_saved = 0
    
    try:
        while True:
            url = f"https://feedbacks-api.wildberries.ru/api/v1/feedbacks/archive?isAnswered=true&take={take}&skip={skip}&order=dateDesc"
            resp = requests.get(url, headers=headers)
            
            if resp.status_code == 422:
                print("🏁 Достигнут предел архива WB (лимит пагинации).")
                break
            elif resp.status_code != 200:
                print(f"❌ Ошибка API WB (Отзывы): {resp.status_code} - {resp.text}")
                break
                
            feedbacks = resp.json().get('data', {}).get('feedbacks', [])
            if not feedbacks:
                break 
                
            valid_feedbacks = []
            for fb in feedbacks:
                fb_text = fb.get('text', '').strip()
                if not fb_text:
                    continue
                    
                sku = fb.get('productDetails', {}).get('supplierArticle', 'Без артикула')
                val = fb.get('productValuation', 5)
                created = fb.get('createdDate', '')
                ans = fb.get('answer', {}).get('text', '') if fb.get('answer') else ''
                
                valid_feedbacks.append({
                    "id": str(fb.get('id')), "sku": sku, "created": created,
                    "val": val, "text": fb_text, "ans": ans
                })
            
            # 🔥 Запись сразу в ДВЕ базы данных
            if valid_feedbacks:
                try:
                    with engine_local.begin() as conn:
                        for item in valid_feedbacks:
                            conn.execute(upsert_sql, item)
                except Exception as e:
                    print(f"❌ Ошибка БД: {e}")

            total_saved += len(valid_feedbacks)
            print(f"📥 Обработано: {skip + len(feedbacks)} | Сохранено с текстом: {total_saved} шт...")
            skip += take
            
        print(f"✅ Успешно синхронизировано {total_saved} архивных отзывов в обе БД!")
        log_to_system("Синхронизатор VOC", "SUCCESS", f"Собрано {total_saved} архивных отзывов")
        
    except Exception as e:
        print(f"🚨 Ошибка сбора архивных отзывов: {e}")
        log_to_system("Синхронизатор VOC", "FAIL", str(e))


def fetch_new_feedbacks():
    print("⏳ Запуск сбора НОВЫХ (неотвеченных) отзывов...")
    headers = {
        "Authorization": os.getenv("WB_API_KEY"), 
        "Content-Type": "application/json"
    }
    
    upsert_sql = text("""
        INSERT INTO wb_feedbacks (id, supplier_article, created_date, valuation, text, answer_text, state)
        VALUES (:id, :sku, :created, :val, :text, :ans, 'active')
        ON CONFLICT (id) 
        DO UPDATE SET 
            valuation = EXCLUDED.valuation,
            answer_text = EXCLUDED.answer_text,
            state = EXCLUDED.state
    """)
    
    skip = 0
    take = 5000
    total_saved = 0
    
    try:
        while True:
            url = f"https://feedbacks-api.wildberries.ru/api/v1/feedbacks?isAnswered=false&take={take}&skip={skip}&order=dateDesc"
            resp = requests.get(url, headers=headers)
            
            if resp.status_code == 422:
                break
            elif resp.status_code != 200:
                print(f"❌ Ошибка API WB (Новые отзывы): {resp.status_code} - {resp.text}")
                break
                
            feedbacks = resp.json().get('data', {}).get('feedbacks', [])
            if not feedbacks:
                break 
                
            valid_feedbacks = []
            for fb in feedbacks:
                fb_text = fb.get('text', '').strip()
                if not fb_text:
                    continue
                    
                sku = fb.get('productDetails', {}).get('supplierArticle', 'Без артикула')
                val = fb.get('productValuation', 5)
                created = fb.get('createdDate', '')
                ans = fb.get('answer', {}).get('text', '') if fb.get('answer') else ''
                
                valid_feedbacks.append({
                    "id": str(fb.get('id')), "sku": sku, "created": created,
                    "val": val, "text": fb_text, "ans": ans
                })
            
            # 🔥 Запись сразу в ДВЕ базы данных
            if valid_feedbacks:
                try:
                    with engine_local.begin() as conn:
                        for item in valid_feedbacks:
                            conn.execute(upsert_sql, item)
                except Exception as e:
                    print(f"❌ Ошибка БД: {e}")

            total_saved += len(valid_feedbacks)
            skip += take
            
        print(f"✅ Успешно синхронизировано {total_saved} новых отзывов в обе БД!")
        
    except Exception as e:
        print(f"🚨 Ошибка сбора новых отзывов: {e}")


def fetch_questions():
    print("⏳ Запуск сбора ВОПРОСОВ (новых и отвеченных)...")
    headers = {
        "Authorization": os.getenv("WB_API_KEY"), 
        "Content-Type": "application/json"
    }
    
    upsert_sql = text("""
        INSERT INTO wb_questions (id, supplier_article, created_date, text, answer_text, state)
        VALUES (:id, :sku, :created, :text, :ans, :state)
        ON CONFLICT (id) 
        DO UPDATE SET answer_text = EXCLUDED.answer_text, state = EXCLUDED.state
    """)
    
    total_saved = 0
    statuses = [('false', 'active'), ('true', 'archive')]
    
    try:
        for is_answered, state_label in statuses:
            skip = 0
            take = 5000
            
            while True:
                url = f"https://feedbacks-api.wildberries.ru/api/v1/questions?isAnswered={is_answered}&take={take}&skip={skip}&order=dateDesc"
                resp = requests.get(url, headers=headers)
                
                if resp.status_code == 422:
                    break
                elif resp.status_code != 200:
                    print(f"❌ Ошибка API WB (Вопросы): {resp.status_code} - {resp.text}")
                    break
                    
                questions = resp.json().get('data', {}).get('questions', [])
                if not questions:
                    break 
                    
                valid_questions = []
                for q in questions:
                    q_text = q.get('text', '').strip()
                    if not q_text:
                        continue
                        
                    sku = q.get('productDetails', {}).get('supplierArticle', 'Без артикула')
                    created = q.get('createdDate', '')
                    ans = q.get('answer', {}).get('text', '') if q.get('answer') else ''
                    
                    valid_questions.append({
                        "id": str(q.get('id')), "sku": sku, "created": created,
                        "text": q_text, "ans": ans, "state": state_label
                    })
                
                # 🔥 Запись сразу в ДВЕ базы данных
                if valid_questions:
                    try:
                        with engine_local.begin() as conn:
                            for item in valid_questions:
                                conn.execute(upsert_sql, item)
                    except Exception as e:
                        print(f"❌ Ошибка БД: {e}")
                    
                total_saved += len(valid_questions)
                skip += take
                
        print(f"✅ Успешно синхронизировано {total_saved} вопросов в обе БД!")
        
    except Exception as e:
        print(f"🚨 Ошибка сбора вопросов: {e}")

# =========================================
# 2. СИНХРОНИЗАЦИЯ ЛОГИСТИКИ И ЗАКАЗОВ
# =========================================
def get_start_date(table, doc_type=None):
    q = f"SELECT MAX(last_change_date) FROM {table}" + (f" WHERE doc_type = '{doc_type}'" if doc_type else "")
    try:
        with engine_local.connect() as conn:
            res = conn.execute(text(q)).scalar()
            if res: return (res - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00")
    except: pass
    return "2025-10-01T00:00:00"

def sync_chunk(df, table, upsert_sql):
    if df.empty: return
    try:
        df.to_sql(f'temp_{table}', engine_local, if_exists='replace', index=False)
        with engine_local.begin() as conn:
            conn.execute(text(upsert_sql)); conn.execute(text(f"DROP TABLE temp_{table};"))
    except Exception as e:
        print(f"❌ Ошибка {table}: {e}")

def fetch_and_save_logistics(url, doc_type):
    current_from = get_start_date("wb_logistics", doc_type)
    print(f"⏳ Качаем логистику {doc_type} начиная с {current_from}...")
    log_to_system("Синхронизатор Логистики", "INFO", f"Старт загрузки {doc_type} с {current_from}")
    request_count = 0
    total_rows = 0
    while True:
        try:
            response = requests.get(url, headers=headers, params={"dateFrom": current_from, "flag": 0}, timeout=60)
            request_count += 1
            if response.status_code == 429: time.sleep(65); continue
            res = response.json()
            if not res or not isinstance(res, list): break
            chunk = [{"srid": safe_str(i.get("srid") or i.get("saleID")), "doc_type": doc_type, "dt": i.get("date"),
                      "supplier_article": safe_str(i.get("supplierArticle") or i.get("saName")), "nm_id": i.get("nmId"),
                      "warehouse_name": i.get("warehouseName"), "category": i.get("category"), "subject": i.get("subject"),
                      "brand": i.get("brand"), "is_cancel": i.get("isCancel", False), "last_change_date": i.get("lastChangeDate"),
                      "income_id": i.get("incomeID"),
                      "finish_price": next((float(v) for v in [i.get("finishedPrice"), i.get("priceWithDisc"), i.get("priceWithDiscount"), i.get("finishPrice")] if v is not None and float(v) > 0), None)
                      } for i in res if safe_str(i.get("srid") or i.get("saleID"))]
            df = pd.DataFrame(chunk).drop_duplicates(subset=['srid'], keep='last')
            df['last_sync'] = pd.Timestamp.now()
            sql = """INSERT INTO wb_logistics (srid, doc_type, dt, supplier_article, nm_id, warehouse_name, category, subject, brand, is_cancel, last_change_date, income_id, finish_price, last_sync)
                     SELECT DISTINCT ON (srid) srid, doc_type, CAST(dt AS TIMESTAMP), supplier_article, nm_id, warehouse_name, category, subject, brand, CAST(is_cancel AS BOOLEAN), CAST(last_change_date AS TIMESTAMP), income_id, CAST(NULLIF(finish_price::text,'None') AS NUMERIC), CAST(last_sync AS TIMESTAMP) FROM temp_wb_logistics ORDER BY srid, last_change_date DESC
                     ON CONFLICT (srid) DO UPDATE SET is_cancel=EXCLUDED.is_cancel, nm_id=COALESCE(EXCLUDED.nm_id, wb_logistics.nm_id), last_change_date=EXCLUDED.last_change_date, income_id=COALESCE(EXCLUDED.income_id, wb_logistics.income_id), finish_price=COALESCE(EXCLUDED.finish_price, wb_logistics.finish_price), last_sync=EXCLUDED.last_sync;"""
            sync_chunk(df, 'wb_logistics', sql)
            total_rows += len(df)
            last_change = res[-1].get("lastChangeDate")
            if not last_change or last_change == current_from: break
            current_from = last_change; time.sleep(62 if request_count >= 9 else 3)
        except Exception as e:
            log_to_system("Синхронизатор Логистики", "ERROR", f"Сбой блока логистики: {e}")
            time.sleep(30)
    log_to_system("Синхронизатор Логистики", "SUCCESS", f"Синхронизация {doc_type} завершена. Добавлено/Обновлено {total_rows} строк.")

def fetch_and_save_orders(url):
    current_from = get_start_date("wb_orders")
    print(f"⏳ Качаем ЗАКАЗЫ начиная с {current_from}...")
    log_to_system("Синхронизатор Заказов", "INFO", f"Старт загрузки заказов с {current_from}")
    request_count = 0 
    total_rows = 0
    while True:
        try:
            response = requests.get(url, headers=headers, params={"dateFrom": current_from, "flag": 0}, timeout=60)
            request_count += 1
            if response.status_code == 429: time.sleep(65); continue
            res = response.json()
            if not res or not isinstance(res, list): break
            chunk = [{"srid": safe_str(i.get("srid")), "dt": i.get("date"), "supplier_article": safe_str(i.get("supplierArticle") or i.get("saName")),
                      "cancel_dt": i.get("cancelDate") if str(i.get("cancelDate")).startswith("20") and i.get("isCancel") else None,
                      "last_change_date": i.get("lastChangeDate")} for i in res if safe_str(i.get("srid"))]
            df = pd.DataFrame(chunk).drop_duplicates(subset=['srid'], keep='last')
            df['last_sync'] = pd.Timestamp.now()
            sql = """INSERT INTO wb_orders (srid, dt, supplier_article, cancel_dt, last_change_date, last_sync)
                     SELECT DISTINCT ON (srid) srid, CAST(dt AS TIMESTAMP), supplier_article, CAST(cancel_dt AS TIMESTAMP), CAST(last_change_date AS TIMESTAMP), CAST(last_sync AS TIMESTAMP) FROM temp_wb_orders ORDER BY srid, last_change_date DESC
                     ON CONFLICT (srid) DO UPDATE SET cancel_dt=EXCLUDED.cancel_dt, last_change_date=EXCLUDED.last_change_date, last_sync=EXCLUDED.last_sync;"""
            sync_chunk(df, 'wb_orders', sql)
            total_rows += len(df)
            last_change = res[-1].get("lastChangeDate")
            if not last_change or last_change == current_from: break
            current_from = last_change; time.sleep(62 if request_count >= 9 else 3)
        except Exception as e:
            log_to_system("Синхронизатор Заказов", "ERROR", f"Сбой блока заказов: {e}")
            time.sleep(30)
    log_to_system("Синхронизатор Заказов", "SUCCESS", f"Синхронизация заказов завершена. Всего: {total_rows} записей.")

def sync_invoices():
    print("⏳ Синхронизация инвойсов из Google Sheets (WB + ЯМ)...")
    log_to_system("Синхронизатор Инвойсов", "INFO", "Парсинг Google-таблицы инвойсов")
    try:
        sheet = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS).open_by_key(SPREADSHEET_ID_INVOICES).get_worksheet(0)
        df = pd.DataFrame(sheet.get_all_records())

        # Нормализуем заголовки, сохраняя оригинальный регистр для поиска
        df.columns = df.columns.str.strip()
        cols_lower  = {c.lower(): c for c in df.columns}

        sup_col = cols_lower.get('номер поставки')
        art_col = cols_lower.get('артикул')
        inv_col = cols_lower.get('инвойс')
        mp_col  = cols_lower.get('мп')   # маркетплейс: "ВБ" / "Яндекс" / "ОЗОН" ...

        if not sup_col or not art_col or not inv_col:
            log_to_system("Синхронизатор Инвойсов", "FAIL", "Не найдены нужные столбцы в таблице")
            return

        # Оставляем строки где есть инвойс И номер поставки
        df_clean = df[
            (df[sup_col].astype(str).str.strip() != '') &
            (~df[inv_col].astype(str).str.lower().isin(['nan', 'none', '', '0', '0.0', '-']))
        ].copy()

        df_clean['s_id'] = df_clean[sup_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        df_clean['a_id'] = df_clean[art_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        df_clean['inv']  = df_clean[inv_col].astype(str).str.strip()

        # Определяем маркетплейс по столбцу МП
        _MP_MAP = {'яндекс': 'ym', 'вб': 'wb', 'wb': 'wb',
                   'озон': 'ozon', 'ozon': 'ozon', 'магнит': 'other'}
        if mp_col:
            df_clean['marketplace'] = (
                df_clean[mp_col].astype(str).str.strip().str.lower()
                .map(lambda x: _MP_MAP.get(x, 'wb'))
            )
        else:
            df_clean['marketplace'] = 'wb'

        sql = text("""
            INSERT INTO wb_invoices (supply_id, supplier_article, invoice_num, marketplace, last_sync)
            VALUES (:sid, :art, :inv, :mp, CURRENT_TIMESTAMP)
            ON CONFLICT (supply_id, supplier_article) DO UPDATE SET
                invoice_num  = EXCLUDED.invoice_num,
                marketplace  = EXCLUDED.marketplace,
                last_sync    = EXCLUDED.last_sync
        """)

        try:
            with engine_local.begin() as conn:
                try: conn.execute(text("SELECT setval(pg_get_serial_sequence('wb_invoices', 'id'), (SELECT COALESCE(MAX(id), 1) FROM wb_invoices));"))
                except: pass
                for _, r in df_clean.iterrows():
                    conn.execute(sql, {
                        "sid": r['s_id'], "art": r['a_id'],
                        "inv": r['inv'],  "mp":  r['marketplace'],
                    })
        except Exception as e:
            print(f"  Ошибка записи инвойсов: {e}")

        ym_cnt = (df_clean['marketplace'] == 'ym').sum()
        wb_cnt = (df_clean['marketplace'] == 'wb').sum()
        log_to_system("Синхронизатор Инвойсов", "SUCCESS",
                      f"Данные по инвойсам обновлены: WB={wb_cnt}, ЯМ={ym_cnt}")
    except Exception as e:
        log_to_system("Синхронизатор Инвойсов", "FAIL", f"Ошибка Google Sheets: {e}")

def sync_assortment_matrix():
    print("⏳ Синхронизация ассортиментной матрицы (с НОВИНКАМИ) из Google Sheets...")
    log_to_system("Синхронизатор Матрицы", "INFO", "Парсинг Google-таблицы ассортимента")
    try:
        sheet = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS).open_by_key(SPREADSHEET_ID_INVOICES).get_worksheet(1)
        raw_data = sheet.get_all_values()
        if len(raw_data) < 2: return
        
        headers = [str(h).strip().lower() for h in raw_data[0]]
        
        art_idx = next((i for i, c in enumerate(headers) if 'артикул' in c and 'wb' not in c), None)
        if art_idx is None: return
        
        n_ru_idx = next((i for i, c in enumerate(headers) if 'наименование' in c and 'кит' not in c), None)
        n_cn_idx = next((i for i, c in enumerate(headers) if 'китайск' in c or ('наименование' in c and 'кит' in c)), None)
        manuf_idx = next((i for i, c in enumerate(headers) if 'завод' in c or 'производитель' in c), None)
        
        nov_idx = next((i for i, c in enumerate(headers) if 'новинк' in c or 'new' in c), None)
        if nov_idx is None and len(headers) >= 12:
            nov_idx = 11

        cat1_idx    = next((i for i, c in enumerate(headers) if 'катег' in c and ('1' in c or 'перв' in c or 'ур' in c)), None)
        status_idx  = next((i for i, c in enumerate(headers) if 'статус' in c), None)

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS wb_assortment (
            supplier_article TEXT PRIMARY KEY, name_ru TEXT, name_cn TEXT, manufacturer TEXT,
            is_new BOOLEAN DEFAULT FALSE, category_1 TEXT, matrix_status TEXT, last_sync TIMESTAMP
        );
        """
        upsert_sql = text("""
            INSERT INTO wb_assortment (supplier_article, name_ru, name_cn, manufacturer, is_new, category_1, matrix_status, last_sync)
            VALUES (:art, :n_ru, :n_cn, :manuf, :is_new, :cat1, :mstatus, CURRENT_TIMESTAMP)
            ON CONFLICT (supplier_article)
            DO UPDATE SET name_ru = EXCLUDED.name_ru, name_cn = EXCLUDED.name_cn,
                manufacturer = EXCLUDED.manufacturer, is_new = EXCLUDED.is_new,
                category_1 = EXCLUDED.category_1, matrix_status = EXCLUDED.matrix_status,
                last_sync = EXCLUDED.last_sync
        """)
        
        try:
            with engine_local.begin() as conn:
                conn.execute(text(create_table_sql))
            try:
                with engine_local.begin() as conn:
                    conn.execute(text("ALTER TABLE wb_assortment ADD COLUMN IF NOT EXISTS is_new BOOLEAN DEFAULT FALSE;"))
            except: pass

            with engine_local.begin() as conn:
                for row in raw_data[1:]:
                    art = str(row[art_idx]).strip() if art_idx < len(row) else ''
                    if not art or art.lower() in ['nan', 'none', '']: continue

                    n_ru  = str(row[n_ru_idx]).strip()  if n_ru_idx  is not None and n_ru_idx  < len(row) else ''
                    n_cn  = str(row[n_cn_idx]).strip()  if n_cn_idx  is not None and n_cn_idx  < len(row) else ''
                    manuf = str(row[manuf_idx]).strip()  if manuf_idx is not None and manuf_idx < len(row) else ''
                    cat1  = str(row[cat1_idx]).strip()   if cat1_idx  is not None and cat1_idx  < len(row) else None
                    mstat = str(row[status_idx]).strip() if status_idx is not None and status_idx < len(row) else None

                    is_new_val = False
                    if nov_idx is not None and nov_idx < len(row):
                        val = str(row[nov_idx]).strip().lower()
                        if val in ['да', '1', '+', 'true', 'yes', 'новинка', 'new', 'v', 'x', 'х', 'ok', 'ок', 'истина']:
                            is_new_val = True

                    conn.execute(upsert_sql, {
                        "art": art, "n_ru": n_ru, "n_cn": n_cn, "manuf": manuf,
                        "is_new": is_new_val, "cat1": cat1 or None, "mstatus": mstat or None,
                    })
            log_to_system("Синхронизатор Матрицы", "SUCCESS", "Матрица SKU успешно обновлена из Google Sheets")
        except Exception as e:
            print(f"❌ Ошибка матрицы: {e}")
    except Exception as e: 
        print(f"🚨 Ошибка синхронизации матрицы: {e}")
        log_to_system("Синхронизатор Матрицы", "FAIL", f"Ошибка: {e}")

def sync_cogs_matrix(full_sync: bool = False):
    """
    Бережная синхронизация себестоимости без удаления данных.
    По умолчанию (full_sync=False) обрабатывает ТОЛЬКО текущий 2026 год.
    """
    sheet_modes = ['2026'] if not full_sync else ['2025', '2026']
    print(f"⏳ Безопасный апдейт COGS из Google Sheets. Обработка листов: {sheet_modes}...")
    
    try:
        client = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS)
        doc = client.open_by_key(SPREADSHEET_ID_FINANCES)
        
        parsed_data = []
        
        # 🔥 УМНЫЙ SQL: Обновляет только пустые ячейки или новые данные, сохраняя историю
        upsert_sql = text("""
            INSERT INTO wb_cogs (supplier_article, invoice_num, cost_value, shipment_date, container_num, last_sync)
            VALUES (:art, :inv, :cost, :s_date, :c_num, CURRENT_TIMESTAMP)
            ON CONFLICT (supplier_article, invoice_num) 
            DO UPDATE SET 
                cost_value = COALESCE(EXCLUDED.cost_value, wb_cogs.cost_value),
                shipment_date = COALESCE(EXCLUDED.shipment_date, wb_cogs.shipment_date),
                container_num = COALESCE(EXCLUDED.container_num, wb_cogs.container_num),
                last_sync = CURRENT_TIMESTAMP;
        """)

        for sheet_name in sheet_modes:
            try:
                sheet = doc.worksheet(sheet_name)
                raw_data = sheet.get_all_values()
                
                if len(raw_data) < 3: continue
                last_valid_invoice = "Не указан"
                last_valid_container = None   # протяжка контейнера вниз по блоку инвойса
                last_valid_date = None        # протяжка даты отгрузки вниз по блоку инвойса

                for row in raw_data[2:]:
                    if len(row) < 5: continue

                    raw_date = str(row[0]).strip()
                    cont_num = str(row[1]).strip()
                    art_raw  = str(row[2]).strip()
                    inv      = str(row[3]).strip()
                    cost_str = str(row[4]).strip()

                    if not art_raw or art_raw.lower() in ['nan', 'none', '']: continue

                    # Новый явный инвойс = граница блока: сбрасываем протяжку контейнера/даты,
                    # чтобы значения предыдущего инвойса не перетекали в следующий.
                    if inv and inv.lower() not in ['nan', 'none', '']:
                        if inv != last_valid_invoice:
                            last_valid_container = None
                            last_valid_date = None
                        last_valid_invoice = inv
                    else:
                        inv = last_valid_invoice

                    # Контейнер: в Google-таблице заполнен только в первой строке блока инвойса.
                    # Протягиваем его вниз по пустым строкам блока (как инвойс).
                    if cont_num.lower() in ['nan', 'none', '', '-']:
                        cont_num = last_valid_container
                    else:
                        last_valid_container = cont_num
                        
                    try:
                        cost_clean = cost_str.replace(',', '.')
                        cost_clean = "".join(cost_clean.split())
                        cost_clean = "".join(c for c in cost_clean if c.isdigit() or c == '.')
                        cost_val = float(cost_clean) if cost_clean else None
                    except: 
                        cost_val = None
                        
                    s_date_val = None
                    if raw_date and raw_date.lower() not in ['nan', 'none', '']:
                        try:
                            if '/' in raw_date:
                                res_dt = pd.to_datetime(raw_date, format='%m/%d/%Y', errors='coerce')
                            else:
                                res_dt = pd.to_datetime(raw_date, dayfirst=True, errors='coerce')
                            
                            if pd.notna(res_dt):
                                s_date_val = res_dt.strftime("%Y-%m-%d")
                        except:
                            s_date_val = None

                    # Дата отгрузки: протягиваем вниз по блоку инвойса так же, как контейнер.
                    if s_date_val:
                        last_valid_date = s_date_val
                    else:
                        s_date_val = last_valid_date

                    # Записываем строку, если есть артикул и хотя бы один значимый параметр для обновления
                    if art_raw and (cost_val or s_date_val or cont_num):
                        articles = [a.strip() for a in art_raw.split('_') if a.strip()]
                        for art in articles:
                            parsed_data.append({
                                "art": art, "inv": inv, "cost": cost_val, 
                                "s_date": s_date_val, "c_num": cont_num
                            })
            except Exception as e:
                print(f"❌ Проблема с листом {sheet_name}: {e}")

        if not parsed_data:
            print("ℹ️ Нет новых данных для обновления.")
            return

        try:
            with engine_local.begin() as conn:
                for item in parsed_data:
                    conn.execute(upsert_sql, item)
            print(f"✅ Данные COGS успешно обновлены (Обработано строк: {len(parsed_data)})")
        except Exception as e:
            print(f"❌ Ошибка обновления COGS: {e}")
            
    except Exception as e: 
        print(f"Ошибка: {e}")


def sync_factories():
    """
    Синхронизация заводов без удаления старых данных.
    Добавляет новые или обновляет изменившиеся.
    """
    print("⏳ Безопасная синхронизация списка Заводов из Google Sheets...")
    try:
        client = gspread.service_account(filename=PATH_TO_GOOGLE_CREDS)
        sheet = client.open_by_key(SPREADSHEET_ID_FINANCES).worksheet("Завод")
        raw_data = sheet.get_all_values()
        
        if len(raw_data) < 2: return
        
        header_row_idx = 0
        for i, row in enumerate(raw_data[:5]):
            row_lower = [str(c).strip().lower() for c in row]
            if any('завод' in cell for cell in row_lower) or any('инвойс' in cell for cell in row_lower):
                header_row_idx = i
                break
        
        parsed_data = []
        for row in raw_data[header_row_idx + 1:]:
            if len(row) < 3: continue
            inv = str(row[0]).strip()
            art = str(row[1]).strip()
            fact = str(row[2]).strip()
            
            if inv and art and fact:
                parsed_data.append({"inv": inv, "art": art, "fact": fact})
                
        upsert_sql = text("""
            INSERT INTO wb_factories (invoice_num, supplier_article, factory_name)
            VALUES (:inv, :art, :fact)
            ON CONFLICT (invoice_num, supplier_article) 
            DO UPDATE SET factory_name = EXCLUDED.factory_name;
        """)
        
        try:
            with engine_local.begin() as conn:
                for item in parsed_data:
                    conn.execute(upsert_sql, item)
            print(f"✅ Справочник заводов успешно обновлен (Строк: {len(parsed_data)})")
        except Exception as e:
            print(f"❌ Ошибка заводов: {e}")
    except Exception as e:
        print(f"🚨 Ошибка синхронизации заводов: {e}")

# =========================================================================
# 4. 🔥 ЗАГРУЗКА RAW-ДАННЫХ РЕЙТИНГОВ С 01.01.2026
# =========================================================================
def fetch_and_save_ratings_raw():
    print("⏳ Запуск RAW-синхронизации рейтингов (Data Lake)...")
    log_to_system("Синхронизатор Рейтингов", "INFO", "Старт сбора аналитики оценок по номенклатурам")
    mapping = {}
    try:
        with engine_local.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT nm_id, supplier_article FROM wb_logistics WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL")).fetchall()
            mapping = {int(r[0]): str(r[1]).strip() for r in result}
    except Exception as e: return

    if not mapping: return

    url = "https://seller-analytics-api.wildberries.ru/api/analytics/v1/item-rating"
    nm_ids_list = list(mapping.keys())
    
    start_date = datetime(2026, 1, 1)
    end_date = datetime.now() - timedelta(days=1)
    days_to_fetch = [(start_date + timedelta(days=x)).strftime("%Y-%m-%d") for x in range((end_date - start_date).days + 1)]
    
    total_days_processed = 0
    for target_date in days_to_fetch:
        try:
            with engine_local.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM wb_ratings_raw WHERE sys_date = '{target_date}'")).scalar()
                days_ago = (datetime.now().date() - datetime.strptime(target_date, "%Y-%m-%d").date()).days
                if count > 0 and days_ago > 3: continue 
        except Exception: pass
            
        print(f"📅 Сбор RAW-данных за {target_date}...")
        all_raw_cards = []

        for i in range(0, len(nm_ids_list), 50):
            chunk = nm_ids_list[i:i + 50]
            payload = {
                "currentPeriod": {"start": target_date, "end": target_date},
                "nmIds": chunk, "orderBy": {"field": "feedbackCount", "mode": "desc"},
                "isNotIncludeNMsWithoutSales": False, "limit": 100, "offset": 0
            }
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=30)
                if res.status_code == 429:
                    time.sleep(30); res = requests.post(url, headers=headers, json=payload, timeout=30)
                if res.status_code == 200:
                    cards = res.json().get("data", {}).get("cards", [])
                    all_raw_cards.extend(cards)
                time.sleep(22) 
            except Exception: time.sleep(10)

        if not all_raw_cards: continue

        df_raw = pd.json_normalize(all_raw_cards)
        df_raw.columns = [str(c).replace(".", "_") for c in df_raw.columns]
        df_raw['sys_date'] = target_date
        
        def get_art(row):
            nm = row.get('nmId') or row.get('nm_id')
            try: return mapping.get(int(nm), "Unknown")
            except: return "Unknown"
        df_raw['sys_article'] = df_raw.apply(get_art, axis=1)

        try:
            with engine_local.begin() as conn:
                try: conn.execute(text(f"DELETE FROM wb_ratings_raw WHERE sys_date = '{target_date}'"))
                except: pass
            df_raw.to_sql('wb_ratings_raw', engine_local, if_exists='append', index=False)
        except Exception as e:
            print(f"   ❌ Ошибка записи рейтингов: {e}")
        total_days_processed += 1
        
    log_to_system("Синхронизатор Рейтингов", "SUCCESS", f"Синхронизировано {total_days_processed} дней с оценками.")


# =========================================================================
# 5. НАКОПИТЕЛЬНЫЕ ИТОГИ РЕЙТИНГОВ ВБ (all-time totals)
# Запрашивает API с широким диапазоном дат → получает ВСЕ отзывы за всё время.
# Сохраняет в wb_ratings_totals (upsert по артикулу).
# =========================================================================
def sync_wb_ratings_totals():
    """
    Шаг 1: item-rating API (12 мес.) → средний рейтинг + разбивка по звёздам.
    Шаг 2: feedbacks API (per nm_id) → countUnanswered + countArchive = total all-time.
    """
    print("⏳ Синхронизация накопительных итогов рейтингов ВБ...")
    log_to_system("Итоги Рейтингов ВБ", "INFO", "Старт")

    mapping = {}
    try:
        with engine_local.connect() as conn:
            result = conn.execute(text(
                "SELECT DISTINCT nm_id, supplier_article FROM wb_logistics "
                "WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL"
            )).fetchall()
            mapping = {int(r[0]): str(r[1]).strip() for r in result}
    except Exception as e:
        log_to_system("Итоги Рейтингов ВБ", "ERROR", str(e))
        return

    if not mapping:
        return

    nm_ids_list = list(mapping.keys())

    # ── Шаг 1: item-rating за последние 12 месяцев ────────────────────────────
    rating_url = "https://seller-analytics-api.wildberries.ru/api/analytics/v1/item-rating"
    end_date   = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=364)).strftime("%Y-%m-%d")
    cards_by_nm = {}

    for i in range(0, len(nm_ids_list), 50):
        chunk = nm_ids_list[i:i + 50]
        payload = {
            "currentPeriod": {"start": start_date, "end": end_date},
            "nmIds": chunk,
            "orderBy": {"field": "feedbackCount", "mode": "desc"},
            "isNotIncludeNMsWithoutSales": False, "limit": 100, "offset": 0
        }
        try:
            res = requests.post(rating_url, headers=headers, json=payload, timeout=30)
            if res.status_code == 429:
                time.sleep(60)
                res = requests.post(rating_url, headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                for card in res.json().get("data", {}).get("cards", []):
                    nm = card.get("nmId") or card.get("nm_id")
                    if nm:
                        cards_by_nm[int(nm)] = card
            time.sleep(22)
        except Exception as ex:
            print(f"  item-rating chunk error: {ex}")
            time.sleep(10)

    if not cards_by_nm:
        log_to_system("Итоги Рейтингов ВБ", "WARNING", "Нет данных item-rating")
        return

    # ── Шаг 2: feedbacks API — all-time total per nm_id ──────────────────────
    fb_url     = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks"
    totals_by_nm = {}
    for nm in nm_ids_list:
        try:
            r = requests.get(
                fb_url,
                headers=headers,
                params={"isAnswered": True, "nmId": nm, "take": 1, "skip": 0},
                timeout=15,
            )
            if r.status_code == 200:
                d = r.json().get("data") or {}
                totals_by_nm[nm] = (d.get("countUnanswered") or 0) + (d.get("countArchive") or 0)
            time.sleep(0.2)
        except Exception:
            time.sleep(1)

    # ── Шаг 3: upsert ─────────────────────────────────────────────────────────
    def _val(card, key):
        return (card.get(key) or {}).get("current") or 0

    saved = 0
    with engine_local.begin() as conn:
        for nm, card in cards_by_nm.items():
            article = mapping.get(nm, "Unknown")
            if article in ("Unknown", "nan", ""):
                continue

            # all-time total: из feedbacks API; запасной вариант — 12-мес. из item-rating
            total_reviews = totals_by_nm.get(nm) or _val(card, "feedbackCount")

            conn.execute(text("""
                INSERT INTO wb_ratings_totals
                    (supplier_article, nm_id, average_rating, review_count,
                     stars_5, stars_4, stars_3, stars_2, stars_1, refreshed_at)
                VALUES
                    (:article, :nm, :rating, :count,
                     :s5, :s4, :s3, :s2, :s1, NOW())
                ON CONFLICT (supplier_article) DO UPDATE SET
                    nm_id          = EXCLUDED.nm_id,
                    average_rating = EXCLUDED.average_rating,
                    review_count   = EXCLUDED.review_count,
                    stars_5 = EXCLUDED.stars_5, stars_4 = EXCLUDED.stars_4,
                    stars_3 = EXCLUDED.stars_3, stars_2 = EXCLUDED.stars_2,
                    stars_1 = EXCLUDED.stars_1,
                    refreshed_at   = NOW()
            """), {
                "article": article,
                "nm":      nm,
                "rating":  _val(card, "feedbackRating"),
                "count":   total_reviews,
                "s5":      _val(card, "fiveStar"),
                "s4":      _val(card, "fourStar"),
                "s3":      _val(card, "threeStar"),
                "s2":      _val(card, "twoStar"),
                "s1":      _val(card, "oneStar"),
            })
            saved += 1

    log_to_system("Итоги Рейтингов ВБ", "SUCCESS", f"{saved} артикулов обновлено")
    print(f"✅ Итоги рейтингов ВБ: {saved} артикулов")


# =========================================================================
# ПОРЯДОК ЗАПУСКА
# =========================================================================
if __name__ == "__main__":
    import subprocess
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    if mode == "logistics":
        print("💰 СИНК ЛОГИСТИКИ ВБ (продажи + заказы)")
        log_to_system("Воркер Логистика", "INFO", "Синк продаж/заказов WB")
        try:
            fetch_and_save_logistics("https://statistics-api.wildberries.ru/api/v1/supplier/sales", "SALE")
            fetch_and_save_orders("https://statistics-api.wildberries.ru/api/v1/supplier/orders")
            log_to_system("Воркер Логистика", "SUCCESS", "Логистика обновлена")
        except Exception as e:
            log_to_system("Воркер Логистика", "FAIL", str(e))
            sys.exit(1)
        sys.exit(0)

    if mode == "logistics_backfill":
        # Ретро-синк: качаем продажи с заданной даты для заполнения finish_price
        from_date = sys.argv[2] if len(sys.argv) > 2 else "2025-10-01T00:00:00"
        print(f"🔄 РЕТРО-СИНК ПРОДАЖ ВБ начиная с {from_date}")
        url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
        current_from = from_date
        request_count = 0
        total_rows = 0
        while True:
            try:
                response = requests.get(url, headers=headers, params={"dateFrom": current_from, "flag": 0}, timeout=60)
                request_count += 1
                if response.status_code == 429:
                    time.sleep(65); continue
                res = response.json()
                if not res or not isinstance(res, list):
                    break
                chunk = [{"srid": safe_str(i.get("srid") or i.get("saleID")), "doc_type": "SALE", "dt": i.get("date"),
                          "supplier_article": safe_str(i.get("supplierArticle") or i.get("saName")), "nm_id": i.get("nmId"),
                          "warehouse_name": i.get("warehouseName"), "category": i.get("category"), "subject": i.get("subject"),
                          "brand": i.get("brand"), "is_cancel": i.get("isCancel", False), "last_change_date": i.get("lastChangeDate"),
                          "income_id": i.get("incomeID"),
                          "finish_price": next((float(v) for v in [i.get("finishedPrice"), i.get("priceWithDisc"), i.get("priceWithDiscount"), i.get("finishPrice")] if v is not None and float(v) > 0), None)
                          } for i in res if safe_str(i.get("srid") or i.get("saleID"))]
                df = pd.DataFrame(chunk).drop_duplicates(subset=['srid'], keep='last')
                df['last_sync'] = pd.Timestamp.now()
                sql = """INSERT INTO wb_logistics (srid, doc_type, dt, supplier_article, nm_id, warehouse_name, category, subject, brand, is_cancel, last_change_date, income_id, finish_price, last_sync)
                         SELECT DISTINCT ON (srid) srid, doc_type, CAST(dt AS TIMESTAMP), supplier_article, nm_id, warehouse_name, category, subject, brand, CAST(is_cancel AS BOOLEAN), CAST(last_change_date AS TIMESTAMP), income_id, CAST(NULLIF(finish_price::text,'None') AS NUMERIC), CAST(last_sync AS TIMESTAMP) FROM temp_wb_logistics ORDER BY srid, last_change_date DESC
                         ON CONFLICT (srid) DO UPDATE SET finish_price=COALESCE(EXCLUDED.finish_price, wb_logistics.finish_price), last_change_date=EXCLUDED.last_change_date, last_sync=EXCLUDED.last_sync;"""
                sync_chunk(df, 'wb_logistics', sql)
                total_rows += len(df)
                last_change = res[-1].get("lastChangeDate")
                print(f"  → {total_rows} строк, последняя дата: {last_change}")
                if not last_change or last_change == current_from:
                    break
                current_from = last_change
                time.sleep(62 if request_count >= 9 else 3)
            except Exception as e:
                print(f"❌ Ошибка ретро-синка: {e}")
                time.sleep(30)
        print(f"✅ Ретро-синк завершён. Обработано {total_rows} строк.")
        sys.exit(0)

    if mode == "ratings":
        # Только рейтинги — запускается в 10:00, когда отчёт WB уже обновлён
        print("⭐ СИНК РЕЙТИНГОВ ВБ (10:00)")
        log_to_system("Воркер Рейтинги", "INFO", "Синк рейтингов WB")
        try:
            sync_wb_ratings_totals()
            fetch_and_save_ratings_raw()
            log_to_system("Воркер Рейтинги", "SUCCESS", "Рейтинги обновлены")
        except Exception as e:
            log_to_system("Воркер Рейтинги", "FAIL", str(e))
            sys.exit(1)
        sys.exit(0)

    # mode == "full" — полный ночной цикл в 01:00
    print("🚀 СТАРТ ЧИСТОГО ВОРКЕРА")
    log_to_system("Единый Воркер", "INFO", "Запуск полного ночного цикла автоматизации")
    try:
        sync_invoices()
        sync_assortment_matrix()
        sync_cogs_matrix()
        sync_factories()
        sync_claims_to_db(fetch_wb_claims())
        fetch_feedbacks_archive()
        fetch_new_feedbacks()
        fetch_questions()
        fetch_and_save_logistics("https://statistics-api.wildberries.ru/api/v1/supplier/sales", "SALE")
        fetch_and_save_orders("https://statistics-api.wildberries.ru/api/v1/supplier/orders")
        print("🏁 СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА")
        log_to_system("Единый Воркер", "SUCCESS", "Синхронизация данных по всем API выполнена успешно")
    except Exception as e:
        print(f"💥 ОШИБКА: {e}")
        log_to_system("Единый Воркер", "FAIL", f"Критический сбой выполнения воркера: {e}")
        sys.exit(1)

    # Автоматическое ИИ-тегирование WB-возвратов после синхронизации
    print("🤖 Запуск ИИ-тегирования WB-претензий...")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(
            [sys.executable, os.path.join(script_dir, "ai_tagger.py"), "wb"],
            timeout=3600
        )
    except Exception as e:
        log_to_system("AI-тегировщик WB", "ERROR", f"Сбой запуска: {e}")