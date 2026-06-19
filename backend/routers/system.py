# backend/routers/system.py
import psutil
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import timedelta
from ..database import get_db
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/system",
    tags=["System / Мониторинг и Синхронизация"], 
    dependencies=[Depends(get_current_user)]
)

@router.get("/monitor")
async def get_system_metrics():
    """Получение метрик сервера (Нагрузка CPU, RAM, Диск)"""
    cpu_usage = psutil.cpu_percent(interval=0.1)
    ram_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    
    return {
        "cpu": {"percent": cpu_usage, "status": "Критично" if cpu_usage >= 85 else "ОК"},
        "ram": {
            "percent": ram_info.percent,
            "used_gb": round(ram_info.used / (1024**3), 1),
            "total_gb": round(ram_info.total / (1024**3), 1)
        },
        "disk": {"free_gb": round(disk_info.free / (1024**3), 1)}
    }

@router.get("/logs")
async def get_system_logs(limit: int = 200, db: Session = Depends(get_db)):
    """Получение системного журнала (последние 200 записей)"""
    query = text("SELECT created_at, action, status, details FROM system_logs ORDER BY created_at DESC LIMIT :limit")
    results = db.execute(query, {"limit": limit}).fetchall()
    
    logs = []
    for row in results:
        logs.append({
            "date": (row[0] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M:%S') if row[0] else "",
            "action": row[1],
            "status": row[2],
            "details": row[3]
        })
    return {"count": len(logs), "data": logs}

@router.get("/sync-status")
async def get_sync_status(
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db)
):
    def build_logs(filter_sql=""):
        sql = f"SELECT created_at, action, status, details FROM system_logs {filter_sql} ORDER BY created_at DESC LIMIT 5"
        rows = db.execute(text(sql)).fetchall()
        result = []
        for i, row in enumerate(rows):
            st = (row[2] or "").upper()
            log_type = "success" if "SUCCESS" in st else "warning" if "WARN" in st or "FAIL" in st or "ОШИБ" in st else "info"
            details = row[3] or ""
            short = f"{details[:90]}..." if len(details) > 90 else details
            result.append({
                "id": i,
                "time": (row[0] + timedelta(hours=3)).strftime('%H:%M') if row[0] else "",
                "type": log_type,
                "text": f"[{row[1]}] {short}"
            })
        return result

    def cnt(q, params=None):
        try:
            return db.execute(text(q), params or {}).scalar() or 0
        except Exception:
            db.rollback()
            return 0

    # "Вчера" = предыдущие сутки относительно серверного времени (UTC)
    YESTERDAY = "(CURRENT_DATE - INTERVAL '1 day')"

    if platform == "ozon":
        last_sync_raw = db.execute(text("SELECT MAX(synced_at) FROM ozon_returns")).scalar()
        last_sync_str = (last_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if last_sync_raw else "Нет данных"
        logs_list = build_logs("WHERE action LIKE 'ozon_%' OR action LIKE '%Ozon%' OR action LIKE '%OZON%'")

        ret_total   = cnt("SELECT COUNT(*) FROM ozon_returns WHERE status_sys != 'Cancelled'")
        ret_yest    = cnt(f"SELECT COUNT(*) FROM ozon_returns WHERE DATE(return_date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') = {YESTERDAY} AND status_sys != 'Cancelled'")
        ret_onwh    = cnt("SELECT COUNT(*) FROM ozon_returns WHERE status_sys IN ('ReturnedToOzon','Utilized','WriteOff')")
        ret_transit = cnt("SELECT COUNT(*) FROM ozon_returns WHERE status_sys IN ('MovingToOzon','Utilizing','WaitingShipment','MovingToSeller','ArrivedAtReturnPlace')")
        ai_ret      = cnt("""SELECT COUNT(*) FROM ozon_returns WHERE
                             cat_1 IS NOT NULL OR cat_2 IS NOT NULL OR cat_3 IS NOT NULL OR
                             cat_4 IS NOT NULL OR cat_5 IS NOT NULL OR cat_6 IS NOT NULL OR
                             cat_7 IS NOT NULL OR cat_8 IS NOT NULL OR cat_9 IS NOT NULL OR
                             cat_10 IS NOT NULL OR cat_11 IS NOT NULL OR cat_12 IS NOT NULL OR cat_13 IS NOT NULL""")
        uniq_sku    = cnt("SELECT COUNT(DISTINCT supplier_article) FROM ozon_returns WHERE supplier_article IS NOT NULL AND supplier_article != ''")

        metrics = [
            {"id": "returns",     "name": "Возвраты всего",            "yesterday": ret_yest,   "total": ret_total,   "color": "text-rose-600"},
            {"id": "ret_onwh",    "name": "На складе / Утилизация",    "yesterday": 0,          "total": ret_onwh,    "color": "text-emerald-600"},
            {"id": "ret_transit", "name": "В пути / В обработке",      "yesterday": 0,          "total": ret_transit, "color": "text-amber-500"},
            {"id": "ai_returns",  "name": "Возвраты ИИ-размечены",     "yesterday": 0,          "total": ai_ret,      "color": "text-indigo-600"},
            {"id": "uniq_sku",    "name": "Уникальных SKU",            "yesterday": 0,          "total": uniq_sku},
        ]
        return {"last_sync": last_sync_str, "status": "active", "logs": logs_list, "metrics": metrics}

    if platform == "ym":
        last_sync_raw = db.execute(text("SELECT MAX(synced_at) FROM ym_returns")).scalar()
        last_sync_str = (last_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if last_sync_raw else "Нет данных"
        logs_list = build_logs("WHERE action LIKE 'ym_%' OR action LIKE '%ЯМ%' OR action LIKE '%ИИ%'")

        ret_total   = cnt("SELECT COUNT(*) FROM ym_returns")
        ret_yest    = cnt(f"SELECT COUNT(*) FROM ym_returns WHERE DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') = {YESTERDAY}")
        ret_appr    = cnt("SELECT COUNT(*) FROM ym_returns WHERE status_ru = 'Одобрено'")
        ret_appr_y  = cnt(f"SELECT COUNT(*) FROM ym_returns WHERE status_ru = 'Одобрено' AND DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') = {YESTERDAY}")
        ret_pend    = cnt("SELECT COUNT(*) FROM ym_returns WHERE status_ru = 'На рассмотрении'")
        ord_total   = cnt("SELECT COUNT(*) FROM ym_orders")
        ord_yest    = cnt(f"SELECT COUNT(*) FROM ym_orders WHERE DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') = {YESTERDAY}")
        fb_total    = cnt("SELECT COUNT(*) FROM ym_feedbacks")
        fb_yest     = cnt(f"SELECT COUNT(*) FROM ym_feedbacks WHERE DATE(created_date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') = {YESTERDAY}")
        ai_ret      = cnt("""SELECT COUNT(*) FROM ym_returns WHERE
                             cat_1 IS NOT NULL OR cat_2 IS NOT NULL OR cat_3 IS NOT NULL OR
                             cat_4 IS NOT NULL OR cat_5 IS NOT NULL OR cat_6 IS NOT NULL OR
                             cat_7 IS NOT NULL OR cat_8 IS NOT NULL OR cat_9 IS NOT NULL OR
                             cat_10 IS NOT NULL OR cat_11 IS NOT NULL OR cat_12 IS NOT NULL OR cat_13 IS NOT NULL""")
        ai_fb       = cnt("SELECT COUNT(*) FROM ym_feedbacks WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed'")
        mat_total   = cnt("SELECT COUNT(*) FROM ym_assortment")

        metrics = [
            {"id": "returns",     "name": "Возвраты",                  "yesterday": ret_yest,   "total": ret_total,  "color": "text-rose-600"},
            {"id": "ret_appr",    "name": "Возвраты одобрены",         "yesterday": ret_appr_y, "total": ret_appr,   "color": "text-emerald-600"},
            {"id": "ret_pend",    "name": "На рассмотрении",           "yesterday": 0,          "total": ret_pend,   "color": "text-amber-500"},
            {"id": "orders",      "name": "Заказы",                    "yesterday": ord_yest,   "total": ord_total},
            {"id": "feedbacks",   "name": "Отзывы",                    "yesterday": fb_yest,    "total": fb_total},
            {"id": "ai_returns",  "name": "Возвраты ИИ-размечены",     "yesterday": 0,          "total": ai_ret,     "color": "text-indigo-600"},
            {"id": "ai_fb",       "name": "Отзывы ИИ-тегированы",      "yesterday": 0,          "total": ai_fb,      "color": "text-purple-500"},
            {"id": "assortment",  "name": "SKU в ассортименте",        "yesterday": 0,          "total": mat_total},
        ]
        return {"last_sync": last_sync_str, "status": "active", "logs": logs_list, "metrics": metrics}

    # ── WB ────────────────────────────────────────────────────────────────────
    main_sync_raw = db.execute(text("SELECT MAX(last_sync) FROM wb_claims")).scalar()
    last_sync_str = (main_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if main_sync_raw else "Нет данных"
    logs_list = build_logs("WHERE action NOT LIKE 'ym_%' AND action NOT LIKE '%ЯМ%'")

    ret_total  = cnt("SELECT COUNT(*) FROM wb_claims")
    ret_yest   = cnt(f"SELECT COUNT(*) FROM wb_claims WHERE DATE(created_dt) = {YESTERDAY}")

    appr_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'одобрено'
                   OR LOWER(TRIM(v."Статус товара")) IN (
                       'товар остается у покупателя (заявка одобрена)',
                       'возврат продавцу', 'в реализацию после проверки wb'
                   )"""
    ret_appr   = cnt(f'SELECT COUNT(*) FROM view_cx_dashboard v WHERE {appr_cond}')
    ret_appr_y = cnt(f"""SELECT COUNT(*) FROM view_cx_dashboard v
                         JOIN wb_claims c ON v."SRID" = c.srid
                         WHERE ({appr_cond}) AND DATE(c.created_dt) = {YESTERDAY}""")

    rej_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'отказ'
                  OR LOWER(TRIM(v."Статус товара")) = 'товар остается у покупателя (заявка отклонена)'"""
    ret_rej    = cnt(f'SELECT COUNT(*) FROM view_cx_dashboard v WHERE {rej_cond}')

    pend_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'на рассмотрении'
                   OR LOWER(TRIM(v."Статус товара")) = 'заявка на рассмотрении'"""
    ret_pend   = cnt(f'SELECT COUNT(*) FROM view_cx_dashboard v WHERE {pend_cond}')

    ai_cond    = "cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13"
    ai_ret     = cnt(f"SELECT COUNT(*) FROM wb_claims WHERE {ai_cond}")

    ord_total  = cnt("SELECT COUNT(*) FROM wb_orders")
    ord_yest   = cnt(f"SELECT COUNT(*) FROM wb_orders WHERE DATE(dt) = {YESTERDAY}")

    sale_total = cnt("SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE'")
    sale_yest  = cnt(f"SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE' AND DATE(dt) = {YESTERDAY}")

    fb_total   = cnt("SELECT COUNT(*) FROM wb_feedbacks")
    fb_yest    = cnt(f"SELECT COUNT(*) FROM wb_feedbacks WHERE DATE(created_date) = {YESTERDAY}")
    ai_fb      = cnt("SELECT COUNT(*) FROM wb_feedbacks WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed'")

    mat_total  = cnt("SELECT COUNT(*) FROM wb_assortment")

    metrics = [
        {"id": "returns",    "name": "Возвраты",              "yesterday": ret_yest,   "total": ret_total,  "color": "text-rose-600"},
        {"id": "ret_appr",   "name": "Возвраты одобрены",     "yesterday": ret_appr_y, "total": ret_appr,   "color": "text-emerald-600"},
        {"id": "ret_rej",    "name": "Возвраты — отказ",      "yesterday": 0,          "total": ret_rej,    "color": "text-red-500"},
        {"id": "ret_pend",   "name": "На рассмотрении",       "yesterday": 0,          "total": ret_pend,   "color": "text-amber-500"},
        {"id": "orders",     "name": "Заказы",                "yesterday": ord_yest,   "total": ord_total},
        {"id": "sales",      "name": "Продажи",               "yesterday": sale_yest,  "total": sale_total},
        {"id": "feedbacks",  "name": "Отзывы",                "yesterday": fb_yest,    "total": fb_total},
        {"id": "ai_returns", "name": "Возвраты ИИ-размечены", "yesterday": 0,          "total": ai_ret,     "color": "text-indigo-600"},
        {"id": "ai_fb",      "name": "Отзывы ИИ-тегированы", "yesterday": 0,          "total": ai_fb,      "color": "text-purple-500"},
        {"id": "assortment", "name": "SKU в ассортименте",    "yesterday": 0,          "total": mat_total},
    ]
    return {"last_sync": last_sync_str, "status": "active", "logs": logs_list, "metrics": metrics}