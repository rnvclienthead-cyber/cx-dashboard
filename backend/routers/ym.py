import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/ym",
    tags=["Yandex Market"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/stats")
def get_ym_stats(db: Session = Depends(get_db)):
    """Краткая статистика ЯМ для главного дашборда."""
    try:
        with db.bind.connect() as conn:
            orders    = conn.execute(text(
                "SELECT COUNT(*) FROM ym_orders WHERE status NOT IN ('CANCELLED','RETURNED')"
            )).scalar()
            returns   = conn.execute(text("SELECT COUNT(*) FROM ym_returns")).scalar()
            feedbacks = conn.execute(text("SELECT COUNT(*) FROM ym_feedbacks")).scalar()
            avg_rating = conn.execute(text(
                "SELECT ROUND(AVG(valuation::numeric),2) FROM ym_feedbacks WHERE valuation > 0"
            )).scalar()
        return {
            "platform":    "ym",
            "orders":      int(orders or 0),
            "returns":     int(returns or 0),
            "feedbacks":   int(feedbacks or 0),
            "avg_rating":  float(avg_rating or 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voc/dashboard")
def get_ym_voc_dashboard(db: Session = Depends(get_db)):
    """VOC-дашборд по данным Яндекс Маркета."""
    try:
        with db.bind.connect() as conn:
            fb_rows = conn.execute(text("""
                SELECT id, supplier_article, created_date, valuation,
                       ai_tags, pro_text, contra_text, comment
                FROM ym_feedbacks
                ORDER BY created_date DESC
            """)).mappings().all()

            q_rows = conn.execute(text("""
                SELECT id, supplier_article, created_date, text, answer_text, state
                FROM ym_questions
                ORDER BY created_date DESC
                LIMIT 100
            """)).mappings().all()

        tags_distribution = {}
        suggestions       = []
        sku_stats         = {}
        funnel = {"Конструкция": 0, "Эргономика": 0, "Функционал": 0,
                  "Сборка": 0, "Ожидания": 0}

        for fb in fb_rows:
            sku = fb["supplier_article"] or "N/A"
            val = int(fb["valuation"] or 0)
            ai  = fb["ai_tags"]

            if sku not in sku_stats:
                sku_stats[sku] = {"total": 0, "negative": 0, "sum_rating": 0, "tags": {}}
            sku_stats[sku]["total"]      += 1
            sku_stats[sku]["sum_rating"] += val
            if val <= 3:
                sku_stats[sku]["negative"] += 1

            if isinstance(ai, str):
                try:
                    ai = json.loads(ai)
                except Exception:
                    ai = None

            if isinstance(ai, dict):
                for tag in ai.get("tags", []):
                    if ":" in tag:
                        parent, _ = [t.strip() for t in tag.split(":", 1)]
                        if "КОНСТРУКЦИЯ" in parent:   funnel["Конструкция"] += 1
                        elif "ЭРГОНОМИКА" in parent:  funnel["Эргономика"]  += 1
                        elif "ФУНКЦИОНАЛ" in parent:  funnel["Функционал"]  += 1
                        elif "СБОРКА" in parent:      funnel["Сборка"]      += 1
                        elif "ОЖИДАНИЯ" in parent:    funnel["Ожидания"]    += 1
                        tags_distribution[tag]         = tags_distribution.get(tag, 0) + 1
                        sku_stats[sku]["tags"][tag]     = sku_stats[sku]["tags"].get(tag, 0) + 1
                if ai.get("suggestion"):
                    suggestions.append({
                        "id":       fb["id"],
                        "sku":      sku,
                        "date":     fb["created_date"].strftime("%Y-%m-%d") if fb["created_date"] else "-",
                        "rating":   val,
                        "text":     ai["suggestion"],
                        "platform": "ym",
                    })
            else:
                # Нет AI-тегов: показываем минусы как сигнал
                contra = (fb["contra_text"] or "").strip()
                if contra:
                    suggestions.append({
                        "id":       fb["id"],
                        "sku":      sku,
                        "date":     fb["created_date"].strftime("%Y-%m-%d") if fb["created_date"] else "-",
                        "rating":   val,
                        "text":     contra,
                        "platform": "ym",
                    })

        sku_list = []
        for k, v in sku_stats.items():
            if v["total"] > 0:
                top_tag = max(v["tags"], key=v["tags"].get) if v["tags"] else "Нет проблем"
                sku_list.append({
                    "sku":        k,
                    "total":      v["total"],
                    "negative":   v["negative"],
                    "avg_rating": round(v["sum_rating"] / v["total"], 2),
                    "top_problem": top_tag,
                    "platform":   "ym",
                })
        sku_list = sorted(sku_list, key=lambda x: x["negative"], reverse=True)[:20]

        return {
            "status":            "success",
            "platform":          "ym",
            "tags_distribution": tags_distribution,
            "funnel":            funnel,
            "suggestions":       suggestions[:50],
            "questions":         [dict(q) for q in q_rows],
            "sku_ranking":       sku_list,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/returns")
def get_ym_returns(db: Session = Depends(get_db)):
    """Возвраты ЯМ с разбивкой по категориям дефектов."""
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    supplier_article,
                    defect_category,
                    COUNT(*)       AS cnt,
                    MIN(created_at) AS first_date,
                    MAX(created_at) AS last_date
                FROM ym_returns
                WHERE supplier_article IS NOT NULL
                GROUP BY supplier_article, defect_category
                ORDER BY cnt DESC
            """)).mappings().all()
        return {"status": "success", "platform": "ym", "data": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/finance/summary")
def get_ym_finance_summary(db: Session = Depends(get_db)):
    """Последние загруженные финансовые отчёты ЯМ."""
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(text("""
                SELECT report_type, period_from, period_to,
                       revenue, commissions, status, downloaded_at
                FROM ym_finance_reports
                WHERE status = 'downloaded'
                ORDER BY downloaded_at DESC
                LIMIT 10
            """)).mappings().all()
        return {"status": "success", "platform": "ym", "data": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


