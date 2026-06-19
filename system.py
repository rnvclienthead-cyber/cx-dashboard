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
            "date": row[0].strftime('%d.%m.%Y %H:%M:%S') if row[0] else "",
            "action": row[1],
            "status": row[2],
            "details": row[3]
        })
    return {"count": len(logs), "data": logs}

@router.get("/sync-status")
async def get_sync_status(
    platform: str = Query("wb", pattern="^(wb|ym|all)$"),
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

    if platform == "ym":
        last_sync_raw = db.execute(text("SELECT MAX(synced_at) FROM ym_returns")).scalar()
        last_sync_str = (last_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if last_sync_raw else "Нет данных"
        logs_list = build_logs("WHERE action LIKE 'ym_%' OR action LIKE '%ЯМ%'")

        def cnt(q): return db.execute(text(q)).scalar() or 0

        metrics = [
            {"id": "ym_orders",    "name": "Заказы ЯМ",              "today": cnt("SELECT COUNT(*) FROM ym_orders WHERE DATE(created_at) >= CURRENT_DATE"),    "total": cnt("SELECT COUNT(*) FROM ym_orders")},
            {"id": "ym_returns",   "name": "Возвраты ЯМ",            "today": cnt("SELECT COUNT(*) FROM ym_returns WHERE DATE(created_at) >= CURRENT_DATE"),   "total": cnt("SELECT COUNT(*) FROM ym_returns"),   "color": "text-rose-600"},
            {"id": "ym_feedbacks", "name": "Отзывы ЯМ",              "today": cnt("SELECT COUNT(*) FROM ym_feedbacks WHERE DATE(created_date) >= CURRENT_DATE"), "total": cnt("SELECT COUNT(*) FROM ym_feedbacks")},
            {"id": "ym_questions", "name": "Вопросы Q&A",            "today": 0, "total": cnt("SELECT COUNT(*) FROM ym_questions")},
            {"id": "ym_assortment","name": "SKU в ассортименте",     "today": 0, "total": cnt("SELECT COUNT(*) FROM ym_assortment")},
            {"id": "ym_prices",    "name": "Цены актуализированы",   "today": 0, "total": cnt("SELECT COUNT(*) FROM ym_prices")},
            {"id": "ym_ai_tagged", "name": "Отзывы с ИИ-тегами",     "today": 0, "total": cnt("SELECT COUNT(*) FROM ym_feedbacks WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed'"), "color": "text-indigo-600"},
        ]
        return {"last_sync": last_sync_str, "status": "active", "logs": logs_list, "metrics": metrics}

    # ── WB (default) ─────────────────────────────────────────────────────────
    main_sync_raw = db.execute(text("SELECT MAX(last_sync) FROM wb_claims")).scalar()
    last_sync_str = (main_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if main_sync_raw else "Нет данных"
    logs_list = build_logs()

    metrics = []
    def get_metric(query_total, query_today):
        t = db.execute(text(query_total)).scalar() or 0
        td = db.execute(text(query_today)).scalar() or 0
        return t, td

    cl_t, cl_td = get_metric("SELECT COUNT(*) FROM wb_claims", "SELECT COUNT(*) FROM wb_claims WHERE DATE(created_dt) >= CURRENT_DATE")
    metrics.append({"id": "claims", "name": "Все возвраты", "today": cl_td, "total": cl_t})

    appr_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'одобрено' OR LOWER(TRIM(v."Статус товара")) IN ('товар остается у покупателя (заявка одобрена)', 'возврат продавцу', 'в реализацию после проверки wb')"""
    appr_t = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v WHERE {appr_cond}")).scalar() or 0
    appr_td = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v JOIN wb_claims c ON v.\"SRID\" = c.srid WHERE ({appr_cond}) AND DATE(c.created_dt) >= CURRENT_DATE")).scalar() or 0
    metrics.append({"id": "approved", "name": "Одобренные возвраты", "today": appr_td, "total": appr_t, "color": "text-emerald-600"})

    rej_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'отказ' OR LOWER(TRIM(v."Статус товара")) = 'товар остается у покупателя (заявка отклонена)'"""
    rej_t = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v WHERE {rej_cond}")).scalar() or 0
    rej_td = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v JOIN wb_claims c ON v.\"SRID\" = c.srid WHERE ({rej_cond}) AND DATE(c.created_dt) >= CURRENT_DATE")).scalar() or 0
    metrics.append({"id": "rejected", "name": "Отказы по возвратам", "today": rej_td, "total": rej_t, "color": "text-red-500"})

    pend_cond = """LOWER(TRIM(v."Решение по возврату покупателю")) = 'на рассмотрении' OR LOWER(TRIM(v."Статус товара")) = 'заявка на рассмотрении'"""
    pend_t = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v WHERE {pend_cond}")).scalar() or 0
    pend_td = db.execute(text(f"SELECT COUNT(*) FROM view_cx_dashboard v JOIN wb_claims c ON v.\"SRID\" = c.srid WHERE ({pend_cond}) AND DATE(c.created_dt) >= CURRENT_DATE")).scalar() or 0
    metrics.append({"id": "pending", "name": "Возвраты на рассмотрении", "today": pend_td, "total": pend_t, "color": "text-amber-500"})

    ai_cond = "cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13"
    ai_t = db.execute(text(f"SELECT COUNT(*) FROM wb_claims WHERE {ai_cond}")).scalar() or 0
    metrics.append({"id": "tagged", "name": "Тегированные ИИ данные", "today": 0, "total": ai_t, "color": "text-indigo-600"})

    ord_t, ord_td = get_metric("SELECT COUNT(*) FROM wb_orders", "SELECT COUNT(*) FROM wb_orders WHERE DATE(dt) >= CURRENT_DATE")
    metrics.append({"id": "orders", "name": "Заказы", "today": ord_td, "total": ord_t})

    sale_t, sale_td = get_metric("SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE'", "SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE' AND DATE(dt) >= CURRENT_DATE")
    metrics.append({"id": "sales", "name": "Продажи", "today": sale_td, "total": sale_t})

    inv_t, inv_td = get_metric("SELECT COUNT(*) FROM wb_invoices", "SELECT COUNT(*) FROM wb_invoices WHERE DATE(created_at) >= CURRENT_DATE")
    metrics.append({"id": "invoices", "name": "Инвойсы (Поставки)", "today": inv_td, "total": inv_t})

    rat_t = db.execute(text("SELECT COUNT(*) FROM wb_ratings_raw")).scalar() or 0
    rat_td = db.execute(text("SELECT COUNT(*) FROM wb_ratings_raw WHERE sys_date = CURRENT_DATE::text")).scalar() or 0
    metrics.append({"id": "ratings", "name": "Оценки и отзывы", "today": rat_td, "total": rat_t})

    mat_t = db.execute(text("SELECT COUNT(*) FROM wb_assortment")).scalar() or 0
    metrics.append({"id": "matrix", "name": "SKU в ассортименте", "today": 0, "total": mat_t})

    return {"last_sync": last_sync_str, "status": "active", "logs": logs_list, "metrics": metrics}