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
    WITH log_costs AS (
        SELECT
            LOWER(srid) AS srid,
            COALESCE(SUM(delivery_rub), 0) + COALESCE(SUM(rebill_logistic_cost), 0) AS logistic_cost
        FROM wb_report_detail
        WHERE doc_type_name = '' AND rebill_logistic_cost > 0
        GROUP BY LOWER(srid)
    )
    SELECT
        TRIM(c.supplier_article) AS sku,
        COUNT(c.srid) AS defects,
        SUM(COALESCE(cog.cost_value, cog_latest.cost_value, 0)
            + COALESCE(lc.logistic_cost, 0)) AS direct_loss
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
    LEFT JOIN log_costs lc ON lc.srid = LOWER(c.srid)
    WHERE c.created_dt >= :start AND c.created_dt <= :end
      AND (LOWER(TRIM(c.status)) IN ('одобрено','2','2.0','да','true')
           OR LOWER(TRIM(c.status_ex)) IN ('одобрено','2','2.0','да','true'))
      AND c.supplier_article IS NOT NULL
      AND TRIM(c.supplier_article) NOT IN ('', 'Без артикула')
    GROUP BY TRIM(c.supplier_article)
""")

_WB_FIN_SQL = text("""
    WITH avg_prices AS (
        SELECT TRIM(supplier_article) AS sku, AVG(finish_price) AS avg_price
        FROM wb_logistics
        WHERE doc_type = 'SALE' AND finish_price IS NOT NULL AND finish_price > 0
        GROUP BY TRIM(supplier_article)
    ),
    cog_latest AS (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS sku, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    )
    SELECT
        COALESCE(SUM(ap.avg_price) FILTER (WHERE ap.avg_price IS NOT NULL), 0) AS lost_revenue,
        COALESCE(SUM(
            CASE WHEN ap.avg_price IS NOT NULL
                      AND COALESCE(cog_exact.cost_value, cl.cost_value) IS NOT NULL
                      AND ap.avg_price > COALESCE(cog_exact.cost_value, cl.cost_value)
                 THEN ap.avg_price - COALESCE(cog_exact.cost_value, cl.cost_value) END
        ), 0) AS lost_profit
    FROM wb_claims c
    LEFT JOIN wb_logistics l ON l.srid = c.srid
    LEFT JOIN (
        SELECT DISTINCT ON (supply_id, supplier_article)
            supply_id, supplier_article, invoice_num
        FROM wb_invoices
    ) inv
        ON SPLIT_PART(l.income_id::text, '.', 1) = SPLIT_PART(inv.supply_id::text, '.', 1)
       AND TRIM(c.supplier_article) = TRIM(inv.supplier_article)
    LEFT JOIN wb_cogs cog_exact
        ON TRIM(cog_exact.supplier_article) = TRIM(c.supplier_article)
       AND TRIM(cog_exact.invoice_num) = TRIM(COALESCE(inv.invoice_num, ''))
       AND cog_exact.cost_value > 0
    LEFT JOIN cog_latest cl ON cl.sku = TRIM(c.supplier_article)
    LEFT JOIN avg_prices ap ON ap.sku = TRIM(c.supplier_article)
    WHERE c.created_dt BETWEEN :start AND :end
      AND (LOWER(TRIM(c.status)) IN ('одобрено','2','2.0','да','true')
           OR LOWER(TRIM(c.status_ex)) IN ('одобрено','2','2.0','да','true'))
      AND c.supplier_article IS NOT NULL
      AND TRIM(c.supplier_article) NOT IN ('', 'Без артикула')
""")

_YM_RETURNS_SQL = text("""
    SELECT
        r.supplier_article AS sku,
        COUNT(*) AS defects,
        SUM(COALESCE(wc.cost_value, 0)) AS direct_loss
    FROM ym_returns r
    LEFT JOIN (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS supplier_article, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    ) wc ON wc.supplier_article = TRIM(r.supplier_article)
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
        total_defects = 0        # для расчёта потерь (только одобренные WB)
        total_defects_ppm = 0    # для PPM-метрики (как PPM-раздел: cat_* = true)
        total_fb      = 0
        positive_fb   = 0
        ai_processed  = 0
        sku_losses: dict  = {}
        sku_defects: dict = {}

        prev_orders_count = 0
        prev_total_defects = 0
        prev_total_defects_ppm = 0
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

                # PPM-метрика по методу PPM-раздела: cat_* = true.
                # Обычные отказы не считаем; отказы из‑за доотправки детали — считаем
                # (брак закрыт внутренним инструментом, но не исчез).
                _cat_filter = "(cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)"
                _not_plain_refusal = """(
                    LOWER(TRIM(COALESCE(status::text, ''))) != 'отказ'
                    OR COALESCE(status_ex, '') ILIKE '%доотправк%'
                    OR EXISTS (
                        SELECT 1 FROM reshipment_requests r
                        WHERE r.matched_srid IS NOT NULL AND TRIM(r.matched_srid) <> ''
                          AND LOWER(TRIM(r.matched_srid)) = LOWER(TRIM(wb_claims.srid))
                    )
                )"""
                total_defects_ppm += conn.execute(text(f"""
                    SELECT COUNT(*) FROM wb_claims
                    WHERE created_dt >= :start AND created_dt <= :end
                      AND {_not_plain_refusal}
                      AND {_cat_filter}
                """), params).scalar() or 0
                prev_total_defects_ppm += conn.execute(text(f"""
                    SELECT COUNT(*) FROM wb_claims
                    WHERE created_dt >= :start AND created_dt <= :end
                      AND {_not_plain_refusal}
                      AND {_cat_filter}
                """), prev_params).scalar() or 0

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
        ppm           = round((total_defects_ppm / orders_count) * 100, 2) if orders_count > 0 else 0.0
        prev_ppm      = round((prev_total_defects_ppm / prev_orders_count) * 100, 2) if prev_orders_count > 0 else None

        top_sku = sorted(
            [{"sku": k, "loss": round(v), "defects": sku_defects.get(k, 0)} for k, v in sku_losses.items()],
            key=lambda x: x["loss"], reverse=True
        )[:5]

        def _delta_pct(curr, prev):
            if prev and prev > 0:
                return round((curr - prev) / prev * 100, 1)
            return None

        prev_label = f"{prev_start_dt.strftime('%-d.%-m')}–{prev_end_dt.strftime('%-d.%-m.%Y')}"

        reshipment_funnel = {
            "sent": 0, "form_submitted": 0,
            "approved": 0,
            "approved_wb": 0, "approved_wb_cost": 0,
            "approved_yandex": 0, "approved_yandex_cost": 0,
            "shipped": 0, "delivered": 0,
            "total_cost": 0,
            "reviews_left": 0,
        }
        reshipment_economics = {
            "requests_with_data": 0,
            "total_ppvz": 0,
            "total_wb_return_costs": 0,
            "total_cog": 0,
            "total_delivery_cost": 0,
            "loss_if_return_v1": 0,
            "loss_if_return_v2": 0,
            "benefit_vs_v1": 0,
            "benefit_vs_v2": 0,
            "avg_benefit_vs_v1": 0,
            "avg_benefit_vs_v2": 0,
            "avg_return_delivery": 372,
            "prev_month": None,
        }
        # Формула фактической стоимости доставки (у Яндекс-заявок cdek_cost — лишь оценка-кандидат):
        # delivery_method='yandex' → shipping_cost (подтверждённая) или yandex_cost (оценка)
        # иначе (cdek или NULL/старые) → cdek_cost
        _delivery_cost_expr = """
            CASE WHEN delivery_method = 'yandex'
                 THEN COALESCE(shipping_cost, yandex_cost, 0)
                 ELSE COALESCE(cdek_cost, 0)
            END
        """
        try:
            with db.bind.connect() as conn:
                row = conn.execute(text(f"""
                    SELECT
                        COUNT(*) AS sent,
                        (SELECT COUNT(*) FROM reshipment_requests) AS form_submitted,
                        (SELECT COUNT(*) FROM reshipment_requests WHERE status NOT IN ('new', 'rejected')) AS approved,
                        (SELECT COUNT(*) FROM reshipment_requests WHERE status NOT IN ('new', 'rejected')
                            AND (delivery_method = 'cdek' OR delivery_method IS NULL)) AS approved_wb,
                        (SELECT COALESCE(SUM(COALESCE(cdek_cost, 0)), 0) FROM reshipment_requests
                            WHERE status NOT IN ('new', 'rejected')
                            AND (delivery_method = 'cdek' OR delivery_method IS NULL)) AS approved_wb_cost,
                        (SELECT COUNT(*) FROM reshipment_requests WHERE status NOT IN ('new', 'rejected')
                            AND delivery_method = 'yandex') AS approved_yandex,
                        (SELECT COALESCE(SUM(COALESCE(shipping_cost, yandex_cost, 0)), 0) FROM reshipment_requests
                            WHERE status NOT IN ('new', 'rejected')
                            AND delivery_method = 'yandex') AS approved_yandex_cost,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE status IN ('packed', 'ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')) AS shipped,
                        (SELECT COUNT(*) FROM reshipment_requests WHERE status = 'delivered') AS delivered,
                        (SELECT COALESCE(SUM({_delivery_cost_expr}), 0)
                            FROM reshipment_requests WHERE status NOT IN ('new', 'rejected')) AS total_cost,
                        (SELECT COUNT(DISTINCT request_id) FROM wb_feedback_snapshots
                            WHERE snapshot_type = 'after' AND request_id IS NOT NULL) AS reviews_left
                    FROM wb_chat_pending
                """)).fetchone()
                if row:
                    reshipment_funnel.update({
                        "sent":                int(row[0] or 0),
                        "form_submitted":      int(row[1] or 0),
                        "approved":            int(row[2] or 0),
                        "approved_wb":         int(row[3] or 0),
                        "approved_wb_cost":    round(float(row[4] or 0)),
                        "approved_yandex":     int(row[5] or 0),
                        "approved_yandex_cost": round(float(row[6] or 0)),
                        "shipped":             int(row[7] or 0),
                        "delivered":           int(row[8] or 0),
                        "total_cost":          round(float(row[9] or 0)),
                        "reviews_left":        int(row[10] or 0),
                    })

                # Средняя стоимость обратной доставки WB→ПВЗ (глобально)
                avg_ret_row = conn.execute(text("""
                    SELECT COALESCE(AVG(delivery_rub), 372.0) AS v
                    FROM wb_report_detail WHERE srid LIKE 'mp.%.r' AND delivery_rub > 0
                """)).fetchone()
                avg_ret_global = float(avg_ret_row[0] or 372.0)

                def _eco_query(month_filter=""):
                    """Агрегатная экономика по одобренным заявкам с фильтром по дате."""
                    return conn.execute(text(f"""
                        WITH approved AS (
                            SELECT r.id, r.matched_srid, r.delivery_method,
                                   {_delivery_cost_expr} AS delivery_cost
                            FROM reshipment_requests r
                            WHERE r.status NOT IN ('new', 'rejected')
                            {month_filter}
                        ),
                        sale_data AS (
                            SELECT DISTINCT ON (a.id) a.id,
                                   COALESCE(d.ppvz_for_pay, 0) AS ppvz,
                                   COALESCE(d.retail_amount, 0) AS retail
                            FROM approved a
                            JOIN wb_report_detail d
                              ON LOWER(d.srid) = LOWER(a.matched_srid)
                             AND d.doc_type_name = 'Продажа'
                            WHERE a.matched_srid IS NOT NULL
                            ORDER BY a.id
                        ),
                        return_data AS (
                            SELECT DISTINCT ON (a.id) a.id,
                                   COALESCE(d.acquiring_fee, 0) AS acq_return
                            FROM approved a
                            JOIN wb_report_detail d
                              ON LOWER(d.srid) = LOWER(a.matched_srid)
                             AND d.doc_type_name = 'Возврат'
                            WHERE a.matched_srid IS NOT NULL
                            ORDER BY a.id
                        ),
                        empty_data AS (
                            SELECT a.id,
                                   COALESCE(SUM(d.rebill_logistic_cost), 0) AS rebill,
                                   COALESCE(SUM(d.penalty), 0)              AS penalty
                            FROM approved a
                            JOIN wb_report_detail d
                              ON LOWER(d.srid) = LOWER(a.matched_srid)
                             AND d.doc_type_name = ''
                            WHERE a.matched_srid IS NOT NULL
                            GROUP BY a.id
                        ),
                        sa_data AS (
                            SELECT DISTINCT ON (a.id) a.id, d.sa_name
                            FROM approved a
                            JOIN wb_report_detail d ON LOWER(d.srid) = LOWER(a.matched_srid)
                            WHERE a.matched_srid IS NOT NULL
                              AND d.sa_name IS NOT NULL AND d.sa_name != ''
                            ORDER BY a.id
                        ),
                        cog_data AS (
                            SELECT DISTINCT ON (s.id) s.id, wc.cost_value AS cog
                            FROM sa_data s
                            JOIN wb_cogs wc
                              ON LOWER(TRIM(wc.supplier_article)) = LOWER(TRIM(s.sa_name))
                             AND wc.cost_value > 0
                            ORDER BY s.id, wc.id DESC
                        ),
                        per_req AS (
                            SELECT
                                a.delivery_cost,
                                sd.ppvz,
                                CASE
                                    WHEN COALESCE(rd.acq_return, 0) > 0 THEN rd.acq_return
                                    WHEN sd.retail > 0 THEN ROUND(sd.retail * 0.04, 2)
                                    ELSE 0
                                END AS acq_return,
                                COALESCE(ed.rebill, 0)  AS rebill,
                                COALESCE(ed.penalty, 0) AS penalty,
                                COALESCE(cd.cog, 0)     AS cog
                            FROM approved a
                            LEFT JOIN sale_data sd  ON sd.id = a.id
                            LEFT JOIN return_data rd ON rd.id = a.id
                            LEFT JOIN empty_data ed  ON ed.id = a.id
                            LEFT JOIN cog_data cd    ON cd.id = a.id
                            WHERE sd.ppvz IS NOT NULL
                        )
                        SELECT
                            COUNT(*)                                              AS requests_with_data,
                            COALESCE(SUM(ppvz), 0)                               AS total_ppvz,
                            COALESCE(SUM(acq_return + rebill + penalty), 0)      AS total_wb_return_costs,
                            COALESCE(SUM(cog), 0)                                AS total_cog,
                            COALESCE(SUM(delivery_cost), 0)                      AS total_delivery_cost,
                            COALESCE(SUM(cog + acq_return + rebill + penalty), 0) AS loss_if_return_v1
                        FROM per_req
                    """)).fetchone()

                def _parse_eco(row, avg_ret):
                    if not row:
                        return None
                    n   = int(row[0] or 0)
                    if n == 0:
                        return None
                    ppvz  = round(float(row[1] or 0))
                    wb_rc = round(float(row[2] or 0))
                    cog   = round(float(row[3] or 0))
                    deliv = round(float(row[4] or 0))
                    lv1   = round(float(row[5] or 0))     # = cog + wb_rc
                    ret_l = round(avg_ret * n)             # обратная логистика
                    lv2   = lv1 + ret_l                   # = cog + wb_rc + ret_logistics
                    bv1   = round(ppvz + wb_rc - deliv)   # COG взаимно отменяется в V1 vs V3
                    bv2   = round(ppvz + wb_rc + ret_l - deliv)
                    net_v3 = ppvz - cog - deliv           # реальный результат V3 (будет <0, но лучше V1/V2)
                    return {
                        "requests_with_data":    n,
                        "total_ppvz":            ppvz,
                        "total_wb_return_costs": wb_rc,
                        "total_cog":             cog,
                        "total_delivery_cost":   deliv,
                        "ret_logistics":         ret_l,
                        "loss_if_return_v1":     lv1,
                        "loss_if_return_v2":     lv2,
                        "net_v3":                net_v3,
                        "benefit_vs_v1":         bv1,
                        "benefit_vs_v2":         bv2,
                        "avg_benefit_vs_v1":     round(bv1 / n) if n else 0,
                        "avg_benefit_vs_v2":     round(bv2 / n) if n else 0,
                        "avg_return_delivery":   round(avg_ret),
                    }

                eco_all  = _parse_eco(_eco_query(), avg_ret_global)
                eco_prev = _parse_eco(
                    _eco_query("AND DATE_TRUNC('month', r.created_at) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')"),
                    avg_ret_global
                )
                if eco_all:
                    reshipment_economics.update(eco_all)
                if eco_prev:
                    reshipment_economics["prev_month"] = eco_prev

                # Все возвраты прошлого месяца из финотчёта WB (независимо от модуля доотправок)
                try:
                    pmr_row = conn.execute(text("""
                        WITH prev_ret AS (
                            SELECT srid, acquiring_fee, retail_amount
                            FROM wb_report_detail
                            WHERE doc_type_name = 'Возврат'
                            AND DATE_TRUNC('month', rr_dt) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                        ),
                        prev_logistic AS (
                            SELECT COALESCE(SUM(delivery_rub), 0) AS total_del
                            FROM wb_report_detail
                            WHERE srid LIKE 'mp.%.r' AND doc_type_name = '' AND delivery_rub > 0
                            AND DATE_TRUNC('month', rr_dt) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                        )
                        SELECT
                            COUNT(r.srid)               AS returns_count,
                            ROUND(SUM(r.acquiring_fee)) AS total_acquiring,
                            ROUND(SUM(r.retail_amount)) AS total_retail,
                            pl.total_del                AS ret_logistics_from_report
                        FROM prev_ret r
                        CROSS JOIN prev_logistic pl
                        GROUP BY pl.total_del
                    """)).fetchone()
                    if pmr_row:
                        ret_cnt = int(pmr_row[0] or 0)
                        acq     = round(float(pmr_row[1] or 0))
                        retail  = round(float(pmr_row[2] or 0))
                        ret_log_report = round(float(pmr_row[3] or 0))
                        ret_log_est = round(avg_ret_global * ret_cnt)
                        reshipment_economics["prev_month_all_returns"] = {
                            "returns_count":            ret_cnt,
                            "total_acquiring":          acq,
                            "total_retail_reversed":    retail,
                            "ret_logistics_from_report": ret_log_report,
                            "ret_logistics_estimated":  ret_log_est,
                            "total_loss_estimated":     acq + ret_log_est,
                            "avg_return_delivery":      round(avg_ret_global),
                        }
                except Exception:
                    pass
        except Exception:
            pass

        def _ppm_period_stats(conn, date_filter=""):
            where = f"WHERE status != 'Аннулирован' {date_filter}"
            row = conn.execute(text(f"""
                SELECT
                    COUNT(*) FILTER (WHERE send_date IS NOT NULL AND send_status != 'Не отправлен') AS total_sent,
                    COUNT(*) FILTER (WHERE factory_reply IS NOT NULL AND factory_reply != '') AS got_reply,
                    COUNT(*) FILTER (WHERE correction_invoice IS NOT NULL AND correction_invoice != '') AS got_correction_invoice,
                    COUNT(*) FILTER (WHERE estimated_improvement_date IS NOT NULL) AS got_improvement_date
                FROM cx_claim_logs {where}
            """)).fetchone()
            if not row:
                return {"total_sent": 0, "got_reply": 0, "got_correction_invoice": 0, "got_improvement_date": 0}
            total = int(row[0] or 0)
            reply = int(row[1] or 0)
            inv   = int(row[2] or 0)
            date_ = int(row[3] or 0)
            return {
                "total_sent": total,
                "got_reply": reply,
                "got_reply_pct": round(reply / total * 100) if total else 0,
                "got_correction_invoice": inv,
                "got_correction_invoice_pct": round(inv / total * 100) if total else 0,
                "got_improvement_date": date_,
                "got_improvement_date_pct": round(date_ / total * 100) if total else 0,
            }

        ppm_realization = {"all": {}, "current_month": {}, "prev_month": {}}
        try:
            with db.bind.connect() as conn:
                ppm_realization["all"] = _ppm_period_stats(conn)
                ppm_realization["current_month"] = _ppm_period_stats(
                    conn,
                    f"AND DATE_TRUNC('month', send_date) = DATE_TRUNC('month', CURRENT_DATE)"
                )
                ppm_realization["prev_month"] = _ppm_period_stats(
                    conn,
                    f"AND DATE_TRUNC('month', send_date) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')"
                )
        except Exception:
            pass

        return {
            "status": "success",
            "prev_period_label": prev_label,
            "reshipment_funnel": reshipment_funnel,
            "reshipment_economics": reshipment_economics,
            "ppm_realization": ppm_realization,
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
                "defects_count":        total_defects_ppm,
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
