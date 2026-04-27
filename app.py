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
COLS_CLAIMS = ['claim_type', 'status', 'status_ex', 'nm_id', 'user_comment', 'wb_comment', 'dt', 'imt_name', 'order_dt', 'srid']
COLS_RETURNED = ['date', 'supplierArticle', 'nmId', 'barcode', 'incomeID', 'supplyID', 'srid']

st.markdown("""
<style>
    .img-zoom { width: 60px; height: 60px; object-fit: cover; border-radius: 5px; transition: transform 0.2s ease-in-out; cursor: pointer; }
    .img-zoom:hover { transform: scale(9.0); z-index: 999; position: relative; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
    .custom-table { width: 100%; border-collapse: collapse; font-family: sans-serif; font-size: 14px; }
    .custom-table th, .custom-table td { border: 1px solid #e0e0e0; padding: 10px; vertical-align: top; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. БАЗА ДАННЫХ И ПАМЯТЬ
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

# ==========================================
# 3. ИИ ДВИЖОК (YANDEX + GROK)
# ==========================================
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
# 4. ОБРАБОТКА ФАЙЛОВ
# ==========================================
def process_uploads(claims, returned, orders):
    df_c = pd.concat([pd.read_excel(f) for f in claims], ignore_index=True).drop_duplicates()
    df_final = df_c
    if returned:
        df_r = pd.concat([pd.read_excel(f) for f in returned], ignore_index=True).drop_duplicates(subset=['srid'], keep='last')
        sku_map = df_r.dropna(subset=['supplierArticle']).set_index(df_r['nmId'].astype(str).str.replace(r'\.0$', '', regex=True))['supplierArticle'].to_dict()
        df_c['nm_id_clean'] = df_c['nm_id'].astype(str).str.replace(r'\.0$', '', regex=True)
        df_final = pd.merge(df_c, df_r, on='srid', how='left')
        df_final['supplierArticle'] = df_final['supplierArticle'].fillna(df_final['nm_id_clean'].map(sku_map))

    df_ord_agg = pd.DataFrame()
    if orders:
        df_o = pd.concat([pd.read_excel(f, sheet_name=0) for f in orders], ignore_index=True)
        sku_col = next((c for c in df_o.columns if 'артикул' in c.lower()), None)
        if sku_col and "Итого заказано, шт." in df_o.columns:
            df_ord_agg = df_o.groupby(sku_col)["Итого заказано, шт."].sum().reset_index()
            df_ord_agg.columns = ['Артикул', 'Заказы шт.']

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
    return res_df, df_ord_agg

# ==========================================
# 5. ИНТЕРФЕЙС
# ==========================================
page = st.sidebar.radio("Навигация", ["📊 Дашборд Аналитики", "🤖 Робот-Загрузчик", "🔬 ИИ Тегирование", "🧠 Обучение ИИ"])

if page == "🤖 Робот-Загрузчик":
    st.title("🤖 Робот-Загрузчик")
    c1, c2, c3 = st.columns(3)
    f_claims = c1.file_uploader("Претензии", accept_multiple_files=True)
    f_returned = c2.file_uploader("Склад", accept_multiple_files=True)
    f_litestat = c3.file_uploader("Заказы", accept_multiple_files=True)
    
    if st.button("🚀 Объединить и загрузить"):
        if f_claims:
            with st.spinner("Склеиваем..."):
                final_tab, orders_tab = process_uploads(f_claims, f_returned, f_litestat)
                client = get_gspread_client()
                ws_ret = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                if not ws_ret.get_all_values() or str(ws_ret.acell('A1').value).strip() != 'Дата':
                    ws_ret.update('A1', [final_tab.columns.tolist()])
                ws_ret.update(f'A{len(ws_ret.get_all_values()) + 1}', final_tab.fillna('').values.tolist())
                if not orders_tab.empty:
                    ws_ord = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Заказы")
                    ws_ord.clear()
                    ws_ord.update('A1', [orders_tab.columns.tolist()] + orders_tab.values.tolist())
                st.success("✅ Данные загружены в Google Sheets!")
        else: st.warning("Загрузите файлы претензий!")

elif page == "🔬 ИИ Тегирование":
    st.title("🔬 ИИ Тегирование (Запуск нейросетей)")
    st.info("Скрипт найдет в Google Таблице все заявки, где еще нет тегов (пустые колонки 'Кат 1-13'), и разметит их.")
    
    model_choice = st.radio("Выберите нейросеть для анализа:", ["YandexGPT (yandex)", "Grok (grok)"])
    model_key = "yandex" if "Yandex" in model_choice else "grok"

    if st.button("🚀 ЗАПУСТИТЬ ТЕГИРОВАНИЕ"):
        with st.spinner(f"Получаю данные из Google Таблицы..."):
            client = get_gspread_client()
            ws = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
            df = pd.DataFrame(ws.get_all_records())
            
            # Ищем строки, где нет обоснования (значит ИИ их еще не трогал)
            unprocessed = df[df['Обоснование'] == '']
            if unprocessed.empty:
                st.success("Все заявки уже размечены!")
            else:
                st.warning(f"Найдено заявок без тегов: {len(unprocessed)}. Запускаем ИИ...")
                
                # Запуск ИИ
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(run_ai_batch_processing(unprocessed, model_key))
                
                # Обновляем таблицу (пишем теги обратно)
                for res in results:
                    row_idx = int(res['id']) + 2 # +2 из-за заголовка и 0-индексации
                    # Ставим "V" в нужные категории
                    for tag in res.get('tags', []):
                        cat_num = tag.split(':')[0].replace('Категория ', '')
                        try:
                            # Ищем номер колонки (Кат 1 это D, Кат 2 это E и тд, нужно посчитать)
                            col_letter = chr(ord('E') - 1 + int(cat_num)) # Примерный расчет
                            ws.update(f'{col_letter}{row_idx}', [['V']])
                        except: pass
                    # Записываем обоснование
                    ws.update(f'R{row_idx}', [[res.get('reasoning', '')]]) 

                st.success(f"✅ Успешно размечено {len(results)} строк! Посмотрите в таблицу.")

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
    except: st.warning("Загрузите файлы через Робота!")
