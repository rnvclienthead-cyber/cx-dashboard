import streamlit as st
import asyncio
import aiohttp
import gspread
import json
import os
import re
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
from google.oauth2.service_account import Credentials
import io

# ==========================================
# 0. КОНФИГУРАЦИЯ (БЕЗОПАСНОСТЬ)
# ==========================================
# В облаке данные берутся из st.secrets
try:
    YANDEX_API_KEY = st.secrets["YANDEX_API_KEY"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
    SPREADSHEET_ID_MAIN = st.secrets["SPREADSHEET_ID_MAIN"]
    SPREADSHEET_ID_INVOICES = st.secrets["SPREADSHEET_ID_INVOICES"]
    # Данные из credentials.json кладем в st.secrets["gcp_service_account"]
    GOOGLE_CREDS = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error("Настройте Secrets в панели Streamlit!")
    st.stop()

COLUMN_OFFSET = 5 

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

CRITICAL_DEFECTS = ["Кат 1", "Кат 2", "Кат 4", "Кат 5", "Кат 7", "Кат 8", "Кат 9"]

COLUMN_NAMES_RU = {
    'id': 'ID заявки', 'claim_type': 'Источник заявки', 'status': 'Решение по возврату', 
    'status_ex': 'Статус товара', 'nm_id': 'Артикул WB (Заявка)', 'user_comment': 'Комментарий покупателя', 
    'wb_comment': 'Ответ покупателю', 'dt': 'Дата заявки', 'imt_name': 'Название товара', 
    'order_dt': 'Дата заказа', 'dt_update': 'Дата рассмотрения', 'photos': 'Фотографии', 
    'video_paths': 'Видео', 'actions': 'Действия', 'price': 'Цена (Заявка)', 
    'currency_code': 'Валюта', 'origin_id_info': 'Сверка IMEI', 'delivery_dt': 'Дата доставки',
    'date': 'Дата продажи', 'lastChangeDate': 'Дата обновления (Поставка)', 
    'supplierArticle': 'Артикул продавца', 'nmId': 'Артикул WB', 'barcode': 'Баркод',
    'incomeID': 'Номер поставки', 'isSupply': 'Договор поставки', 'srid': 'ID заказа (SRID)'
}

COLS_RETURNED = ['date', 'supplierArticle', 'nmId', 'barcode', 'incomeID', 'supplyID', 'srid']
COLS_CLAIMS = ['claim_type', 'status', 'status_ex', 'nm_id', 'user_comment', 'dt', 'srid']

# ==========================================
# ЯДРО БАЗЫ ДАННЫХ
# ==========================================
@st.cache_resource
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_data(ttl=300) 
def load_and_merge_dashboard_data():
    client = get_gspread_client()
    try:
        sheet_main = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
        df_main = pd.DataFrame(sheet_main.get_all_records())
        
        sheet_inv = client.open_by_key(SPREADSHEET_ID_INVOICES).sheet1
        inv_vals = sheet_inv.get_all_values()
        
        # Умный поиск колонки поставки в основной базе
        possible_keys = ['номер поставки', 'id поставки', 'поставка (флаг)', 'incomeid']
        main_key_col = next((c for c in df_main.columns if str(c).strip().lower() in possible_keys), None)
        
        if df_main.empty or not main_key_col:
            return df_main, pd.DataFrame(), None
            
        df_main['Key_Clean'] = df_main[main_key_col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        
        # Обработка шлюза инвойсов
        if len(inv_vals) > 1:
            headers = [h.strip().lower() for h in inv_vals[0]]
            df_inv = pd.DataFrame(inv_vals[1:], columns=headers)
            df_inv['номер поставки'] = df_inv['номер поставки'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            df_inv['инвойс'] = df_inv['инвойс'].astype(str).str.strip()
            df_inv = df_inv[df_inv['инвойс'] != '']
            
            # Группировка: один номер поставки -> список инвойсов
            inv_grouped = df_inv.groupby('номер поставки')['инвойс'].apply(lambda x: ', '.join(sorted(set(x)))).reset_index()
            df_merged = pd.merge(df_main, inv_grouped, left_on='Key_Clean', right_on='номер поставки', how='left')
            df_merged['Инвойс'] = df_merged['инвойс'].fillna('Инвойс не найден')
        else:
            df_merged = df_main
            df_merged['Инвойс'] = 'Шлюз пуст'
            
        return df_merged, pd.DataFrame(), None
    except Exception as e:
        return None, None, str(e)

# ==========================================
# РОБОТ: ОБРАБОТКА ЗАГРУЖЕННЫХ ФАЙЛОВ
# ==========================================
def process_uploaded_files(claims_files, returned_files, litestat_files):
    report_lines = [f"📊 ОТЧЕТ ОБ ОБРАБОТКЕ ({datetime.now().strftime('%d.%m.%Y %H:%M')})\n" + "="*50]
    
    # 1. Обработка Претензий
    all_cla = []
    for f in claims_files:
        df = pd.read_excel(f)
        df.columns = [str(c).strip() for c in df.columns]
        all_cla.append(df[[c for c in COLS_CLAIMS if c in df.columns]])
    
    df_cla = pd.concat(all_cla, ignore_index=True)
    initial_count = len(df_cla)
    df_cla = df_cla.drop_duplicates(ignore_index=True) # Удаление дублей по всей строке
    report_lines.append(f"\n📂 ПРЕТЕНЗИИ:\n• Загружено строк: {initial_count}\n• Удалено полных дублей: {initial_count - len(df_cla)}")
    
    # 2. Обогащение данными со склада
    if returned_files:
        all_ret = [pd.read_excel(f) for f in returned_files]
        df_ret = pd.concat(all_ret, ignore_index=True).drop_duplicates(subset=['srid'], keep='last')
        
        df_cla['nm_id_str'] = df_cla['nm_id'].astype(str).str.replace(r'\.0$', '', regex=True)
        # Маппинг Артикулов
        sku_map = df_ret.dropna(subset=['supplierArticle']).set_index(df_ret['nmId'].astype(str).str.replace(r'\.0$', '', regex=True))['supplierArticle'].to_dict()
        
        final_df = pd.merge(df_cla, df_ret, on='srid', how='left', suffixes=('', '_drop'))
        final_df['supplierArticle'] = final_df['supplierArticle'].fillna(final_df['nm_id_str'].map(sku_map))
        
        matched = final_df['supplierArticle'].notna().sum()
        report_lines.append(f"\n📂 СКЛАД:\n• Привязано артикулов: {matched} из {len(df_cla)}")
    else:
        final_df = df_cla
        report_lines.append("\n⚠️ Данные склада не загружены!")

    # Формирование таблицы для Google Sheets
    export_df = pd.DataFrame()
    export_df['Дата'] = pd.to_datetime(final_df.get('dt', ''), errors='coerce').dt.strftime('%d.%m.%Y')
    export_df['Артикул'] = final_df.get('supplierArticle', 'Без артикула')
    export_df['Текст_Клиента'] = final_df.get('user_comment', '')
    export_df['SRID'] = final_df.get('srid', '')
    
    # Добавляем все остальные колонки согласно словарю
    for col in final_df.columns:
        if col not in ['dt', 'supplierArticle', 'user_comment', 'srid']:
            export_df[COLUMN_NAMES_RU.get(col, col)] = final_df[col]
            
    return export_df, "\n".join(report_lines)

# ==========================================
# ИНТЕРФЕЙС (STREAMLIT)
# ==========================================
st.sidebar.title("CX Cloud Dashboard")
mode = st.sidebar.radio("Меню", ["📊 Аналитика", "⚙️ Загрузка файлов"])

if mode == "⚙️ Загрузка файлов":
    st.title("⚙️ Робот-Обработчик")
    
    c1, c2 = st.columns(2)
    with c1:
        f_claims = st.file_uploader("1. Файлы Претензий (claims)", accept_multiple_files=True)
    with c2:
        f_ret = st.file_uploader("2. Файлы Склада (returned)", accept_multiple_files=True)
    
    if st.button("🚀 Начать объединение и очистку", use_container_width=True):
        if f_claims:
            with st.spinner("Магия данных..."):
                final_table, report_text = process_uploaded_files(f_claims, f_ret, [])
                
                # Отправка в Google Sheets
                client = get_gspread_client()
                sheet = client.open_by_key(SPREADSHEET_ID_MAIN).worksheet("Возвраты")
                
                # Умное добавление без сдвига форматирования
                existing = sheet.get_all_values()
                if not existing or existing[0][0] != 'Дата':
                    sheet.update('A1', [final_table.columns.tolist()])
                    start_row = 2
                else:
                    start_row = len(existing) + 1
                
                sheet.update(f'A{start_row}', final_table.fillna('').values.tolist())
                
                st.success("✅ Данные в Google Sheets обновлены!")
                st.download_button("📥 Скачать отчет об объединении", report_text, file_name="report.txt")
                st.text_area("Лог обработки", report_text, height=200)
        else:
            st.warning("Загрузите хотя бы один файл претензий!")

elif mode == "📊 Аналитика":
    st.title("📊 BI Дашборд")
    df, _, err = load_and_merge_dashboard_data()
    
    if err: st.error(err)
    elif df is not None:
        # Ваш существующий код графиков (Матрица, Брак, Поставки)
        st.info("Дашборд готов. Используйте фильтры слева.")
        # ... здесь вставьте блоки tab1, tab2, tab3 из вашего прошлого кода ...
