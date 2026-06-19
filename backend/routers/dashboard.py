from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
import calendar
from ..database import get_db
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Executive Dashboard / Главная панель управления"],
    dependencies=[Depends(get_current_user)]
)

def _prev_month(dt: datetime) -> datetime:
    """Тот же день/время, но месяц назад. Если в прошлом месяце меньше дней — берём последний."""
    month = dt.month - 1
    year  = dt.year
    if month == 0:
        month = 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


_WB_CLAIMS_SQL = text("""
    SELECT
        TRIM(c.supplier_article) AS sku,
        COUNT(c.srid) AS defects,
        SUM(COALESCE(cog.cost_value, cog_latest.cost_value, 0)) AS direct_loss
    FROM wb_claims c
    LEFT JOIN wb_logistics l ON l.srid = c.srid
    LEFT JOIN (
        SELECT DISTINCT ON (supply_id, supplier_article)
            supply_id, supplier_article, invoice_num
        FROM wb_invoices
    ) inv
        ON SPLIT_PART(l.income_id::text, '.', 1) = SPLIT_PART(inv.supply_id::text, '.', 1)
       AND TRIM(c.supplier_article) = TRIM(inv.supplier_article)
    LEFT JOIN wb_cogs cog
        ON TRIM(cog.supplier_article) = TRIM(c.supplier_article)
       AND TRIM(cog.invoice_num) = TRIM(COALESCE(inv.invoice_num, ''))
       AND cog.cost_value > 0
    LEFT JOIN (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS supplier_article, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    ) cog_latest ON cog_latest.supplier_article = TRIM(c.supplier_article)
    WHERE c.created_dt >= :start AND c.created_dt <= :end
      AND (LOWER(TRIM(c.status)) IN ('одобрено','2','2.0','да','true')
           OR LOWER(TRIM(c.status_ex)) IN ('одобрено','2','2.0','да','true'))
      AND c.supplier_article IS NOT NULL
      AND TRIM(c.supplier_article) NOT IN ('', 'Без артикула')
    GROUP BY TRIM(c.supplier_article)
""")

_WB_FIN_SQL = text("""
    WITH avg_costs AS (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS sku, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    )
    SELECT
        COALESCE(SUM(l.finish_price) FILTER (WHERE l.finish_price > 0), 0) AS lost_revenue,
        COALESCE(SUM(
            CASE WHEN l.finish_price > 0 AND ac.cost_value IS NOT NULL AND l.finish_price > ac.cost_value
                 THEN l.finish_price - ac.cost_value END
        ), 0) AS lost_profit
    FROM wb_claims c
    JOIN wb_logistics l ON l.srid = c.srid
    LEFT JOIN avg_costs ac ON ac.sku = TRIM(c.supplier_article)
    WHERE c.created_dt BETWEEN :start AND :end
      AND (LOWER(TRIM(c.status)) IN ('одобрено','2','2.0','да','true')
           OR LOWER(TRIM(c.status_ex)) IN ('одобрено','2','2.0','да','true'))
""")

_YM_RETURNS_SQL = text("""
    SELECT
        r.supplier_article AS sku,
        COUNT(*) AS defects,
        SUM(COALESCE(p.price, 0)) AS direct_loss
    FROM ym_returns r
    LEFT JOIN ym_prices p ON p.supplier_article = r.supplier_article
    WHERE r.created_at >= :start AND r.created_at <= :end
      AND r.supplier_article IS NOT NULL
    GROUP BY r.supplier_article
""")

_YM_FIN_SQL = text("""
    WITH avg_costs AS (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS sku, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    )
    SELECT
        COALESCE(SUM(o.buyer_total) FILTER (WHERE o.buyer_total > 0), 0) AS lost_revenue,
        COALESCE(SUM(
            CASE WHEN o.buyer_total > 0 AND ac.cost_value IS NOT NULL AND o.buyer_total > ac.cost_value
                 THEN o.buyer_total - ac.cost_value END
        ), 0) AS lost_profit
    FROM ym_returns r
    LEFT JOIN ym_orders o ON o.order_id = r.order_id
                         AND o.supplier_article = TRIM(r.supplier_article)
    LEFT JOIN avg_costs ac ON ac.sku = TRIM(r.supplier_article)
    WHERE r.created_at BETWEEN :start AND :end
      AND r.supplier_article IS NOT NULL
""")


@router.get("/executive")
def get_executive_summary(
    start_date: str, end_date: str,
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db),
):
    try:
        start_dt = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_dt   = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
        params   = {"start": start_dt, "end": end_dt}

        # Предыдущий период: те же числа месяца назад
        prev_start_dt = _prev_month(start_dt)
        prev_end_dt   = _prev_month(end_dt)
        prev_params   = {"start": prev_start_dt, "end": prev_end_dt}

        orders_count  = 0
        total_loss    = 0.0
        prev_total_loss = 0.0
        total_defects = 0
        total_fb      = 0
        positive_fb   = 0
        ai_processed  = 0
        sku_losses: dict  = {}
        sku_defects: dict = {}

        prev_orders_count = 0
        prev_total_defects = 0
        prev_total_fb     = 0
        prev_positive_fb  = 0
        prev_ai_processed = 0

        lost_revenue      = 0.0
        lost_profit       = 0.0
        prev_lost_revenue = 0.0
        prev_lost_profit  = 0.0

        with db.bind.connect() as conn:

            # ── WB ─────────────────────────────────────────────────────────
            if platform in ("wb", "all"):
                orders_count += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_orders
                    WHERE dt >= :start AND dt <= :end AND cancel_dt IS NULL
                """), params).scalar() or 0
                prev_orders_count += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_orders
                    WHERE dt >= :start AND dt <= :end AND cancel_dt IS NULL
                """), prev_params).scalar() or 0

                for row in conn.execute(_WB_CLAIMS_SQL, params).mappings().all():
                    sku = row["sku"]
                    d   = int(row["defects"] or 0)
                    lv  = float(row["direct_loss"] or 0)
                    total_defects += d
                    total_loss    += lv
                    sku_losses[sku]  = sku_losses.get(sku, 0)  + lv
                    sku_defects[sku] = sku_defects.get(sku, 0) + d

                for row in conn.execute(_WB_CLAIMS_SQL, prev_params).mappings().all():
                    prev_total_loss    += float(row["direct_loss"] or 0)
                    prev_total_defects += int(row["defects"] or 0)

                wb_csat = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           COUNT(CASE WHEN valuation >= 4 THEN 1 END) AS positive
                    FROM wb_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                """), params).fetchone()
                total_fb    += int(wb_csat[0] or 0)
                positive_fb += int(wb_csat[1] or 0)

                wb_csat_prev = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           COUNT(CASE WHEN valuation >= 4 THEN 1 END) AS positive
                    FROM wb_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                """), prev_params).fetchone()
                prev_total_fb    += int(wb_csat_prev[0] or 0)
                prev_positive_fb += int(wb_csat_prev[1] or 0)

                ai_processed += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_claims
                    WHERE created_dt >= :start AND created_dt <= :end
                      AND (cat_1 IS NOT NULL OR cat_2 IS NOT NULL OR cat_3 IS NOT NULL)
                """), params).scalar() or 0
                ai_processed += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                      AND ai_tags IS NOT NULL AND ai_tags ? 'processed'
                """), params).scalar() or 0

                prev_ai_processed += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_claims
                    WHERE created_dt >= :start AND created_dt <= :end
                      AND (cat_1 IS NOT NULL OR cat_2 IS NOT NULL OR cat_3 IS NOT NULL)
                """), prev_params).scalar() or 0
                prev_ai_processed += conn.execute(text("""
                    SELECT COUNT(*) FROM wb_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                      AND ai_tags IS NOT NULL AND ai_tags ? 'processed'
                """), prev_params).scalar() or 0

                wb_fin = conn.execute(_WB_FIN_SQL, params).fetchone()
                lost_revenue      += float(wb_fin[0] or 0)
                lost_profit       += float(wb_fin[1] or 0)
                wb_fin_prev = conn.execute(_WB_FIN_SQL, prev_params).fetchone()
                prev_lost_revenue += float(wb_fin_prev[0] or 0)
                prev_lost_profit  += float(wb_fin_prev[1] or 0)

            # ── YM ─────────────────────────────────────────────────────────
            if platform in ("ym", "all"):
                orders_count += conn.execute(text("""
                    SELECT COUNT(*) FROM ym_orders
                    WHERE created_at >= :start AND created_at <= :end
                      AND status NOT IN ('CANCELLED', 'RETURNED')
                """), params).scalar() or 0
                prev_orders_count += conn.execute(text("""
                    SELECT COUNT(*) FROM ym_orders
                    WHERE created_at >= :start AND created_at <= :end
                      AND status NOT IN ('CANCELLED', 'RETURNED')
                """), prev_params).scalar() or 0

                for row in conn.execute(_YM_RETURNS_SQL, params).mappings().all():
                    sku = row["sku"]
                    d   = int(row["defects"] or 0)
                    lv  = float(row["direct_loss"] or 0)
                    total_defects += d
                    total_loss    += lv
                    sku_losses[sku]  = sku_losses.get(sku, 0)  + lv
                    sku_defects[sku] = sku_defects.get(sku, 0) + d

                for row in conn.execute(_YM_RETURNS_SQL, prev_params).mappings().all():
                    prev_total_loss    += float(row["direct_loss"] or 0)
                    prev_total_defects += int(row["defects"] or 0)

                ym_csat = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           COUNT(CASE WHEN valuation >= 4 THEN 1 END) AS positive
                    FROM ym_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                """), params).fetchone()
                total_fb    += int(ym_csat[0] or 0)
                positive_fb += int(ym_csat[1] or 0)

                ym_csat_prev = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           COUNT(CASE WHEN valuation >= 4 THEN 1 END) AS positive
                    FROM ym_feedbacks
                    WHERE created_date >= :start AND created_date <= :end
                """), prev_params).fetchone()
                prev_total_fb    += int(ym_csat_prev[0] or 0)
                prev_positive_fb += int(ym_csat_prev[1] or 0)

                ym_fin = conn.execute(_YM_FIN_SQL, params).fetchone()
                lost_revenue      += float(ym_fin[0] or 0)
                lost_profit       += float(ym_fin[1] or 0)
                ym_fin_prev = conn.execute(_YM_FIN_SQL, prev_params).fetchone()
                prev_lost_revenue += float(ym_fin_prev[0] or 0)
                prev_lost_profit  += float(ym_fin_prev[1] or 0)

        csat_pct      = round((positive_fb / total_fb) * 100, 1) if total_fb > 0 else 100.0
        prev_csat_pct = round((prev_positive_fb / prev_total_fb) * 100, 1) if prev_total_fb > 0 else None
        ppm           = round((total_defects / orders_count) * 100, 2) if orders_count > 0 else 0.0
        prev_ppm      = round((prev_total_defects / prev_orders_count) * 100, 2) if prev_orders_count > 0 else None

        top_sku = sorted(
            [{"sku": k, "loss": round(v), "defects": sku_defects.get(k, 0)} for k, v in sku_losses.items()],
            key=lambda x: x["loss"], reverse=True
        )[:5]

        def _delta_pct(curr, prev):
            if prev and prev > 0:
                return round((curr - prev) / prev * 100, 1)
            return None

        prev_label = f"{prev_start_dt.strftime('%-d.%-m')}–{prev_end_dt.strftime('%-d.%-m.%Y')}"

        return {
            "status": "success",
            "prev_period_label": prev_label,
            "metrics": {
                "total_loss":           round(total_loss),
                "total_loss_delta":     _delta_pct(total_loss, prev_total_loss),
                "ppm":                  ppm,
                "prev_ppm":             prev_ppm,
                "ppm_delta":            _delta_pct(ppm, prev_ppm) if prev_ppm is not None else None,
                "csat":                 csat_pct,
                "prev_csat":            prev_csat_pct,
                "csat_delta":           _delta_pct(csat_pct, prev_csat_pct) if prev_csat_pct is not None else None,
                "ai_processed":         ai_processed,
                "prev_ai_processed":    prev_ai_processed,
                "ai_processed_delta":   _delta_pct(ai_processed, prev_ai_processed) if prev_ai_processed > 0 else None,
                "orders_count":         orders_count,
                "feedbacks_count":      total_fb,
                "defects_count":        total_defects,
                "lost_revenue":         round(lost_revenue),
                "lost_profit":          round(lost_profit),
                "prev_lost_revenue":    round(prev_lost_revenue),
                "prev_lost_profit":     round(prev_lost_profit),
                "lost_revenue_delta":   _delta_pct(lost_revenue, prev_lost_revenue),
                "lost_profit_delta":    _delta_pct(lost_profit, prev_lost_profit),
            },
            "top_problem_sku": top_sku,
        }
    except Exception as e:
        print(f"Ошибка формирования главного дашборда: {e}")
        raise HTTPException(status_code=500, detail=str(e))
