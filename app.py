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

def load_ai_memory():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
        records = sheet.get_all_records()
        if records:
            return "\n".join([f"Текст: {r['Контент']} -> Правильный тег: {r['Правильные теги']}" for r in records])
    except: pass
    return "Опыта пока нет."

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
# 4. ИИ ДВИЖОК (РАЗДЕЛЬНЫЙ)
# ==========================================
async def fetch_ai_tags(session, batch, memory, model="yandex"):
    content = "\n".join([f"ID {i['id']}: {i['text']}" for i in batch])
    system_prompt = f"""Ты эксперт контроля качества. Размети отзывы по категориям: {list(CATEGORIES.values())}.
    ПРАВИЛО 12: Если клиент хвалит, но есть мелкий дефект (или рейтинг 4-5) - СТРОГО Категория 12.
    ОПЫТ ОШИБОК: {memory}
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "tags": ["Категория"], "reasoning": "..."}}]}}"""

    # Логика Yandex
    if "yandex" in model:
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "x-folder-id": FOLDER_ID}
        
        # Определяем, какую версию дергать: Lite или Pro
        yandex_model_name = "yandexgpt-lite" if model == "yandex-lite" else "yandexgpt"
        
        # Вот здесь скобки теперь закрыты и добавлены messages:
        payload = {
            "modelUri": f"gpt://{FOLDER_ID}/{yandex_model_name}/latest",
            "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
            "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": content}]
        }
        
        try:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    text = res['result']['alternatives'][0]['message']['text']
                    return json.loads(re.sub(r'```json|```', '', text).strip()).get('results', [])
        except: return []

    # Логика Grok
    elif model == "grok":
        url = "https://api.x.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "grok-beta",
            "temperature": 0.1,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
        }
        try:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    text = res['choices'][0]['message']['content']
                    return json.loads(re.sub(r'```json|```', '', text).strip()).get('results', [])
        except: return []
    return []

async def fetch_ai_crosscheck(session, batch, memory):
    content = "\n".join([f"ID {i['id']}: {i['text']}" for i in batch])
    system_prompt = f"""Ты строгий аудитор. Проверь теги, которые уже поставила первая нейросеть. 
    Учитывай наш опыт: {memory}.
    Если есть логическая ошибка (например, тег 'Производственный дефект', а суть в 'не подошел цвет'), исправь на правильную категорию.
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "tags": ["Категория"], "reasoning": "Исправлено: ..."}}]}}"""
    
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "grok-beta",
        "temperature": 0.1,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
    }
    try:
        async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
            if resp.status == 200:
                res = await resp.json()
                text = res['choices'][0]['message']['content']
                return json.loads(re.sub(r'```json|```', '', text).strip()).get('results', [])
    except: return []

async def run_ai_batch_processing(df_to_tag, model_choice, mode="tagging"):
    memory = load_ai_memory()
    results = []
    async with aiohttp.ClientSession() as session:
        batch = []
        for idx, row in df_to_tag.iterrows():
            if mode == "tagging":
                batch.append({"id": idx, "text": f"Артикул: {row.get('Артикул','')}. Текст: {row.get('Текст_Клиента','')}"})
            else:
                # В режиме проверки передаем еще и старое обоснование
                batch.append({"id": idx, "text": f"Текст: {row.get('Текст_Клиента','')}. Обоснование ИИ 1: {row.get('Обоснование','')}"})
                
            if len(batch) >= 10:
                if mode == "tagging": res = await fetch_ai_tags(session, batch, memory, model_choice)
                else: res = await fetch_ai_crosscheck(session, batch, memory)
                
                if res: results.extend(res)
                batch = []
                
        if batch:
            if mode == "tagging": res = await fetch_ai_tags(session, batch, memory, model_choice)
            else: res = await fetch_ai_crosscheck(session, batch, memory)
            if res: results.extend(res)
            
    return results
    
# ==========================================
# 5. ИНТЕРФЕЙС И НАВИГАЦИЯ
# ==========================================
# СНАЧАЛА ОБЪЯВЛЯЕМ ФУНКЦИЮ:
def get_col_letter(col_idx):
    if col_idx < 26: return chr(ord('A') + col_idx)
    return chr(ord('A') + (col_idx // 26) - 1) + chr(ord('A') + (col_idx % 26))

page = st.sidebar.radio("Навигация", ["🤖 Робот-Загрузчик", "🔬 ИИ Тегирование", "📝 Модерация", "🧠 Обучение ИИ", "📊 Дашборд"])

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
# 6. РУЧНАЯ МОДЕРАЦИЯ
# ==========================================

elif page == "🔬 ИИ Тегирование":
    st.title("🔬 ИИ Тегирование и Проверка")
    
    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
    headers = ws.row_values(1)
    # Приводим все заголовки к нижнему регистру и убираем пробелы для надежного поиска
    header_map_clean = {str(name).strip().lower(): get_col_letter(idx) for idx, name in enumerate(headers)}
    header_map_original = {str(name).strip(): get_col_letter(idx) for idx, name in enumerate(headers)}
    
    df = pd.DataFrame(ws.get_all_records())
    
    with st.expander("🛠 Рентген таблицы (Проверьте, видит ли робот ваши колонки)"):
        st.write("Робот нашел следующие колонки в Google Таблице:", header_map_original)
    
    t1, t2 = st.tabs(["1️⃣ Первичная разметка", "2️⃣ Перекрестная проверка (Grok)"])
    
    with t1:
        st.subheader("Разметка новых заявок")
        # Ищем колонку обоснования, даже если она называется чуть иначе
        unprocessed = df[df.get('Обоснование', '') == '']
        
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
                log_container = st.container() # Контейнер для вывода логов
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    for i in range(0, total_rows, batch_size):
                        chunk = unprocessed.iloc[i:i+batch_size]
                        progress = int(((i + len(chunk)) / total_rows) * 100)
                        status_text.text(f"⏳ Прогресс: {progress}% (строки {i} из {total_rows})")
                        
                        results = loop.run_until_complete(run_ai_batch_processing(chunk, model_key, mode="tagging"))
                        
                        # ВЫВОДИМ ОТВЕТ ИИ НА ЭКРАН (чтобы понять, не пустой ли он)
                        with log_container:
                            with st.expander(f"Сырой ответ ИИ (Пачка {i} - {i+len(chunk)})"):
                                st.json(results) if results else st.error("❌ ИИ ВЕРНУЛ ПУСТОТУ! (Сбой API или кривой ответ)")

                        for res in results:
                            row_idx = int(res['id']) + 2 
                            
                            # Бронебойная запись тегов (ищем только цифру)
                            for tag in res.get('tags', []):
                                import re
                                cat_num_match = re.search(r'\d+', tag)
                                if cat_num_match:
                                    cat_num = cat_num_match.group()
                                    target_header = f"кат {cat_num}" # Ищем в нижнем регистре
                                    
                                    if target_header in header_map_clean:
                                        ws.update(f"{header_map_clean[target_header]}{row_idx}", [['1']]) # Ставим 1, как в вашем скрипте Лилии
                                    else:
                                        log_container.warning(f"Не нашел колонку 'Кат {cat_num}' в таблице!")
                            
                            # Запись обоснования
                            if "обоснование" in header_map_clean:
                                ws.update(f"{header_map_clean['обоснование']}{row_idx}", [[res.get('reasoning', '')]])
                            else:
                                log_container.warning("Не нашел колонку 'Обоснование' в таблице!")
                                
                        progress_bar.progress(min(1.0, (i + len(chunk)) / total_rows))
                    
                    st.success("✅ Тегирование завершено! Проверьте Google Таблицу.")
                except Exception as e:
                    st.error(f"🛑 ПРОЦЕСС ОСТАНОВЛЕН ИЗ-ЗА ОШИБКИ: {e}")
        else:
            st.success("🎉 Все строки уже размечены!")

    with t2:
        st.subheader("Глубокая проверка (Аудит от Grok)")
        st.info("В разработке: Здесь появится аудит размеченных строк после успешного завершения первичного тегирования.")

elif page == "📝 Модерация":
    st.title("📋 Модерация (Ручная проверка)")
    st.markdown("Проверьте теги, поставленные ИИ. Выберите подходящие категории и нажмите «Сохранить».")

    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
    df = pd.DataFrame(ws.get_all_records())
    
    if 'Обоснование' in df.columns and 'Корректировка' in df.columns:
        to_review = df[(df['Обоснование'] != '') & (df['Корректировка'] == '')].head(20)
    else: to_review = pd.DataFrame()

    if not to_review.empty:
        cats_list = list(CATEGORIES.values())
        for idx, row in to_review.iterrows():
            row_index_gs = idx + 2 
            with st.container():
                st.markdown('<div class="review-card">', unsafe_allow_html=True)
                col_info, col_photos, col_action = st.columns([2, 1.5, 1.5])
                
                with col_info:
                    st.markdown(f"**Артикул:** {row.get('Артикул', '---')} | **Дата:** {row.get('Дата', '')}")
                    st.markdown(f"<div class='client-text'>{row.get('Текст_Клиента', 'Нет текста')}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='ai-reason'>🤖 Обоснование: {row.get('Обоснование', '')}</div>", unsafe_allow_html=True)
                
                with col_photos:
                    photos_raw = str(row.get('Фотографии', ''))
                    # Вытаскиваем чистые ссылки (избавляемся от формул и разделителей-точек с запятой)
                    urls = re.findall(r'https?://[^\s"\'\;]+', photos_raw)
                    
                    if urls:
                        photos_html = "".join([f'<img src="{url}" class="img-zoom">' for url in urls[:5]])
                        st.markdown(photos_html, unsafe_allow_html=True)
                    else:
                        st.write("Нет медиафайлов")
                
                with col_action:
                    selected_cats = []
                    for cat in cats_list:
                        if st.checkbox(cat, key=f"cat_{row_index_gs}_{cat}"): selected_cats.append(cat)
                    
                    other_text = st.text_input("Другая причина", key=f"other_{row_index_gs}")
                    if other_text: selected_cats.append(other_text.strip())
                    
                    if st.button("💾 Сохранить", key=f"btn_{row_index_gs}", type="primary", use_container_width=True):
                        final_string = "; ".join(selected_cats)
                        if not final_string: final_string = "Ок (Подтверждено)"
                        
                        headers = ws.row_values(1)
                        if "Корректировка" in headers:
                            col_letter = get_col_letter(headers.index("Корректировка"))
                            ws.update(f'{col_letter}{row_index_gs}', [[final_string]])
                            
                            ws_mem = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
                            ws_mem.append_row([row.get('Текст_Клиента', ''), final_string])
                            
                            st.success("Сохранено в базу!")
                            st.rerun()
                        else: st.error("Колонка 'Корректировка' не найдена.")
                st.markdown('</div>', unsafe_allow_html=True)
    else: st.success("🎉 Все новые возвраты проверены!")

# ==========================================
# 7. ДАШБОРД
# ==========================================

elif page == "🧠 Обучение ИИ":
    st.title("🧠 База знаний ИИ (Умный импорт)")
    st.markdown("Загрузите старый файл с проверенными отзывами или претензиями. Робот сам склеит текст из разных колонок, расшифрует номера тегов и загрузит их в память нейросети.")

    f_import = st.file_uploader("📂 Загрузить базу знаний (Excel/CSV)", type=['xlsx', 'csv', 'xls'])

    if st.button("📥 Обучить нейросеть", type="primary"):
        if f_import:
            with st.spinner("Анализируем колонки и склеиваем данные..."):
                df_import = safe_read(f_import)
                
                if not df_import.empty:
                    # 1. Ищем колонки с текстом (берем все три, если они есть)
                    text_cols = [c for c in df_import.columns if str(c).lower().strip() in ['текст отзыва', 'достоинства', 'недостатки']]
                    # Ищем колонку с тегами
                    tag_col = next((c for c in df_import.columns if 'какой тег' in str(c).lower()), None)

                    if not text_cols or not tag_col:
                        st.error("❌ Ошибка: В файле не найдены колонки 'Текст отзыва / Достоинства / Недостатки' или 'Какой тег'.")
                        st.write("Найденные колонки:", list(df_import.columns))
                    else:
                        memory_data = []
                        
                        # 2. Сборка данных
                        for idx, row in df_import.iterrows():
                            # Клеим текст из 3 колонок, игнорируя пустоты (NaN)
                            parts = []
                            for tc in text_cols:
                                val = str(row[tc])
                                if val and val.lower() != 'nan' and val.strip():
                                    parts.append(val.strip())
                            combined_text = " ".join(parts)

                            # Разбираем кривые теги ("1; 3", "2 4" и т.д.)
                            raw_tags = str(row[tag_col])
                            if raw_tags and raw_tags.lower() != 'nan' and combined_text:
                                # Регулярное выражение вытаскивает все числа из строки
                                import re
                                nums = re.findall(r'\d+', raw_tags)
                                
                                mapped_tags = []
                                for num in nums:
                                    cat_id = int(num)
                                    if cat_id in CATEGORIES:
                                        mapped_tags.append(CATEGORIES[cat_id])

                                if mapped_tags:
                                    # Склеиваем расшифрованные категории через точку с запятой
                                    final_tags_str = "; ".join(mapped_tags)
                                    memory_data.append([combined_text, final_tags_str])

                        # 3. Загрузка в Google Sheets
                        if memory_data:
                            try:
                                client = get_gspread_client()
                                sheet = client.open_by_key(SPREADSHEET_ID_MAIN)
                                
                                # Проверяем, есть ли лист, если нет - создаем
                                try:
                                    ws_mem = sheet.worksheet("Память_ИИ")
                                except gspread.exceptions.WorksheetNotFound:
                                    ws_mem = sheet.add_worksheet(title="Память_ИИ", rows="1000", cols="2")
                                    ws_mem.append_row(["Контент", "Правильные теги"])

                                # Заливаем все собранные данные разом
                                ws_mem.append_rows(memory_data)
                                st.success(f"✅ ИИ успешно изучил {len(memory_data)} новых примеров!")
                                st.balloons()
                            except Exception as e:
                                st.error(f"❌ Ошибка записи в Google Таблицу: {e}")
                        else:
                            st.warning("⚠️ Не найдено валидных строк для импорта (возможно, колонка тегов пустая).")
        else:
            st.warning("Пожалуйста, загрузите файл.")

elif page == "📊 Дашборд":
    st.title("📊 BI Аналитика")
    try:
        client = get_gspread_client()
        df = pd.DataFrame(client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты").get_all_records())
        inv_vals = client.open_by_key(SPREADSHEET_ID_INVOICES).sheet1.get_all_values()
        if len(inv_vals) > 1:
            inv_map = pd.DataFrame(inv_vals[1:], columns=[h.lower().strip() for h in inv_vals[0]]).groupby('номер поставки')['инвойс'].apply(lambda x: ', '.join(set(x))).to_dict()
            s_col = next((c for c in df.columns if str(c).lower().strip() in ['номер поставки', 'incomeid']), 'incomeID')
            df['Инвойс'] = df[s_col].astype(str).str.replace(r'\.0$', '', regex=True).map(inv_map).fillna("Не найден")
        
        f_art = st.sidebar.multiselect("Артикул", sorted(df['Артикул'].unique()))
        df_f = df[df['Артикул'].isin(f_art)] if f_art else df
        
        t_heat, t_sup, t_data = st.tabs(["🔥 Матрица", "📦 Поставки", "📋 Детализация"])
        with t_heat:
            h_data = []
            for art in df_f['Артикул'].unique():
                temp = df_f[df_f['Артикул'] == art]
                h_data.append({**{'Артикул': art}, **{f"Кат {i}": (temp[f"Кат {i}"] == "V").sum() for i in range(1,14) if f"Кат {i}" in temp.columns}})
            if h_data: st.plotly_chart(px.imshow(pd.DataFrame(h_data).set_index('Артикул'), text_auto=True, color_continuous_scale="Reds"), use_container_width=True)
        
        with t_sup:
            s_col = next((c for c in df_f.columns if str(c).lower().strip() in ['номер поставки', 'incomeid']), 'incomeID')
            st.plotly_chart(px.bar(df_f.groupby([s_col, 'Инвойс']).size().reset_index(name='Кол-во'), x=s_col, y='Кол-во', color='Инвойс', text_auto=True), use_container_width=True)
            
        with t_data:
            html = '<table class="custom-table"><tr><th>Дата</th><th>Артикул</th><th>Текст</th><th>Фото</th></tr>'
            for _, r in df_f.tail(50).iterrows():
                photos = "".join([f'<img src="{l if l.startswith("http") else "https:"+l}" class="img-zoom">' for l in str(r.get('Фотографии','')).split(';')[:3] if l])
                html += f"<tr><td>{r.get('Дата','')}</td><td>{r.get('Артикул','')}</td><td>{r.get('Текст_Клиента','')}</td><td>{photos}</td></tr>"
            st.markdown(html + '</table>', unsafe_allow_html=True)
    except Exception as e: st.warning(f"Загрузите файлы через Робота! ({e})")
