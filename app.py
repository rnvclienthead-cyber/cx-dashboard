import streamlit as st
import asyncio
import aiohttp
import gspread
import json
import re
import pandas as pd
import plotly.express as px
import io
from datetime import datetime
from google.oauth2.service_account import Credentials

# ==========================================
# 1. ИНИЦИАЛИЗАЦИЯ И SECRETS
# ==========================================
st.set_page_config(page_title="CX AI Enterprise", layout="wide")

try:
    YANDEX_API_KEY = st.secrets["YANDEX_API_KEY"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
    SPREADSHEET_ID_MAIN = st.secrets["SPREADSHEET_ID_MAIN"]
    SPREADSHEET_ID_INVOICES = st.secrets["SPREADSHEET_ID_INVOICES"]
    GOOGLE_CREDS = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error("❌ Ошибка конфигурации Secrets!")
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
    'nm_id': 'Артикул WB (Заявка)', 'user_comment': 'Комментарий покупателя', 
    'wb_comment': 'Ответ покупателю', 'dt': 'Дата заявки', 'imt_name': 'Название товара', 
    'order_dt': 'Дата заказа', 'dt_update': 'Дата рассмотрения', 'photos': 'Фотографии', 
    'video_paths': 'Видео', 'price': 'Цена', 'srid': 'ID заказа (SRID)', 'supplierArticle': 'Артикул продавца', 
    'nmId': 'Артикул WB', 'incomeID': 'Номер поставки'
}

st.markdown("""
<style>
    .img-zoom { width: 60px; height: 60px; object-fit: cover; border-radius: 5px; transition: transform 0.2s ease-in-out; cursor: pointer; }
    .img-zoom:hover { transform: scale(9.0); z-index: 999; position: relative; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
    .custom-table { width: 100%; border-collapse: collapse; font-family: sans-serif; font-size: 14px; }
    .custom-table th, .custom-table td { border: 1px solid #e0e0e0; padding: 10px; vertical-align: top; }
    .report-card { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #4CAF50; margin-bottom: 15px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. БАЗА ДАННЫХ, ПАМЯТЬ И ИИ
# ==========================================
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

async def fetch_ai_tags(session, batch, memory, model="yandex"):
    content = "\n".join([f"ID {i['id']}: {i['text']}" for i in batch])
    system_prompt = f"""Ты эксперт контроля качества. Размети отзывы по категориям: {list(CATEGORIES.values())}.
    ПРАВИЛО КАТЕГОРИИ 12: Если клиент хвалит, но есть мелкий дефект (или рейтинг 4-5) - СТРОГО Категория 12.
    ОПЫТ ОШИБОК: {memory}
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "tags": ["Категория"], "reasoning": "..."}}]}}"""

    if model == "yandex":
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "x-folder-id": FOLDER_ID}
        payload = {
            "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
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

async def run_ai_batch_processing(df_to_tag, model_choice):
    memory = load_ai_memory()
    results = []
    async with aiohttp.ClientSession() as session:
        batch = []
        for idx, row in df_to_tag.iterrows():
            batch.append({"id": idx, "text": f"Артикул: {row.get('Артикул','')}. Текст: {row.get('Текст_Клиента','')}"})
            if len(batch) >= 10:
                res = await fetch_ai_tags(session, batch, memory, model_choice)
                results.extend(res)
                batch = []
        if batch:
            res = await fetch_ai_tags(session, batch, memory, model_choice)
            results.extend(res)
    return results

# ==========================================
# 3. ВСЕЯДНАЯ ЧИТАЛКА И ОБРАБОТКА ДАННЫХ
# ==========================================
def safe_read(file_obj):
    bytes_data = file_obj.getvalue()
    name = file_obj.name.lower()
    
    st.warning(f"🕵️ ОТЛАДКА ФАЙЛА: {name}")
    
    # 1. Заглядываем внутрь файла (показываем первые 300 символов)
    try:
        raw_text = bytes_data[:300].decode('utf-8', errors='ignore')
        st.code(f"Что физически находится внутри файла:\n{raw_text}")
    except:
        st.code("Файл чисто бинарный, прочитать текст не удалось.")

    # 2. Пытаемся открыть как Excel и ловим точную ошибку
    try:
        df = pd.read_excel(io.BytesIO(bytes_data))
        st.success("✅ Успешно прочитано как Excel!")
        return df
    except Exception as e:
        st.error(f"❌ Ошибка при чтении Excel: {type(e).__name__} — {e}")
        
    # 3. Пытаемся открыть как CSV и ловим ошибку
    try:
        df = pd.read_csv(io.BytesIO(bytes_data), sep=';', encoding='utf-8')
        st.success("✅ Успешно прочитано как CSV!")
        return df
    except Exception as e:
        st.error(f"❌ Ошибка при чтении CSV: {type(e).__name__} — {e}")
        
    return pd.DataFrame()
    
def process_claims_and_returns(claims_files, returned_files):
    report = []
    # 1. Читаем Претензии
    raw_claims = pd.concat([safe_read(f) for f in claims_files], ignore_index=True)
    df_c = raw_claims.drop_duplicates(subset=['srid']) if 'srid' in raw_claims.columns else raw_claims.drop_duplicates()
    
    report.append(f"📥 Претензии: Загружено {len(raw_claims)} строк.")
    if len(raw_claims) > len(df_c):
        report.append(f"🧹 Удалено локальных дублей: {len(raw_claims) - len(df_c)} шт.")

    df_final = df_c
    # 2. Читаем Возвраты (Склад)
    if returned_files:
        df_r = pd.concat([safe_read(f) for f in returned_files], ignore_index=True).drop_duplicates(subset=['srid'], keep='last')
        sku_map = df_r.dropna(subset=['supplierArticle']).set_index(df_r['nmId'].astype(str).str.replace(r'\.0$', '', regex=True))['supplierArticle'].to_dict()
        df_c['nm_id_clean'] = df_c['nm_id'].astype(str).str.replace(r'\.0$', '', regex=True)
        df_final = pd.merge(df_c, df_r, on='srid', how='left')
        df_final['supplierArticle'] = df_final['supplierArticle'].fillna(df_final['nm_id_clean'].map(sku_map))
        report.append(f"📦 Склад: Успешно привязаны артикулы для {df_final['supplierArticle'].notna().sum()} заявок.")

    # 3. Форматирование
    res_df = pd.DataFrame()
    res_df['Дата'] = pd.to_datetime(df_final.get('dt', ''), errors='coerce').dt.strftime('%d.%m.%Y')
    res_df['Артикул'] = df_final.get('supplierArticle', 'Без артикула')
    res_df['Текст_Клиента'] = df_final.get('user_comment', '')
    res_df['SRID'] = df_final.get('srid', '')
    
    for i in range(1, 14): res_df[f"Кат {i}"] = ""
    res_df['Обоснование'] = ""
    res_df['Корректировка'] = ""
    
    for col in df_final.columns:
        if col not in ['dt', 'supplierArticle', 'user_comment', 'srid', 'nm_id_clean'] and not col.endswith('_drop'):
            res_df[COLUMN_NAMES_RU.get(col, col)] = df_final[col]
            
    return res_df, report

def process_litestat(litestat_files):
    report = []
    # Читаем все файлы и отбрасываем пустые (если не прочитались)
    all_o = [df for df in [safe_read(f) for f in litestat_files] if not df.empty]
    
    if not all_o:
        report.append("❌ Ошибка: Не удалось прочитать ни один файл Litestat.")
        return pd.DataFrame(), report
        
    df_o = pd.concat(all_o, ignore_index=True)
    report.append(f"📥 Litestat: Успешно прочитано {len(df_o)} сырых строк.")
    
    # Расширенный поиск колонок (на случай, если Litestat поменял названия)
    sku_col = next((c for c in df_o.columns if 'артикул' in str(c).lower()), None)
    qty_col = next((c for c in df_o.columns if any(kw in str(c).lower() for kw in ['заказано', 'количество', 'кол-во'])), None)
    
    if sku_col and qty_col:
        # Убираем возможные пробелы и мусор из чисел
        df_o[qty_col] = pd.to_numeric(df_o[qty_col].astype(str).str.replace(' ', '').str.replace(',', '.'), errors='coerce').fillna(0)
        
        df_ord_agg = df_o.groupby(sku_col)[qty_col].sum().reset_index()
        df_ord_agg.columns = ['Артикул', 'Заказы шт.']
        report.append(f"✅ Успешно агрегировано по {len(df_ord_agg)} уникальным артикулам.")
        return df_ord_agg, report
    else:
        # Выводим подсказку, какие колонки скрипт вообще увидел
        cols_preview = ", ".join([str(c) for c in df_o.columns[:5]])
        report.append(f"❌ Ошибка: В файле не найдены колонки 'Артикул' или 'Заказано'. Вижу такие колонки: [{cols_preview}...]")
        return pd.DataFrame(), report

# ==========================================
# 4. ИНТЕРФЕЙС И НАВИГАЦИЯ
# ==========================================
page = st.sidebar.radio("Навигация", ["📊 Дашборд Аналитики", "🤖 Робот-Загрузчик", "🔬 ИИ Тегирование", "🧠 Обучение ИИ"])

if page == "🤖 Робот-Загрузчик":
    st.title("🤖 Робот-Загрузчик Данных")
    
    # --- БЛОК 1: ПРЕТЕНЗИИ И ВОЗВРАТЫ ---
    st.markdown("### 1. Обработка Претензий и Склада")
    c1, c2 = st.columns(2)
    f_claims = c1.file_uploader("📂 Загрузите Претензии (Claims)", accept_multiple_files=True, key="claims")
    f_returned = c2.file_uploader("📂 Загрузите Склад (Returned)", accept_multiple_files=True, key="returns")
    
    if st.button("🚀 Синхронизировать Претензии", type="primary"):
        if f_claims:
            with st.spinner("Склеиваем данные и проверяем дубликаты..."):
                final_tab, report_log = process_claims_and_returns(f_claims, f_returned)
                
                # Подключение к Google и проверка дублей
                client = get_gspread_client()
                ws_ret = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                
                # Получаем существующие SRID
                existing_data = ws_ret.get_all_records(expected_headers=final_tab.columns.tolist())
                existing_srids = set([str(row.get('SRID', '')) for row in existing_data if row.get('SRID')])
                
                report_log.append(f"☁️ Найдено в Google Таблице: {len(existing_data)} записей.")
                
                # Фильтрация новых данных по SRID
                new_data = final_tab[~final_tab['SRID'].astype(str).isin(existing_srids)]
                duplicates_gs = len(final_tab) - len(new_data)
                
                if duplicates_gs > 0:
                    report_log.append(f"🛡️ Отсеяно дублей (уже есть в Google): {duplicates_gs} шт.")
                
                report_log.append(f"✅ К добавлению в таблицу: **{len(new_data)} новых строк**.")

                if not new_data.empty:
                    if not existing_data: # Если таблица пустая
                        ws_ret.update('A1', [new_data.columns.tolist()])
                    start_row = len(ws_ret.get_all_values()) + 1
                    ws_ret.update(f'A{start_row}', new_data.fillna('').values.tolist())
                    st.success("Данные успешно добавлены в Google Sheets!")
                else:
                    st.info("Новых уникальных заявок не найдено. Таблица не обновлялась.")
                
                st.markdown(f'<div class="report-card">{"<br>".join(report_log)}</div>', unsafe_allow_html=True)
        else:
            st.warning("Загрузите хотя бы файл претензий!")

    st.divider()

    # --- БЛОК 2: LITESTAT ---
    st.markdown("### 2. Загрузка Заказов (Litestat)")
    f_litestat = st.file_uploader("📂 Загрузите отчет Litestat", accept_multiple_files=True, key="litestat")
    
    if st.button("📊 Обновить данные по Заказам"):
        if f_litestat:
            with st.spinner("Анализируем заказы..."):
                orders_tab, report_log = process_litestat(f_litestat)
                
                if not orders_tab.empty:
                    client = get_gspread_client()
                    ws_ord = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
                    ws_ord.clear() # Литстат перезаписывает лист для актуальной аналитики
                    ws_ord.update('A1', [orders_tab.columns.tolist()] + orders_tab.values.tolist())
                    st.success("Данные по заказам обновлены в Google Sheets!")
                    
                st.markdown(f'<div class="report-card">{"<br>".join(report_log)}</div>', unsafe_allow_html=True)
        else:
            st.warning("Загрузите файл Litestat!")

elif page == "🔬 ИИ Тегирование":
    st.title("🔬 ИИ Тегирование")
    model_choice = st.radio("Нейросеть:", ["YandexGPT (yandex)", "Grok (grok)"])
    model_key = "yandex" if "Yandex" in model_choice else "grok"

    if st.button("🚀 ЗАПУСТИТЬ ТЕГИРОВАНИЕ"):
        with st.spinner(f"Работаем..."):
            client = get_gspread_client()
            ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
            df = pd.DataFrame(ws.get_all_records())
            
            unprocessed = df[df['Обоснование'] == '']
            if unprocessed.empty: st.success("Всё уже размечено!")
            else:
                st.warning(f"Найдено {len(unprocessed)} заявок. Ждите...")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(run_ai_batch_processing(unprocessed, model_key))
                
                for res in results:
                    row_idx = int(res['id']) + 2
                    for tag in res.get('tags', []):
                        cat_num = tag.split(':')[0].replace('Категория ', '')
                        try:
                            col_letter = chr(ord('E') - 1 + int(cat_num))
                            ws.update(f'{col_letter}{row_idx}', [['V']])
                        except: pass
                    ws.update(f'R{row_idx}', [[res.get('reasoning', '')]]) 
                st.success(f"✅ Размечено {len(results)} строк!")

elif page == "🧠 Обучение ИИ":
    st.title("🧠 Обучение ИИ")
    if st.button("💾 Сохранить правки"):
        client = get_gspread_client()
        ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
        ws_mem = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
        data = ws.get_all_values()
        try:
            corr_idx = data[0].index("Корректировка")
            new_ex = [[r[2], r[corr_idx]] for r in data[1:] if len(r) > corr_idx and str(r[corr_idx]).strip() != ""]
            if new_ex:
                ws_mem.append_rows(new_ex)
                st.success(f"Добавлено {len(new_ex)} примеров!")
            else: st.info("Нет новых правок.")
        except: st.error("Колонка 'Корректировка' не найдена.")

elif page == "📊 Дашборд Аналитики":
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
