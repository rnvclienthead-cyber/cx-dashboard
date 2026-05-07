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
from sqlalchemy import create_engine, text

st.set_page_config(page_title="CX AI Enterprise", layout="wide")

# --- CSS Стили (Обновленные: Навигация + Старые стили) ---
st.markdown("""
    <style>
    /* НАВИГАЦИЯ: Скрываем кружочки радио-кнопок */
    [data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child { display: none !important; }
    
    /* НАВИГАЦИЯ: Стилизуем пункты под плоские кнопки */
    [data-testid="stSidebar"] div[role="radiogroup"] > label {
        background-color: transparent;
        padding: 8px 12px;
        border-radius: 6px;
        margin-bottom: 2px;
        color: #475569;
        transition: all 0.2s ease;
        cursor: pointer;
    }
    /* НАВИГАЦИЯ: Эффект наведения */
    [data-testid="stSidebar"] div[role="radiogroup"] > label:hover { background-color: #f1f5f9; }
    
    /* НАВИГАЦИЯ: Активная кнопка */
    [data-testid="stSidebar"] div[role="radiogroup"] > label[data-checked="true"] {
        background-color: #eff6ff;
        color: #2563eb;
        font-weight: 600;
    }
    
    /* НАВИГАЦИЯ: Заголовки групп */
    [data-testid="stSidebar"] h3 {
        padding-top: 1rem;
        padding-bottom: 0.5rem;
        font-size: 0.95rem;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    /* ВАШИ СТАРЫЕ СТИЛИ (Сохранены) */
    [data-testid="stDataFrame"] { font-size: 11px !important; }
    .detail-card { border: 1px solid #ddd; padding: 15px; border-radius: 8px; margin-bottom: 15px; background-color: #fcfcfc; }
    .media-row { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 10px; }
    .media-row a { background: transparent !important; padding: 0 !important; margin: 0 !important; border: none !important; display: inline-flex; }
    .photo-zoom { width: 140px !important; height: 140px !important; object-fit: cover !important; border-radius: 8px !important; transition: transform 0.3s ease; cursor: pointer; }
    .photo-zoom:hover { transform: scale(4); z-index: 9999; position: relative; border-radius: 0px !important; box-shadow: 0 20px 50px rgba(0,0,0,0.8) !important; }
    .video-link-btn { display: inline-block; padding: 8px 14px; background-color: #2563eb; color: white !important; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 13px; }
    .video-link-btn:hover { background-color: #1d4ed8; }
    .ai-tags-box { background-color: #f0fdf4; padding: 10px 14px; border-radius: 6px; font-size: 14px; color: #166534; margin-bottom: 15px; font-weight: 500; border-left: 4px solid #22c55e; }
    </style>
""", unsafe_allow_html=True)

# --- ГРУППИРОВКА И КРАСИВАЯ НАВИГАЦИЯ ---
tech_menu = {
    ":material/sync: Синхронизатор": "🤖 Робот-Синхронизатор",
    ":material/science: ИИ Тегирование": "🔬 ИИ Тегирование",
    ":material/rule_folder: Модерация": "📝 Модерация",
    ":material/psychology: Обучение ИИ": "🧠 Обучение ИИ"
}

ops_menu = {
    ":material/bar_chart: Отчет производства": "📊 Отчет производства",
    ":material/star: Рейтинг товаров": "⭐ Рейтинг товаров",
    ":material/receipt_long: Системный Журнал": "📜 Системный Журнал"
}

if 'active_menu_key' not in st.session_state:
    st.session_state.active_menu_key = list(tech_menu.keys())[0]

def nav_tech(): st.session_state.active_menu_key = st.session_state.tech_nav
def nav_ops(): st.session_state.active_menu_key = st.session_state.ops_nav

st.sidebar.markdown("### :material/settings_applications: Технический блок")
t_idx = list(tech_menu.keys()).index(st.session_state.active_menu_key) if st.session_state.active_menu_key in tech_menu else None
st.sidebar.radio("tech", list(tech_menu.keys()), index=t_idx, key="tech_nav", on_change=nav_tech, label_visibility="collapsed")

st.sidebar.markdown("### :material/monitoring: Операционный блок")
o_idx = list(ops_menu.keys()).index(st.session_state.active_menu_key) if st.session_state.active_menu_key in ops_menu else None
st.sidebar.radio("ops", list(ops_menu.keys()), index=o_idx, key="ops_nav", on_change=nav_ops, label_visibility="collapsed")

all_menus = {**tech_menu, **ops_menu}
page = all_menus.get(st.session_state.active_menu_key, "🤖 Робот-Синхронизатор")

# --- ГЛОБАЛЬНЫЙ ТРЕКЕР СМЕНЫ СТРАНИЦ ---
if st.session_state.get('current_tab') != page:
    st.session_state.current_tab = page
    st.session_state.matrix_key = int(time.time())
    st.session_state.show_detail_trigger = None
    st.session_state.last_click_id = None
# ---------------------------------------

try:
    YANDEX_API_KEY = st.secrets["YANDEX_API_KEY"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
    SPREADSHEET_ID_MAIN = st.secrets["SPREADSHEET_ID_MAIN"]
    SPREADSHEET_ID_INVOICES = st.secrets["SPREADSHEET_ID_INVOICES"]
    GOOGLE_CREDS = dict(st.secrets["gcp_service_account"])
    DB_URL = st.secrets.get("DB_URL") 
    engine = create_engine(DB_URL) if DB_URL else None
except Exception as e:
    st.error(f":material/warning: Ошибка в Secrets: {e}")
    st.stop()

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

# ==========================================
# БАЗОВЫЕ ФУНКЦИИ 
# ==========================================
def safe_read(file_obj):
    bytes_data = file_obj.getvalue()
    name = file_obj.name.lower()
    if name.endswith('.xlsx') or name.endswith('.xls'):
        try:
            eng = 'calamine' if name.endswith('.xlsx') else 'xlrd'
            return pd.read_excel(io.BytesIO(bytes_data), engine=eng)
        except Exception:
            try: 
                return pd.read_excel(io.BytesIO(bytes_data), engine='openpyxl')
            except Exception:
                try: 
                    return pd.read_html(io.BytesIO(bytes_data))[0]
                except Exception: 
                    pass
                    
    for enc in ['utf-8-sig', 'utf-8', 'windows-1251', 'utf-16']:
        for sep in [';', '\t', ',']:
            try:
                text_data = bytes_data.decode(enc)
                df = pd.read_csv(io.StringIO(text_data), sep=sep, engine='python', on_bad_lines='skip')
                if len(df.columns) > 1: return df
            except Exception: 
                continue
    st.error(f"⚠️ Не удалось прочитать файл {file_obj.name}.")
    return pd.DataFrame()

@st.cache_resource
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    return gspread.authorize(Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes))

def get_memory_records():
    try: 
        return get_gspread_client().open_by_key(SPREADSHEET_ID_MAIN).worksheet("Память_ИИ").get_all_records()
    except: 
        return []

def add_system_log(action, status, details=""):
    if not engine:
        return
    
    sql = text("""
        INSERT INTO system_logs (action, status, details) 
        VALUES (:action, :status, :details)
    """)
    
    try:
        with engine.begin() as conn:
            conn.execute(sql, {
                "action": action, 
                "status": status, 
                "details": details
            })
    except Exception as e:
        # Если база упала, выводим ошибку хотя бы в консоль сервера
        print(f"🚨 КРИТИЧЕСКАЯ ОШИБКА SQL-ЛОГИРОВАНИЯ: {e}")

def update_db_row(srid, updates_dict):
    if not engine or not updates_dict: return False
    
    # Сохраняем в основную базу
    set_clauses = [f"{k} = :{k}" for k in updates_dict.keys()]
    sql = text(f"UPDATE wb_claims SET {', '.join(set_clauses)} WHERE srid = :srid")
    
    try:
        with engine.begin() as conn:
            conn.execute(sql, {**updates_dict, "srid": srid})
            
            # САМООБУЧЕНИЕ: Если была ручная корректировка, сохраняем её как опыт
            if "correction" in updates_dict and updates_dict["correction"]:
                # Достаем текст отзыва для этой заявки
                claim_text = conn.execute(text("SELECT user_comment FROM wb_claims WHERE srid = :srid"), {"srid": srid}).scalar()
                if claim_text:
                    conn.execute(text("""
                        INSERT INTO ai_knowledge_base (content, tags, source) 
                        VALUES (:txt, :tgs, 'manual')
                        ON CONFLICT (content) DO UPDATE SET tags = EXCLUDED.tags
                    """), {"txt": claim_text, "tgs": updates_dict["correction"]})
        return True
    except Exception as e:
        return False

# ==========================================
# ИИ ДВИЖОК
# ==========================================
def parse_ai_response(text):
    try:
        clean_text = re.sub(r'```json|```', '', str(text)).strip()
        # Защита от мусора до/после JSON
        start = min([i for i in [clean_text.find('['), clean_text.find('{')] if i >= 0] or [0])
        end = max(clean_text.rfind(']'), clean_text.rfind('}')) + 1
        if start >= 0 and end > 0:
            clean_text = clean_text[start:end]
            
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict): return parsed.get('results', [])
        elif isinstance(parsed, list): return parsed
        else: return [{"error": f"Неожиданный формат: {type(parsed)}"}]
    except Exception as e:
        return [{"error": f"Сбой формата JSON: {str(e)} | Ответ ИИ: {text}"}]
        
def find_similar_examples_sql(target_text, engine, top_n=15):
    """
    Ищет похожие примеры в SQL базе знаний, используя умный алгоритм Триграмм (pg_trgm).
    Понимает опечатки и находит смысл.
    """
    if not target_text or not engine:
        return "Опыта пока нет."

    # SQL запрос: ищем тексты, похожесть которых больше 10% (0.1), сортируем от самых похожих к менее
    sql = text("""
        SELECT content, tags, similarity(content, :target_text) as sml
        FROM ai_knowledge_base
        WHERE similarity(content, :target_text) > 0.05
        ORDER BY sml DESC
        LIMIT :top_n
    """)

    try:
        with engine.connect() as conn:
            results = conn.execute(sql, {"target_text": target_text, "top_n": top_n}).fetchall()

        if not results:
            return "Прямых совпадений в опыте не найдено."

        # Собираем результаты в текст для ИИ
        best_matches = [f"Текст: {row[0]} -> Тег: {row[1]}" for row in results]
        return "\n".join(best_matches)

    except Exception as e:
        print(f"Ошибка поиска в базе знаний: {e}")
        return "Ошибка доступа к опыту."

fetch_ai_tags

async def fetch_ai_crosscheck(session, batch, engine):
    content = "\n".join([f"ID {i['id']}: {i['text']}" for i in batch])
    combined_target_text = " ".join([i['text'] for i in batch])

    # 🌟 ОБРАЩАЕМСЯ К SQL БАЗЕ ЗНАНИЙ (Новая функция!)
    relevant_memory = find_similar_examples_sql(combined_target_text, engine)

    system_prompt = f"""Ты — строгий аудитор. Твоя задача — проверить правильность тегов, которые поставила другая нейросеть.

ДОСТУПНЫЕ КАТЕГОРИИ (ID: Название):
{json.dumps(CATEGORIES, ensure_ascii=False, indent=2)}

--- ИСТОРИЧЕСКАЯ БАЗА ЗНАНИЙ (ОПЫТ) ---
{relevant_memory}
----------------------------------------

ПРАВИЛО: Если текущие теги противоречат опыту из базы, ты должен исправить их, записав правильный тег в поле `correction`.
ОТВЕТЬ СТРОГО В ЭТОМ JSON ФОРМАТЕ: {{"results": [{{"id": "...", "audit_status": "Согласен", "audit_comment": "", "correction": ""}}]}}"""

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
    except Exception as e:
        print(f"Ошибка Grok (Аудит): {e}")

    return []

async def run_ai_batch_processing(df_to_tag, model_choice, memory_string="", mode="tagging"):
    results = []
    async with aiohttp.ClientSession() as session:
        batch = []
        for idx, row in df_to_tag.iterrows():
            # Формируем данные для батча в зависимости от режима
            if mode == "tagging":
                batch.append({
                    "id": str(row['srid']), 
                    "text": f"Артикул: {row.get('supplier_article','')}. Текст: {row.get('user_comment','')}"
                })
            else:
                current_tags = [str(c) for c in range(1, 14) if row.get(f'cat_{c}') == True]
                tags_str = ", ".join(current_tags) if current_tags else "Нет тегов"
                batch.append({
                    "id": str(row['srid']), 
                    "text": f"Текст: {row.get('user_comment','')}. Текущие теги (ID): {tags_str}"
                })
            
            # Обработка накопленной пачки
            if len(batch) >= 10:
                if mode == "tagging": 
                    res = await fetch_ai_tags(session, batch, memory_string, model_choice)
                else: 
                    # Для аудита оставляем передачу engine, так как там логика не менялась
                    res = await fetch_ai_crosscheck(session, batch, engine) 
                if res: 
                    results.extend(res)
                batch = []
                
        # Обработка остатков в батче
        if batch:
            if mode == "tagging": 
                res = await fetch_ai_tags(session, batch, memory_string, model_choice)
            else: 
                res = await fetch_ai_crosscheck(session, batch, engine)
            if res: 
                results.extend(res)
            
    return results

# ==========================================
# ИНТЕРФЕЙС И НАВИГАЦИЯ
# ==========================================

if page == "🤖 Робот-Синхронизатор":
    st.title("🤖 Статус Базы Данных (Supabase)")
    st.info("Сбор логистики и претензий теперь работает автоматически (через скрипт `worker.py`). Streamlit больше не зависает.")
    
    if engine:
        try:
            with engine.connect() as conn:
                claims_count = conn.execute(text("SELECT COUNT(*) FROM wb_claims")).scalar()
                orders_count = conn.execute(text("SELECT COUNT(*) FROM wb_logistics WHERE doc_type='ORDER'")).scalar()
                sales_count = conn.execute(text("SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE'")).scalar()
                
            c1, c2, c3 = st.columns(3)
            c1.metric("Всего Претензий в БД", claims_count)
            c2.metric("Строк Заказов (ORDER)", orders_count)
            c3.metric("Строк Продаж (SALE)", sales_count)
            st.success("✅ База данных подключена и работает штатно.")
            st.markdown("💡 *Чтобы загрузить свежие данные с Wildberries, запустите `worker.py`.*")
        except Exception as e: 
            st.error(f"⚠️ Ошибка подключения к базе данных: {e}")
    else: 
        st.warning("⚠️ База данных не подключена. Проверьте DB_URL.")

elif page == "🧠 Обучение ИИ":
    st.title("🧠 База знаний ИИ (Умный импорт)")
    st.markdown("Загрузите исторический файл с проверенными отзывами. Робот всё поймет, расшифрует теги и загрузит в свою память (Google Sheets).")
    f_import = st.file_uploader("📂 Загрузить базу знаний (Excel/CSV)", type=['xlsx', 'csv', 'xls'])

    if st.button("📥 Загрузить и обновить память", type="primary"):
        if f_import:
            with st.spinner("Анализируем структуру файла и разрешаем конфликты..."):
                # УМНОЕ ЧТЕНИЕ ФАЙЛА
                bytes_data = f_import.getvalue()
                name = f_import.name.lower()
                df_import = pd.DataFrame()
                
                try:
                    if name.endswith(('.xlsx', '.xls')):
                        eng = 'calamine' if name.endswith('.xlsx') else 'xlrd'
                        try:
                            df_import = pd.read_excel(io.BytesIO(bytes_data), engine=eng)
                        except:
                            df_import = pd.read_excel(io.BytesIO(bytes_data), engine='openpyxl')
                    else:
                        for enc in ['utf-8-sig', 'utf-8', 'windows-1251']:
                            for sep in [';', '\t', ',']:
                                try:
                                    df_import = pd.read_csv(io.BytesIO(bytes_data), sep=sep, engine='python', encoding=enc)
                                    if len(df_import.columns) > 1: break
                                except: pass
                                
                    if not df_import.empty:
                        # Проверка на сбитые заголовки (как в "Датасете для отзывов")
                        if df_import.columns.astype(str).str.contains('Unnamed').sum() > 2:
                            row0 = df_import.iloc[0].astype(str).str.lower()
                            if any('текст' in s for s in row0.values):
                                new_header = df_import.iloc[0]
                                df_import = df_import[1:]
                                df_import.columns = new_header
                except Exception as e:
                    st.error(f"⚠️ Ошибка чтения файла: {e}")

                if not df_import.empty:
                    # Ищем все возможные текстовые колонки (чтобы склеить Достоинства и Недостатки)
                    text_cols = [str(c) for c in df_import.columns if any(kw in str(c).lower() for kw in ['текст', 'достоинства', 'недостатки', 'comment', 'комментарий покупателя'])]
                    
                    # Ищем колонку с готовыми текстовыми решениями (как в "Лилиях")
                    corr_col = next((str(c) for c in df_import.columns if any(kw in str(c).lower() for kw in ['корректировка', 'исправление', 'комментарий']) and str(c).lower() not in [str(x).lower() for x in text_cols]), None)
                    
                    if not text_cols: 
                        st.error("❌ Ошибка: В файле не найдены колонки с текстом.")
                    else:
                        new_memory_dict = {}
                        for idx, row in df_import.iterrows():
                            # Собираем текст, игнорируя ошибки типов
                            parts = []
                            for tc in text_cols:
                                if tc in row and pd.notna(row[tc]):
                                    cell_val = str(row[tc]).strip()
                                    if cell_val.lower() not in ['nan', 'none', '']:
                                        parts.append(cell_val)
                            
                            combined_text = " ".join(parts).strip()
                            if not combined_text: continue
                            
                            final_tags = []
                            
                            # 1. Приоритет ручному текстовому комментарию (если он есть)
                            if corr_col and pd.notna(row[corr_col]):
                                val = str(row[corr_col]).strip()
                                # Разбиваем строку по точке с запятой или запятой
                                split_vals = [v.strip() for v in re.split(r'[;,]', val) if v.strip()]
                                for v in split_vals:
                                    # Проверяем, есть ли такое значение в нашем словаре
                                    if v in CATEGORIES.values():
                                        final_tags.append(v)
                                    
                            # 2. Если ручного текста нет, ищем отметки 1/+/Да в колонках
                            if not final_tags:
                                for col in df_import.columns:
                                    col_str = str(col).lower()
                                    cat_id = None
                                    
                                    # Обрабатываем колонки, которые просто названы цифрами (1.0, 2.0)
                                    if isinstance(col, (int, float)):
                                        cat_id = int(col)
                                    else:
                                        # Ищем слово Кат/Cat и цифру рядом
                                        match = re.search(r'(?:кат|cat|категория)?\s*(\d+)', col_str)
                                        if match:
                                            cat_id = int(match.group(1))
                                            
                                    if cat_id and cat_id in CATEGORIES:
                                        cell_val = str(row[col]).strip().lower()
                                        if cell_val in ['1', '1.0', '+', 'true', 'да', 'v']:
                                            final_tags.append(CATEGORIES[cat_id])
                                            
                            if final_tags: 
                                new_memory_dict[combined_text] = "; ".join(final_tags)

                        if new_memory_dict:
                            try:
                                # Сохраняем в SQL через UPSERT (если текст такой же - обновим тег)
                                with engine.begin() as conn:
                                    for txt, tgs in new_memory_dict.items():
                                        conn.execute(text("""
                                            INSERT INTO ai_knowledge_base (content, tags, source) 
                                            VALUES (:txt, :tgs, 'training')
                                            ON CONFLICT (content) DO UPDATE SET tags = EXCLUDED.tags
                                        """), {"txt": txt, "tgs": tgs})
                                
                                st.success(f":material/check_circle: Опыт успешно записан в SQL-базу! Всего добавлено: {len(new_memory_dict)} примеров.")
                            except Exception as e: 
                                st.error(f"Ошибка записи в SQL: {e}")
                        else: 
                            st.warning("⚠️ Не найдено валидных тегов в файле. Проверьте, стоят ли '1' в колонках категорий.")
        else: 
            st.warning("Пожалуйста, загрузите файл.")

elif page == "🔬 ИИ Тегирование":
    st.title("🔬 ИИ Тегирование и Проверка")
    
    if engine:
        # Загружаем данные напрямую из PostgreSQL
        query_unprocessed = """
            SELECT srid, supplier_article, user_comment 
            FROM wb_claims 
            WHERE NOT (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
        """
        df_unprocessed = pd.read_sql(query_unprocessed, engine)
        
        query_audit = """
            SELECT srid, user_comment, cat_1, cat_2, cat_3, cat_4, cat_5, cat_6, cat_7, cat_8, cat_9, cat_10, cat_11, cat_12, cat_13 
            FROM wb_claims 
            WHERE (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
            AND (audit_status IS NULL OR audit_status = '')
        """
        df_audit = pd.read_sql(query_audit, engine)
        
        t1, t2 = st.tabs(["1️⃣ Первичная разметка", "2️⃣ Перекрестная проверка (Grok)"])
        
        with t1:
            st.subheader("Разметка новых заявок (Только ID)")
            if not df_unprocessed.empty:
                total_rows = len(df_unprocessed)
                col1, col2 = st.columns(2)
                batch_size = col1.slider("Размер пачки", 5, 50, 10, key="batch_tag")
                model_choice = col2.radio("Модель:", ["YandexGPT Lite (Дешево)", "YandexGPT Pro (Умнее)", "Grok (xAI)"], key="mod_tag")
                
                model_key = "yandex-lite" if "Lite" in model_choice else "yandex-pro" if "Pro" in model_choice else "grok"
                est_cost = total_rows * (0.08 if model_key == "yandex-lite" else 0.40 if model_key == "yandex-pro" else 0.50)
                st.info(f"📊 **Аналитика:** Найдено **{total_rows}** строк без тегов.\n💰 **Предварительный расход:** ~{est_cost:.2f} руб.")
                
                if st.button("🚀 ЗАПУСТИТЬ ТЕГИРОВАНИЕ", type="primary"):
                    st.cache_data.clear() 
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    log_container = st.container()
                    add_system_log("Запуск тегирования", "INFO", f"Строк: {total_rows}")
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    for i in range(0, total_rows, batch_size):
                        chunk = df_unprocessed.iloc[i:i+batch_size].copy()
                        
                        # 1. Готовим "шпаргалку" из БД для пачки
                        memory_list = []
                        for _, row in chunk.iterrows():
                            t_text = str(row.get('user_comment',''))
                            if t_text:
                                mem = find_similar_examples_sql(t_text, engine, top_n=2)
                                if mem and "Прямых совпадений" not in mem:
                                    memory_list.append(mem)
                        chunk_memory = "\n".join(set(memory_list)) if memory_list else "Опыта пока нет."
                        
                        # 2. ВОЗВРАЩАЕМ СТАРУЮ ХИТРОСТЬ: Делаем короткие REF_ID
                        # Это защитит нас от того, что ИИ перепутает сложный SRID
                        chunk['temp_id'] = [f"REF_{x}" for x in range(len(chunk))]
                        srid_map = dict(zip(chunk['temp_id'], chunk['srid'])) # Словарь REF -> настоящий SRID
                        
                        # Формируем данные для отправки с короткими ID
                        batch_to_send = []
                        for _, row in chunk.iterrows():
                            batch_to_send.append({
                                "id": row['temp_id'], 
                                "text": f"Артикул: {row.get('supplier_article','')}. Текст: {row.get('user_comment','')}"
                            })
                        
                        # 3. Отправляем в ИИ
                        async def send_batch():
                            async with aiohttp.ClientSession() as session:
                                return await fetch_ai_tags(session, batch_to_send, chunk_memory, model_key)
                        
                        results = loop.run_until_complete(send_batch())
                        
                        # 4. Сохраняем в SQL и пишем подробности в SQL-журнал
                        if results:
                            saved_count = 0
                            batch_details = [] # Список для формирования TEXT в колонку details
                            
                            for res in results:
                                if "error" in res:
                                    batch_details.append(f"❌ Ошибка ИИ: {res.get('error')}")
                                    continue
                                
                                # Очистка ID
                                raw_id = str(res.get('id', '')).upper()
                                num_match = re.search(r'\d+', raw_id)
                                
                                if not num_match: 
                                    batch_details.append(f"⚠️ Сбой ID: ИИ вернул '{raw_id}' без цифр")
                                    continue 
                                    
                                clean_temp_id = f"REF_{num_match.group()}"
                                cats_array = res.get('category_ids', [])
                                real_srid = srid_map.get(clean_temp_id)
                                
                                if real_srid and cats_array:
                                    updates = {f"cat_{re.search(r'\d+', str(c)).group()}": True for c in cats_array if re.search(r'\d+', str(c))}
                                    if updates:
                                        # Пытаемся обновить строку в БД
                                        if update_db_row(real_srid, updates):
                                            saved_count += 1
                                            batch_details.append(f"✅ SRID {real_srid}: теги {cats_array}")
                                        else:
                                            batch_details.append(f"🚨 ОШИБКА SQL: SRID {real_srid} не обновлен")
                                else:
                                    batch_details.append(f"❓ ПРОПУСК: SRID для {clean_temp_id} не найден в маппинге")
                            
                            # Формируем итоговый текст для колонки details в system_logs
                            full_log_text = f"Пачка обработана: {saved_count} из {len(chunk)} сохранены.\n" + "\n".join(batch_details)
                            
                            # Пишем в SQL через твой add_system_log
                            if saved_count > 0:
                                add_system_log(f"Пачка {i}", "SUCCESS", full_log_text)
                            else:
                                add_system_log(f"Пачка {i}", "WARNING", full_log_text)
                        else:
                            add_system_log(f"Пачка {i}", "ERROR", "ИИ вернул пустой результат (результаты отсутствуют)")

        with t2:
            st.subheader("Глубокая проверка (Аудит от Grok)")
            if not df_audit.empty:
                total_audit_rows = len(df_audit)
                batch_size_audit = st.slider("Размер пачки для аудита", 5, 50, 10, key="batch_audit")
                st.info(f"Найдено строк для проверки: **{total_audit_rows}**")

                if st.button("🕵️‍♂️ ЗАПУСТИТЬ АУДИТ", type="primary"):
                    progress_bar_audit = st.progress(0)
                    status_text_audit = st.empty()
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    for i in range(0, total_audit_rows, batch_size_audit):
                        chunk = df_audit.iloc[i:i+batch_size_audit]
                        status_text_audit.text(f"⏳ Аудит: {int(((i + len(chunk)) / total_audit_rows) * 100)}%")
                        
                        results = loop.run_until_complete(run_ai_batch_processing(chunk, "grok", mode="crosscheck"))
                        
                        for res in results:
                            if "error" in res: continue
                            srid = res.get('id')
                            audit_status = str(res.get('audit', '')).strip()
                            comment_text = str(res.get('comment', '')).strip()
                            cats_array = res.get('category_ids', [])
                            
                            updates = {'audit_status': audit_status, 'audit_comment': comment_text}
                            
                            if audit_status.upper() != "ОК" and cats_array:
                                # Сбрасываем старые теги и ставим новые
                                for c in range(1, 14): updates[f'cat_{c}'] = False
                                for cat_val in cats_array:
                                    cat_num_match = re.search(r'\d+', str(cat_val))
                                    if cat_num_match: updates[f"cat_{cat_num_match.group()}"] = True
                            
                            if srid: update_db_row(srid, updates)
                            
                        progress_bar_audit.progress(min(1.0, (i + len(chunk)) / total_audit_rows))
                    st.success("✅ Аудит завершен! Ошибки исправлены.")
                    st.rerun()
            else:
                st.success("🎉 Все размеченные заявки проверены аудитором!")

elif page == "📝 Модерация":
    st.title("📋 Модерация (Ручная проверка через Supabase)")

    @st.dialog("Просмотр видео")
    def play_video_modal(url): 
        st.video(url)

    def render_pagination(total_pages, key_prefix):
        if total_pages <= 1: return
        curr = st.session_state.mod_page
        if total_pages <= 7: window = list(range(1, total_pages + 1))
        elif curr <= 4: window = [1, 2, 3, 4, 5, "...", total_pages]
        elif curr >= total_pages - 3: window = [1, "...", total_pages - 4, total_pages - 3, total_pages - 2, total_pages - 1, total_pages]
        else: window = [1, "...", curr - 1, curr, curr + 1, "...", total_pages]

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

    if engine:
        st.markdown("### 🔍 Фильтр обращений")
        filter_mode = st.radio("Показать обращения:", ["Все ожидающие модерации", "С замечаниями от Аудитора (Кросс-проверка)"], horizontal=True)
        
        # БРОНЕБОЙНЫЙ ЗАПРОС: Избегаем дублирования колонок, чтобы Pandas не обрезал ссылки!
        query = """
            SELECT *
            FROM view_cx_dashboard
            WHERE ("1" OR "2" OR "3" OR "4" OR "5" OR "6" OR "7" OR "8" OR "9" OR "10" OR "11" OR "12" OR "13")
            AND ("Корректировка" IS NULL OR "Корректировка" = '')
        """
        if filter_mode == "С замечаниями от Аудитора (Кросс-проверка)":
            query += " AND UPPER(\"Аудит\") LIKE '%ОШИБКА%'"
            
        to_review = pd.read_sql(query, engine)
        
        if not to_review.empty:
            total_items = len(to_review)
            ITEMS_PER_PAGE = 20
            total_pages = max(1, (total_items - 1) // ITEMS_PER_PAGE + 1)
            
            if 'mod_page' not in st.session_state: st.session_state.mod_page = 1
            if st.session_state.mod_page > total_pages: st.session_state.mod_page = 1
            
            st.success(f"Найдено обращений в очереди: **{total_items}**")
            render_pagination(total_pages, key_prefix="top")
            
            start_idx = (st.session_state.mod_page - 1) * ITEMS_PER_PAGE
            current_page_df = to_review.iloc[start_idx:start_idx + ITEMS_PER_PAGE]
            
            cats_list = list(CATEGORIES.values())
            reverse_cats = {v.strip().lower(): k for k, v in CATEGORIES.items()}
            
            for idx, row in current_page_df.iterrows():
                srid = str(row['SRID']).strip()
                st.markdown("---")
                col_info, col_media = st.columns([1.2, 1])
                
                with col_info:
                    st.markdown(f"**Артикул:** {row.get('Артикул продавца', '---')} | **Дата заявки:** {row.get('Дата и время оформления заявки на возврат', '')}")
                    st.info(row.get('Комментарий покупателя', 'Нет текста'))
                    
                    ai_selected = [CATEGORIES[i] for i in range(1, 14) if row.get(str(i)) == True]
                    ai_text = ", ".join(ai_selected) if ai_selected else "Категории не определены"
                    st.markdown(f'<div class="ai-tags-box">🤖 <b>Выбор ИИ:</b> {ai_text}</div>', unsafe_allow_html=True)
                    
                    selected_cats = st.multiselect("Выберите правильные категории:", options=cats_list, default=[], key=f"ms_{srid}")
                    
                    if st.button("💾 Сохранить решение", key=f"btn_{srid}", type="primary"):
                        if selected_cats:
                            updates = {f"cat_{i}": False for i in range(1, 14)}
                            for cat_name in selected_cats:
                                cat_num = reverse_cats.get(cat_name.strip().lower())
                                if cat_num: updates[f"cat_{cat_num}"] = True
                            updates["correction"] = "; ".join(selected_cats)
                            
                            if update_db_row(srid, updates):
                                st.success("Сохранено в БД!")
                                time.sleep(0.5)
                                st.rerun()

                with col_media:
                    raw_photos = str(row.get('db_photos', '')).replace('nan', '').replace('None', '').strip()
                    raw_videos = str(row.get('db_videos', '')).replace('nan', '').replace('None', '').strip()
                    media_raw = raw_photos + " " + raw_videos
                    
                    urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[,<>]+', media_raw)
                    if urls:
                        videos, row_photos = [], []
                        for u in urls[:10]: 
                            clean_url = u.strip()
                            if clean_url.startswith("//"): clean_url = "https:" + clean_url
                            if any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): videos.append(clean_url)
                            else: row_photos.append(clean_url)
                        
                        if row_photos:
                            # Встроенный CSS для красивого зума + защита от блокировок WB
                            html_imgs = '<style>.mod-zoom { transition: transform 0.2s ease; cursor: pointer; border-radius: 8px; object-fit: cover; } .mod-zoom:hover { transform: scale(2.5); z-index: 9999; position: relative; border-radius: 0px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }</style>'
                            html_imgs += '<div style="display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 15px;">'
                            for p in row_photos[:6]:
                                html_imgs += f'<a href="{p}" target="_blank" rel="noreferrer noopener"><img src="{p}" class="mod-zoom" style="width: 130px; height: 130px;" referrerpolicy="no-referrer"></a>'
                            html_imgs += '</div>'
                            st.markdown(html_imgs, unsafe_allow_html=True)
                        
                        if videos:
                            for v_idx, v_url in enumerate(videos):
                                if st.button("🎥 Видео", key=f"vid_{srid}_{v_idx}"): play_video_modal(v_url)
            
            st.markdown("---")
            render_pagination(total_pages, key_prefix="bottom")
        else: 
            st.success("🎉 Очередь пуста! Все обращения проверены.")

elif page == "📊 Отчет производства":
    st.title("📊 Отчет производства")
    
    if 'matrix_key' not in st.session_state: st.session_state.matrix_key = int(time.time())
    if 'last_click_id' not in st.session_state: st.session_state.last_click_id = None
    if 'prev_inv' not in st.session_state: st.session_state.prev_inv = None
    if 'prev_sku' not in st.session_state: st.session_state.prev_sku = None
    if 'prev_date' not in st.session_state: st.session_state.prev_date = None

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
                except Exception: pass
        return zip_buffer.getvalue()

    @st.dialog("Детализация пересечения", width="large")
    def show_matrix_details(sku, reason_name, filtered_df, reason_id):
        st.subheader(f"📦 Артикул: {sku} | 🛠 Причина: {reason_name}")
        
        # ИСПРАВЛЕНИЕ: Добавлен .str.lower() для безошибочного совпадения True/true
        details = filtered_df[(filtered_df['Артикул продавца'] == sku) & (filtered_df[str(reason_id)].astype(str).str.strip().str.lower().isin(['1', '1.0', '+', 'true', 'да']))]
        
        if not details.empty:
            all_photos = []
            for _, r in details.iterrows():
                m_raw = str(r.get('db_photos', '')).replace('nan', '').replace('None', '')
                urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[,]+', m_raw)
                for u in urls:
                    clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                    if clean_url.startswith("//"): clean_url = "https:" + clean_url
                    if not any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): all_photos.append(clean_url)
            
            if all_photos:
                if st.button(f"📥 Скачать ВСЕ фото ({len(all_photos)} шт.)", type="primary", key=f"dl_all_{sku}_{reason_id}"):
                    with st.spinner("Сбор фото и архивация..."):
                        zip_all = create_images_zip(all_photos)
                        b64 = base64.b64encode(zip_all).decode()
                        components.html(f'<a id="dl" href="data:application/zip;base64,{b64}" download="{sku}_{reason_id}_ALL.zip"></a><script>document.getElementById("dl").click();</script>', width=0, height=0)
            
            st.markdown("---")
            for _, r in details.iterrows():
                with st.container():
                    st.markdown('<div class="detail-card">', unsafe_allow_html=True)
                    c1, media_col = st.columns([1.2, 1])
                    
                    row_photos, videos = [], []
                    raw_photos = str(r.get('db_photos', '')).replace('nan', '').replace('None', '').strip()
                    raw_videos = str(r.get('db_videos', '')).replace('nan', '').replace('None', '').strip()
                    m_raw = raw_photos + " " + raw_videos
                    
                    urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[,]+', m_raw)
                    for u in urls:
                        clean_url = u.replace("']", "").replace("'", "").replace('"', '')
                        if clean_url.startswith("//"): clean_url = "https:" + clean_url
                        if any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): videos.append(clean_url)
                        else: row_photos.append(clean_url)

                    with c1:
                        st.write(f"💬 **Текст клиента:**\n{r.get('Комментарий покупателя', '---')}")
                        
                        # Вывод очищенного инвойса (Даты мы отсюда удалили)
                        st.write(f"🧾 **Инвойс:** {r.get('Инвойс', '---')} | **Поставка:** {r.get('Номер поставки_ОРИГИНАЛ', '---')}")
                        
                        if row_photos:
                            if st.button("📥 Скачать фото", key=f"dl_row_{r.name}"):
                                with st.spinner("Архивация..."):
                                    zip_row = create_images_zip(row_photos)
                                    b64 = base64.b64encode(zip_row).decode()
                                    components.html(f'<a id="dl" href="data:application/zip;base64,{b64}" download="order_{r.get("Инвойс", "photos")}.zip"></a><script>document.getElementById("dl").click();</script>', width=0, height=0)

                    with media_col:
                        if row_photos:
                            # Возвращаем HTML-рендеринг для работы CSS-класса photo-zoom
                            html_imgs = '<div class="media-row">'
                            for p in row_photos[:6]:
                                html_imgs += f'<a href="{p}" target="_blank"><img src="{p}" class="photo-zoom"></a>'
                            html_imgs += '</div>'
                            st.markdown(html_imgs, unsafe_allow_html=True)
                            
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

    @st.cache_data(ttl=120) 
    def load_cached_hybrid_data():
        query = """
            SELECT 
                v."SRID", v."Дата и время оформления заявки на возврат", v."Дата заказа", v."Дата и время получения заказа покупателем",
                v."Артикул продавца", v."Комментарий покупателя", v."Решение по возврату покупателю", v."Статус товара",
                v."1", v."2", v."3", v."4", v."5", v."6", v."7", v."8", v."9", v."10", v."11", v."12", v."13",
                v."Корректировка", v."Номер поставки", c.photos AS db_photos, c.video_paths AS db_videos
            FROM view_cx_dashboard v
            LEFT JOIN wb_claims c ON v."SRID" = c.srid
        """
        df_temp = pd.read_sql(query, engine)
        
        # --- БРОНЕБОЙНАЯ СТАНДАРТИЗАЦИЯ ДАТЫ ---
        # Ищем колонку по ключевым словам, игнорируя то, как база передала регистр
        date_col = next((c for c in df_temp.columns if 'оформления заявки' in str(c).lower()), None)
        
        if date_col:
            df_temp['Дата_ДТ'] = pd.to_datetime(df_temp[date_col], errors='coerce')
            df_temp[date_col] = df_temp['Дата_ДТ'].dt.strftime('%d.%m.%Y %H:%M').fillna('Не указана')
        else:
            df_temp['Дата_ДТ'] = pd.NaT  # Заглушка, если колонки вообще нет
            
        # ЖЕСТКИЙ ФИЛЬТР: Сюда можно добавить новые статусы, если найдешь их через SQL!
        
        # ЖЕСТКИЙ ФИЛЬТР: Сюда можно добавить новые статусы, если найдешь их через SQL!
        valid_statuses = ['одобрено', '2', '2.0', 'да', 'true']
        df_temp = df_temp[
            df_temp['Решение по возврату покупателю'].astype(str).str.strip().str.lower().isin(valid_statuses) |
            df_temp['Статус товара'].astype(str).str.strip().str.lower().isin(valid_statuses)
        ]
        
        df_temp['Артикул продавца'] = df_temp['Артикул продавца'].astype(str).str.strip()
        df_temp = df_temp[~df_temp['Артикул продавца'].str.lower().isin(['nan', 'none', '', 'null'])]

        if 'Номер поставки' not in df_temp.columns:
            df_temp['Номер поставки'] = 'Не указан'
        
        # Сохраняем оригинальный номер для отображения
        df_temp['Номер поставки_ОРИГИНАЛ'] = df_temp['Номер поставки'].astype(str).replace(['nan', 'None', ''], 'Не указан').str.strip()
            
        try:
            inv_id = st.secrets.get("SPREADSHEET_ID_INVOICES", "")
            if inv_id:
                df_inv = pd.DataFrame(get_gspread_client().open_by_key(inv_id).get_worksheet(0).get_all_records())
                if 'supplyID' in df_inv.columns and 'Номер поставки' not in df_inv.columns: 
                    df_inv.rename(columns={'supplyID': 'Номер поставки'}, inplace=True)
                    
                if not df_inv.empty and 'Номер поставки' in df_inv.columns:
                    # ИСПРАВЛЕНИЕ: Жесткая вырезка .0 через .str.replace()
                    df_temp['Номер поставки_clean'] = df_temp['Номер поставки'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.lower()
                    df_temp['Номер поставки_clean'] = df_temp['Номер поставки_clean'].replace(['nan', 'none', ''], 'не указан')
                    
                    # СТРОГАЯ ОЧИСТКА ИНВОЙСОВ + АГРЕГАЦИЯ ТОЛЬКО ПО ПОСТАВКЕ
                    df_inv['Номер поставки_clean'] = df_inv['Номер поставки'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.lower()
                    
                    # Убиваем все невидимые пробелы в названиях колонок
                    df_inv.columns = df_inv.columns.str.strip()
                    
                    # Умный поиск колонки с инвойсом
                    inv_col = next((col for col in df_inv.columns if 'инвойс' in col.lower()), None)
                    
                    if inv_col:
                        # Переименовываем для стандарта
                        df_inv = df_inv.rename(columns={inv_col: 'Инвойс'})
                        
                        # МАГИЯ АГРЕГАЦИИ: Так как артикула в таблице нет, группируем инвойсы только по Поставке!
                        # Если на 1 поставку есть 2 инвойса, они склеятся в строку "INV-1, INV-2"
                        inv_grouped = df_inv.dropna(subset=['Инвойс']).groupby(
                            'Номер поставки_clean'
                        )['Инвойс'].apply(lambda x: ', '.join(x.astype(str).unique())).reset_index()
                        
                        if 'Инвойс' in df_temp.columns: 
                            df_temp = df_temp.drop(columns=['Инвойс'])
                        
                        # Мержим по одному ключу - Номеру поставки
                        df_temp = df_temp.merge(inv_grouped, on='Номер поставки_clean', how='left')
                    else:
                        st.warning(f"🚨 В таблице Инвойсов не найдена колонка 'Инвойс'. Доступные колонки: {df_inv.columns.tolist()}")
                    
                    # Мы уже сделали мерж выше через inv_grouped, эти строки просто лишние и вызывают ошибку
                    if 'Номер поставки_clean' in df_temp.columns:
                        df_temp.drop(columns=['Номер поставки_clean'], inplace=True)
        except Exception as e: 
            print(f"Ошибка загрузки инвойсов: {e}")
            
        return df_temp

    @st.cache_data(ttl=120)
    def load_cached_orders():
        # Загружаем сырые данные по заказам (артикул, дата заказа, дата отмены)
        query = """
            SELECT dt, supplier_article AS "Артикул продавца", cancel_dt 
            FROM wb_orders
        """
        try: 
            df_ord = pd.read_sql(query, engine)
            
            if df_ord.empty:
                return pd.DataFrame()

            # Очистка артикулов
            df_ord['Артикул продавца'] = df_ord['Артикул продавца'].astype(str).str.strip()
            df_ord = df_ord[~df_ord['Артикул продавца'].str.lower().isin(['nan', 'none', '', 'null'])]
            
            # Приводим дату к началу месяца для группировки
            df_ord['Месяц_ДТ'] = pd.to_datetime(df_ord['dt']).dt.to_period('M').dt.to_timestamp()
            
            # МАСТЕР-ФУНКЦИЯ ПОДСЧЕТА:
            # Оставляем только те строки, у которых cancel_dt отсутствует (пустое значение/NaT)
            valid_orders = df_ord[pd.isna(df_ord['cancel_dt'])]
            
            # Суммируем оставшиеся (чистые) строки по Месяцу и Артикулу
            final_orders = valid_orders.groupby(['Артикул продавца', 'Месяц_ДТ']).size().reset_index(name='Чистые_заказы')
            
            return final_orders
        except Exception as e: 
            print(f"Ошибка загрузки заказов: {e}")
            return pd.DataFrame()

    try:
        with st.spinner("📊 Загрузка и анализ данных..."):
            df = load_cached_hybrid_data()
            df_orders = load_cached_orders()

        if not df.empty:
            if 'Инвойс' not in df.columns: df['Инвойс'] = 'Не указан'
            df['Инвойс'] = df['Инвойс'].fillna('Не указан')
            df['Номер поставки_ОРИГИНАЛ'] = df.get('Номер поставки_ОРИГИНАЛ', 'Не указан').fillna('Не указан')
            
            def has_tags(row): return any(str(row.get(str(i),'')).strip().lower() in ['1','1.0','+','true','да'] for i in range(1,14))
            df['Размечено'] = df.apply(has_tags, axis=1)
            
            st.markdown("### 🔍 Глобальные фильтры")
            f_col1, f_col2, f_col3 = st.columns(3)
            
            inv_list = ['Все'] + sorted(list(set([str(x) for x in df['Инвойс'] if str(x).strip() and str(x) != 'Не указан'])))
            sku_list = ['Все'] + sorted(list(set([str(x) for x in df['Артикул продавца'] if str(x).strip()])))
            
            # --- УМНЫЙ ФИЛЬТР ДАТ (МЕСЯЦЫ) ---
            months_ru = {1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель', 5: 'Май', 6: 'Июнь', 
                         7: 'Июль', 8: 'Август', 9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'}
            today_date = datetime.now()
            
            period_options = []
            for i in range(6): 
                m = today_date.month - i
                y = today_date.year
                if m <= 0:
                    m += 12
                    y -= 1
                period_options.append(f"{months_ru[m]} {y}")
            period_options.append("За всё время")

            # 1. Фильтр Даты (Выпадающий список)
            selected_month = f_col1.selectbox("Период (Дата заявки):", period_options, index=0)
            # 2. Фильтр Артикула
            selected_sku = f_col2.selectbox("Артикул:", sku_list)
            # 3. Фильтр Инвойса
            selected_inv = f_col3.selectbox("Инвойс / Поставка:", inv_list)
            
            # Проверяем изменения любого из трех фильтров
            if st.session_state.prev_inv != selected_inv or st.session_state.prev_sku != selected_sku or st.session_state.prev_date != selected_month:
                st.session_state.show_detail_trigger = None
                st.session_state.last_click_id = None
                st.session_state.prev_inv = selected_inv
                st.session_state.prev_sku = selected_sku
                st.session_state.prev_date = selected_month
                st.session_state.matrix_key += 1 
            
            df_filtered = df.copy()
            
            # Применяем фильтр по месяцу
            if selected_month != "За всё время":
                month_name, year_str = selected_month.split(' ')
                selected_m = list(months_ru.keys())[list(months_ru.values()).index(month_name)]
                selected_y = int(year_str)
                df_filtered = df_filtered[(df_filtered['Дата_ДТ'].dt.month == selected_m) & (df_filtered['Дата_ДТ'].dt.year == selected_y)]

            # Применяем текстовые фильтры
            if selected_sku != 'Все': df_filtered = df_filtered[df_filtered['Артикул продавца'].astype(str) == selected_sku]
            if selected_inv != 'Все': df_filtered = df_filtered[df_filtered['Инвойс'].astype(str) == selected_inv]

            total_rows = len(df_filtered)
            tagged_rows = df_filtered['Размечено'].sum()
            corrected_rows = len(df_filtered[df_filtered.get('Корректировка', '').astype(str).str.strip() != ''])
            
            accuracy = round((1 - (corrected_rows / tagged_rows)) * 100, 1) if tagged_rows > 0 else 0
            processed_percent = round((tagged_rows / total_rows) * 100, 1) if total_rows > 0 else 0
            
            st.markdown("### 📈 Общая статистика (ТОЛЬКО 'Одобрено')")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Всего заявок", total_rows)
            c2.metric("Размечено", f"{tagged_rows} ({processed_percent}%)")
            c3.metric("Изменено вручную", corrected_rows)
            c4.metric("Точность ИИ", f"{accuracy}%")
            st.markdown("---")
            
            tab_matrix, tab_ppm = st.tabs(["🧮 Тепловая Матрица Производства", "⚠️ Расчет PPM и Рекламации"])
            
            with tab_matrix:
                st.info("💡 **Кликните на любой цветной квадрат для мгновенной детализации!**")
                matrix_list = []
                for i in range(1, 14):
                    cat_col = str(i)
                    if cat_col in df_filtered.columns:
                        temp = df_filtered[df_filtered[cat_col].astype(str).str.strip().str.lower().isin(['1', '1.0', '+', 'true', 'да'])]
                        for _, r in temp.iterrows():
                            matrix_list.append({
                                'Артикул продавца': str(r.get('Артикул продавца', 'Без артикула')).strip(),
                                'Причина': f"{i}. {CATEGORIES[i]}",
                                'ID': i,
                                'Инвойс': str(r.get('Инвойс', 'Не указан')).strip(),
                                'Номер поставки': str(r.get('Номер поставки_ОРИГИНАЛ', 'Не указан')).strip()
                            })

                if matrix_list:
                    df_matrix = pd.DataFrame(matrix_list)
                    pivot = pd.crosstab(df_matrix['Причина'], df_matrix['Артикул продавца']).fillna(0).astype(int)
                    pivot['ID'] = [int(x.split('.')[0]) for x in pivot.index]
                    reason_totals = pivot.drop(columns=['ID']).sum(axis=1).to_dict()
                    df_melt = pivot.reset_index().melt(id_vars=['Причина', 'ID'], var_name='Артикул продавца', value_name='Дефекты')
                    df_melt['Артикул_Метка'] = df_melt['Артикул продавца'] 
                    df_melt['Причина_Метка'] = df_melt['Причина'].apply(lambda x: f"{x} [{reason_totals.get(x, 0)}]")
                    df_melt['Текст'] = df_melt['Дефекты'].apply(lambda x: str(x) if x > 0 else "")

                    import altair as alt
                    click_selector = alt.selection_point(name='cell_click', fields=['Артикул_Метка', 'Причина_Метка'])
                    base = alt.Chart(df_melt).encode(x=alt.X('Артикул_Метка:N', title=None, axis=alt.Axis(labelAngle=-90, labelLimit=1000, orient='bottom')), y=alt.Y('Причина_Метка:N', title=None, axis=alt.Axis(labelLimit=1000), sort=alt.EncodingSortField(field='ID', order='ascending')))
                    rects = base.mark_rect(stroke='white', strokeWidth=1).encode(color=alt.Color('Дефекты:Q', scale=alt.Scale(scheme='blues'), legend=None), tooltip=[alt.Tooltip('Артикул продавца:N', title='Артикул'), alt.Tooltip('Причина:N', title='Причина'), alt.Tooltip('Дефекты:Q', title='Кол-во')])
                    text = base.mark_text(baseline='middle', fontSize=11).encode(text='Текст:N', color=alt.condition(alt.datum.Дефекты > (df_melt['Дефекты'].max() / 2), alt.value('white'), alt.value('black')))
                    final_chart = alt.layer(rects, text).properties(height=max(400, len(pivot) * 35 + 100)).add_params(click_selector)
                    
                    event = st.altair_chart(final_chart, use_container_width=True, on_select="rerun", key=f"prod_matrix_{st.session_state.matrix_key}")
                    
                    try:
                        if event and hasattr(event, "selection"):
                            sel = event.selection.get("cell_click", [])
                            if sel and len(sel) > 0:
                                sku_clicked = sel[0].get('Артикул_Метка')
                                reason_clicked = sel[0].get('Причина_Метка')
                                if sku_clicked and reason_clicked:
                                    current_click_id = f"{sku_clicked}_{reason_clicked}"
                                    if current_click_id != st.session_state.get('last_click_id'):
                                        st.session_state.last_click_id = current_click_id
                                        st.session_state.show_detail_trigger = {'sku': sku_clicked.split(' [')[0], 'reason': reason_clicked.split(' [')[0], 'df': df_filtered, 'id': int(reason_clicked.split('.')[0])}
                                        st.session_state.matrix_key += 1
                                        st.rerun()
                    except Exception as e: 
                        st.error(f"Ошибка системы перехвата клика: {e}")
                    
                    if st.session_state.get('show_detail_trigger'):
                        t = st.session_state.show_detail_trigger
                        show_matrix_details(t['sku'], t['reason'], t['df'], t['id'])
                
                else: 
                    st.info("Данных для матрицы пока нет.")

                st.markdown("---")
                st.markdown("### 📦 Проблемные поставки и инвойсы (Топ-15)")
                st.markdown("""<style>#vg-tooltip-element { font-family: sans-serif; font-size: 11px !important; line-height: 1.3 !important; box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important; border-radius: 6px !important; border: 1px solid #e0e0e0 !important; max-width: 600px !important; white-space: normal !important;} #vg-tooltip-element table tr { border-bottom: 1px solid #d1d5db; } #vg-tooltip-element table td { padding: 6px 8px !important; vertical-align: top; } #vg-tooltip-element table td.key { color: #6b7280; font-weight: 600; white-space: nowrap; }</style>""", unsafe_allow_html=True)
                
                if matrix_list:
                    supply_grouped = []
                    df_matrix_data = pd.DataFrame(matrix_list)
                    
                    if 'Номер поставки' in df_matrix_data.columns:
                        # Группируем по Номеру поставки
                        for supply, group in df_matrix_data.groupby('Номер поставки'):
                            
                            # ИСКЛЮЧАЕМ пустые значения и системные нули
                            clean_supply = str(supply).strip()
                            if clean_supply in ['Не указан', '', '0', '0.0'] or pd.isna(supply): 
                                continue
                            
                            # Собираем уникальные инвойсы для этой поставки
                            invoices = ", ".join(sorted(list(set([str(x) for x in group['Инвойс'] if str(x) != 'Не указан' and str(x).strip()]))))
                            
                            # Считаем, сколько каких артикулов было с браком в этой поставке
                            all_skus = " • ".join([f"{k} ({v} шт.)" for k, v in group['Артикул продавца'].value_counts().items()])
                            
                            supply_grouped.append({
                                'Номер поставки': clean_supply, 
                                'Дефекты': len(group), 
                                'Инвойсы': invoices if invoices else "Не указаны", 
                                'Список Артикулов': all_skus
                            })
                        
                        if supply_grouped:
                            # Сортируем по количеству брака и берем топ-15
                            df_supply = pd.DataFrame(supply_grouped).sort_values('Дефекты', ascending=False).head(15)
                            
                            supply_chart = alt.Chart(df_supply).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                                x=alt.X('Номер поставки:N', sort='-y', title=None, axis=alt.Axis(labelAngle=-45, labelLimit=500)), 
                                y=alt.Y('Дефекты:Q', title='Количество дефектов'), 
                                color=alt.Color('Дефекты:Q', scale=alt.Scale(scheme='oranges'), legend=None), 
                                tooltip=[
                                    alt.Tooltip('Номер поставки:N', title='Поставка'), 
                                    alt.Tooltip('Дефекты:Q', title='Всего дефектов'), 
                                    alt.Tooltip('Инвойсы:N', title='Инвойсы (связанные)'), 
                                    alt.Tooltip('Список Артикулов:N', title='Артикулы')
                                ]
                            ).properties(height=350)
                            
                            st.altair_chart(supply_chart, use_container_width=True)
                        else: 
                            st.info("Нет данных по поставкам (или у всех дефектов не указан номер поставки).")
                    else:
                        st.info("Невозможно построить график: отсутствует колонка 'Номер поставки'.")
                else: 
                    st.info("Данных для матрицы пока нет.")

            with tab_ppm:
                st.info("💡 **Аналитика PPM**: Расчет идет только по заявкам, которые: 1) Имеют статус «Одобрено» 2) Размечены ИИ или вручную. Предел PPM установлен на 10 000 (1%).")
                import numpy as np
                import plotly.graph_objects as go
                
                if not df_orders.empty and 'Артикул продавца' in df_orders.columns:
                    # --- ПОДГОТОВКА ДАТ ДЛЯ ФИЛЬТРА ---
                    months_ru = {1: 'Янв', 2: 'Фев', 3: 'Мар', 4: 'Апр', 5: 'Май', 6: 'Июн', 
                                 7: 'Июл', 8: 'Авг', 9: 'Сен', 10: 'Окт', 11: 'Ноя', 12: 'Дек'}
                    
                    # ИСПРАВЛЕНИЕ: Берем сразу 'Месяц_ДТ', так как сырого 'dt' в этой таблице уже нет
                    df_orders['Месяц_ДТ'] = pd.to_datetime(df_orders['Месяц_ДТ'])
                    df_orders['Месяц_Стр'] = df_orders['Месяц_ДТ'].dt.month.map(months_ru) + " " + df_orders['Месяц_ДТ'].dt.year.astype(str)
                    
                    df_approved = df_filtered[df_filtered['Размечено'] == True].copy()
                    if not df_approved.empty and 'Дата_ДТ' in df_approved.columns:
                        df_approved['Месяц_ДТ'] = df_approved['Дата_ДТ'].dt.to_period('M').dt.to_timestamp()
                        df_approved['Месяц_Стр'] = df_approved['Месяц_ДТ'].dt.month.map(months_ru) + " " + df_approved['Месяц_ДТ'].dt.year.astype(str)
                    else:
                        df_approved['Месяц_ДТ'] = pd.NaT
                        df_approved['Месяц_Стр'] = 'Неизвестно'

                    # Сортируем доступные месяцы от новых к старым
                    available_months = df_orders.drop_duplicates('Месяц_Стр').sort_values('Месяц_ДТ', ascending=False)['Месяц_Стр'].tolist()

                    # --- ГЛОБАЛЬНАЯ ПАНЕЛЬ ФИЛЬТРОВ ---
                    st.markdown("### 🎛 Панель управления")
                    f_col1, f_col2 = st.columns(2)
                    
                    selected_months = f_col1.multiselect("📅 Период (Месяцы):", options=available_months, default=[], help="Оставьте пустым для отображения за всё время")
                    
                    # Применяем фильтр дат
                    filt_orders = df_orders[df_orders['Месяц_Стр'].isin(selected_months)] if selected_months else df_orders
                    filt_approved = df_approved[df_approved['Месяц_Стр'].isin(selected_months)] if selected_months else df_approved

                    # --- 1. АВТОМАТИЧЕСКИЙ ABC-АНАЛИЗ И ПОДСЧЕТ БРАКА ---
                    filt_orders['Чистые_заказы'] = pd.to_numeric(filt_orders.get('Чистые_заказы', 0), errors='coerce').fillna(0)
                    orders_total = filt_orders.groupby('Артикул продавца')['Чистые_заказы'].sum().reset_index()
                    orders_total = orders_total.sort_values('Чистые_заказы', ascending=False)
                    
                    total_sum = orders_total['Чистые_заказы'].sum()
                    if total_sum > 0:
                        orders_total['cum_perc'] = orders_total['Чистые_заказы'].cumsum() / total_sum
                        orders_total['ABC_Группа'] = pd.cut(orders_total['cum_perc'], bins=[0, 0.8, 0.95, 1.1], labels=['A', 'B', 'C'], right=True)
                    else:
                        orders_total['ABC_Группа'] = 'C'
                        
                    defects_total = filt_approved.groupby('Артикул продавца').size().reset_index(name='Одобренный брак (шт)') if not filt_approved.empty else pd.DataFrame(columns=['Артикул продавца', 'Одобренный брак (шт)'])
                    
                    ppm_df = pd.merge(orders_total[['Артикул продавца', 'Чистые_заказы', 'ABC_Группа']], defects_total, on='Артикул продавца', how='left').fillna(0)
                    ppm_df['PPM'] = np.where(ppm_df['Чистые_заказы'] > 0, (ppm_df['Одобренный брак (шт)'] / ppm_df['Чистые_заказы']) * 1000000, 0).astype(int)
                    ppm_df['Доля брака, %'] = np.where(ppm_df['Чистые_заказы'] > 0, round((ppm_df['Одобренный брак (шт)'] / ppm_df['Чистые_заказы']) * 100, 2), 0)

                    # --- ФИЛЬТР ПО АРТИКУЛАМ / ГРУППАМ ---
                    all_skus = ppm_df['Артикул продавца'].tolist()
                    article_options = ['[Все артикулы]', '[Вся Группа A]', '[Вся Группа B]', '[Вся Группа C]'] + all_skus
                    selected_articles = f_col2.multiselect("📦 Объекты (Группы или Артикулы):", options=article_options, default=['[Все артикулы]'])

                    # Распаковка выбранных объектов в список конкретных артикулов
                    active_skus = set()
                    if not selected_articles or '[Все артикулы]' in selected_articles:
                        active_skus.update(all_skus)
                    else:
                        for item in selected_articles:
                            if item.startswith('[Вся Группа'):
                                group_letter = item[-2]
                                active_skus.update(ppm_df[ppm_df['ABC_Группа'] == group_letter]['Артикул продавца'].tolist())
                            else:
                                active_skus.add(item)
                    active_skus = list(active_skus)

                    # Формируем итоговую таблицу для отображения
                    display_df = ppm_df[ppm_df['Артикул продавца'].isin(active_skus)].sort_values(by=['ABC_Группа', 'PPM'], ascending=[True, False])

                    st.markdown("---")
                    st.markdown("### 🗂 Статистика по выбранным фильтрам")
                    col_a, col_b, col_c = st.columns(3)
                    col_a.metric("Группа A (Выбрано)", len(display_df[display_df['ABC_Группа'] == 'A']))
                    col_b.metric("Группа B (Выбрано)", len(display_df[display_df['ABC_Группа'] == 'B']))
                    col_c.metric("Группа C (Выбрано)", len(display_df[display_df['ABC_Группа'] == 'C']))
                    
                    # --- ДВУХКОЛОНОЧНЫЙ ИНТЕРФЕЙС (МАСТЕР-ДЕТАЛЬ) ---
                    st.info("💡 **Интерактив:** Кликните на любую строку в таблице, чтобы график динамики показал данные именно по ней.")
                    col_table, col_chart = st.columns([1.4, 2], gap="large") 
                    clicked_sku = None

                    with col_table:
                        st.markdown("#### 📋 Список товаров")
                        def highlight_ppm(row):
                            return ['background-color: #fee2e2; color: #991b1b' if row['PPM'] > 10000 else ''] * len(row)
                        
                        cols_to_show = ['Артикул продавца', 'ABC_Группа', 'Чистые_заказы', 'Одобренный брак (шт)', 'Доля брака, %', 'PPM']
                        styled_df = display_df[cols_to_show].style.apply(highlight_ppm, axis=1)
                        
                        try:
                            event = st.dataframe(
                                styled_df, 
                                use_container_width=True, 
                                hide_index=True,
                                on_select="rerun",
                                selection_mode="single-row",
                                height=470,
                                column_config={
                                    "Артикул продавца": st.column_config.TextColumn("Артикул", width="medium"),
                                    "ABC_Группа": st.column_config.TextColumn("ABC", width="small"),
                                    "Чистые_заказы": st.column_config.NumberColumn("Заказы", format="%d"),
                                    "Одобренный брак (шт)": st.column_config.NumberColumn("Брак (шт)", format="%d"),
                                    "Доля брака, %": st.column_config.NumberColumn("%", format="%.2f"),
                                    "PPM": st.column_config.NumberColumn("PPM", format="%d")
                                }
                            )
                            if event.selection.rows:
                                clicked_sku = display_df.iloc[event.selection.rows[0]]['Артикул продавца']
                        except TypeError:
                            st.dataframe(styled_df, use_container_width=True, hide_index=True)

                    with col_chart:
                        plot_skus = [clicked_sku] if clicked_sku else active_skus
                        chart_title = f"📉 Динамика: {clicked_sku}" if clicked_sku else ("📉 Динамика: Все артикулы" if '[Все артикулы]' in selected_articles or not selected_articles else "📉 Динамика: Выбранные объекты")
                        
                        st.markdown(f"#### {chart_title}")
                        
                        sku_orders = df_orders[df_orders['Артикул продавца'].isin(plot_skus)].groupby('Месяц_ДТ')['Чистые_заказы'].sum().reset_index()
                        sku_defects = df_approved[df_approved['Артикул продавца'].isin(plot_skus)].groupby('Месяц_ДТ').size().reset_index(name='Брак')
                        
                        sku_orders['Месяц_ДТ'] = pd.to_datetime(sku_orders['Месяц_ДТ'])
                        sku_defects['Месяц_ДТ'] = pd.to_datetime(sku_defects['Месяц_ДТ'])
                        
                        monthly_df = pd.merge(sku_orders, sku_defects, on='Месяц_ДТ', how='outer').fillna(0).sort_values('Месяц_ДТ')
                        
                        if not monthly_df.empty:
                            monthly_df['Месяц_Стр'] = monthly_df['Месяц_ДТ'].dt.month.map(months_ru) + " " + monthly_df['Месяц_ДТ'].dt.year.astype(str)
                            monthly_df['PPM'] = np.where(monthly_df['Чистые_заказы'] > 0, (monthly_df['Брак'] / monthly_df['Чистые_заказы']) * 1000000, 0).astype(int)
                            
                            fig = go.Figure()
                            fig.add_trace(go.Bar(x=monthly_df['Месяц_Стр'], y=monthly_df['Чистые_заказы'], name='Заказы', marker_color='#3b82f6', yaxis='y'))
                            fig.add_trace(go.Scatter(x=monthly_df['Месяц_Стр'], y=monthly_df['PPM'], name='PPM', mode='lines+markers', line=dict(color='#ef4444', width=3), yaxis='y2'))
                            fig.add_hline(y=10000, line_dash="dot", line_color="#f59e0b", annotation_text="Предел 10 000", yref='y2')
                            
                            fig.update_layout(
                                height=420,
                                xaxis=dict(title="Месяц"),
                                yaxis=dict(title="Кол-во заказов", side='left', showgrid=False),
                                yaxis2=dict(title="Уровень PPM", side='right', overlaying='y', showgrid=True),
                                legend=dict(x=0.01, y=0.99),
                                hovermode="x unified",
                                margin=dict(l=0, r=0, t=10, b=0)
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("Нет данных для графика.")

                    # --- РЕКЛАМАЦИЯ ---
                    st.markdown("---")
                    st.markdown("### 📝 Генерация рекламации на завод")
                    ppm_alerts = display_df[display_df['PPM'] > 10000]
                    selected_sku_claim = st.selectbox("Выберите проблемный артикул для подготовки письма:", ppm_alerts['Артикул продавца'].tolist() if not ppm_alerts.empty else display_df['Артикул продавца'].tolist())
                    
                    if selected_sku_claim and not df_approved.empty:
                        sku_defects = df_approved[df_approved['Артикул продавца'] == selected_sku_claim]
                        all_photos_claim = []
                        for _, r in sku_defects.iterrows():
                            urls = re.findall(r'(?:https?:)?//[^\s"\'\;\]\[,<>]+', str(r.get('db_photos', '')).replace('nan', ''))
                            for u in urls:
                                clean_url = u.replace("']", "").replace("'", "").replace('"', '').strip()
                                if clean_url.startswith("//"): clean_url = "https:" + clean_url
                                if not any(ext in clean_url.lower() for ext in ['.mp4', '.mov', '.avi']): all_photos_claim.append(clean_url)
                                    
                        all_photos_claim = list(set(all_photos_claim))[:15]
                        
                        c_ppm = display_df[display_df['Артикул продавца'] == selected_sku_claim]['PPM'].values[0]
                        c_prc = display_df[display_df['Артикул продавца'] == selected_sku_claim]['Доля брака, %'].values[0]
                        c_qty = display_df[display_df['Артикул продавца'] == selected_sku_claim]['Одобренный брак (шт)'].values[0]
                        
                        claim_text = f"Здравствуйте!\n\nИнформируем вас о превышении допустимого уровня брака по товару (Артикул: {selected_sku_claim}).\nУровень PPM составляет {c_ppm} ({c_prc}% от всех заказов за период).\nВсего зафиксировано и подтверждено брака: {int(c_qty)} ед. (статус заявки - 'Одобрено').\n\nПросим провести внутреннюю проверку на производстве и устранить причину дефектов.\n"
                        if all_photos_claim: 
                            claim_text += "\nСсылки на фотографии брака для подтверждения:\n" + "\n".join(all_photos_claim)
                        st.text_area("Готовое письмо:", value=claim_text, height=300)
                        
                        if all_photos_claim:
                            if st.button("📥 Скачать архив с фото для завода", key="dl_claim_photos", type="primary"):
                                with st.spinner("Сбор фото..."):
                                    components.html(f'<a id="dl_c" href="data:application/zip;base64,{base64.b64encode(create_images_zip(all_photos_claim)).decode()}" download="Рекламация_{selected_sku_claim}.zip"></a><script>document.getElementById("dl_c").click();</script>', width=0, height=0)
                else: 
                    st.warning("⚠️ Нет данных о заказах. Сначала запустите синхронизацию.")

    except Exception as e: 
        st.error(f"Ошибка Отчета: {e}")

elif page == "⭐ Рейтинг товаров":
    st.title("⭐ Мониторинг Рейтинга (В разработке)")
    st.info("Здесь будет отображаться динамика рейтинга товаров (звезды) на основе данных из API Wildberries.")
    st.markdown("Мы настроим воркер на ежедневный сбор оценок, чтобы вы могли видеть падения рейтинга в реальном времени и сопоставлять их с данными PPM.")
        
elif page == "📜 Системный Журнал":
    st.title("📜 Системный Журнал (SQL Edition)")
    st.markdown("Здесь сохраняется хронология процессов напрямую из базы данных.")
    
    if st.button("🔄 Обновить журнал"): 
        st.rerun()

    if engine:
        try:
            # Читаем последние 200 записей
            query = "SELECT created_at as \"Дата\", action as \"Действие\", status as \"Статус\", details as \"Детали\" FROM system_logs ORDER BY created_at DESC LIMIT 200"
            df_logs = pd.read_sql(query, engine)

            if not df_logs.empty:
                # Форматируем дату для удобства
                df_logs['Дата'] = pd.to_datetime(df_logs['Дата']).dt.strftime('%d.%m.%Y %H:%M:%S')

                def color_status(val):
                    color = '#22c55e' if val == 'INFO' or val == 'SUCCESS' else '#ef4444' if 'ERR' in str(val) else '#f59e0b'
                    return f'color: {color}; font-weight: bold;'

                st.dataframe(
                    df_logs.style.map(color_status, subset=['Статус']), 
                    width="stretch", 
                    height=600,
                    hide_index=True
                )
            else:
                st.info("Журнал пуст. Все события будут появляться здесь после запуска тегирования.")
        except Exception as e:
            st.error(f"Ошибка чтения логов из SQL: {e}")
    else:
        st.warning("База данных не подключена. Проверьте DB_URL.")
