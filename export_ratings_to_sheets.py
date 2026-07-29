"""
Экспорт рейтингов WB и YM в Google Sheets — матричный формат.
Строки = артикулы, столбцы = даты, ячейки = рейтинг за день.
Запуск: python3 export_ratings_to_sheets.py
"""

import os
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, text
import gspread
from google.oauth2.service_account import Credentials

# ─── Конфиг ──────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE     = os.path.join(BASE_DIR, "bot_api_key.json")
ID_FILE        = os.path.join(BASE_DIR, ".sheets_id")
DB_URL         = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
SCOPES         = ["https://spreadsheets.google.com/feeds",
                  "https://www.googleapis.com/auth/drive"]
DAYS_BACK      = 60   # сколько дней назад брать данные

# ─── БД ──────────────────────────────────────────────────────────────────────
engine = create_engine(DB_URL)

def fetch_wb_summary():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                t.supplier_article,
                ROUND(t.average_rating::numeric, 2)    AS rating,
                t.review_count                          AS total,
                COALESCE(r."feedbackCount_current", 0)  AS new_today,
                t.stars_5, t.stars_4, t.stars_3, t.stars_2, t.stars_1,
                TO_CHAR(t.refreshed_at, 'DD.MM.YYYY HH24:MI') AS updated
            FROM wb_ratings_totals t
            LEFT JOIN wb_ratings_raw r
                ON r.sys_article = t.supplier_article
                AND r.sys_date = (SELECT MAX(sys_date) FROM wb_ratings_raw)
            ORDER BY t.review_count DESC NULLS LAST
        """)).fetchall()
    return rows

def fetch_ym_summary():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            WITH totals AS (
                SELECT
                    TRIM(supplier_article)                     AS sku,
                    COUNT(*)                                   AS total,
                    ROUND(AVG(valuation)::numeric, 2)          AS rating,
                    COUNT(CASE WHEN valuation = 5 THEN 1 END)  AS stars_5,
                    COUNT(CASE WHEN valuation = 4 THEN 1 END)  AS stars_4,
                    COUNT(CASE WHEN valuation = 3 THEN 1 END)  AS stars_3,
                    COUNT(CASE WHEN valuation = 2 THEN 1 END)  AS stars_2,
                    COUNT(CASE WHEN valuation = 1 THEN 1 END)  AS stars_1
                FROM ym_feedbacks
                WHERE supplier_article IS NOT NULL AND TRIM(supplier_article) != '' AND valuation IS NOT NULL
                GROUP BY TRIM(supplier_article)
            ),
            new_today AS (
                SELECT TRIM(supplier_article) AS sku, COUNT(*) AS cnt
                FROM ym_feedbacks
                WHERE supplier_article IS NOT NULL AND created_date::date = CURRENT_DATE
                GROUP BY TRIM(supplier_article)
            )
            SELECT t.sku AS supplier_article, t.rating, t.total,
                   COALESCE(n.cnt, 0) AS new_today,
                   t.stars_5, t.stars_4, t.stars_3, t.stars_2, t.stars_1,
                   TO_CHAR(NOW(), 'DD.MM.YYYY HH24:MI') AS updated
            FROM totals t LEFT JOIN new_today n ON n.sku = t.sku
            ORDER BY t.total DESC
        """)).fetchall()
    return rows

SUMMARY_HEADER = ["Артикул", "Рейтинг", "Всего оценок", "+Вчера",
                  "★5", "★4", "★3", "★2", "★1", "Обновлено"]

def build_summary(rows):
    return [list(SUMMARY_HEADER)] + [
        [r.supplier_article, float(r.rating or 0), int(r.total or 0),
         int(r.new_today or 0), int(r.stars_5 or 0), int(r.stars_4 or 0),
         int(r.stars_3 or 0), int(r.stars_2 or 0), int(r.stars_1 or 0), r.updated or ""]
        for r in rows
    ]

def update_summary_sheet(ws, values):
    ws.clear()
    if values:
        ws.update(values, "A1")
    ws.format("A1:J1", {"textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 1.0}})
    try:
        ws.freeze(rows=1)
    except Exception:
        pass


def fetch_wb_matrix():
    """Возвращает {supplier_article: {date_str: rating}}"""
    since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                sys_article                                        AS sku,
                sys_date::text                                     AS dt,
                ROUND(CAST(COALESCE("feedbackRating_current", 0) AS numeric), 2) AS rating
            FROM wb_ratings_raw
            WHERE sys_article IS NOT NULL
              AND sys_article NOT IN ('Unknown','nan','')
              AND sys_date >= :since
            ORDER BY sys_date
        """), {"since": since}).fetchall()

    data = defaultdict(dict)
    for r in rows:
        if r.rating and r.rating > 0:
            data[r.sku][r.dt] = float(r.rating)
    return data

def fetch_ym_matrix():
    """Возвращает {supplier_article: {date_str: rating}}"""
    since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                supplier_article                          AS sku,
                report_date::text                         AS dt,
                ROUND(COALESCE(group_rating, 0)::numeric, 2) AS rating
            FROM ym_ratings_report
            WHERE supplier_article IS NOT NULL
              AND report_date >= :since
            ORDER BY report_date
        """), {"since": since}).fetchall()

    data = defaultdict(dict)
    for r in rows:
        if r.rating and r.rating > 0:
            data[r.sku][r.dt] = float(r.rating)
    return data

# ─── Построение матрицы ───────────────────────────────────────────────────────
def build_matrix(data: dict) -> list[list]:
    """
    data = {sku: {date_str: rating}}
    Возвращает список строк для Google Sheets:
    [["Артикул", "01.07", "02.07", ...], [sku1, val, val, ...], ...]
    """
    if not data:
        return [["Нет данных"]]

    # Все даты по возрастанию
    all_dates = sorted({dt for sku_dates in data.values() for dt in sku_dates.values()})
    # На случай если даты попали в values() — берём из ключей
    all_dates = sorted({dt for sku_dates in data.values() for dt in sku_dates.keys()})

    # Заголовок: "Артикул" + даты в формате ДД.ММ
    def fmt_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
        except Exception:
            return d

    header = ["Артикул"] + [fmt_date(d) for d in all_dates]

    # Строки: сортируем SKU по алфавиту
    rows = [header]
    for sku in sorted(data.keys()):
        sku_dates = data[sku]
        row = [sku] + [sku_dates.get(d, "") for d in all_dates]
        rows.append(row)

    return rows

# ─── Sheets ──────────────────────────────────────────────────────────────────
def get_spreadsheet(gc):
    if os.path.exists(ID_FILE):
        saved_id = open(ID_FILE).read().strip()
        if saved_id:
            return gc.open_by_key(saved_id)
    raise RuntimeError("Файл .sheets_id не найден. Запустите с аргументом ID таблицы.")

def get_or_add_sheet(sh, title):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=500, cols=200)

def update_matrix_sheet(ws, matrix):
    ws.clear()
    if not matrix:
        return
    ws.update(matrix, "A1")
    # Заголовок жирный + заморозить первую строку и столбец
    ws.format("A1:ZZ1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 1.0}})
    ws.format("A1:A500", {"textFormat": {"bold": True}})
    try:
        ws.freeze(rows=1, cols=1)
    except Exception:
        pass

# ─── Главное ─────────────────────────────────────────────────────────────────
def main():
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc    = gspread.authorize(creds)
    sh    = get_spreadsheet(gc)
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")

    # ── WB сводная ──
    wb_sum = fetch_wb_summary()
    ws     = get_or_add_sheet(sh, "WB — Рейтинги")
    update_summary_sheet(ws, build_summary(wb_sum))
    print(f"[{now}] WB сводная: {len(wb_sum)} строк → «WB — Рейтинги»")

    # ── YM сводная ──
    ym_sum = fetch_ym_summary()
    ws     = get_or_add_sheet(sh, "YM — Рейтинги")
    update_summary_sheet(ws, build_summary(ym_sum))
    print(f"[{now}] YM сводная: {len(ym_sum)} строк → «YM — Рейтинги»")

    # ── WB по дням ──
    wb_data   = fetch_wb_matrix()
    wb_matrix = build_matrix(wb_data)
    ws_wb     = get_or_add_sheet(sh, "WB — По дням")
    update_matrix_sheet(ws_wb, wb_matrix)
    print(f"[{now}] WB матрица: {len(wb_data)} артикулов, {len(wb_matrix[0])-1 if wb_matrix else 0} дней → «WB — По дням»")

    # ── YM по дням ──
    ym_data   = fetch_ym_matrix()
    ym_matrix = build_matrix(ym_data)
    ws_ym     = get_or_add_sheet(sh, "YM — По дням")
    update_matrix_sheet(ws_ym, ym_matrix)
    print(f"[{now}] YM матрица: {len(ym_data)} артикулов, {len(ym_matrix[0])-1 if ym_matrix else 0} дней → «YM — По дням»")

    print(f"Таблица: https://docs.google.com/spreadsheets/d/{sh.id}")

if __name__ == "__main__":
    main()
