import streamlit as st
import asyncio
import aiohttp
import gspread
import json
import re
import os
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from google.oauth2.service_account import Credentials
import io

# ==========================================
# 1. ИНИЦИАЛИЗАЦИЯ И SECRETS
# ==========================================
st.set_page_config(page_title="CX AI Enterprise Dashboard", layout="wide")

try:
    YANDEX_API_KEY = st.secrets["YANDEX_API_KEY"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
    SPREADSHEET_ID_MAIN = st.secrets["SPREADSHEET_ID_MAIN"]
    SPREADSHEET_ID_INVOICES = st.secrets["SPREADSHEET_ID_INVOICES"]
    GOOGLE_CREDS = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"❌ Ошибка конфигурации Secrets: {e}")
    st.stop()

# Константы категорий
CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}
CRITICAL_DEFECTS = ["Кат 1", "Кат 2", "Кат 4", "Кат 5", "Кат 7", "Кат 8", "Кат 9"]

COLUMN_NAMES_RU = {
    'claim_type': 'Источник заявки', 'status': 'Решение', 'status_ex': 'Статус товара',
    'nm_id': 'Артикул WB (Заявка)', 'user_comment': 'Комментарий покупателя', 
    'wb_comment': 'Ответ покупателю', 'dt': 'Дата заявки', 'imt_name': 'Название товара', 
    'order_dt': 'Дата заказа', 'dt_update': 'Дата рассмотрения', 'photos': 'Фотографии', 
    'video_paths': 'Видео', 'price': 'Цена (Заявка)', 'currency_code': 'Валюта', 
    'srid': 'ID заказа (SRID)', 'delivery_dt': 'Дата доставки', 
    'supplierArticle': 'Артикул продавца', 'nmId': 'Артикул WB', 
    'incomeID': 'Номер поставки', 'isSupply': 'Договор поставки'
}

COLS_CLAIMS = ['claim_type', 'status', 'status_ex', 'nm_id', 'user_comment', 'wb_comment', 'dt', 'imt_name', 'order_dt', 'srid']
COLS_RETURNED = ['date', 'supplierArticle', 'nmId', 'barcode', 'incomeID', 'supplyID', 'isSupply', 'srid']

# CSS для оформления (включая зум 9.0x)
st.markdown("""
<style>
    .img-zoom { width: 60px; height: 60px; object-fit: cover; border-radius: 5px; transition: transform 0.2s ease-in-out; cursor: pointer; }
    .img-zoom:hover { transform: scale(9.0); z-index: 999; position: relative; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
    .report-card { background-color: #f0f2f6; padding: 20px; border-radius: 10px; border-left: 5px solid #ff4b4b; margin: 10px 0; }
    .stTable { font-size: 12px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. СЕРВИСНЫЕ ФУНКЦИИ (GOOGLE & AI)
# ==========================================
@st.cache_resource
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes)
    return gspread.authorize(creds)

def load_ai_memory():
    """Загрузка базы обучения из листа 'Память_ИИ'"""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
        records = sheet.get_all_records()
        if records:
            return "\n".join([f"Текст: {r['Контент']} -> Правильный тег: {r['Правильные теги']}" for r in records])
    except: pass
    return "Примеров обучения пока нет."

async def fetch_yandex_ai(session, batch, memory):
    """Асинхронный запрос к YandexGPT с учетом Категории 12"""
    url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
    model_uri = f"gpt://{FOLDER_ID}/yandexgpt/latest"
    
    content = "\n".join([f"ID {i['id']}: {i['text']}" for i in batch])
    system_prompt = f"""Ты эксперт по контролю качества мебели и товаров для дома. 
    Твоя задача: Разметить отзывы/претензии по категориям: {', '.join(CATEGORIES.values())}.
    
    ВАЖНОЕ ПРАВИЛО (Категория 12): Если клиент поставил 4 или 5 звезд (или тон отзыва в целом позитивный), 
    но жалуется на мелкий брак или дефект, СТРОГО классифицируй это как 'Категория 12: Субъективное Не подошло'.
    
    УЧИТЫВАЙ ПРЕДЫДУЩИЙ ОПЫТ (ОБУЧЕНИЕ):
    {memory}
    
    Ответь ТОЛЬКО в формате JSON: {{"results": [{{"id": "...", "tags": ["Категория"], "reasoning": "..."}}]}}"""

    payload = {
        "modelUri": model_uri,
        "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
        "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": content}]
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "x-folder-id": FOLDER_ID}
    
    try:
        async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
            if resp.status == 200:
                res = await resp.json()
                text = res['result']['alternatives'][0]['message']['text']
                return json.loads(re.sub(r'```json|```', '', text).strip()).get('results', [])
    except: return []

# ==========================================
# 3. ДВИЖОК ОБРАБОТКИ ФАЙЛОВ
# ==========================================
def process_uploads(claims, returned, orders):
    log = [f"📅 Отчет от {datetime.now().strftime('%H:%M:%S')}"]
    
    # Склейка Claims
    all_c = [pd.read_excel(f) for f in claims]
    df_c = pd.concat(all_c, ignore_index=True).drop_duplicates()
    log.append(f"✅ Claims: {len(df_c)} строк.")

    # Склейка Склада
    df_final = df_c
    if returned:
        all_r = [pd.read_excel(f) for f in returned]
        df_r = pd.concat(all_r, ignore_index=True).drop_duplicates(subset=['srid'], keep='last')
        
        # Маппинг артикулов по nmId
        sku_map = df_r.dropna(subset=['supplierArticle']).set_index(df_r['nmId'].astype(str).str.replace(r'\.0$', '', regex=True))['supplierArticle'].to_dict()
        df_c['nm_id_clean'] = df_c['nm_id'].astype(str).str.replace(r'\.0$', '', regex=True)
        
        df_final = pd.merge(df_c, df_r, on='srid', how='left', suffixes=('', '_drop'))
        df_final['supplierArticle'] = final_df['supplierArticle'].fillna(final_df['nm_id_clean'].map(sku_map))
        log.append(f"✅ Склад: Найдено артикулов для {df_final['supplierArticle'].notna().sum()} заявок.")

    # Склейка Заказов (Litestat)
    df_ord_agg = pd.DataFrame()
    if orders:
        all_o = [pd.read_excel(f, sheet_name=0) for f in orders]
        df_o = pd.concat(all_o, ignore_index=True)
        sku_col = next((c for c in df_o.columns if 'артикул' in c.lower() or c.lower() == 'артикул'), None)
        qty_col = "Итого заказано, шт."
        if sku_col and qty_col in df_o.columns:
            df_ord_agg = df_o.groupby(sku_col)[qty_col].sum().reset_index()
            df_ord_agg.columns = ['Артикул', 'Заказы шт.']
            log.append(f"✅ Litestat: Обработано {len(df_ord_agg)} артикулов.")

    # Финальная чистка колонок
    res_df = pd.DataFrame()
    res_df['Дата'] = pd.to_datetime(df_final.get('dt', ''), errors='coerce').dt.strftime('%d.%m.%Y')
    res_df['Артикул'] = df_final.get('supplierArticle', 'Без артикула')
    res_df['Текст_Клиента'] = df_final.get('user_comment', '')
    res_df['SRID'] = df_final.get('srid', '')
    
    # Категории (пустые)
    for i in range(1, 14): res_df[f"Кат {i}"] = ""
    res_df['Обоснование'] = ""
    res_df['Корректировка'] = ""
    
    # Доп колонки из оригинальных файлов
    for col in df_final.columns:
        if col not in ['dt', 'supplierArticle', 'user_comment', 'srid', 'nm_id_clean'] and not col.endswith('_drop'):
            res_df[COLUMN_NAMES_RU.get(col, col)] = df_final[col]

    return res_df, df_ord_agg, "\n".join(log)

# ==========================================
# 4. ИНТЕРФЕЙС И НАВИГАЦИЯ
# ==========================================
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/d/df/WB_logo.png", width=100)
st.sidebar.title("CX AI Enterprise")
page = st.sidebar.radio("Навигация", ["📊 Дашборд Аналитики", "🤖 Робот-Загрузчик", "🧠 Обучение ИИ"])

# --- СТРАНИЦА РОБОТА ---
if page == "🤖 Робот-Загрузчик":
    st.title("🤖 Робот-Загрузчик данных")
    st.markdown("Загрузите файлы для автоматической склейки, очистки от дублей и синхронизации с Google Sheets.")
    
    c1, c2, c3 = st.columns(3)
    f_claims = c1.file_uploader("📂 Претензии (Claims)", accept_multiple_files=True)
    f_returned = c2.file_uploader("📂 Склад (Returned)", accept_multiple_files=True)
    f_litestat = c3.file_uploader("📂 Заказы (Litestat)", accept_multiple_files=True)
    
    if st.button("🚀 Начать магию объединения", use_container_width=True):
        if f_claims:
            with st.spinner("Работаем с данными..."):
                final_tab, orders_tab, report = process_uploads(f_claims, f_returned, f_litestat)
                
                client = get_gspread_client()
                # 1. Запись возвратов
                ws_ret = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                current_data = ws_ret.get_all_values()
                if not current_data or str(current_data[0][0]).strip() != 'Дата':
                    ws_ret.update('A1', [final_tab.columns.tolist()])
                
                start_row = len(ws_ret.get_all_values()) + 1
                ws_ret.update(f'A{start_row}', final_tab.fillna('').values.tolist())
                
                # 2. Запись заказов
                if not orders_tab.empty:
                    ws_ord = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
                    ws_ord.clear()
                    ws_ord.update('A1', [orders_tab.columns.tolist()] + orders_tab.values.tolist())
                
                st.success("✅ Данные успешно улетели в Google Sheets!")
                st.markdown(f'<div class="report-card">{report.replace("\n", "<br>")}</div>', unsafe_allow_html=True)
        else:
            st.warning("Нужно загрузить хотя бы один файл претензий!")

# --- СТРАНИЦА ОБУЧЕНИЯ ---
elif page == "🧠 Обучение ИИ":
    st.title("🧠 Обучение ИИ (Human-in-the-Loop)")
    st.info("Робот сканирует колонку 'Корректировка' в Google Таблице и запоминает ваши правки.")
    
    target_sheet = st.selectbox("Какой лист учим?", ["Возвраты", "Отзывы"])
    if st.button("💾 Сохранить мои правки в память ИИ"):
        client = get_gspread_client()
        ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet(target_sheet)
        ws_mem = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ")
        
        data = ws.get_all_values()
        header = data[0]
        # Ищем колонку "Корректировка" (обычно 20-я или по названию)
        try:
            corr_idx = header.index("Корректировка")
            text_idx = header.index("Текст_Клиента") if "Текст_Клиента" in header else 2
        except: 
            st.error("Не найдены нужные колонки!"); st.stop()
            
        new_examples = []
        for row in data[1:]:
            if len(row) > corr_idx and str(row[corr_idx]).strip() != "":
                new_examples.append([row[text_idx], row[corr_idx]])
        
        if new_examples:
            ws_mem.append_rows(new_examples)
            st.success(f"Запомнил {len(new_examples)} новых примеров!")
        else:
            st.info("Новых правок в таблице не обнаружено.")

# --- СТРАНИЦА ДАШБОРДА ---
elif page == "📊 Дашборд Аналитики":
    st.title("📊 BI Аналитика Качества CX")
    
    # Загрузка данных
    client = get_gspread_client()
    try:
        ws_main = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
        df = pd.DataFrame(ws_main.get_all_records())
        ws_ord = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
        df_ord = pd.DataFrame(ws_ord.get_all_records())
        
        # Подгрузка инвойсов (Шлюз)
        inv_vals = client.open_by_key(SPREADSHEET_ID_INVOICES).sheet1.get_all_values()
        if len(inv_vals) > 1:
            df_inv_raw = pd.DataFrame(inv_vals[1:], columns=[h.lower().strip() for h in inv_vals[0]])
            inv_map = df_inv_raw.groupby('номер поставки')['инвойс'].apply(lambda x: ', '.join(sorted(set(x)))).to_dict()
            
            supply_col = next((c for c in df.columns if str(c).lower().strip() in ['номер поставки', 'incomeid']), 'incomeID')
            df['Инвойс'] = df[supply_col].astype(str).str.replace(r'\.0$', '', regex=True).map(inv_map).fillna("Не найден")
            
        # Боковые фильтры
        st.sidebar.header("Фильтрация")
        f_art = st.sidebar.multiselect("Артикул", options=sorted(df['Артикул'].unique()), default=[])
        df_f = df[df['Артикул'].isin(f_art)] if f_art else df
        
        # Ключевые метрики
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Всего заявок", len(df_f))
        
        crit_sum = 0
        for i in [1,2,4,5,7,8,9]:
            col = f"Кат {i}"
            if col in df_f.columns: crit_sum += (df_f[col] == "V").sum()
        
        m2.metric("Критический брак", crit_sum, delta_color="inverse")
        
        # Вкладки графиков
        tab_heat, tab_supply, tab_data = st.tabs(["🔥 Матрица Дефектов", "📦 Анализ Поставок", "📋 Детализация (Media)"])
        
        with tab_heat:
            st.subheader("Тепловая карта причин по артикулам")
            cat_cols = [f"Кат {i}" for i in range(1, 14)]
            heat_data = []
            for art in df_f['Артикул'].unique():
                row = {'Артикул': art}
                temp = df_f[df_f['Артикул'] == art]
                for c in cat_cols:
                    row[c] = (temp[c] == "V").sum() if c in temp.columns else 0
                heat_data.append(row)
            
            df_heat = pd.DataFrame(heat_data).set_index('Артикул')
            if not df_heat.empty:
                fig_h = px.imshow(df_heat, text_auto=True, color_continuous_scale="Reds", aspect="auto")
                st.plotly_chart(fig_h, use_container_width=True)
            else: st.write("Нет данных для матрицы.")

        with tab_supply:
            st.subheader("Брак в разрезе поставок и инвойсов")
            s_col = next((c for c in df_f.columns if str(c).lower().strip() in ['номер поставки', 'incomeid']), 'incomeID')
            supply_stats = df_f.groupby([s_col, 'Инвойс']).size().reset_index(name='Кол-во')
            fig_s = px.bar(supply_stats, x=s_col, y='Кол-во', color='Инвойс', text_auto=True, barmode='group')
            st.plotly_chart(fig_s, use_container_width=True)

        with tab_data:
            st.subheader("Все записи с интерактивными фото")
            # Генерация HTML таблицы для зума 9.0х
            html_code = '<table class="custom-table"><tr><th>Дата</th><th>Артикул</th><th>Комментарий</th><th>Фото</th><th>Инвойс</th></tr>'
            
            for _, r in df_f.tail(50).iterrows(): # Последние 50 для скорости
                photos_html = ""
                if str(r.get('Фотографии', '')) != "":
                    links = str(r['Фотографии']).split(';')
                    for l in links[:3]: # Максимум 3 фото
                        url = f"https:{l}" if l.startswith('//') else l
                        photos_html += f'<img src="{url}" class="img-zoom">'
                
                html_code += f"""
                <tr>
                    <td>{r.get('Дата','')}</td>
                    <td><b>{r.get('Артикул','')}</b></td>
                    <td style="max-width:300px">{r.get('Текст_Клиента','')}</td>
                    <td>{photos_html}</td>
                    <td>{r.get('Инвойс','-')}</td>
                </tr>
                """
            html_code += "</table>"
            st.markdown(html_code, unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Ошибка загрузки данных из Google Sheets: {e}")
        st.info("Подсказка: Сначала загрузите файлы через вкладку 'Робот-Загрузчик'.")
