# backend/routers/system.py
import psutil
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import timedelta
from ..database import get_db

router = APIRouter(
    prefix="/api/v1/system",
    tags=["System / Мониторинг и Синхронизация"]
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
async def get_sync_status(db: Session = Depends(get_db)):
    """Получение данных для дашборда Робота-Синхронизатора"""
    stats = {}
    
    # 1. Время последней синхронизации
    main_sync_raw = db.execute(text("SELECT MAX(last_sync) FROM wb_claims")).scalar()
    rat_sync_raw = db.execute(text("SELECT MAX(last_sync) FROM wb_ratings")).scalar()
    
    stats["main_sync"] = (main_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if main_sync_raw else "Нет данных"
    stats["rating_sync"] = (rat_sync_raw + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if rat_sync_raw else "Нет данных"
    
    # 2. Общие счетчики базы
    stats["claims_total"] = db.execute(text("SELECT COUNT(*) FROM wb_claims")).scalar() or 0
    stats["orders_total"] = db.execute(text("SELECT COUNT(*) FROM wb_orders")).scalar() or 0
    stats["sales_total"] = db.execute(text("SELECT COUNT(*) FROM wb_logistics WHERE doc_type='SALE'")).scalar() or 0
    
    try: stats["invoices_total"] = db.execute(text("SELECT COUNT(*) FROM wb_invoices")).scalar() or 0
    except: stats["invoices_total"] = 0
    
    try: stats["ratings_total"] = db.execute(text("SELECT COUNT(*) FROM wb_ratings")).scalar() or 0
    except: stats["ratings_total"] = 0

    # 3. Расчет Одобренных и Отказов (Точная логика из Streamlit)
    appr_query = text("""
        SELECT COUNT(*) FROM view_cx_dashboard
        WHERE LOWER(TRIM("Решение по возврату покупателю")) IN ('одобрено', '2', '2.0', 'да', 'true')
           OR LOWER(TRIM("Статус товара")) IN ('одобрено', '2', '2.0', 'да', 'true')
    """)
    stats["claims_approved"] = db.execute(appr_query).scalar() or 0

    rej_query = text("""
        SELECT COUNT(*) FROM view_cx_dashboard
        WHERE LOWER(TRIM("Решение по возврату покупателю")) IN ('отклонено', 'отказ', 'false', 'нет', 'отмена')
           OR LOWER(TRIM("Статус товара")) IN ('отклонено', 'отказ', 'false', 'нет', 'отмена')
    """)
    stats["claims_rejected"] = db.execute(rej_query).scalar() or 0

    return stats