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


_PROBLEM_CATEGORIES = {
    1: "Некомплект: Фурнитура",
    2: "Некомплект: Несущие детали",
    3: "Состояние упаковки",
    4: "Производственный дефект",
    5: "Механические повреждения",
    6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость",
    8: "Пересорт / Ошибка склада",
    9: "Качество материалов",
    10: "Габариты и Размер",
    11: "Несоответствие описанию",
    12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У",
}

_WB_NOT_PLAIN_REFUSAL = """(
    LOWER(TRIM(COALESCE(status::text, ''))) != 'отказ'
    OR COALESCE(status_ex, '') ILIKE '%доотправк%'
    OR EXISTS (
        SELECT 1 FROM reshipment_requests r
        WHERE r.matched_srid IS NOT NULL AND TRIM(r.matched_srid) <> ''
          AND LOWER(TRIM(r.matched_srid)) = LOWER(TRIM(wb_claims.srid))
    )
)"""


def _score_problem_sku(defects: int, orders: int, cost, rating) -> dict:
    """Частота на 1000 заказов + индекс проблемы 0–100 + зона."""
    rate = round(defects / orders * 1000, 1) if orders and orders > 0 else None

    if rate is None:
        f_norm = min(100.0, defects / 30 * 100)  # без заказов опираемся на объём
    else:
        f_norm = min(100.0, rate / 40 * 100)

    v_norm = min(100.0, defects / 30 * 100)

    if cost is not None and cost > 0:
        money_norm = min(100.0, (defects * float(cost)) / 50000 * 100)
    else:
        money_norm = v_norm

    if rating is None:
        r_norm = 25.0
    elif rating >= 4.7:
        r_norm = 0.0
    else:
        r_norm = max(0.0, (4.7 - float(rating)) / 3.7 * 100)

    index = int(round(0.40 * f_norm + 0.25 * v_norm + 0.25 * money_norm + 0.10 * r_norm))
    index = max(0, min(100, index))
    if index < 30:
        zone = "low"
    elif index <= 60:
        zone = "mid"
    else:
        zone = "high"

    return {
        "rate_per_1000": rate,
        "problem_index": index,
        "problem_zone": zone,
    }


def _enrich_skus(conn, params: dict, platform: str, sku_list: list) -> dict:
    """Рейтинг / заказы / себестоимость для списка артикулов."""
    enrich = {s: {"rating": None, "orders": 0, "cost": None} for s in sku_list}
    if not sku_list:
        return enrich

    sku_params = {f"sku_{i}": s for i, s in enumerate(sku_list)}
    sku_in = ", ".join(f":sku_{i}" for i in range(len(sku_list)))

    try:
        cogs_rows = conn.execute(text(f"""
            SELECT DISTINCT ON (TRIM(supplier_article))
                TRIM(supplier_article) AS sku, cost_value
            FROM wb_cogs
            WHERE cost_value > 0
              AND TRIM(supplier_article) IN ({sku_in})
            ORDER BY TRIM(supplier_article), id DESC
        """), sku_params).mappings().all()
        for r in cogs_rows:
            enrich[r["sku"]]["cost"] = round(float(r["cost_value"] or 0), 1)
    except Exception:
        pass

    if platform in ("wb", "all"):
        try:
            rating_rows = conn.execute(text(f"""
                SELECT TRIM(supplier_article) AS sku, average_rating
                FROM wb_ratings_totals
                WHERE TRIM(supplier_article) IN ({sku_in})
            """), sku_params).mappings().all()
            for r in rating_rows:
                if r["average_rating"] is not None:
                    enrich[r["sku"]]["rating"] = round(float(r["average_rating"]), 2)
        except Exception:
            pass

        try:
            order_rows = conn.execute(text(f"""
                SELECT TRIM(supplier_article) AS sku, COUNT(*) AS cnt
                FROM wb_orders
                WHERE dt >= :start AND dt <= :end AND cancel_dt IS NULL
                  AND TRIM(supplier_article) IN ({sku_in})
                GROUP BY TRIM(supplier_article)
            """), {**params, **sku_params}).mappings().all()
            for r in order_rows:
                enrich[r["sku"]]["orders"] = enrich[r["sku"]].get("orders", 0) + int(r["cnt"] or 0)
        except Exception:
            pass

    if platform in ("ym", "all"):
        try:
            ym_rating_rows = conn.execute(text(f"""
                SELECT TRIM(supplier_article) AS sku,
                       ROUND(AVG(valuation)::numeric, 2) AS average_rating
                FROM ym_feedbacks
                WHERE valuation IS NOT NULL
                  AND TRIM(supplier_article) IN ({sku_in})
                GROUP BY TRIM(supplier_article)
            """), sku_params).mappings().all()
            for r in ym_rating_rows:
                if enrich[r["sku"]]["rating"] is None and r["average_rating"] is not None:
                    enrich[r["sku"]]["rating"] = float(r["average_rating"])
        except Exception:
            pass

        try:
            ym_order_rows = conn.execute(text(f"""
                SELECT TRIM(supplier_article) AS sku, COUNT(*) AS cnt
                FROM ym_orders
                WHERE created_at >= :start AND created_at <= :end
                  AND status NOT IN ('CANCELLED', 'RETURNED')
                  AND TRIM(supplier_article) IN ({sku_in})
                GROUP BY TRIM(supplier_article)
            """), {**params, **sku_params}).mappings().all()
            for r in ym_order_rows:
                enrich[r["sku"]]["orders"] = enrich[r["sku"]].get("orders", 0) + int(r["cnt"] or 0)
        except Exception:
            pass

    return enrich


_CAT_ANY_FILTER = (
    "(cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 "
    "OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)"
)


def _build_top_sku_by_problem(conn, params: dict, platform: str) -> tuple[list, list]:
    """
    Возвращает (by_type, overall):
    - by_type: топ-5 SKU по каждому типу (без индекса)
    - overall: топ SKU по всем проблемам сразу с частотой и индексом

    Ускорение: обогащаем (рейтинг/заказы/COGS) только кандидатов топа,
    а не все артикулы с проблемами — иначе дашборд отвечает ~20с и фильтр периода «зависает».
    """
    cat_counts: dict[int, dict[str, int]] = {i: {} for i in range(1, 14)}
    overall_counts: dict[str, int] = {}

    cat_sum_exprs = ",\n".join(
        f"SUM(CASE WHEN cat_{i} THEN 1 ELSE 0 END) AS c{i}" for i in range(1, 14)
    )
    overall_expr = f"COUNT(*) FILTER (WHERE {_CAT_ANY_FILTER}) AS overall_defects"

    if platform in ("wb", "all"):
        rows = conn.execute(text(f"""
            SELECT TRIM(supplier_article) AS sku,
                   {overall_expr},
                   {cat_sum_exprs}
            FROM wb_claims
            WHERE created_dt >= :start AND created_dt <= :end
              AND {_WB_NOT_PLAIN_REFUSAL}
              AND supplier_article IS NOT NULL
              AND TRIM(supplier_article) NOT IN ('', 'Без артикула')
            GROUP BY TRIM(supplier_article)
        """), params).mappings().all()
        for row in rows:
            sku = row["sku"]
            ov = int(row["overall_defects"] or 0)
            if ov > 0:
                overall_counts[sku] = overall_counts.get(sku, 0) + ov
            for i in range(1, 14):
                cnt = int(row[f"c{i}"] or 0)
                if cnt > 0:
                    cat_counts[i][sku] = cat_counts[i].get(sku, 0) + cnt

    if platform in ("ym", "all"):
        rows = conn.execute(text(f"""
            SELECT TRIM(supplier_article) AS sku,
                   {overall_expr},
                   {cat_sum_exprs}
            FROM ym_returns
            WHERE created_at >= :start AND created_at <= :end
              AND supplier_article IS NOT NULL
              AND TRIM(supplier_article) NOT IN ('', 'Без артикула')
            GROUP BY TRIM(supplier_article)
        """), params).mappings().all()
        for row in rows:
            sku = row["sku"]
            ov = int(row["overall_defects"] or 0)
            if ov > 0:
                overall_counts[sku] = overall_counts.get(sku, 0) + ov
            for i in range(1, 14):
                cnt = int(row[f"c{i}"] or 0)
                if cnt > 0:
                    cat_counts[i][sku] = cat_counts[i].get(sku, 0) + cnt

    if not overall_counts and not any(cat_counts.values()):
        return [], []

    # Кандидаты для обогащения: топ-5 по каждому типу + топ-50 по всем проблемам
    enrich_skus: set[str] = set()
    by_type_ranked: dict[int, list] = {}
    for cat_id in range(1, 14):
        sku_map = cat_counts[cat_id]
        if not sku_map:
            continue
        ranked = sorted(sku_map.items(), key=lambda x: (-x[1], x[0]))[:5]
        by_type_ranked[cat_id] = ranked
        for sku, _ in ranked:
            enrich_skus.add(sku)

    overall_candidates = sorted(overall_counts.items(), key=lambda x: (-x[1], x[0]))[:50]
    for sku, _ in overall_candidates:
        enrich_skus.add(sku)

    enrich = _enrich_skus(conn, params, platform, sorted(enrich_skus))

    # --- По типам: топ-5 по числу проблем, без индекса ---
    by_type = []
    for cat_id, name in _PROBLEM_CATEGORIES.items():
        sku_map = cat_counts[cat_id]
        total = sum(sku_map.values())
        if total == 0:
            continue
        ranked = by_type_ranked.get(cat_id) or []
        skus = []
        for sku, cnt in ranked:
            e = enrich.get(sku, {})
            skus.append({
                "sku": sku,
                "defects": cnt,
                "share_pct": round(cnt / total * 100, 1) if total else 0,
                "rating": e.get("rating"),
                "orders": e.get("orders") or 0,
                "cost": e.get("cost"),
            })
        by_type.append({
            "cat_id": cat_id,
            "cat_name": name,
            "total": total,
            "skus": skus,
        })

    # --- Сводка по всем проблемам: частота + индекс ---
    top_cat_by_sku: dict[str, tuple[int, str, int]] = {}
    for cat_id, name in _PROBLEM_CATEGORIES.items():
        for sku, cnt in cat_counts[cat_id].items():
            if sku not in enrich_skus:
                continue
            prev = top_cat_by_sku.get(sku)
            if prev is None or cnt > prev[2]:
                top_cat_by_sku[sku] = (cat_id, name, cnt)

    overall_total = sum(overall_counts.values())
    overall = []
    for sku, cnt in overall_candidates:
        e = enrich.get(sku, {})
        orders = e.get("orders") or 0
        cost = e.get("cost")
        rating = e.get("rating")
        metrics = _score_problem_sku(cnt, orders, cost, rating)
        lead = top_cat_by_sku.get(sku)
        overall.append({
            "sku": sku,
            "defects": cnt,
            "share_pct": round(cnt / overall_total * 100, 1) if overall_total else 0,
            "rating": rating,
            "orders": orders,
            "cost": cost,
            "top_cat_id": lead[0] if lead else None,
            "top_cat_name": lead[1] if lead else None,
            **metrics,
        })

    overall.sort(key=lambda x: (-x["problem_index"], -x["defects"], x["sku"]))
    return by_type, overall[:20]


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
                # Воронка доотправок — строго в выбранном периоде дашборда
                row = conn.execute(text(f"""
                    SELECT
                        (SELECT COUNT(*) FROM wb_chat_pending
                            WHERE created_at >= :start AND created_at <= :end) AS sent,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end) AS form_submitted,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')) AS approved,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND (delivery_method = 'cdek' OR delivery_method IS NULL)) AS approved_wb,
                        (SELECT COALESCE(SUM(COALESCE(cdek_cost, 0)), 0) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND (delivery_method = 'cdek' OR delivery_method IS NULL)) AS approved_wb_cost,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND delivery_method = 'yandex') AS approved_yandex,
                        (SELECT COALESCE(SUM(COALESCE(shipping_cost, yandex_cost, 0)), 0) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND delivery_method = 'yandex') AS approved_yandex_cost,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status IN ('packed', 'ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')) AS shipped,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status = 'delivered') AS delivered,
                        (SELECT COALESCE(SUM({_delivery_cost_expr}), 0)
                            FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')) AS total_cost,
                        (SELECT COUNT(DISTINCT s.request_id)
                            FROM wb_feedback_snapshots s
                            JOIN reshipment_requests r ON r.id = s.request_id
                            WHERE s.snapshot_type = 'after'
                              AND s.request_id IS NOT NULL
                              AND r.created_at >= :start AND r.created_at <= :end) AS reviews_left
                """), params).fetchone()
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

                def _eco_query(date_filter=""):
                    """Агрегатная экономика по одобренным заявкам с фильтром по дате."""
                    return conn.execute(text(f"""
                        WITH approved AS (
                            SELECT r.id, r.matched_srid, r.delivery_method,
                                   {_delivery_cost_expr} AS delivery_cost
                            FROM reshipment_requests r
                            WHERE r.status NOT IN ('new', 'rejected')
                            {date_filter}
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
                    """), params).fetchone()

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

                eco_period = _parse_eco(
                    _eco_query("AND r.created_at >= :start AND r.created_at <= :end"),
                    avg_ret_global
                )
                if eco_period:
                    reshipment_economics.update(eco_period)
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

        top_sku_by_problem = []
        sku_problem_index = []
        try:
            with db.bind.connect() as conn:
                top_sku_by_problem, sku_problem_index = _build_top_sku_by_problem(conn, params, platform)
        except Exception as e:
            print(f"Ошибка top_sku_by_problem: {e}")

        return {
            "status": "success",
            "prev_period_label": prev_label,
            "reshipment_funnel": reshipment_funnel,
            "reshipment_economics": reshipment_economics,
            "ppm_realization": ppm_realization,
            "top_sku_by_problem": top_sku_by_problem,
            "sku_problem_index": sku_problem_index,
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
