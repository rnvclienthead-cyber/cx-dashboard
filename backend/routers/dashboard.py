from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
import calendar
from ..database import get_db
from ..ppm_defects import ppm_defect_sql, ym_not_plain_refusal_sql, ym_reship_refusal_sql
from ..wb_return_logistics import FALLBACK, fetch_global_fallback, sku_return_log_cte
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Executive Dashboard / Главная панель управления"],
    dependencies=[Depends(get_current_user)]
)

# Фактическая стоимость доставки в воронке и экономике.
# Яндекс/Ozon: подтверждённая цена заказа, иначе оценка калькулятора.
# СДЭК и старые заявки без перевозчика: факт СДЭК, иначе оценка.
RESHIPMENT_DELIVERY_COST_SQL = """
    CASE WHEN delivery_method = 'yandex'
         THEN COALESCE(shipping_cost, yandex_cost, 0)
         WHEN delivery_method = 'ozon'
         THEN COALESCE(ozon_cost_actual, shipping_cost, ozon_cost, 0)
         ELSE COALESCE(cdek_cost_actual, cdek_cost, 0)
    END
"""


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

# Отказ из инструмента «Отправка деталей»: автокомментарий или заявка с этим SRID.
_WB_RESHIP_REFUSAL_PRED = """(
    COALESCE(c.status_ex, '') ILIKE '%доотправк%'
    OR EXISTS (
        SELECT 1 FROM reshipment_requests r
        WHERE r.matched_srid IS NOT NULL AND TRIM(r.matched_srid) <> ''
          AND LOWER(TRIM(r.matched_srid)) = LOWER(TRIM(c.srid))
    )
)"""

_YM_RESHIP_REFUSAL_PRED = ym_reship_refusal_sql("r")


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


def _parse_reshipment_economics(row, avg_return_delivery: float):
    """Собирает итоговую экономику из SQL-агрегата по доотправкам."""
    if not row:
        return None
    n = int(row[0] or 0)
    if n == 0:
        return None

    stay_count   = int(row[1] or 0)
    return_count = int(row[2] or 0)
    ppvz         = round(float(row[3] or 0))
    saved_sales  = round(float(row[4] or 0))
    wb_rc        = round(float(row[5] or 0))
    cog          = round(float(row[6] or 0))
    delivery     = round(float(row[7] or 0))
    stay_cog     = round(float(row[8] or 0))
    return_cog   = round(float(row[9] or 0))
    return_log   = round(float(row[10] or 0))

    # Прежние два справочных сценария сохраняем без изменений. Они нужны для
    # сравнения крайних вариантов возврата со смешанным фактическим сценарием.
    ret_logistics = round(avg_return_delivery * n)
    loss_if_return_v1 = cog + wb_rc
    loss_if_return_v2 = loss_if_return_v1 + ret_logistics

    # Фактический сценарий возврата:
    # 2-СБ (кроме Full Black) остаётся у клиента; Full Black, BO, BS и
    # прочие SKU возвращаются на склад, поэтому их себестоимость не теряем.
    counterfactual_loss = stay_cog + wb_rc + return_log
    reshipment_result = ppvz - cog - delivery
    real_benefit = reshipment_result + counterfactual_loss
    # Справочные зелёные блоки показывают именно выгоду инструмента
    # относительно сценариев возврата:
    #   выгода = результат_с_доотправкой - результат_сценария_возврата
    # Варианты возврата сверху уже показаны как потери (loss_if_return_v*),
    # поэтому здесь добавляем их к фактическому результату доотправки.
    benefit_vs_v1 = reshipment_result + loss_if_return_v1
    benefit_vs_v2 = reshipment_result + loss_if_return_v2

    return {
        "requests_with_data":          n,
        "stay_with_client_count":     stay_count,
        "return_to_stock_count":      return_count,
        "total_ppvz":                  ppvz,
        "saved_sales_amount":          saved_sales,
        "total_wb_return_costs":       wb_rc,
        "total_cog":                   cog,
        "total_delivery_cost":         delivery,
        "stay_with_client_cog":        stay_cog,
        "return_to_stock_cog":         return_cog,
        "return_logistics":            return_log,
        "ret_logistics":               ret_logistics,
        "loss_if_return_v1":           loss_if_return_v1,
        "loss_if_return_v2":           loss_if_return_v2,
        "benefit_vs_v1":               benefit_vs_v1,
        "benefit_vs_v2":               benefit_vs_v2,
        "avg_benefit_vs_v1":           round(benefit_vs_v1 / n),
        "avg_benefit_vs_v2":           round(benefit_vs_v2 / n),
        "counterfactual_return_loss":  counterfactual_loss,
        "net_v3":                      reshipment_result,
        "real_benefit":                real_benefit,
        "avg_real_benefit":            round(real_benefit / n),
        "avg_return_delivery":         round(return_log / return_count) if return_count else round(avg_return_delivery),
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
            SELECT TRIM(r.supplier_article) AS sku,
                   {overall_expr},
                   {cat_sum_exprs}
            FROM ym_returns r
            WHERE r.created_at >= :start AND r.created_at <= :end
              AND {ym_not_plain_refusal_sql("r")}
              AND r.supplier_article IS NOT NULL
              AND TRIM(r.supplier_article) NOT IN ('', 'Без артикула')
            GROUP BY TRIM(r.supplier_article)
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
    WITH filtered_claims AS (
        SELECT srid, TRIM(supplier_article) AS supplier_article
        FROM wb_claims
        WHERE created_dt >= :start AND created_dt <= :end
          AND (LOWER(TRIM(status)) IN ('одобрено','2','2.0','да','true')
               OR LOWER(TRIM(status_ex)) IN ('одобрено','2','2.0','да','true'))
          AND supplier_article IS NOT NULL
          AND TRIM(supplier_article) NOT IN ('', 'Без артикула')
    )
    SELECT
        c.supplier_article AS sku,
        COUNT(c.srid) AS defects,
        SUM(COALESCE(cog.cost_value, cog_latest.cost_value, 0)
            + COALESCE(lc.logistic_cost, 0)) AS direct_loss
    FROM filtered_claims c
    LEFT JOIN wb_logistics l ON l.srid = c.srid
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(d.delivery_rub), 0) + COALESCE(SUM(d.rebill_logistic_cost), 0) AS logistic_cost
        FROM wb_report_detail d
        WHERE d.srid = c.srid
          AND d.doc_type_name = ''
          AND d.rebill_logistic_cost > 0
    ) lc ON true
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
    GROUP BY c.supplier_article
""")

_WB_FIN_SQL = text("""
    WITH avg_prices AS (
        SELECT TRIM(l.supplier_article) AS sku, AVG(l.finish_price) AS avg_price
        FROM wb_logistics l
        WHERE l.doc_type = 'SALE' AND l.finish_price IS NOT NULL AND l.finish_price > 0
          AND l.supplier_article IN (
              SELECT TRIM(supplier_article) FROM wb_claims
              WHERE created_dt BETWEEN :start AND :end
                AND (LOWER(TRIM(status)) IN ('одобрено','2','2.0','да','true')
                     OR LOWER(TRIM(status_ex)) IN ('одобрено','2','2.0','да','true'))
                AND supplier_article IS NOT NULL
                AND TRIM(supplier_article) NOT IN ('', 'Без артикула')
          )
        GROUP BY TRIM(l.supplier_article)
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

# Экономия по отказам — зеркало недополученного дохода:
# сумма розничных цен заявок со статусом «Отказ» (прибыль в API есть, на карточке не показываем).
_WB_SAVED_SQL = text(f"""
    WITH avg_prices AS (
        SELECT TRIM(l.supplier_article) AS sku, AVG(l.finish_price) AS avg_price
        FROM wb_logistics l
        WHERE l.doc_type = 'SALE' AND l.finish_price IS NOT NULL AND l.finish_price > 0
          AND l.supplier_article IN (
              SELECT TRIM(supplier_article) FROM wb_claims
              WHERE created_dt BETWEEN :start AND :end
                AND (LOWER(TRIM(status)) IN ('отказ','отклонено','1','1.0','3','3.0')
                     OR LOWER(TRIM(status_ex)) IN ('отказ','отклонено','1','1.0','3','3.0'))
                AND supplier_article IS NOT NULL
                AND TRIM(supplier_article) NOT IN ('', 'Без артикула')
          )
        GROUP BY TRIM(l.supplier_article)
    ),
    cog_latest AS (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS sku, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    )
    SELECT
        COUNT(*) AS cnt,
        COALESCE(SUM(ap.avg_price) FILTER (WHERE ap.avg_price IS NOT NULL), 0) AS saved_revenue,
        COALESCE(SUM(
            CASE WHEN ap.avg_price IS NOT NULL
                      AND COALESCE(cog_exact.cost_value, cl.cost_value) IS NOT NULL
                      AND ap.avg_price > COALESCE(cog_exact.cost_value, cl.cost_value)
                 THEN ap.avg_price - COALESCE(cog_exact.cost_value, cl.cost_value) END
        ), 0) AS saved_profit,
        COUNT(*) FILTER (WHERE {_WB_RESHIP_REFUSAL_PRED}) AS reship_cnt,
        COALESCE(SUM(ap.avg_price) FILTER (
            WHERE ap.avg_price IS NOT NULL AND {_WB_RESHIP_REFUSAL_PRED}
        ), 0) AS reship_revenue,
        COUNT(*) FILTER (WHERE NOT {_WB_RESHIP_REFUSAL_PRED}) AS other_cnt,
        COALESCE(SUM(ap.avg_price) FILTER (
            WHERE ap.avg_price IS NOT NULL AND NOT {_WB_RESHIP_REFUSAL_PRED}
        ), 0) AS other_revenue
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
      AND (LOWER(TRIM(c.status)) IN ('отказ','отклонено','1','1.0','3','3.0')
           OR LOWER(TRIM(c.status_ex)) IN ('отказ','отклонено','1','1.0','3','3.0'))
      AND c.supplier_article IS NOT NULL
      AND TRIM(c.supplier_article) NOT IN ('', 'Без артикула')
""")

_YM_SAVED_SQL = text(f"""
    WITH avg_costs AS (
        SELECT DISTINCT ON (TRIM(supplier_article))
            TRIM(supplier_article) AS sku, cost_value
        FROM wb_cogs WHERE cost_value > 0
        ORDER BY TRIM(supplier_article), id DESC
    )
    SELECT
        COUNT(*) AS cnt,
        COALESCE(SUM(o.buyer_total) FILTER (WHERE o.buyer_total > 0), 0) AS saved_revenue,
        COALESCE(SUM(
            CASE WHEN o.buyer_total > 0 AND ac.cost_value IS NOT NULL AND o.buyer_total > ac.cost_value
                 THEN o.buyer_total - ac.cost_value END
        ), 0) AS saved_profit,
        COUNT(*) FILTER (WHERE {_YM_RESHIP_REFUSAL_PRED}) AS reship_cnt,
        COALESCE(SUM(o.buyer_total) FILTER (
            WHERE o.buyer_total > 0 AND {_YM_RESHIP_REFUSAL_PRED}
        ), 0) AS reship_revenue,
        COUNT(*) FILTER (WHERE NOT {_YM_RESHIP_REFUSAL_PRED}) AS other_cnt,
        COALESCE(SUM(o.buyer_total) FILTER (
            WHERE o.buyer_total > 0 AND NOT {_YM_RESHIP_REFUSAL_PRED}
        ), 0) AS other_revenue
    FROM ym_returns r
    LEFT JOIN ym_orders o ON o.order_id = r.order_id
                         AND o.supplier_article = TRIM(r.supplier_article)
    LEFT JOIN avg_costs ac ON ac.sku = TRIM(r.supplier_article)
    WHERE r.created_at BETWEEN :start AND :end
      AND r.supplier_article IS NOT NULL
      AND (
            UPPER(TRIM(COALESCE(r.status, ''))) = 'REJECTED'
         OR LOWER(TRIM(COALESCE(r.status_ru, ''))) IN ('отказ', 'отклонено')
      )
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

        saved_revenue      = 0.0
        saved_profit       = 0.0
        saved_count        = 0
        saved_reship_count = 0
        saved_reship_revenue = 0.0
        saved_other_count  = 0
        saved_other_revenue = 0.0
        prev_saved_revenue = 0.0
        prev_saved_profit  = 0.0
        prev_saved_count   = 0

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

                # PPM-метрика по методу PPM-раздела (с августа 2026 — только теги акта).
                # Обычные отказы не считаем; отказы из‑за доотправки детали — считаем
                # (брак закрыт внутренним инструментом, но не исчез).
                _cat_filter = ppm_defect_sql("created_dt")
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

                wb_saved = conn.execute(_WB_SAVED_SQL, params).mappings().first()
                saved_count          += int(wb_saved["cnt"] or 0)
                saved_revenue        += float(wb_saved["saved_revenue"] or 0)
                saved_profit         += float(wb_saved["saved_profit"] or 0)
                saved_reship_count   += int(wb_saved["reship_cnt"] or 0)
                saved_reship_revenue += float(wb_saved["reship_revenue"] or 0)
                saved_other_count    += int(wb_saved["other_cnt"] or 0)
                saved_other_revenue  += float(wb_saved["other_revenue"] or 0)
                wb_saved_prev = conn.execute(_WB_SAVED_SQL, prev_params).mappings().first()
                prev_saved_count     += int(wb_saved_prev["cnt"] or 0)
                prev_saved_revenue   += float(wb_saved_prev["saved_revenue"] or 0)
                prev_saved_profit    += float(wb_saved_prev["saved_profit"] or 0)

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

                ym_saved = conn.execute(_YM_SAVED_SQL, params).mappings().first()
                saved_count          += int(ym_saved["cnt"] or 0)
                saved_revenue        += float(ym_saved["saved_revenue"] or 0)
                saved_profit         += float(ym_saved["saved_profit"] or 0)
                saved_reship_count   += int(ym_saved["reship_cnt"] or 0)
                saved_reship_revenue += float(ym_saved["reship_revenue"] or 0)
                saved_other_count    += int(ym_saved["other_cnt"] or 0)
                saved_other_revenue  += float(ym_saved["other_revenue"] or 0)
                ym_saved_prev = conn.execute(_YM_SAVED_SQL, prev_params).mappings().first()
                prev_saved_count     += int(ym_saved_prev["cnt"] or 0)
                prev_saved_revenue   += float(ym_saved_prev["saved_revenue"] or 0)
                prev_saved_profit    += float(ym_saved_prev["saved_profit"] or 0)

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
            "approved_ozon": 0, "approved_ozon_cost": 0,
            "approved_wb": 0, "approved_wb_cost": 0,
            "approved_yandex": 0, "approved_yandex_cost": 0,
            "shipped": 0, "returns_prevented": 0, "delivered": 0,
            "total_cost": 0,
            "reviews_left": 0,
        }
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
                              AND delivery_method = 'ozon') AS approved_ozon,
                        (SELECT COALESCE(SUM(COALESCE(ozon_cost_actual, shipping_cost, ozon_cost, 0)), 0) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND delivery_method = 'ozon') AS approved_ozon_cost,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status NOT IN ('new', 'rejected')
                              AND (delivery_method = 'cdek' OR delivery_method IS NULL)) AS approved_wb,
                        (SELECT COALESCE(SUM(COALESCE(cdek_cost_actual, cdek_cost, 0)), 0) FROM reshipment_requests
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
                              AND status IN ('ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')) AS shipped,
                        (SELECT COUNT(DISTINCT LOWER(TRIM(r.matched_srid)))
                            FROM reshipment_requests r
                            JOIN wb_claims c
                              ON LOWER(TRIM(c.srid)) = LOWER(TRIM(r.matched_srid))
                            WHERE r.created_at >= :start AND r.created_at <= :end
                              AND r.status IN ('ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')
                              AND r.matched_srid IS NOT NULL AND TRIM(r.matched_srid) != ''
                              AND LOWER(TRIM(COALESCE(c.status::text, ''))) IN ('отказ', 'отклонено', '1', '3')) AS returns_prevented,
                        (SELECT COUNT(*) FROM reshipment_requests
                            WHERE created_at >= :start AND created_at <= :end
                              AND status = 'delivered') AS delivered,
                        (SELECT COALESCE(SUM({RESHIPMENT_DELIVERY_COST_SQL}), 0)
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
                        "sent":                 int(row[0] or 0),
                        "form_submitted":       int(row[1] or 0),
                        "approved":             int(row[2] or 0),
                        "approved_ozon":        int(row[3] or 0),
                        "approved_ozon_cost":   round(float(row[4] or 0)),
                        "approved_wb":          int(row[5] or 0),
                        "approved_wb_cost":     round(float(row[6] or 0)),
                        "approved_yandex":      int(row[7] or 0),
                        "approved_yandex_cost": round(float(row[8] or 0)),
                        "shipped":              int(row[9] or 0),
                        "returns_prevented":    int(row[10] or 0),
                        "delivered":            int(row[11] or 0),
                        "total_cost":           round(float(row[12] or 0)),
                        "reviews_left":         int(row[13] or 0),
                    })
        except Exception as e:
            print(f"Ошибка воронки доотправок: {e}")

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
                "saved_revenue":        round(saved_revenue),
                "saved_profit":         round(saved_profit),
                "saved_count":          saved_count,
                "saved_reship_count":   saved_reship_count,
                "saved_reship_revenue": round(saved_reship_revenue),
                "saved_other_count":    saved_other_count,
                "saved_other_revenue":  round(saved_other_revenue),
                "prev_saved_revenue":   round(prev_saved_revenue),
                "prev_saved_profit":    round(prev_saved_profit),
                "prev_saved_count":     prev_saved_count,
                "saved_revenue_delta":  _delta_pct(saved_revenue, prev_saved_revenue),
                "saved_profit_delta":   _delta_pct(saved_profit, prev_saved_profit),
            },
            "top_problem_sku": top_sku,
        }
    except Exception as e:
        print(f"Ошибка формирования главного дашборда: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _period_bounds(start_date: str, end_date: str) -> dict:
    start_dt = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
    return {"start": start_dt, "end": end_dt}


def _empty_reshipment_economics() -> dict:
    return {
        "requests_with_data": 0,
        "stay_with_client_count": 0,
        "return_to_stock_count": 0,
        "total_ppvz": 0,
        "saved_sales_amount": 0,
        "total_wb_return_costs": 0,
        "total_cog": 0,
        "total_delivery_cost": 0,
        "stay_with_client_cog": 0,
        "return_to_stock_cog": 0,
        "return_logistics": 0,
        "ret_logistics": 0,
        "loss_if_return_v1": 0,
        "loss_if_return_v2": 0,
        "benefit_vs_v1": 0,
        "benefit_vs_v2": 0,
        "avg_benefit_vs_v1": 0,
        "avg_benefit_vs_v2": 0,
        "counterfactual_return_loss": 0,
        "real_benefit": 0,
        "avg_real_benefit": 0,
        "net_v3": 0,
        "avg_return_delivery": FALLBACK,
    }


def load_reshipment_economics(db, params: dict) -> dict:
    """Экономика доотправок — отдельно от сводки, чтобы главная открывалась быстрее."""
    empty = _empty_reshipment_economics()
    delivery_cost_expr = RESHIPMENT_DELIVERY_COST_SQL
    try:
        with db.bind.connect() as conn:
            avg_ret_global = fetch_global_fallback(conn)
            row = conn.execute(text(f"""
                WITH {sku_return_log_cte()},
                shipped_requests AS (
                    SELECT r.id, r.matched_srid, r.delivery_method,
                           r.product_category,
                           {delivery_cost_expr} AS delivery_cost
                    FROM reshipment_requests r
                    WHERE r.status IN ('ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')
                      AND r.created_at >= :start AND r.created_at <= :end
                ),
                sale_data AS (
                    SELECT DISTINCT ON (a.id) a.id,
                           COALESCE(d.ppvz_for_pay, 0) AS ppvz,
                           COALESCE(d.retail_amount, 0) AS retail,
                           d.sa_name
                    FROM shipped_requests a
                    JOIN wb_report_detail d
                      ON d.srid = a.matched_srid
                     AND d.doc_type_name = 'Продажа'
                    WHERE a.matched_srid IS NOT NULL
                    ORDER BY a.id, (COALESCE(d.ppvz_for_pay, 0) > 0) DESC, d.rrd_id DESC
                ),
                return_data AS (
                    SELECT DISTINCT ON (a.id) a.id,
                           COALESCE(d.acquiring_fee, 0) AS acq_return
                    FROM shipped_requests a
                    JOIN wb_report_detail d
                      ON d.srid = a.matched_srid
                     AND d.doc_type_name = 'Возврат'
                    WHERE a.matched_srid IS NOT NULL
                    ORDER BY a.id, (COALESCE(d.acquiring_fee, 0) > 0) DESC, d.rrd_id DESC
                ),
                empty_data AS (
                    SELECT a.id,
                           COALESCE(SUM(d.rebill_logistic_cost), 0) AS rebill,
                           COALESCE(SUM(d.penalty), 0)              AS penalty,
                           COALESCE(SUM(d.delivery_rub), 0)         AS delivery
                    FROM shipped_requests a
                    JOIN wb_report_detail d
                      ON d.srid = a.matched_srid
                     AND d.doc_type_name = ''
                    WHERE a.matched_srid IS NOT NULL
                    GROUP BY a.id
                ),
                sa_data AS (
                    SELECT a.id,
                           COALESCE(
                               NULLIF(TRIM(sd.sa_name), ''),
                               (SELECT l.supplier_article
                                  FROM wb_logistics l
                                 WHERE a.matched_srid IS NOT NULL
                                   AND l.srid = a.matched_srid
                                   AND l.supplier_article IS NOT NULL
                                   AND TRIM(l.supplier_article) != ''
                                 LIMIT 1),
                               a.product_category,
                               ''
                           ) AS sa_name
                    FROM shipped_requests a
                    LEFT JOIN sale_data sd ON sd.id = a.id
                ),
                article_data AS (
                    SELECT id, sa_name,
                           LOWER(REPLACE(REPLACE(REPLACE(TRIM(sa_name), '-', ''), '_', ''), ' ', '')) AS article_key
                    FROM sa_data
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
                        sd.retail,
                        CASE
                            WHEN COALESCE(rd.acq_return, 0) > 0 THEN rd.acq_return
                            WHEN sd.retail > 0 THEN ROUND(sd.retail * 0.04, 2)
                            ELSE 0
                        END AS acq_return,
                        COALESCE(ed.rebill, 0)  AS rebill,
                        COALESCE(ed.penalty, 0) AS penalty,
                        COALESCE(ed.delivery, 0) AS wb_delivery,
                        COALESCE(cd.cog, 0)     AS cog,
                        (
                            (ad.article_key LIKE '2сб%' OR ad.article_key LIKE '2sb%')
                            AND ad.article_key NOT LIKE '%fullblack%'
                        ) AS stays_with_client,
                        COALESCE(srl.avg_delivery, :avg_return_delivery) AS return_delivery
                    FROM shipped_requests a
                    LEFT JOIN sale_data sd  ON sd.id = a.id
                    LEFT JOIN return_data rd ON rd.id = a.id
                    LEFT JOIN empty_data ed  ON ed.id = a.id
                    LEFT JOIN article_data ad ON ad.id = a.id
                    LEFT JOIN cog_data cd    ON cd.id = a.id
                    LEFT JOIN sku_return_log srl ON srl.sku_l = LOWER(TRIM(ad.sa_name))
                    WHERE sd.ppvz IS NOT NULL
                )
                SELECT
                    COUNT(*) AS requests_with_data,
                    COUNT(*) FILTER (WHERE stays_with_client) AS stay_with_client_count,
                    COUNT(*) FILTER (WHERE NOT stays_with_client) AS return_to_stock_count,
                    COALESCE(SUM(ppvz), 0) AS total_ppvz,
                    COALESCE(SUM(retail), 0) AS saved_sales_amount,
                    COALESCE(SUM(acq_return + rebill + penalty + wb_delivery), 0) AS total_wb_return_costs,
                    COALESCE(SUM(cog), 0) AS total_cog,
                    COALESCE(SUM(delivery_cost), 0) AS total_delivery_cost,
                    COALESCE(SUM(cog) FILTER (WHERE stays_with_client), 0) AS stay_with_client_cog,
                    COALESCE(SUM(cog) FILTER (WHERE NOT stays_with_client), 0) AS return_to_stock_cog,
                    COALESCE(SUM(return_delivery) FILTER (WHERE NOT stays_with_client), 0) AS return_logistics
                FROM per_req
            """), {**params, "avg_return_delivery": avg_ret_global}).fetchone()
        parsed = _parse_reshipment_economics(row, avg_ret_global)
        return parsed or empty
    except Exception as e:
        print(f"Ошибка экономики доотправок: {e}")
        return empty


@router.get("/executive/sku-problems")
def get_executive_sku_problems(
    start_date: str, end_date: str,
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db),
):
    params = _period_bounds(start_date, end_date)
    by_type, overall = [], []
    try:
        with db.bind.connect() as conn:
            by_type, overall = _build_top_sku_by_problem(conn, params, platform)
    except Exception as e:
        print(f"Ошибка top_sku_by_problem: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "status": "success",
        "top_sku_by_problem": by_type,
        "sku_problem_index": overall,
    }


@router.get("/executive/reshipment-economics")
def get_executive_reshipment_economics(
    start_date: str, end_date: str,
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db),
):
    params = _period_bounds(start_date, end_date)
    return {
        "status": "success",
        "reshipment_economics": load_reshipment_economics(db, params),
    }
