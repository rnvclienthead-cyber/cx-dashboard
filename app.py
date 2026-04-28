import streamlit as st
import asyncio
import aiohttp
import gspread
import json
import re
import pandas as pd
import plotly.express as px
from datetime import datetime
from google.oauth2.service_account import Credentials
import io

st.set_page_config(page_title="CX AI Enterprise", layout="wide")

# Инициализация ключей для сброса загрузчиков после успешной обработки
if 'claims_key' not in st.session_state: st.session_state.claims_key = 0
if 'litestat_key' not in st.session_state: st.session_state.litestat_key = 0

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

COLUMN_NAMES_RU = {
    'claim_type': 'Источник заявки', 'status': 'Решение', 'status_ex': 'Статус товара',
    'nm_id': 'Артикул WB (Заявка)', 'user_comment': 'Комментар покупателя', 
    'wb_comment': 'Ответ покупателю', 'dt': 'Дата заявки', 'imt_name': 'Название товара', 
    'order_dt': 'Дата заказа', 'dt_update': 'Дата рассмотрения', 'photos': 'Фотографии', 
    'video_paths': 'Видео', 'price': 'Цена', 'srid': 'ID заказа (SRID)', 'supplierArticle': 'Артикул продавца', 
    'nmId': 'Артикул WB', 'incomeID': 'Номер поставки'
}

# CSS Стили (включая зум фото на 30% при наведении)
st.markdown("""
<style>
    .img-zoom { width: 60px; height: 60px; object-fit: cover; border-radius: 5px; transition: transform 0.2s ease-in-out; cursor: zoom-in; }
    .img-zoom:hover { transform: scale(1.3); z-index: 999; position: relative; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
    .custom-table { width: 100%; border-collapse: collapse; font-family: sans-serif; font-size: 14px; }
    .custom-table th, .custom-table td { border: 1px solid #e0e0e0; padding: 10px; vertical-align: top; }
    .report-card { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #4CAF50; margin-bottom: 15px; }
    .review-card { background: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; border-left: 4px solid #2563eb; }
    .client-text { background: #f9fafb; padding: 12px; border-radius: 6px; font-size: 14px; color: #4b5563; margin-bottom: 10px; }
    .ai-reason { font-size: 13px; color: #059669; margin-top: 10px; font-style: italic; }
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
# 3. ОБРАБОТКА ДАННЫХ
# ==========================================
def process_claims_and_returns(claims_files, returned_files):
    report = []
    raw_claims = pd.concat([safe_read(f) for f in claims_files], ignore_index=True)
    df_c = raw_claims.drop_duplicates(subset=['srid']) if 'srid' in raw_claims.columns else raw_claims.drop_duplicates()
    
    report.append(f"📥 Претензии: Загружено {len(raw_claims)} строк.")
    if len(raw_claims) > len(df_c):
        report.append(f"🧹 Удалено локальных дублей: {len(raw_claims) - len(df_c)} шт.")

    df_final = df_c
    if returned_files:
        df_r = pd.concat([safe_read(f) for f in returned_files], ignore_index=True).drop_duplicates(subset=['srid'], keep='last')
        sku_map = df_r.dropna(subset=['supplierArticle']).set_index(df_r['nmId'].astype(str).str.replace(r'\.0$', '', regex=True))['supplierArticle'].to_dict()
        df_c['nm_id_clean'] = df_c['nm_id'].astype(str).str.replace(r'\.0$', '', regex=True)
        df_final = pd.merge(df_c, df_r, on='srid', how='left')
        df_final['supplierArticle'] = df_final['supplierArticle'].fillna(df_final['nm_id_clean'].map(sku_map))
        report.append(f"📦 Склад: Успешно привязаны артикулы для {df_final['supplierArticle'].notna().sum()} заявок.")

    res_df = pd.DataFrame()
    res_df['Дата'] = pd.to_datetime(df_final.get('dt', ''), errors='coerce').dt.strftime('%d.%m.%Y')
    res_df['Артикул'] = df_final.get('supplierArticle', 'Без артикула')
    res_df['Текст_Клиента'] = df_final.get('user_comment', '')
    res_df['SRID'] = df_final.get('srid', '')
    res_df['Источник заявки'] = df_final.get('claim_type', '')
    
    for i in range(1, 14): res_df[f"Кат {i}"] = ""
    res_df['Обоснование'] = ""
    res_df['Корректировка'] = ""
    
    for col in df_final.columns:
        if col not in ['dt', 'supplierArticle', 'user_comment', 'srid', 'claim_type', 'nm_id_clean'] and not col.endswith('_drop'):
            res_df[COLUMN_NAMES_RU.get(col, col)] = df_final[col]
            
    return res_df, report

def process_litestat(litestat_files):
    report = []
    all_o = [df for df in [safe_read(f) for f in litestat_files] if not df.empty]
    
    if not all_o:
        report.append("❌ Ошибка: Не удалось прочитать файлы Litestat.")
        return pd.DataFrame(), report
        
    df_o = pd.concat(all_o, ignore_index=True)
    report.append(f"📥 Litestat: Успешно прочитано {len(df_o)} сырых строк.")
    
    sku_col = next((c for c in df_o.columns if 'артикул' in str(c).lower()), None)
    qty_col = next((c for c in df_o.columns if 'итого заказано' in str(c).lower()), None)
    if not qty_col:
        qty_col = next((c for c in df_o.columns if any(kw in str(c).lower() for kw in ['заказано', 'количество', 'кол-во'])), None)
    
    if sku_col and qty_col:
        df_o[qty_col] = pd.to_numeric(df_o[qty_col].astype(str).str.replace(' ', '').str.replace(',', '.'), errors='coerce').fillna(0)
        df_o = df_o[df_o[sku_col].notna()]
        df_o = df_o[~df_o[sku_col].astype(str).str.lower().str.contains('итого|всего|total', na=False)]
        
        df_ord_agg = df_o.groupby(sku_col)[qty_col].sum().reset_index()
        df_ord_agg.columns = ['Артикул', 'Заказы шт.']
        
        total_sum = df_ord_agg['Заказы шт.'].sum()
        report.append(f"✅ Агрегировано по столбцу **'{qty_col}'**. Общая сумма заказов: **{int(total_sum)} шт.**")
        return df_ord_agg, report
    else:
        cols_preview = ", ".join([str(c) for c in df_o.columns[:10]])
        report.append(f"❌ Ошибка: Нужные колонки не найдены. Вижу: [{cols_preview}...]")
        return pd.DataFrame(), report
        
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
                batch.append({"id": f"REF_{idx}", "text": f"Артикул: {row.get('Артикул','')}. Текст: {row.get('Текст_Клиента','')}"})
            else:
                # Собираем текущие теги, чтобы Grok видел, что проверять
                current_tags = [str(c) for c in range(1, 14) if str(row.get(f'Кат {c}', '')).strip() in ['1', '1.0', '+', 'v', 'да', 'true']]
                tags_str = ", ".join(current_tags) if current_tags else "Нет тегов"
                batch.append({"id": f"REF_{idx}", "text": f"Текст: {row.get('Текст_Клиента','')}. Текущие теги (ID): {tags_str}"})
                
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

page = st.sidebar.radio("Навигация", ["🤖 Робот-Загрузчик", "🔬 ИИ Тегирование", "📝 Модерация", "🧠 Обучение ИИ", "📊 Дашборд", "📜 Системный Журнал"])

if page == "🤖 Робот-Загрузчик":
    st.title("🤖 Робот-Загрузчик")
    
    with st.expander("1. Претензии и Склад", expanded=True):
        c1, c2 = st.columns(2)
        f_claims = c1.file_uploader("📂 Загрузите Претензии", accept_multiple_files=True, key=f"cl_{st.session_state.claims_key}")
        f_returned = c2.file_uploader("📂 Загрузите Склад", accept_multiple_files=True)
        
        if st.button("🚀 Синхронизировать Претензии", type="primary"):
            if f_claims:
                with st.spinner("Склеиваем данные и проверяем дубликаты..."):
                    final_tab, report_log = process_claims_and_returns(f_claims, f_returned)
                    client = get_gspread_client()
                    ws_ret = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                    
                    existing_data = ws_ret.get_all_records(expected_headers=final_tab.columns.tolist())
                    existing_srids = set([str(row.get('SRID', '')) for row in existing_data if row.get('SRID')])
                    report_log.append(f"☁️ Найдено в Google Таблице: {len(existing_data)} записей.")
                    
                    new_data = final_tab[~final_tab['SRID'].astype(str).isin(existing_srids)]
                    duplicates_gs = len(final_tab) - len(new_data)
                    if duplicates_gs > 0: report_log.append(f"🛡️ Отсеяно дублей (уже есть в Google): {duplicates_gs} шт.")
                    report_log.append(f"✅ К добавлению: **{len(new_data)} новых строк**.")

                    if not new_data.empty:
                        if not existing_data: ws_ret.update('A1', [new_data.columns.tolist()])
                        ws_ret.update(f'A{len(ws_ret.get_all_values()) + 1}', new_data.fillna('').values.tolist())
                        st.success("Данные успешно добавлены!")
                    else: st.info("Новых уникальных заявок не найдено.")
                    
                    st.markdown(f'<div class="report-card">{"<br>".join(report_log)}</div>', unsafe_allow_html=True)
                    st.session_state.claims_key += 1
            else: st.warning("Загрузите файл претензий!")

    with st.expander("2. Litestat (Заказы)"):
        f_litestat = st.file_uploader("📂 Отчет Litestat", accept_multiple_files=True, key=f"ls_{st.session_state.litestat_key}")
        if st.button("📊 Обновить данные по Заказам"):
            if f_litestat:
                with st.spinner("Анализируем заказы..."):
                    orders_tab, report_log = process_litestat(f_litestat)
                    if not orders_tab.empty:
                        client = get_gspread_client()
                        ws_ord = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
                        ws_ord.clear()
                        ws_ord.update('A1', [orders_tab.columns.tolist()] + orders_tab.values.tolist())
                        st.success(f"Обновлено! Сумма: {int(orders_tab['Заказы шт.'].sum())}")
                    st.markdown(f'<div class="report-card">{"<br>".join(report_log)}</div>', unsafe_allow_html=True)
                    st.session_state.litestat_key += 1
            else: st.warning("Загрузите файл Litestat!")

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
        return any(str(row.get(f'Кат {i}','')).strip().lower() in ['1','1.0','+','v','да','true'] for i in range(1,14))
    
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
                                        header = f"кат {cat_num}"
                                        if header in header_map_clean:
                                            batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['1']]})

                        # ПРИНУДИТЕЛЬНАЯ РАЗМЕТКА (100% заполняемость)
                        skipped_rows = []
                        for idx, row in chunk.iterrows():
                            row_idx = idx + 2
                            if row_idx not in tagged_row_idxs:
                                skipped_rows.append(str(row_idx))
                                header = "кат 12" # Принудительно ставим "Не подошло"
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
                                    header = f"кат {c}"
                                    if header in header_map_clean:
                                        batch_updates.append({'range': f"{header_map_clean[header]}{row_idx}", 'values': [['']]})
                                # 2. Ставим новые правильные
                                for cat_val in cats_array:
                                    cat_num_match = re.search(r'\d+', str(cat_val))
                                    if cat_num_match:
                                        cat_num = int(cat_num_match.group())
                                        header = f"кат {cat_num}"
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
    st.markdown("Проверьте теги, поставленные ИИ, и результаты аудита Grok.")

    # Добавляем CSS для эффекта увеличения фото при наведении
    st.markdown("""
    <style>
    .hover-zoom {
        width: 6%;
        border-radius: 4px;
        margin-right: 5px;
        transition: transform 0.2s;
        cursor: pointer;
    }
    .hover-zoom:hover {
        transform: scale(5);
        z-index: 999;
        position: relative;
    }
    </style>
    """, unsafe_allow_html=True)

    try:
        client = get_gspread_client()
        ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
        data = ws.get_all_values()
        
        if len(data) > 1:
            headers = [str(h).strip() for h in data[0]]
            df = pd.DataFrame(data[1:], columns=headers)
            
            if 'Аудит' not in df.columns: df['Аудит'] = ''
            if 'Комментарий' not in df.columns: df['Комментарий'] = ''
            if 'Корректировка' not in df.columns: df['Корректировка'] = ''

            def has_tags(row):
                return any(str(row.get(f'Кат {i}','')).strip().lower() in ['1','1.0','+','v','да','true'] for i in range(1,14))
            df['has_any_tag'] = df.apply(has_tags, axis=1)

            filter_option = st.radio("Кого проверяем?", ["Все размеченные (без ручной корректировки)", "Только с ОШИБКОЙ от аудитора (Grok)"], horizontal=True)
            
            if "ОШИБКОЙ" in filter_option:
                to_review = df[(df['has_any_tag']) & (df['Корректировка'].astype(str).str.strip() == '') & (df['Аудит'].astype(str).str.upper() == 'ОШИБКА')].head(20)
            else:
                to_review = df[(df['has_any_tag']) & (df['Корректировка'].astype(str).str.strip() == '')].head(20)

            if not to_review.empty:
                cats_list = list(CATEGORIES.values())
                reverse_cats = {v.strip().lower(): k for k, v in CATEGORIES.items()}
                header_map_clean = {str(name).strip().lower(): get_col_letter(idx) for idx, name in enumerate(headers)}
                
                for idx, row in to_review.iterrows():
                    row_index_gs = idx + 2 
                    with st.container():
                        st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
                        col_info, col_photos = st.columns([1.5, 1])
                        
                        current_tags = []
                        for c in range(1, 14):
                            if str(row.get(f'Кат {c}', '')).strip().lower() in ['1','1.0','+','v','да','true']:
                                current_tags.append(CATEGORIES.get(c, f"Категория {c}"))
                        tags_str = "; ".join(current_tags) if current_tags else "Нет"
                        
                        with col_info:
                            st.markdown(f"**Артикул:** {row.get('Артикул', '---')} | **Дата:** {row.get('Дата', '')}")
                            st.markdown(f"*{row.get('Текст_Клиента', 'Нет текста')}*")
                            st.markdown(f"🤖 **Тег ИИ:** {tags_str}")
                            
                            if str(row.get('Аудит', '')).strip():
                                st.markdown(f"🕵️‍♂️ **Аудит Grok:** {row.get('Аудит', '')} | 📝 **Коммент:** {row.get('Комментарий', '')}")
                        
                        with col_photos:
                            photos_raw = str(row.get('Фотографии', '')) + " " + str(row.get('Видео', ''))
                            urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[]+', photos_raw)
                            
                            if urls:
                                media_html = ""
                                for u in urls[:5]:
                                    clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                                    if clean_url.startswith("//"): clean_url = "https:" + clean_url
                                    
                                    if '.mp4' in clean_url.lower() or '.mov' in clean_url.lower():
                                        st.video(clean_url)
                                    else:
                                        # Применяем новый класс hover-zoom
                                        media_html += f'<a href="{clean_url}" target="_blank"><img src="{clean_url}" class="hover-zoom"></a>'
                                st.markdown(media_html, unsafe_allow_html=True)
                            else:
                                st.write("Нет фото/видео")
                        
                        st.markdown("**Быстрый выбор категорий:**")
                        cat_cols = st.columns(3)
                        selected_cats = []
                        for i, cat in enumerate(cats_list):
                            # Убрали автоматическое проставление галочек (value=False)
                            if cat_cols[i % 3].checkbox(cat, value=False, key=f"cat_{row_index_gs}_{i}"):
                                selected_cats.append(cat)
                        
                        col_other, col_btn = st.columns([3, 1])
                        other_text = col_other.text_input("Другая причина", key=f"other_{row_index_gs}", label_visibility="collapsed", placeholder="Свой вариант...")
                        if other_text: selected_cats.append(other_text.strip())
                        
                        if col_btn.button("💾 Сохранить", key=f"btn_{row_index_gs}", type="primary", use_container_width=True):
                            final_string = "; ".join(selected_cats) if selected_cats else "Ок (Подтверждено)"
                            
                            batch_updates = []
                            for c in range(1, 14):
                                header = f"кат {c}"
                                if header in header_map_clean:
                                    batch_updates.append({'range': f"{header_map_clean[header]}{row_index_gs}", 'values': [['']]})
                                    
                            for cat_name in selected_cats:
                                clean_tag = cat_name.strip().lower()
                                if clean_tag in reverse_cats:
                                    cat_num = reverse_cats[clean_tag]
                                    header = f"кат {cat_num}"
                                    if header in header_map_clean:
                                        batch_updates.append({'range': f"{header_map_clean[header]}{row_index_gs}", 'values': [['1']]})
                            
                            if "корректировка" in header_map_clean:
                                batch_updates.append({'range': f"{header_map_clean['корректировка']}{row_index_gs}", 'values': [[final_string]]})
                            
                            if batch_updates: ws.batch_update(batch_updates)
                            
                            ws_mem = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
                            ws_mem.append_row([row.get('Текст_Клиента', ''), final_string])
                            
                            st.success("Сохранено!")
                            st.rerun()
            else: 
                st.success("🎉 Очередь на модерацию пуста!")
        else:
            st.warning("Таблица пуста.")
    except Exception as e:
        st.error(f"Ошибка загрузки данных модерации: {e}")
        
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
                    text_cols = [c for c in df_import.columns if str(c).lower().strip() in ['текст отзыва', 'достоинства', 'недостатки', 'текст клиента', 'текст_клиента', 'user_comment']]
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
# 7. АНАЛИТИКА И ДАШБОРД
# ==========================================

elif page == "📊 Дашборд":
    st.title("📊 Аналитика, Инвойсы и Матрица")
    
    st.markdown("""
    <style>
    .detail-card { border: 1px solid #ddd; padding: 15px; border-radius: 8px; margin-bottom: 15px; background-color: #f9f9f9; }
    .detail-text { font-size: 16px; margin-bottom: 10px; color: #333; }
    .hover-zoom-small { width: 40px; height: 40px; object-fit: cover; border-radius: 4px; margin-right: 5px; transition: transform 0.2s; }
    .hover-zoom-small:hover { transform: scale(5); z-index: 999; position: relative; }
    </style>
    """, unsafe_allow_html=True)

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN)
        ws_returns = sheet.worksheet("Возвраты")
        df = pd.DataFrame(ws_returns.get_all_records())
        
        if not df.empty:
           # --- ИНТЕГРАЦИЯ ИНВОЙСОВ ИЗ ДРУГОЙ ТАБЛИЦЫ ---
            try:
                # Берем ID второй таблицы из секретов
                INVOICE_SHEET_ID = st.secrets["SPREADSHEET_ID_INVOICES"]
                # Открываем второй файл
                sheet_inv = client.open_by_key(INVOICE_SHEET_ID)
                # Укажите здесь точное название вкладки (листа) внутри файла с инвойсами
                # Обычно это "Лист 1" или "Sheet1", если вы его не переименовывали
                ws_inv = sheet_inv.worksheet("Лист 1") 
                df_inv = pd.DataFrame(ws_inv.get_all_records())
                
                if not df_inv.empty and 'Артикул' in df_inv.columns:
                    df_inv_unique = df_inv.drop_duplicates(subset=['Артикул'])
                    if 'Инвойс' in df.columns: df = df.drop(columns=['Инвойс'])
                    if 'Номер поставки' in df.columns: df = df.drop(columns=['Номер поставки'])
                    
                    cols_to_merge = ['Артикул']
                    if 'Инвойс' in df_inv.columns: cols_to_merge.append('Инвойс')
                    if 'Номер поставки' in df_inv.columns: cols_to_merge.append('Номер поставки')
                    df = df.merge(df_inv_unique[cols_to_merge], on='Артикул', how='left')
                    
            except Exception as e:
                st.warning(f"Не удалось подтянуть данные из файла инвойсов: {e}")
            if not df_inv.empty and 'Артикул' in df_inv.columns:
                df_inv_unique = df_inv.drop_duplicates(subset=['Артикул'])
                if 'Инвойс' in df.columns: df = df.drop(columns=['Инвойс'])
                if 'Номер поставки' in df.columns: df = df.drop(columns=['Номер поставки'])
                
                cols_to_merge = ['Артикул']
                if 'Инвойс' in df_inv.columns: cols_to_merge.append('Инвойс')
                if 'Номер поставки' in df_inv.columns: cols_to_merge.append('Номер поставки')
                df = df.merge(df_inv_unique[cols_to_merge], on='Артикул', how='left')
                
            if 'Инвойс' not in df.columns: df['Инвойс'] = 'Не указан'
            if 'Номер поставки' not in df.columns: df['Номер поставки'] = 'Не указан'

            def has_tags(row):
                return any(str(row.get(f'Кат {i}','')).strip().lower() in ['1','1.0','+','v','да','true'] for i in range(1, 14))
            df['Размечено'] = df.apply(has_tags, axis=1)
            
            st.markdown("### 🔍 Глобальные фильтры")
            f_col1, f_col2 = st.columns(2)
            inv_list = ['Все'] + sorted(list(set([str(x) for x in df['Инвойс'] if str(x).strip()])))
            sku_list = ['Все'] + sorted(list(set([str(x) for x in df['Артикул'] if str(x).strip()])))
            
            selected_inv = f_col1.selectbox("Инвойс / Поставка:", inv_list)
            selected_sku = f_col2.selectbox("Артикул:", sku_list)
            
            df_filtered = df.copy()
            if selected_inv != 'Все': df_filtered = df_filtered[df_filtered['Инвойс'].astype(str) == selected_inv]
            if selected_sku != 'Все': df_filtered = df_filtered[df_filtered['Артикул'].astype(str) == selected_sku]

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
            
            matrix_data = []
            for idx, row in df_filtered[df_filtered['Размечено']].iterrows():
                art = str(row.get('Артикул', 'Без артикула')).strip()
                if not art: art = 'Без артикула'
                for i in range(1, 14):
                    if str(row.get(f'Кат {i}','')).strip().lower() in ['1','1.0','+','v','да','true']:
                        cat_name = f"{i}. {CATEGORIES.get(i)}"
                        matrix_data.append({'Артикул': art, 'Причина': cat_name, 'Инвойс': row.get('Инвойс')})
            
            if matrix_data:
                df_matrix = pd.DataFrame(matrix_data)
                
                st.markdown("### 🧮 Тепловая Матрица: Артикул — Причина (Кликабельно)")
                pivot_df = pd.crosstab(df_matrix['Причина'], df_matrix['Артикул'])
                pivot_df['ID'] = [int(x.split('.')[0]) for x in pivot_df.index]
                pivot_df = pivot_df.sort_values('ID').drop('ID', axis=1)
                
                import plotly.express as px
                fig_matrix = px.imshow(pivot_df, text_auto=True, color_continuous_scale='Blues', aspect="auto")
                
                # Обработка клика по матрице
                selection = st.plotly_chart(fig_matrix, use_container_width=True, on_select="rerun")
                
                click_sku = None
                click_reason = None
                if selection and "selection" in selection and selection["selection"].get("points"):
                    points = selection["selection"]["points"]
                    if points:
                        click_sku = points[0]["x"]
                        click_reason = points[0]["y"]

                st.markdown("### 📦 Проблемные Инвойсы")
                inv_counts = df_matrix['Инвойс'].value_counts().reset_index()
                inv_counts.columns = ['Инвойс / Поставка', 'Количество дефектов']
                st.dataframe(inv_counts.head(10), use_container_width=True)

                st.markdown("### 🔎 Детализация пересечения")
                det_c1, det_c2 = st.columns(2)
                
                # Если был клик по матрице, применяем эти значения по умолчанию
                all_skus = sorted(df_matrix['Артикул'].unique())
                all_reasons = sorted(df_matrix['Причина'].unique(), key=lambda x: int(x.split('.')[0]))
                
                idx_sku = all_skus.index(click_sku) if click_sku in all_skus else 0
                idx_reason = all_reasons.index(click_reason) if click_reason in all_reasons else 0
                
                det_sku = det_c1.selectbox("Артикул для детализации:", all_skus, index=idx_sku)
                det_reason = det_c2.selectbox("Проблема:", all_reasons, index=idx_reason)
                
                reason_id = int(det_reason.split('.')[0])
                detail_df = df_filtered[(df_filtered['Артикул'] == det_sku) & (df_filtered[f'Кат {reason_id}'].astype(str).str.strip().str.lower().isin(['1','1.0','+','v','да','true']))]
                
                if not detail_df.empty:
                    st.success(f"Найдено записей: {len(detail_df)}")
                    
                    # Формируем карточки для полного отображения текста и медиа
                    for d_idx, d_row in detail_df.iterrows():
                        st.markdown('<div class="detail-card">', unsafe_allow_html=True)
                        
                        # Текст клиента с полным переносом
                        client_text = str(d_row.get('Текст_Клиента', ''))
                        st.markdown(f"<div class='detail-text'><b>Текст клиента:</b><br>{client_text}</div>", unsafe_allow_html=True)
                        
                        col_a, col_b = st.columns([2, 1])
                        with col_a:
                            st.markdown(f"**Поставка:** {d_row.get('Номер поставки', '---')} | **Инвойс:** {d_row.get('Инвойс', '---')} | **Дата:** {d_row.get('Дата', '---')}")
                            st.markdown(f"🕵️‍♂️ **Аудит:** {d_row.get('Аудит', '---')} | 📝 **Коммент:** {d_row.get('Комментарий', '---')}")
                        
                        with col_b:
                            photos_raw = str(d_row.get('Фотографии', '')) + " " + str(d_row.get('Видео', ''))
                            urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[]+', photos_raw)
                            if urls:
                                media_html = ""
                                for u in urls[:5]:
                                    clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                                    if clean_url.startswith("//"): clean_url = "https:" + clean_url
                                    if '.mp4' not in clean_url.lower() and '.mov' not in clean_url.lower():
                                        media_html += f'<a href="{clean_url}" target="_blank"><img src="{clean_url}" class="hover-zoom-small"></a>'
                                st.markdown(media_html, unsafe_allow_html=True)
                        st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.info("Нет данных по этому пересечению.")
            else:
                st.info("Нет данных для построения аналитики.")
        else:
            st.warning("Таблица пуста.")
            
    except Exception as e:
        st.error(f"Ошибка при загрузке дашборда: {e}")
        
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
