import streamlit as st
import asyncio
import aiohttp
import gspread
import json
import re
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
import io
import zipfile
import urllib.request
import base64
import streamlit.components.v1 as components
import time

st.set_page_config(page_title="CX AI Enterprise", layout="wide")

# --- ГЛОБАЛЬНЫЙ ТРЕКЕР СМЕНЫ СТРАНИЦ ---
page = st.sidebar.radio("Навигация", ["🤖 Робот-Загрузчик", "🔬 ИИ Тегирование", "📝 Модерация", "🧠 Обучение ИИ", "📊 Отчет производства", "📜 Системный Журнал"])

if st.session_state.get('current_tab') != page:
    st.session_state.current_tab = page
    # При любом переключении меню полностью убиваем память графика и окон
    st.session_state.matrix_key = int(time.time())
    st.session_state.show_detail_trigger = None
    st.session_state.last_click_id = None
# ---------------------------------------

# Инициализация ключей для сброса загрузчиков после успешной обработки
if 'claims_key' not in st.session_state: st.session_state.claims_key = 0

try:
    YANDEX_API_KEY = st.secrets["YANDEX_API_KEY"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
    SPREADSHEET_ID_MAIN = st.secrets["SPREADSHEET_ID_MAIN"]
    SPREADSHEET_ID_INVOICES = st.secrets["SPREADSHEET_ID_INVOICES"]
    GOOGLE_CREDS = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"❌ Ошибка в Secrets: {e}")
    st.stop()

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

# --- НОВЫЙ СЛОВАРЬ НАЗВАНИЙ КОЛОНОК (По утвержденной таблице) ---
COLUMN_NAMES_RU = {
    'supplierArticle': 'Артикул продавца',
    'nmId': 'Артикул WB',
    'dt': 'Дата и время оформления заявки на возврат',
    'user_comment': 'Комментарий покупателя',
    'claim_type': 'Источник заявки',
    'status': 'Решение по возврату покупателю',
    'status_ex': 'Статус товара',
    'dt_update': 'Дата и время рассмотрения заявки',
    'price': 'Фактическая цена с учетом всех скидок (к взиманию с покупателя)',
    'incomeID': 'Номер поставки',
    'srid': 'SRID',
    'wb_comment': 'Ответ покупателю',
    'imt_name': 'Название товара',
    'order_dt': 'Дата заказа',
    'photos': 'Фотографии',
    'video_paths': 'Видео',
    
    # Дополнительные поля (если будут)
    'actions': 'Варианты ответа продавца на заявку',
    'currency_code': 'Код валюты цены',
    'origin_id_info': 'Результат сверки IMEI для возврата через ПВЗ',
    'delivery_dt': 'Дата и время получения заказа покупателем',
    'lastChangeDate': 'Дата и время обновления информации в сервисе',
    'warehouseName': 'Склад отгрузки',
    'warehouseType': 'Тип склада хранения товаров',
    'countryName': 'Страна',
    'oblastOkrugName': 'Округ',
    'regionName': 'Регион',
    'barcode': 'Баркод',
    'category': 'Категория',
    'subject': 'Предмет',
    'brand': 'Бренд',
    'techSize': 'Размер товара',
    'isSupply': 'Договор поставки',
    'isRealization': 'Договор реализации',
    'totalPrice': 'Цена без скидок',
    'discountPercent': 'Скидка продавца, %',
    'spp': 'Скидка WB, %',
    'paymentSaleAmount': 'Скидка за оплату WB Кошельком, ₽',
    'forPay': 'К перечислению продавцу',
    'finishedPrice': 'Фактическая цена с учётом всех скидок',
    'priceWithDisc': 'Цена со скидкой продавца',
    'saleID': 'Уникальный ID продажи/возврата',
    'sticker': 'ID стикера',
    'gNumber': 'ID корзины покупателя'
}

# --- СЛОВАРИ СТАТУСОВ ---
CLAIM_TYPES = {
    '1': 'Портал покупателей',
    '3': 'Чат'
}

STATUSES = {
    '0': 'На рассмотрении',
    '1': 'Отказ',
    '2': 'Одобрено'
}

STATUS_EX = {
    '0': 'Заявка на рассмотрении',
    '1': 'Товар остается у покупателя (Заявка отклонена)',
    '2': 'Товар в утиль (Сдан на ПВЗ)',
    '5': 'Товар остается у покупателя (Заявка одобрена)',
    '8': 'В реализацию после проверки WB',
    '10': 'Возврат продавцу'
}

# CSS Стили (включая зум фото на 30% при наведении)
st.markdown("""
    <style>
    [data-testid="stDataFrame"] { font-size: 11px !important; }
    .detail-card { border: 1px solid #ddd; padding: 15px; border-radius: 8px; margin-bottom: 15px; background-color: #fcfcfc; }
    
    .media-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 10px; }
    
    /* АГРЕССИВНЫЙ СБРОС ВШИТЫХ СТИЛЕЙ СТРИМЛИТА */
    .media-row a { 
        background: transparent !important; 
        padding: 0 !important; 
        margin: 0 !important; 
        border: none !important;
        display: inline-flex; /* Убивает скрытые переносы и отступы ссылок */
    }
    
    .photo-zoom { 
        width: 140px !important; 
        height: 140px !important; 
        object-fit: cover !important; 
        border-radius: 8px !important; 
        transition: transform 0.3s ease, border-radius 0.3s ease; 
        cursor: pointer; 
        
        /* Убиваем фоны и внутренние отступы (padding), которые давали белую рамку */
        border: none !important; 
        outline: none !important; 
        background: transparent !important; 
        background-color: transparent !important;
        padding: 0 !important; 
        margin: 0 !important;
    }
    
    .photo-zoom:hover { 
        transform: scale(4); 
        z-index: 9999; 
        position: relative; 
        
        /* Полностью убираем закругления при зуме — картинка станет ровным прямоугольником */
        border-radius: 0px !important; 
        box-shadow: 0 20px 50px rgba(0,0,0,0.8) !important; 
    }
    
    .video-link-btn {
        display: inline-block; padding: 8px 14px; background-color: #2563eb; 
        color: white !important; border-radius: 6px; text-decoration: none; 
        font-weight: bold; font-size: 13px; transition: background-color 0.2s;
    }
    .video-link-btn:hover { background-color: #1d4ed8; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. ВСЕЯДНАЯ ЧИТАЛКА И БАЗА ДАННЫХ
# ==========================================
def safe_read(file_obj):
    bytes_data = file_obj.getvalue()
    name = file_obj.name.lower()
    
    if name.endswith('.xlsx') or name.endswith('.xls'):
        try:
            engine = 'calamine' if name.endswith('.xlsx') else 'xlrd'
            return pd.read_excel(io.BytesIO(bytes_data), engine=engine)
        except Exception:
            try:
                return pd.read_excel(io.BytesIO(bytes_data), engine='openpyxl')
            except Exception:
                try:
                    return pd.read_html(io.BytesIO(bytes_data))[0]
                except Exception: pass

    encodings = ['utf-8-sig', 'utf-8', 'windows-1251', 'utf-16']
    separators = [';', '\t', ',']
    
    for enc in encodings:
        for sep in separators:
            try:
                text_data = bytes_data.decode(enc)
                df = pd.read_csv(io.StringIO(text_data), sep=sep, engine='python', on_bad_lines='skip')
                if len(df.columns) > 1: return df
            except Exception: continue
            
    st.error(f"⚠️ Не удалось прочитать файл {file_obj.name}. Формат не распознан.")
    return pd.DataFrame()

@st.cache_resource
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    return gspread.authorize(Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes))

def get_memory_records():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
        return sheet.get_all_records()
    except:
        return []

def add_system_log(action, status, details=""):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN)
        try:
            ws_log = sheet.worksheet("Логи")
        except gspread.exceptions.WorksheetNotFound:
            ws_log = sheet.add_worksheet(title="Логи", rows="1000", cols="4")
            ws_log.append_row(["Дата и Время", "Действие", "Статус", "Детали"])

        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        ws_log.append_row([now, action, status, details])
    except Exception as e:
        import streamlit as st
        # Теперь вы точно увидите, почему лог не пишется!
        st.error(f"🚨 ОШИБКА ЗАПИСИ ЛОГА В ГУГЛ ТАБЛИЦУ: {e}")

# ==========================================
# 3. ОБРАБОТКА ДАННЫХ И API WILDBERRIES
# ==========================================
import requests
import time
from datetime import datetime, timedelta
import pandas as pd

def fetch_wb_api(url, params=None):
    """Функция запроса с обработкой лимитов и ошибок"""
    wb_key = str(st.secrets.get("WB_API_KEY", "")).strip()
    if not wb_key or wb_key == "None": return None

    headers = {
        "Authorization": wb_key,
        "Content-Type": "application/json"
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                st.warning("⚠️ Сработал лимит WB. Пауза 5 секунд...")
                time.sleep(5)
                continue
            elif response.status_code == 404:
                return {"error_404": True}
            elif response.status_code == 401:
                return {"error_401": True, "text": "Ключ невалиден или нет прав на этот раздел"}
            else:
                return {"error": response.status_code, "text": response.text}
        except Exception as e:
            time.sleep(2)
    return None

def process_wb_api_sync(existing_gs_records):
    report = []
    wb_key = st.secrets.get("WB_API_KEY")
    if not wb_key:
        report.append("❌ Ошибка: Ключ WB_API_KEY не найден в Secrets.")
        return pd.DataFrame(), pd.DataFrame(), 0, 0, report

    TARGET_COLUMNS = [
        'Дата и время оформления заявки на возврат', 'Артикул продавца', 'Артикул WB', 'Комментарий покупателя', 
        'SRID', 'Источник заявки', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', 
        'Обоснование', 'Корректировка', 'Аудит', 'Комментарий', 'Статус', 'Решение по возврату покупателю', 
        'Статус товара', 'Ответ покупателю', 'Название товара', 'Дата заказа', 'Дата и время рассмотрения заявки', 
        'Фотографии', 'Видео', 'Варианты ответа продавца на заявку', 'Фактическая цена с учетом всех скидок (к взиманию с покупателя)', 
        'Код валюты цены', 'Результат сверки IMEI для возврата через ПВЗ', 'Дата и время получения заказа покупателем', 
        'Дата и время обновления информации в сервисе', 'Склад отгрузки', 'Тип склада хранения товаров', 'Страна', 
        'Округ', 'Регион', 'Баркод', 'Категория', 'Предмет', 'Бренд', 'Размер товара', 'Номер поставки', 
        'Договор поставки', 'Договор реализации', 'Цена без скидок', 'Скидка продавца, %', 'Скидка WB, %', 
        'Скидка за оплату WB Кошельком, ₽', 'К перечислению продавцу', 'Фактическая цена с учётом всех скидок', 
        'Цена со скидкой продавца', 'Уникальный ID продажи/возврата', 'ID стикера', 'ID корзины покупателя'
    ]
    
    MANUAL_COLS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', 'Обоснование', 'Корректировка', 'Аудит', 'Комментарий']

    # 1. ТЯНЕМ ЯДРО - CLAIMS
    st.info("⏳ Запрашиваем Претензии (Активные и Архивные)...")
    claims_url = "https://returns-api.wildberries.ru/api/v1/claims" 
    claims_data = []
    
    for archive_status in ["false", "true"]:
        params = {"limit": 100, "offset": 0, "is_archive": archive_status} 
        while True:
            res_c = fetch_wb_api(claims_url, params=params)
            if res_c and isinstance(res_c, dict):
                if res_c.get("error_401") or res_c.get("error_404") or res_c.get("error"):
                    report.append("❌ Ошибка API Claims.")
                    return pd.DataFrame(), pd.DataFrame(), 0, 0, report
                batch = res_c.get("claims", [])
                if not batch: break 
                claims_data.extend(batch)
                if len(batch) < params["limit"]: break
                params["offset"] += params["limit"]
                time.sleep(3.5) 
            else:
                break

    if not claims_data:
        report.append("⚠️ За этот период новых претензий не найдено.")
        return pd.DataFrame(), pd.DataFrame(), 0, 0, report
        
    df_c = pd.DataFrame(claims_data)
    comment_col = 'user_comment' if 'user_comment' in df_c.columns else 'comment'
    if comment_col in df_c.columns:
        df_c = df_c[df_c[comment_col].notna() & (df_c[comment_col].astype(str).str.strip() != '')]
    report.append(f"📥 Претензии: Скачано {len(df_c)} заявок с текстом.")

    # 2. ТЯНЕМ ЛОГИСТИКУ: ПРОДАЖИ + ЗАКАЗЫ (180 дней)
    st.info("⏳ Запрашиваем логистику (Продажи + Заказы)...")
    date_from = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00")
    
    time.sleep(2)
    sales_url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
    sales_raw = fetch_wb_api(sales_url, params={"dateFrom": date_from})
    df_sales = pd.DataFrame(sales_raw) if isinstance(sales_raw, list) else pd.DataFrame()

    time.sleep(2)
    orders_url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
    orders_raw = fetch_wb_api(orders_url, params={"dateFrom": date_from})
    df_orders = pd.DataFrame(orders_raw) if isinstance(orders_raw, list) else pd.DataFrame()

    # --- ФОРМИРУЕМ АГРЕГИРОВАННУЮ БАЗУ ЗАКАЗОВ (ДЛЯ ДАШБОРДА) ---
    df_orders_grouped = pd.DataFrame()
    if not df_orders.empty:
        df_orders['Дата'] = pd.to_datetime(df_orders['date']).dt.strftime('%d.%m.%Y')
        df_orders['Отмена'] = df_orders['isCancel'].fillna(False).astype(bool).astype(int)
        
        df_orders_grouped = df_orders.groupby(['Дата', 'nmId', 'category', 'subject', 'brand']).agg(
            Всего_заказов=('srid', 'count'),
            Отмен=('Отмена', 'sum')
        ).reset_index()
        
        df_orders_grouped['Чистые_заказы'] = df_orders_grouped['Всего_заказов'] - df_orders_grouped['Отмен']
        df_orders_grouped.rename(columns={
            'nmId': 'Артикул WB', 'category': 'Категория', 
            'subject': 'Предмет', 'brand': 'Бренд'
        }, inplace=True)
        report.append(f"📊 Заказы: Сформирована сводная таблица по дням ({len(df_orders_grouped)} строк).")
    # -------------------------------------------------------------

    # Обогащаем претензии логистикой
    if not df_sales.empty or not df_orders.empty:
        df_logistics = pd.concat([df_sales, df_orders], ignore_index=True)
        df_logistics = df_logistics.drop_duplicates(subset=['srid'], keep='last')
        df_final = pd.merge(df_c, df_logistics, on='srid', how='left', suffixes=('', '_drop'))
    else:
        df_final = df_c

    # 3. МАППИНГ И ОБРАБОТКА ПРЕТЕНЗИЙ
    if 'nmId' not in df_final.columns and 'nm_id' in df_final.columns: df_final.rename(columns={'nm_id': 'nmId'}, inplace=True)
    if 'claim_type' in df_final.columns: df_final['claim_type'] = df_final['claim_type'].astype(str).map(CLAIM_TYPES).fillna(df_final['claim_type'])
    if 'status' in df_final.columns: df_final['status'] = df_final['status'].astype(str).map(STATUSES).fillna(df_final['status'])
    if 'status_ex' in df_final.columns: df_final['status_ex'] = df_final['status_ex'].astype(str).map(STATUS_EX).fillna(df_final['status_ex'])

    temp_df = pd.DataFrame()
    for col in df_final.columns:
        if col not in ['id', 'date'] and not col.endswith('_drop'):
            target_name = 'Номер поставки' if col == 'incomeID' else COLUMN_NAMES_RU.get(col, col)
            temp_df[target_name] = df_final[col]
            
    if 'Дата и время оформления заявки на возврат' in temp_df.columns:
        temp_df['Дата и время оформления заявки на возврат'] = pd.to_datetime(temp_df['Дата и время оформления заявки на возврат'], errors='coerce').dt.strftime('%d.%m.%Y')

    api_df = pd.DataFrame(columns=TARGET_COLUMNS)
    for col in TARGET_COLUMNS: api_df[col] = temp_df[col] if col in temp_df.columns else ""
    api_df = api_df.fillna("").astype(str)

    # 4. UPSERT ПРЕТЕНЗИЙ
    new_count, updated_count = 0, 0
    if existing_gs_records:
        gs_df = pd.DataFrame(existing_gs_records).astype(str)
        for col in TARGET_COLUMNS:
            if col not in gs_df.columns: gs_df[col] = ""
            
        gs_df = gs_df.drop_duplicates(subset=['SRID'], keep='last')
        api_df = api_df.reset_index(drop=True).drop_duplicates(subset=['SRID'], keep='last')
        
        gs_df.set_index('SRID', inplace=True)
        api_df.set_index('SRID', inplace=True)
        
        update_cols = [c for c in TARGET_COLUMNS if c not in MANUAL_COLS and c != 'SRID']
        common_srids = gs_df.index.intersection(api_df.index)
        
        if not common_srids.empty:
            gs_df.loc[common_srids, update_cols] = api_df.loc[common_srids, update_cols]
            updated_count = len(common_srids)
            
        new_srids = api_df.index.difference(gs_df.index)
        if not new_srids.empty:
            gs_df = pd.concat([gs_df, api_df.loc[new_srids]])
            new_count = len(new_srids)
        
        final_ordered_df = gs_df.reset_index()[TARGET_COLUMNS]
    else:
        final_ordered_df = api_df.reset_index(drop=True).drop_duplicates(subset=['SRID'], keep='last')[TARGET_COLUMNS]
        new_count = len(final_ordered_df)

    # ВОЗВРАЩАЕМ ОБЕ ТАБЛИЦЫ
    return final_ordered_df, df_orders_grouped, new_count, updated_count, report
        
# ==========================================
# 4. ИИ ДВИЖОК С УМНЫМ ПОИСКОМ (RAG) И АУДИТОМ
# ==========================================

# Умный парсер ответов ИИ
def parse_ai_response(text):
    try:
        clean_text = re.sub(r'```json|```', '', text).strip()
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict): return parsed.get('results', [])
        elif isinstance(parsed, list): return parsed
        else: return [{"error": f"Неожиданный формат: {type(parsed)}"}]
    except json.JSONDecodeError:
        return [{"error": f"Сбой формата JSON: {text}"}]

# Встроенный мини-поисковик (находит 10 похожих отзывов)
def find_similar_examples(target_text, memory_records, top_n=10):
    if not memory_records: return "Опыта пока нет."
    
    import re
    target_words = set(re.findall(r'\b\w{3,}\b', target_text.lower()))
    if not target_words: return "Опыта пока нет."

    scored = []
    for r in memory_records:
        mem_text = str(r.get('Контент', '')).lower()
        mem_words = set(re.findall(r'\b\w{3,}\b', mem_text))
        if not mem_words: continue
        
        score = len(target_words.intersection(mem_words))
        if score > 0:
            scored.append((score, f"Текст: {r.get('Контент')} -> Тег: {r.get('Правильные теги')}"))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    best_matches = [x[1] for x in scored[:top_n]]
    
    if best_matches:
        return "\n".join(best_matches)
    return "Прямых совпадений в опыте не найдено. Действуй по инструкции."

# Первичное тегирование (Только цифры = максимальная экономия)
async def fetch_ai_tags(session, batch, memory_records, model="yandex"):
    content_lines = []
    combined_target_text = ""
    for i in batch:
        content_lines.append(f"ID {i['id']}: {i['text']}")
        combined_target_text += i['text'] + " "
    content = "\n".join(content_lines)
    
    relevant_memory = find_similar_examples(combined_target_text, memory_records, top_n=10)

    system_prompt = f"""Ты эксперт контроля качества. 
    Категории (ID: Название): {json.dumps(CATEGORIES, ensure_ascii=False)}
    ПРАВИЛО 12: Если клиент хвалит, но есть мелкий дефект (рейтинг 4-5) - СТРОГО Категория 12.
    ВОТ ПРИМЕРЫ ПОХОЖИХ СИТУАЦИЙ ИЗ БАЗЫ:
    {relevant_memory}
    
    ИНСТРУКЦИЯ: Верни ТОЛЬКО массив category_ids (цифры подходящих категорий). Никакого текста!
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "category_ids": [1, 5]}}]}}"""

    if "yandex" in model:
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "x-folder-id": FOLDER_ID}
        yandex_model_name = "yandexgpt-lite" if model == "yandex-lite" else "yandexgpt"
        payload = {
            "modelUri": f"gpt://{FOLDER_ID}/{yandex_model_name}/latest",
            "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
            "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": content}]
        }
        try:
            async with session.post(url, headers=headers, json=payload, timeout=45) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return parse_ai_response(res['result']['alternatives'][0]['message']['text'])
                else: return [{"error": f"Ошибка Яндекса ({resp.status}): {await resp.text()}"}]
        except Exception as e: return [{"error": f"Системная ошибка Яндекса: {str(e)}"}]

    elif model == "grok":
        url = "https://api.x.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "grok-beta",
            "temperature": 0.1,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
        }
        try:
            async with session.post(url, headers=headers, json=payload, timeout=45) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return parse_ai_response(res['choices'][0]['message']['content'])
                else: return [{"error": f"Ошибка Grok ({resp.status}): {await resp.text()}"}]
        except Exception as e: return [{"error": f"Системная ошибка Grok: {str(e)}"}]
    return []

# Перекрестная проверка (Аудит от Grok)
async def fetch_ai_crosscheck(session, batch, memory_records):
    content_lines = []
    combined_target_text = ""
    for i in batch:
        content_lines.append(f"ID {i['id']}: {i['text']}")
        combined_target_text += i['text'] + " "
    content = "\n".join(content_lines)
    
    relevant_memory = find_similar_examples(combined_target_text, memory_records, top_n=10)

    system_prompt = f"""Ты строгий аудитор. Проверь теги первой нейросети. 
    Категории (ID: Название): {json.dumps(CATEGORIES, ensure_ascii=False)}
    ПРИМЕРЫ ПРАВИЛЬНЫХ РЕШЕНИЙ:
    {relevant_memory}
    
    ИНСТРУКЦИЯ:
    1. audit: Если старые теги верны, напиши "ОК". Если ошибка, напиши "ОШИБКА".
    2. comment: Если нашел ошибку, напиши почему (кратко). Если ОК, оставь пустым.
    3. category_ids: Массив ПРАВИЛЬНЫХ цифр категорий.
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "audit": "ОК", "comment": "", "category_ids": [1]}}]}}"""
    
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "grok-beta",
        "temperature": 0.1,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
    }
    try:
        async with session.post(url, headers=headers, json=payload, timeout=45) as resp:
            if resp.status == 200:
                res = await resp.json()
                return parse_ai_response(res['choices'][0]['message']['content'])
            else: return [{"error": f"Ошибка API Grok ({resp.status}): {await resp.text()}"}]
    except Exception as e: return [{"error": f"Системная ошибка Grok: {str(e)}"}]

# Оркестратор партий
async def run_ai_batch_processing(df_to_tag, model_choice, mode="tagging"):
    memory_records = get_memory_records() # Качаем базу 1 раз
    results = []
    async with aiohttp.ClientSession() as session:
        batch = []
        for idx, row in df_to_tag.iterrows():
            if mode == "tagging":
                batch.append({"id": f"REF_{idx}", "text": f"Артикул: {row.get('Артикул продавца','')}. Текст: {row.get('Комментарий покупателя','')}"})
            else:
                # Собираем текущие теги, чтобы Grok видел, что проверять
                current_tags = [str(c) for c in range(1, 14) if str(row.get(str(c), '')).strip() in ['1', '1.0', '+', 'v', 'да', 'true']]
                tags_str = ", ".join(current_tags) if current_tags else "Нет тегов"
                batch.append({"id": f"REF_{idx}", "text": f"Текст: {row.get('Комментарий покупателя','')}. Текущие теги (ID): {tags_str}"})
                
            if len(batch) >= 10:
                if mode == "tagging": res = await fetch_ai_tags(session, batch, memory_records, model_choice)
                else: res = await fetch_ai_crosscheck(session, batch, memory_records)
                if res: results.extend(res)
                batch = []
                
        if batch:
            if mode == "tagging": res = await fetch_ai_tags(session, batch, memory_records, model_choice)
            else: res = await fetch_ai_crosscheck(session, batch, memory_records)
            if res: results.extend(res)
            
    return results
    
# ==========================================
# 5. ИНТЕРФЕЙС И НАВИГАЦИЯ
# ==========================================
# СНАЧАЛА ОБЪЯВЛЯЕМ ФУНКЦИЮ:
def get_col_letter(col_idx):
    if col_idx < 26: return chr(ord('A') + col_idx)
    return chr(ord('A') + (col_idx // 26) - 1) + chr(ord('A') + (col_idx % 26))

if page == "🤖 Робот-Загрузчик":
    st.title("🤖 Робот-Загрузчик (API Режим)")
    
    with st.expander("1. Синхронизация с API Wildberries", expanded=True):
        st.write("Робот сам подключится к WB, заберет новые претензии, обновит статусы, а также выгрузит агрегированные чистые заказы.")
        
        if st.button("🚀 ЗАПУСТИТЬ СИНХРОНИЗАЦИЮ", type="primary"):
            with st.spinner("Связываемся с Wildberries, обновляем статусы и склеиваем данные..."):
                client = get_gspread_client()
                ws_ret = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                
                # Скачиваем текущую базу претензий
                existing_data = ws_ret.get_all_records()
                
                # Запускаем магию API (теперь возвращает и заказы тоже)
                final_tab, df_orders_grouped, new_added, rows_updated, report_log = process_wb_api_sync(existing_data)
                
                if not final_tab.empty:
                    # 1. ОБНОВЛЯЕМ ПРЕТЕНЗИИ
                    ws_ret.clear()
                    ws_ret.update([final_tab.columns.values.tolist()] + final_tab.values.tolist())
                    
                    # 2. СОХРАНЯЕМ ЗАКАЗЫ (На отдельный лист)
                    if not df_orders_grouped.empty:
                        try:
                            # Пробуем найти лист "Заказы"
                            ws_orders = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
                        except gspread.exceptions.WorksheetNotFound:
                            # Если его нет — создаем
                            ws_orders = client.open_by_key(SPREADSHEET_ID_MAIN).add_worksheet(title="Заказы", rows="1000", cols="10")
                        
                        # Перезаписываем данные о заказах
                        ws_orders.clear()
                        ws_orders.update([df_orders_grouped.columns.values.tolist()] + df_orders_grouped.values.tolist())
                        report_log.append("✅ **Заказы:** Сводная таблица чистых заказов успешно сохранена на лист 'Заказы'.")
                    
                    report_log.append(f"🔄 **Обновлено старых заявок:** {rows_updated} шт.")
                    report_log.append(f"✅ **Добавлено новых заявок:** {new_added} шт.")
                    st.success("Синхронизация успешно завершена! Таблицы актуальны.")
                else:
                    st.warning("Не удалось сформировать таблицу (нет данных от WB).")
                    
                st.markdown(f'<div class="report-card">{"<br>".join(report_log)}</div>', unsafe_allow_html=True)

# ==========================================
# 6. РУЧНАЯ МОДЕРАЦИЯ И ТЕГИРОВАНИЕ
# ==========================================

elif page == "🔬 ИИ Тегирование":
    st.title("🔬 ИИ Тегирование и Проверка")
    
    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
    headers = ws.row_values(1)
    
    header_map_clean = {str(name).strip().lower(): get_col_letter(idx) for idx, name in enumerate(headers)}
    header_map_original = {str(name).strip(): get_col_letter(idx) for idx, name in enumerate(headers)}
    
    df = pd.DataFrame(ws.get_all_records())
    
    # Создаем виртуальные колонки, чтобы код не падал, если вы забыли их добавить
    if 'Аудит' not in df.columns: df['Аудит'] = ''
    if 'Комментарий' not in df.columns: df['Комментарий'] = ''
    
    with st.expander("🛠 Рентген таблицы (Проверьте, видит ли робот ваши колонки)"):
        st.write("Робот нашел следующие колонки в Google Таблице:", header_map_original)
        
    # Умный фильтр: проверяет, есть ли хотя бы один тег в строке
    def has_tags(row):
        return any(str(row.get(str(i),'')).strip().lower() in ['1','1.0','+','v','да','true'] for i in range(1,14))
    
    df['has_any_tag'] = df.apply(has_tags, axis=1)
    
    t1, t2 = st.tabs(["1️⃣ Первичная разметка", "2️⃣ Перекрестная проверка (Grok)"])
    
    with t1:
        st.subheader("Разметка новых заявок (Только ID)")
        
        # Берем строки, где вообще нет тегов
        unprocessed = df[~df['has_any_tag']]
        
        if not unprocessed.empty:
            total_rows = len(unprocessed)
            
            col1, col2 = st.columns(2)
            batch_size = col1.slider("Размер пачки", 5, 50, 10, key="batch_tag")
            model_choice = col2.radio("Модель:", ["YandexGPT Lite (Дешево)", "YandexGPT Pro (Умнее)", "Grok (xAI)"], key="mod_tag")
            
            if "Lite" in model_choice: model_key = "yandex-lite"
            elif "Pro" in model_choice: model_key = "yandex-pro"
            else: model_key = "grok"

            cost_per_row = 0.08 if model_key == "yandex-lite" else 0.40 if model_key == "yandex-pro" else 0.50
            est_cost = total_rows * cost_per_row
            
            st.info(f"📊 **Аналитика:** Найдено **{total_rows}** строк без тегов.\n💰 **Предварительный расход:** ~{est_cost:.2f} руб.")
            
            if st.button("🚀 ЗАПУСТИТЬ ТЕГИРОВАНИЕ", type="primary"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                log_container = st.container()
                
                add_system_log("Запуск тегирования", "INFO", f"Строк: {total_rows}. Нейросеть: {model_key}")
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    for i in range(0, total_rows, batch_size):
                        chunk = unprocessed.iloc[i:i+batch_size]
                        progress = int(((i + len(chunk)) / total_rows) * 100)
                        status_text.text(f"⏳ Прогресс: {progress}% (Обработка пачки {i} из {total_rows})")
                        
                        chunk_to_send = chunk.copy()
                        results = loop.run_until_complete(run_ai_batch_processing(chunk_to_send, model_key, mode="tagging"))
                        
                        batch_updates = []
                        has_error = False
                        
                        # Собираем ID строк, которые ИИ успешно разметил
                        tagged_row_idxs = set()

                        for res in results:
                            if "error" in res: 
                                has_error = True
                                continue
                                
                            clean_id = re.sub(r'[^\d]', '', str(res.get('id') or res.get('ID') or ''))
                            if not clean_id: continue
                            row_idx = int(clean_id) + 2 
                            
                            cats_array = res.get('category_ids', []) or res.get('tags', [])
                            
                            if cats_array:
                                tagged_row_idxs.add(row_idx)
                                for cat_val in cats_array:
                                    cat_num_match = re.search(r'\d+', str(cat_val))
                                    if cat_num_match:
                                        cat_num = int(cat_num_match.group())
                                        header = str(cat_num)
                                        if header in header_map_clean:
                                            batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['1']]})

                        # ПРИНУДИТЕЛЬНАЯ РАЗМЕТКА (100% заполняемость)
                        skipped_rows = []
                        for idx, row in chunk.iterrows():
                            row_idx = idx + 2
                            if row_idx not in tagged_row_idxs:
                                skipped_rows.append(str(row_idx))
                                header = "12" # Принудительно ставим "Не подошло"
                                if header in header_map_clean:
                                    batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['1']]})

                        if batch_updates: ws.batch_update(batch_updates)
                        
                        # Детальный логгинг пачки
                        log_details = f"Строки {i} - {i+len(chunk)} обработаны."
                        if skipped_rows:
                            log_details += f" ИИ пропустил строки: {', '.join(skipped_rows)}. Им принудительно поставлена Кат 12."
                            
                        if has_error: add_system_log("Обработка пачки", "WARNING", log_details + " Были ошибки API.")
                        else: add_system_log("Обработка пачки", "SUCCESS", log_details)
                        
                        progress_bar.progress(min(1.0, (i + len(chunk)) / total_rows))
                    
                    st.success("✅ Тегирование успешно завершено! Все данные синхронизированы.")
                    add_system_log("Финиш тегирования", "SUCCESS", f"Все {total_rows} строк успешно обработаны.")
                except Exception as e:
                    st.error(f"🛑 ПРОЦЕСС ОСТАНОВЛЕН: {e}")
                    add_system_log("КРИТИЧЕСКАЯ ОШИБКА", "ERROR", f"Процесс прерван. Ошибка: {str(e)}")
        else:
            st.success("🎉 Все строки уже имеют первичную разметку!")
                    
    with t2:
        st.subheader("Глубокая проверка (Аудит от Grok)")
        st.markdown("Grok прочитает отзыв, посмотрит на уже стоящие теги и решит: всё ОК или есть Ошибка.")
        
        # Ищем строки, где теги ЕСТЬ, а Аудита еще НЕТ
        unprocessed_audit = df[(df['has_any_tag']) & (df['Аудит'].astype(str).str.strip() == '')]
        
        if not unprocessed_audit.empty:
            total_audit_rows = len(unprocessed_audit)
            batch_size_audit = st.slider("Размер пачки для аудита", 5, 50, 10, key="batch_audit")
            
            st.info(f"Найдено строк для проверки: **{total_audit_rows}**")

            if st.button("🕵️‍♂️ ЗАПУСТИТЬ АУДИТ", type="primary"):
                progress_bar_audit = st.progress(0)
                status_text_audit = st.empty()
                log_container_audit = st.container()
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    for i in range(0, total_audit_rows, batch_size_audit):
                        chunk = unprocessed_audit.iloc[i:i+batch_size_audit]
                        status_text_audit.text(f"⏳ Аудит: пачка {i} из {total_audit_rows}")
                        
                        results = loop.run_until_complete(run_ai_batch_processing(chunk, "grok", mode="crosscheck"))
                        batch_updates = []
                        
                        with log_container_audit:
                            with st.expander(f"Лог аудита (Строки {i} - {i+len(chunk)})"):
                                if results: st.json(results)

                        for res in results:
                            if "error" in res: continue
                            clean_id = re.sub(r'[^\d]', '', str(res.get('id') or ''))
                            if not clean_id: continue
                            row_idx = int(clean_id) + 2 
                            
                            audit_status = str(res.get('audit', '')).strip()
                            comment_text = str(res.get('comment', '')).strip()
                            cats_array = res.get('category_ids', []) or res.get('tags', [])
                            
                            # Запись Аудита и Комментария
                            if "аудит" in header_map_clean:
                                batch_updates.append({'range': f"{header_map_clean['аудит']}{row_idx}", 'values': [[audit_status]]})
                            if "комментарий" in header_map_clean:
                                batch_updates.append({'range': f"{header_map_clean['комментарий']}{row_idx}", 'values': [[comment_text]]})

                            # Если Grok нашел ошибку и дал новые теги - ПЕРЕЗАПИСЫВАЕМ ИХ
                            if audit_status.upper() != "ОК" and cats_array:
                                # 1. Стираем все старые галочки
                                for c in range(1, 14):
                                    header = str(c)
                                    if header in header_map_clean:
                                        batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['']]})
                                # 2. Ставим новые правильные
                                for cat_val in cats_array:
                                    cat_num_match = re.search(r'\d+', str(cat_val))
                                    if cat_num_match:
                                        cat_num = int(cat_num_match.group())
                                        header = str(cat_num)
                                        if header in header_map_clean:
                                            batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['1']]})

                        if batch_updates: ws.batch_update(batch_updates)
                        progress_bar_audit.progress(min(1.0, (i + len(chunk)) / total_audit_rows))
                        
                    st.success("✅ Аудит завершен! Ошибки исправлены.")
                except Exception as e:
                    st.error(f"🛑 ОШИБКА АУДИТА: {e}")
        else:
            st.success("🎉 Все размеченные строки уже проверены аудитором!")

elif page == "📝 Модерация":
    st.title("📋 Модерация (Ручная проверка)")

    # Стили: фото по горизонтали, увеличены (140px), стилизация кнопок пагинации
    st.markdown("""
    <style>
    .media-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 10px; }
    .photo-zoom { width: 140px; height: 140px; object-fit: cover; border-radius: 8px; transition: transform 0.3s ease; cursor: pointer; }
    .photo-zoom:hover { transform: scale(4); z-index: 9999; position: relative; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
    .ai-tags-box { background-color: #f0fdf4; padding: 10px 14px; border-radius: 6px; font-size: 14px; color: #166534; margin-bottom: 15px; font-weight: 500; border-left: 4px solid #22c55e; }
    </style>
    """, unsafe_allow_html=True)

    @st.dialog("Просмотр видео")
    def play_video_modal(url):
        st.video(url)

    # Функция отрисовки красивой пагинации
    def render_pagination(total_pages, key_prefix):
        if total_pages <= 1: return
        
        curr = st.session_state.mod_page
        
        # Логика окна страниц (например: 1, 2, 3, ..., 10)
        if total_pages <= 7: window = list(range(1, total_pages + 1))
        elif curr <= 4: window = [1, 2, 3, 4, 5, "...", total_pages]
        elif curr >= total_pages - 3: window = [1, "...", total_pages - 4, total_pages - 3, total_pages - 2, total_pages - 1, total_pages]
        else: window = [1, "...", curr - 1, curr, curr + 1, "...", total_pages]

        # Контейнер для центрирования
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            cols = st.columns(len(window) + 2, gap="small")
            
            with cols[0]:
                if st.button("«", key=f"{key_prefix}_prev", disabled=(curr == 1), use_container_width=True):
                    st.session_state.mod_page -= 1
                    st.rerun()
                    
            for i, p in enumerate(window):
                with cols[i+1]:
                    if p == "...":
                        st.markdown("<div style='text-align: center; color: #888; padding-top: 5px; font-weight: bold;'>...</div>", unsafe_allow_html=True)
                    else:
                        if st.button(str(p), key=f"{key_prefix}_page_{p}", type="primary" if p == curr else "secondary", use_container_width=True):
                            st.session_state.mod_page = p
                            st.rerun()
                            
            with cols[-1]:
                if st.button("»", key=f"{key_prefix}_next", disabled=(curr == total_pages), use_container_width=True):
                    st.session_state.mod_page += 1
                    st.rerun()

    try:
        client = get_gspread_client()
        ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
        data = ws.get_all_values()
        
        if len(data) > 1:
            headers = [str(h).strip() for h in data[0]]
            df = pd.DataFrame(data[1:], columns=headers)
            
            def has_tags(row): return any(str(row.get(str(i),'')).strip().lower() in ['1','1.0','+','v','да','true'] for i in range(1,14))
            df['has_any_tag'] = df.apply(has_tags, axis=1)
            
            st.markdown("### 🔍 Фильтр обращений")
            filter_mode = st.radio("Показать обращения:", ["Все ожидающие модерации", "С замечаниями от Аудитора (Кросс-проверка)"], horizontal=True)
            
            base_review = df[(df['has_any_tag']) & (df.get('Корректировка', '').astype(str).str.strip() == '')]
            if filter_mode == "С замечаниями от Аудитора (Кросс-проверка)":
                if 'Аудит' in base_review.columns: base_review = base_review[base_review['Аудит'].astype(str).str.upper().str.contains('ОШИБКА')]
                else: st.warning("Колонка 'Аудит' отсутствует в таблице!")
                    
            to_review = base_review
            
            if not to_review.empty:
                total_items = len(to_review)
                ITEMS_PER_PAGE = 20
                total_pages = max(1, (total_items - 1) // ITEMS_PER_PAGE + 1)
                
                # Защита состояния страницы
                if 'mod_page' not in st.session_state: st.session_state.mod_page = 1
                if st.session_state.mod_page > total_pages: st.session_state.mod_page = 1
                
                st.success(f"Найдено обращений в очереди: **{total_items}**")
                
                # ПАГИНАЦИЯ (ВЕРХ)
                render_pagination(total_pages, key_prefix="top")
                
                start_idx = (st.session_state.mod_page - 1) * ITEMS_PER_PAGE
                end_idx = start_idx + ITEMS_PER_PAGE
                current_page_df = to_review.iloc[start_idx:end_idx]
                
                cats_list = list(CATEGORIES.values())
                reverse_cats = {v.strip().lower(): k for k, v in CATEGORIES.items()}
                header_map_clean = {str(name).strip().lower(): get_col_letter(idx) for idx, name in enumerate(headers)}
                
                for idx, row in current_page_df.iterrows():
                    row_index_gs = idx + 2 
                    st.markdown("---")
                    
                    col_info, col_media = st.columns([1.2, 1])
                    with col_info:
                        st.markdown(f"**Артикул:** {row.get('Артикул продавца', '---')} | **Дата:** {row.get('Дата и время оформления заявки на возврат', '')}")
                        st.info(row.get('Комментарий покупателя', 'Нет текста'))
                        
                        ai_selected = []
                        for i in range(1, 14):
                            if str(row.get(str(i), '')).strip().lower() in ['1', '1.0', '+', 'v', 'да', 'true']: ai_selected.append(f"{CATEGORIES[i]}")
                        ai_text = ", ".join(ai_selected) if ai_selected else "Категории не определены"
                        st.markdown(f'<div class="ai-tags-box">🤖 <b>Выбор ИИ:</b> {ai_text}</div>', unsafe_allow_html=True)
                        
                        selected_cats = st.multiselect("Выберите правильные категории:", options=cats_list, default=[], key=f"ms_{row_index_gs}")
                        
                        if st.button("💾 Сохранить решение", key=f"btn_{row_index_gs}", type="primary"):
                            if selected_cats:
                                batch_updates = []
                                for c in range(1, 14):
                                    h = str(c)
                                    if h in header_map_clean: batch_updates.append({'range': f"{header_map_clean[h]}{row_index_gs}", 'values': [['']]})
                                for cat_name in selected_cats:
                                    cat_num = reverse_cats.get(cat_name.strip().lower())
                                    if cat_num: batch_updates.append({'range': f"{header_map_clean[str(cat_num)]}{row_index_gs}", 'values': [['1']]})
                                
                                corr_header = "корректировка"
                                if corr_header in header_map_clean:
                                    corr_val = "; ".join(selected_cats)
                                    batch_updates.append({'range': f"{header_map_clean[corr_header]}{row_index_gs}", 'values': [[corr_val]]})
                                    
                                ws.batch_update(batch_updates)
                                st.success("Сохранено!")
                                st.rerun()

                    with col_media:
                        media_raw = str(row.get('Фотографии', '')) + " " + str(row.get('Видео', ''))
                        urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[]+', media_raw)
                        if urls:
                            videos = []
                            images_html = '<div class="media-row">'
                            for u in urls[:6]: 
                                clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                                if clean_url.startswith("//"): clean_url = "https:" + clean_url
                                
                                if any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): videos.append(clean_url)
                                else: images_html += f'<a href="{clean_url}" target="_blank"><img src="{clean_url}" class="photo-zoom"></a>'
                            images_html += '</div>'
                            st.markdown(images_html, unsafe_allow_html=True)
                            
                            if videos:
                                v_cols = st.columns(len(videos))
                                for v_idx, v_url in enumerate(videos):
                                    with v_cols[v_idx]:
                                        if st.button("🎥 Видео", key=f"vid_{row_index_gs}_{v_idx}"): play_video_modal(v_url)
                
                st.markdown("---")
                # ПАГИНАЦИЯ (НИЗ)
                render_pagination(total_pages, key_prefix="bottom")
            else:
                st.success("🎉 Очередь пуста! Все обращения проверены.")
    except Exception as e:
        st.error(f"Ошибка модерации: {e}")
        
elif page == "🧠 Обучение ИИ":
    st.title("🧠 База знаний ИИ (Умный импорт)")
    st.markdown("Загрузите исторический файл с проверенными отзывами. Робот всё поймет, расшифрует теги и загрузит в свою память. Новые корректировки всегда заменяют старые!")

    f_import = st.file_uploader("📂 Загрузить базу знаний (Excel/CSV)", type=['xlsx', 'csv', 'xls'])

    if st.button("📥 Загрузить и обновить память", type="primary"):
        if f_import:
            with st.spinner("Анализируем структуру файла и разрешаем конфликты..."):
                df_import = safe_read(f_import)
                if not df_import.empty:
                    import re
                    text_cols = [c for c in df_import.columns if str(c).lower().strip() in ['текст отзыва', 'достоинства', 'недостатки', 'текст клиента', 'текст_клиента', 'user_comment', 'комментарий покупателя']]
                    corr_col = next((c for c in df_import.columns if any(kw in str(c).lower() for kw in ['корректировка', 'исправление', 'комментарий'])), None)
                    tag_col = next((c for c in df_import.columns if 'какой тег' in str(c).lower()), None)
                    cat_columns = [c for c in df_import.columns if re.search(r'\d+', str(c)) and ('кат' in str(c).lower() or str(c).strip().isdigit())]
                    
                    if not text_cols: st.error("❌ Ошибка: В файле не найдены колонки с текстом.")
                    else:
                        new_memory_dict = {}
                        for idx, row in df_import.iterrows():
                            parts = [str(row[tc]).strip() for tc in text_cols if pd.notna(row[tc]) and str(row[tc]).strip().lower() != 'nan' and str(row[tc]).strip()]
                            combined_text = " ".join(parts)
                            if not combined_text: continue
                            
                            final_tags = ""
                            if corr_col and pd.notna(row[corr_col]) and str(row[corr_col]).strip().lower() != 'nan' and str(row[corr_col]).strip():
                                final_tags = str(row[corr_col]).strip()
                            elif cat_columns: 
                                found_cats = [CATEGORIES[int(re.search(r'\d+', str(c)).group())] for c in cat_columns if re.search(r'\d+', str(c)) and int(re.search(r'\d+', str(c)).group()) in CATEGORIES and str(row[c]).strip().lower() in ['1', '1.0', 'v', '+', 'да', 'true']]
                                if found_cats: final_tags = "; ".join(found_cats)
                            elif tag_col and pd.notna(row[tag_col]):
                                found_cats = [CATEGORIES[int(n)] for n in re.findall(r'\d+', str(row[tag_col])) if int(n) in CATEGORIES]
                                if found_cats: final_tags = "; ".join(found_cats)
                                    
                            if final_tags: new_memory_dict[combined_text] = final_tags

                        if new_memory_dict:
                            try:
                                client = get_gspread_client()
                                sheet = client.open_by_key(SPREADSHEET_ID_MAIN)
                                try: ws_mem = sheet.worksheet("Память_ИИ")
                                except:
                                    ws_mem = sheet.add_worksheet(title="Память_ИИ", rows="1000", cols="2")
                                    ws_mem.append_row(["Контент", "Правильные теги"])

                                existing_records = ws_mem.get_all_records()
                                combined_memory = {str(r.get('Контент', '')).strip(): str(r.get('Правильные теги', '')).strip() for r in existing_records if str(r.get('Контент', '')).strip()}
                                combined_memory.update(new_memory_dict)
                                
                                ws_mem.clear()
                                ws_mem.update('A1', [["Контент", "Правильные теги"]] + [[k, v] for k, v in combined_memory.items()])
                                st.success(f"✅ База знаний успешно обновлена! ИИ выучил новые данные. Всего в памяти: {len(combined_memory)} уникальных примеров.")
                            except Exception as e: st.error(f"❌ Ошибка записи в Google Таблицу: {e}")
                        else: st.warning("⚠️ Не найдено валидных тегов в файле.")
        else: st.warning("Пожалуйста, загрузите файл.")

# ==========================================
# 7. ОТЧЕТ ПРОИЗВОДСТВА (Altair Хитмап + Фикс Текста и Кликов)
# ==========================================

elif page == "📊 Отчет производства":
    st.title("📊 Отчет производства")
    
    # 1. ГАРАНТИРОВАННЫЙ СБРОС И ЗАЩИТА ОТ ФАНТОМОВ
    if 'matrix_key' not in st.session_state:
        import time
        st.session_state.matrix_key = int(time.time())
    if 'last_click_id' not in st.session_state:
        st.session_state.last_click_id = None
    if 'prev_inv' not in st.session_state:
        st.session_state.prev_inv = None
    if 'prev_sku' not in st.session_state:
        st.session_state.prev_sku = None
    
    st.markdown("""
    <style>
    /* Оставляем стили только для карточек детализации */
    .detail-card { border: 1px solid #ddd; padding: 15px; border-radius: 8px; margin-bottom: 15px; background-color: #fcfcfc; }
    .media-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 10px; }
    .photo-zoom { 
        width: 140px; height: 140px; object-fit: cover; border-radius: 8px; 
        transition: transform 0.3s ease; cursor: pointer; border: none !important;
    }
    .photo-zoom:hover { transform: scale(4); z-index: 9999; position: relative; box-shadow: 0 15px 30px rgba(0,0,0,0.5) !important; }
    .video-link-btn {
        display: inline-block; padding: 8px 14px; background-color: #2563eb; 
        color: white !important; border-radius: 6px; text-decoration: none; 
        font-weight: bold; font-size: 13px; transition: background-color 0.2s;
    }
    .video-link-btn:hover { background-color: #1d4ed8; }
    </style>
    """, unsafe_allow_html=True)

    def create_images_zip(urls):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for i, url in enumerate(urls):
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=3) as response:
                        img_data = response.read()
                        ext = ".jpg"
                        if ".png" in url.lower(): ext = ".png"
                        elif ".jpeg" in url.lower(): ext = ".jpeg"
                        zip_file.writestr(f"photo_{i+1}{ext}", img_data)
                except Exception:
                    pass
        return zip_buffer.getvalue()

    @st.dialog("Детализация пересечения", width="large")
    def show_matrix_details(sku, reason_name, filtered_df, reason_id):
        st.subheader(f"📦 Артикул: {sku} | 🛠 Причина: {reason_name}")
        
        details = filtered_df[
            (filtered_df['Артикул продавца'] == sku) & 
            (filtered_df[str(reason_id)].astype(str).str.strip().isin(['1', '1.0', '+']))
        ]
        
        if not details.empty:
            all_photos = []
            for _, r in details.iterrows():
                m_raw = str(r.get('Фотографии', '')) + " " + str(r.get('Видео', ''))
                urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[]+', m_raw)
                for u in urls:
                    clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                    if clean_url.startswith("//"): clean_url = "https:" + clean_url
                    if not any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']):
                        all_photos.append(clean_url)
            
            if all_photos:
                if st.button(f"📥 Скачать ВСЕ фото ({len(all_photos)} шт.)", type="primary", key=f"dl_all_{sku}_{reason_id}"):
                    with st.spinner("Сбор фото и архивация... (Пожалуйста, подождите)"):
                        zip_all = create_images_zip(all_photos)
                        b64 = base64.b64encode(zip_all).decode()
                        dl_link = f'''
                        <a id="dl" href="data:application/zip;base64,{b64}" download="{sku}_{reason_id}_ALL.zip"></a>
                        <script>document.getElementById("dl").click();</script>
                        '''
                        components.html(dl_link, width=0, height=0)
            
            st.markdown("---")
            for _, r in details.iterrows():
                with st.container():
                    st.markdown('<div class="detail-card">', unsafe_allow_html=True)
                    c1, media_col = st.columns([1.2, 1])
                    m_raw = str(r.get('Фотографии', '')) + " " + str(r.get('Видео', ''))
                    urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[]+', m_raw)
                    row_photos, videos = [], []
                    for u in urls:
                        clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                        if clean_url.startswith("//"): clean_url = "https:" + clean_url
                        if any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): videos.append(clean_url)
                        else: row_photos.append(clean_url)

                    with c1:
                        st.write(f"💬 **Текст клиента:**\n{r.get('Комментарий покупателя', '---')}")
                        st.write(f"📅 **Дата:** {r.get('Дата и время оформления заявки на возврат', '---')}")
                        st.write(f"🧾 **Инвойс:** {r.get('Инвойс', '---')} | **Поставка:** {r.get('Номер поставки', '---')}")
                        if row_photos:
                            if st.button("📥 Скачать фото", key=f"dl_row_{r.name}"):
                                with st.spinner("Архивация..."):
                                    zip_row = create_images_zip(row_photos)
                                    b64 = base64.b64encode(zip_row).decode()
                                    filename = f"order_{r.get('Инвойс', 'photos')}.zip"
                                    dl_link = f'''
                                    <a id="dl" href="data:application/zip;base64,{b64}" download="{filename}"></a>
                                    <script>document.getElementById("dl").click();</script>
                                    '''
                                    components.html(dl_link, width=0, height=0)
                    
                    with media_col:
                        if row_photos:
                            images_html = '<div class="media-row">'
                            for p in row_photos[:6]:
                                images_html += f'<a href="{p}" target="_blank"><img src="{p}" class="photo-zoom"></a>'
                            st.markdown(images_html + '</div>', unsafe_allow_html=True)
                        if videos:
                            for v_idx, v_url in enumerate(videos):
                                st.markdown(f'<a href="{v_url}" target="_blank" class="video-link-btn">🎥 Видео {v_idx+1}</a>', unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.write("Нет данных по этому пересечению.")

        if st.button("Закрыть детализацию"):
            st.session_state.show_detail_trigger = None
            st.session_state.last_click_id = None
            st.rerun()

    # --- ТРИГГЕР ОТКРЫТИЯ ОКНА ---
    if st.session_state.get('show_detail_trigger'):
        t = st.session_state.show_detail_trigger
        show_matrix_details(t['sku'], t['reason'], t['df'], t['id'])

    # ⚡️ ФУНКЦИЯ С КЭШЕМ (Качает данные раз в 5 минут)
    @st.cache_data(ttl=300) 
    def load_cached_google_data():
        client = get_gspread_client()
        sheet_main = client.open_by_key(SPREADSHEET_ID_MAIN)
        df_temp = pd.DataFrame(sheet_main.worksheet("Возвраты").get_all_records())
        
        # Обновленные названия для проверки
        if 'supplyID' in df_temp.columns and 'Номер поставки' not in df_temp.columns:
            df_temp.rename(columns={'supplyID': 'Номер поставки'}, inplace=True)
        
        try:
            inv_id = st.secrets.get("SPREADSHEET_ID_INVOICES", "")
            if inv_id:
                sheet_inv = client.open_by_key(inv_id)
                df_inv = pd.DataFrame(sheet_inv.get_worksheet(0).get_all_records())
                
                if 'supplyID' in df_inv.columns and 'Номер поставки' not in df_inv.columns:
                    df_inv.rename(columns={'supplyID': 'Номер поставки'}, inplace=True)
                
                if not df_inv.empty and 'Номер поставки' in df_inv.columns:
                    df_inv.columns = [str(c).strip() for c in df_inv.columns]
                    df_inv_unique = df_inv.drop_duplicates(subset=['Номер поставки'])
                    if 'Инвойс' in df_temp.columns: df_temp = df_temp.drop(columns=['Инвойс'])
                    cols_to_merge = ['Номер поставки']
                    if 'Инвойс' in df_inv.columns: cols_to_merge.append('Инвойс')
                    df_temp = df_temp.merge(df_inv_unique[cols_to_merge], on='Номер поставки', how='left')
        except Exception:
            pass
            
        return df_temp

    try:
        # Мгновенно берем данные из оперативной памяти
        df = load_cached_google_data()

        if not df.empty:
            if 'Инвойс' not in df.columns: df['Инвойс'] = 'Не указан'
            if 'Номер поставки' not in df.columns: df['Номер поставки'] = 'Не указан'
            
            def has_tags(row): return any(str(row.get(str(i),'')).strip() in ['1','1.0','+'] for i in range(1,14))
            df['Размечено'] = df.apply(has_tags, axis=1)
            
            st.markdown("### 🔍 Глобальные фильтры")
            f_col1, f_col2 = st.columns(2)
            inv_list = ['Все'] + sorted(list(set([str(x) for x in df['Инвойс'] if str(x).strip()])))
            sku_list = ['Все'] + sorted(list(set([str(x) for x in df['Артикул продавца'] if str(x).strip()])))
            
            selected_inv = f_col1.selectbox("Инвойс / Поставка:", inv_list)
            selected_sku = f_col2.selectbox("Артикул:", sku_list)
            
            # Если фильтры изменились, принудительно сбрасываем окно
            if st.session_state.prev_inv != selected_inv or st.session_state.prev_sku != selected_sku:
                st.session_state.show_detail_trigger = None
                st.session_state.last_click_id = None
                st.session_state.prev_inv = selected_inv
                st.session_state.prev_sku = selected_sku
                st.session_state.matrix_key += 1 
            
            df_filtered = df.copy()
            if selected_inv != 'Все': df_filtered = df_filtered[df_filtered['Инвойс'].astype(str) == selected_inv]
            if selected_sku != 'Все': df_filtered = df_filtered[df_filtered['Артикул продавца'].astype(str) == selected_sku]

            total_rows = len(df_filtered)
            tagged_rows = df_filtered['Размечено'].sum()
            corrected_rows = len(df_filtered[df_filtered.get('Корректировка', '') != ''])
            
            accuracy = round((1 - (corrected_rows / tagged_rows)) * 100, 1) if tagged_rows > 0 else 0
            processed_percent = round((tagged_rows / total_rows) * 100, 1) if total_rows > 0 else 0
            
            st.markdown("### 📈 Общая статистика")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Всего заявок", total_rows)
            c2.metric("Размечено", f"{tagged_rows} ({processed_percent}%)")
            c3.metric("Изменено вручную", corrected_rows)
            c4.metric("Точность ИИ", f"{accuracy}%")
            
            matrix_list = []
            for i in range(1, 14):
                cat_col = str(i)
                if cat_col in df_filtered.columns:
                    temp = df_filtered[df_filtered[cat_col].astype(str).str.strip().isin(['1', '1.0', '+'])]
                    for _, r in temp.iterrows():
                        matrix_list.append({
                            'Артикул продавца': str(r.get('Артикул продавца', 'Без артикула')).strip(),
                            'Причина': f"{i}. {CATEGORIES[i]}",
                            'ID': i,
                            'Инвойс': str(r.get('Инвойс', 'Не указан')).strip(),
                            'Номер поставки': str(r.get('Номер поставки', 'Не указан')).strip()
                        })

            st.markdown("---")
            st.markdown("### 🧮 Тепловая Матрица Производства")
            st.info("💡 **Кликните на любой цветной квадрат для мгновенной детализации!**")
            
            if matrix_list:
                df_matrix = pd.DataFrame(matrix_list)
                
                pivot = pd.crosstab(df_matrix['Причина'], df_matrix['Артикул продавца']).fillna(0).astype(int)
                pivot['ID'] = [int(x.split('.')[0]) for x in pivot.index]
                
                sku_totals = pivot.drop(columns=['ID']).sum(axis=0).to_dict()
                reason_totals = pivot.drop(columns=['ID']).sum(axis=1).to_dict()
                
                df_melt = pivot.reset_index().melt(id_vars=['Причина', 'ID'], var_name='Артикул продавца', value_name='Дефекты')
                
                df_melt['Артикул_Метка'] = df_melt['Артикул продавца'] 
                df_melt['Причина_Метка'] = df_melt['Причина'].apply(lambda x: f"{x} [{reason_totals.get(x, 0)}]")
                
                df_melt['Текст'] = df_melt['Дефекты'].apply(lambda x: str(x) if x > 0 else "")

                import altair as alt
                
                click_selector = alt.selection_point(name='cell_click', fields=['Артикул_Метка', 'Причина_Метка'])
                
                base = alt.Chart(df_melt).encode(
                    x=alt.X('Артикул_Метка:N', title=None, axis=alt.Axis(labelAngle=-90, labelLimit=1000, orient='bottom')),
                    y=alt.Y('Причина_Метка:N', title=None, axis=alt.Axis(labelLimit=1000), sort=alt.EncodingSortField(field='ID', order='ascending'))
                )
                
                rects = base.mark_rect(stroke='white', strokeWidth=1).encode(
                    color=alt.Color('Дефекты:Q', scale=alt.Scale(scheme='blues'), legend=None),
                    tooltip=[alt.Tooltip('Артикул продавца:N', title='Артикул'), alt.Tooltip('Причина:N', title='Причина'), alt.Tooltip('Дефекты:Q', title='Кол-во')]
                )
                
                text = base.mark_text(baseline='middle', fontSize=11).encode(
                    text='Текст:N',
                    color=alt.condition(
                        alt.datum.Дефекты > (df_melt['Дефекты'].max() / 2),
                        alt.value('white'),
                        alt.value('black')
                    )
                )
                
                chart_height = max(400, len(pivot) * 35 + 100)
                
                final_chart = alt.layer(rects, text).properties(height=chart_height).add_params(click_selector)
                
                # 2. РЕНДЕР С ДИНАМИЧЕСКИМ КЛЮЧОМ
                event = st.altair_chart(
                    final_chart, 
                    use_container_width=True, 
                    on_select="rerun",
                    key=f"prod_matrix_{st.session_state.matrix_key}"
                )
                
                # 3. ИСПРАВЛЕННЫЙ ПЕРЕХВАТЧИК КЛИКОВ
                try:
                    if event and hasattr(event, "selection"):
                        sel = event.selection.get("cell_click", [])
                        
                        if sel and len(sel) > 0:
                            clicked_point = sel[0]
                            sku_clicked = clicked_point.get('Артикул_Метка')
                            reason_clicked = clicked_point.get('Причина_Метка')
                            
                            if sku_clicked and reason_clicked:
                                current_click_id = f"{sku_clicked}_{reason_clicked}"
                                
                                if current_click_id != st.session_state.get('last_click_id'):
                                    st.session_state.last_click_id = current_click_id
                                    
                                    clean_sku = sku_clicked.split(' [')[0]
                                    clean_reason = reason_clicked.split(' [')[0]
                                    reason_id_clicked = int(clean_reason.split('.')[0])
                                    
                                    st.session_state.show_detail_trigger = {
                                        'sku': clean_sku,
                                        'reason': clean_reason,
                                        'df': df_filtered,
                                        'id': reason_id_clicked
                                    }
                                    
                                    st.session_state.matrix_key += 1
                                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка системы перехвата клика: {e}")

            else:
                st.info("Данных для матрицы пока нет.")

            st.markdown("---")
            st.markdown("### 📦 Проблемные Инвойсы (Топ-15)")
            
            # --- CSS для всплывающего окна (Компактный вид) ---
            st.markdown("""
            <style>
            #vg-tooltip-element {
                font-family: sans-serif;
                font-size: 11px !important;
                line-height: 1.3 !important;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
                border-radius: 6px !important;
                border: 1px solid #e0e0e0 !important;
                max-width: 600px !important;
                white-space: normal !important;
            }
            #vg-tooltip-element table tr {
                border-bottom: 1px solid #d1d5db;
            }
            #vg-tooltip-element table tr:last-child {
                border-bottom: none;
            }
            #vg-tooltip-element table td {
                padding: 6px 8px !important;
                vertical-align: top;
            }
            #vg-tooltip-element table td.key {
                color: #6b7280;
                font-weight: 600;
                white-space: nowrap;
            }
            </style>
            """, unsafe_allow_html=True)
            
            if matrix_list:
                df_matrix_inv = pd.DataFrame(matrix_list)
                
                inv_grouped = []
                for inv, group in df_matrix_inv.groupby('Инвойс'):
                    defect_count = len(group)
                    
                    supplies = ", ".join(sorted(list(set([str(x) for x in group['Номер поставки'] if str(x) != 'Не указан']))))
                    if not supplies: 
                        supplies = "Не указана"
                    
                    sku_counts = group['Артикул продавца'].value_counts()
                    
                    all_skus = " • ".join([f"{k} ({v} шт.)" for k, v in sku_counts.items()])
                        
                    inv_grouped.append({
                        'Инвойс': inv,
                        'Дефекты': defect_count,
                        'Поставки': supplies,
                        'Список Артикулов': all_skus
                    })
                
                if inv_grouped:
                    df_inv_chart = pd.DataFrame(inv_grouped).sort_values('Дефекты', ascending=False).head(15)
                    
                    import altair as alt
                    
                    inv_chart = alt.Chart(df_inv_chart).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                        x=alt.X('Инвойс:N', sort='-y', title=None, axis=alt.Axis(labelAngle=-45, labelLimit=500)),
                        y=alt.Y('Дефекты:Q', title='Количество дефектов'),
                        color=alt.Color('Дефекты:Q', scale=alt.Scale(scheme='oranges'), legend=None),
                        tooltip=[
                            alt.Tooltip('Инвойс:N', title='Инвойс'),
                            alt.Tooltip('Дефекты:Q', title='Всего дефектов'),
                            alt.Tooltip('Поставки:N', title='Поставки'),
                            alt.Tooltip('Список Артикулов:N', title='Артикулы')
                        ]
                    ).properties(height=350)
                    
                    st.altair_chart(inv_chart, use_container_width=True)
                else:
                    st.info("Нет данных по инвойсам.")
            else:
                st.info("Данных для инвойсов пока нет.")

    except Exception as e:
        st.error(f"Ошибка Отчета: {e}")

# ==========================================
# 8. СИСТЕМНЫЙ ЖУРНАЛ
# ==========================================

elif page == "📜 Системный Журнал":
    st.title("📜 Системный Журнал (Черный ящик)")
    st.markdown("Здесь сохраняется хронология всех процессов. Если Макбук уснул или пропал интернет, вы всегда сможете посмотреть, на каком моменте остановилась работа.")
    
    if st.button("🔄 Обновить журнал"):
        st.rerun()

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN)
        ws_log = sheet.worksheet("Логи")
        records = ws_log.get_all_records()
        
        if records:
            df_logs = pd.DataFrame(records)
            # Переворачиваем таблицу, чтобы свежие логи были сверху
            df_logs = df_logs.iloc[::-1].reset_index(drop=True)
            
            # Красивая раскраска статусов
            def color_status(val):
                color = 'green' if val == 'SUCCESS' else 'red' if val == 'ERROR' else 'orange' if val == 'WARNING' else 'blue'
                return f'color: {color}; font-weight: bold;'
                
            st.dataframe(df_logs.style.applymap(color_status, subset=['Статус']), use_container_width=True, height=600)
        else:
            st.info("Журнал пуст. Запустите тегирование, чтобы появились первые записи.")
    except Exception as e:
        st.warning("Лист 'Логи' еще не создан. Он появится автоматически при первом запуске тегирования.")
