"""
Экспорт рейтингов WB, YM и Ozon в Google Sheets — матричный формат.
Строки = артикулы, столбцы = даты, ячейки = рейтинг за день.
Запуск: python3 export_ratings_to_sheets.py
"""

import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, text
import gspread
from google.oauth2.service_account import Credentials

# ─── Конфиг ──────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE     = os.path.join(BASE_DIR, "bot_api_key.json")
ID_FILE        = os.path.join(BASE_DIR, ".sheets_id")
# «Рейтинги для Артема». Файл .sheets_id на сервере однажды пропал,
# и ежедневная выгрузка молча падала. ID держим в коде.
SPREADSHEET_ID = "1Bp-iVm4eGCS3Xym2fgbPPcX9v3j5o8TnrGwjlBJd5Xw"
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
    sheet_id = SPREADSHEET_ID
    if len(sys.argv) > 1 and sys.argv[1].strip():
        sheet_id = sys.argv[1].strip()
    elif os.path.exists(ID_FILE):
        saved_id = open(ID_FILE).read().strip()
        if saved_id:
            sheet_id = saved_id
    return gc.open_by_key(sheet_id)

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
    hide_columns_through_last_filled_day(ws, matrix)


def hide_columns_through_last_filled_day(ws, matrix):
    """Скрыть столбцы с датами до последнего дня, в котором уже есть оценка.

    Сам последний заполненный день и столбец артикула остаются видимыми.
    После новой выгрузки прячется предыдущий последний день.
    """
    if not matrix or len(matrix[0]) < 2:
        return
    width = len(matrix[0])
    last = 0
    for row in matrix[1:]:
        for idx in range(1, min(len(row), width)):
            if row[idx] not in ("", None):
                last = max(last, idx)
    if last < 1:
        return
    end_unhide = max(getattr(ws, "col_count", 0) or 0, last + 1)
    requests = []
    if last >= 2:
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": last,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        })
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": ws.id,
                "dimension": "COLUMNS",
                "startIndex": last,
                "endIndex": end_unhide,
            },
            "properties": {"hiddenByUser": False},
            "fields": "hiddenByUser",
        }
    })
    ws.spreadsheet.batch_update({"requests": requests})


def fetch_ozon_summary():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            WITH days AS (
                SELECT
                    report_date,
                    sku,
                    COALESCE(NULLIF(TRIM(supplier_article), ''), sku::text) AS supplier_article,
                    COALESCE(average_rating, 0) AS average_rating,
                    COALESCE(review_count, 0) AS review_count
                FROM ozon_ratings_report
            ),
            ranked AS (
                SELECT days.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY supplier_article
                           ORDER BY report_date DESC, sku
                       ) AS rn
                FROM days
            ),
            stars AS (
                SELECT
                    TRIM(supplier_article) AS supplier_article,
                    COUNT(*) FILTER (WHERE rating = 5) AS stars_5,
                    COUNT(*) FILTER (WHERE rating = 4) AS stars_4,
                    COUNT(*) FILTER (WHERE rating = 3) AS stars_3,
                    COUNT(*) FILTER (WHERE rating = 2) AS stars_2,
                    COUNT(*) FILTER (WHERE rating = 1) AS stars_1
                FROM ozon_reviews
                WHERE supplier_article IS NOT NULL
                  AND TRIM(supplier_article) <> ''
                  AND rating BETWEEN 1 AND 5
                GROUP BY TRIM(supplier_article)
            ),
            yday AS (
                SELECT TRIM(supplier_article) AS supplier_article, COUNT(*) AS cnt
                FROM ozon_reviews
                WHERE supplier_article IS NOT NULL
                  AND created_at::date = CURRENT_DATE - 1
                GROUP BY TRIM(supplier_article)
            )
            SELECT
                l.supplier_article,
                ROUND(l.average_rating::numeric, 2) AS rating,
                l.review_count AS total,
                COALESCE(y.cnt, 0) AS new_today,
                COALESCE(s.stars_5, 0) AS stars_5,
                COALESCE(s.stars_4, 0) AS stars_4,
                COALESCE(s.stars_3, 0) AS stars_3,
                COALESCE(s.stars_2, 0) AS stars_2,
                COALESCE(s.stars_1, 0) AS stars_1,
                TO_CHAR(l.report_date, 'DD.MM.YYYY') AS updated
            FROM ranked l
            LEFT JOIN stars s ON s.supplier_article = l.supplier_article
            LEFT JOIN yday y ON y.supplier_article = l.supplier_article
            WHERE l.rn = 1
            ORDER BY l.review_count DESC NULLS LAST
        """)).fetchall()
    return rows


def fetch_ozon_matrix():
    """Возвращает {supplier_article: {date_str: rating}}"""
    since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                COALESCE(NULLIF(TRIM(supplier_article), ''), sku::text) AS sku,
                report_date::text AS dt,
                ROUND(COALESCE(average_rating, 0)::numeric, 2) AS rating
            FROM ozon_ratings_report
            WHERE report_date >= :since
            ORDER BY report_date
        """), {"since": since}).fetchall()

    data = defaultdict(dict)
    for r in rows:
        if r.sku and r.rating and r.rating > 0:
            data[r.sku][r.dt] = float(r.rating)
    return data

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

    # ── Ozon сводная ──
    oz_sum = fetch_ozon_summary()
    ws     = get_or_add_sheet(sh, "OZON — Рейтинги")
    update_summary_sheet(ws, build_summary(oz_sum))
    print(f"[{now}] Ozon сводная: {len(oz_sum)} строк → «OZON — Рейтинги»")

    # ── Ozon по дням ──
    oz_data   = fetch_ozon_matrix()
    oz_matrix = build_matrix(oz_data)
    ws_oz     = get_or_add_sheet(sh, "OZON — По дням")
    update_matrix_sheet(ws_oz, oz_matrix)
    print(f"[{now}] Ozon матрица: {len(oz_data)} артикулов, {len(oz_matrix[0])-1 if oz_matrix else 0} дней → «OZON — По дням»")

    print(f"Таблица: https://docs.google.com/spreadsheets/d/{sh.id}")

if __name__ == "__main__":
    main()
