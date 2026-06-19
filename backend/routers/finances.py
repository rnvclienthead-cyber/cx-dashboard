from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from ..database import get_db
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/finances",
    tags=["Finances Analytics"],
    dependencies=[Depends(get_current_user)]
)

@router.get("/loss-analytics")
def get_financial_loss_data(
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db),
):
    try:
        # 1. Себестоимость (COG) по инвойсам и последняя по артикулу
        cogs_rows = db.execute(text(
            "SELECT TRIM(supplier_article) AS sku, TRIM(invoice_num) AS invoice, cost_value "
            "FROM wb_cogs ORDER BY id ASC"
        )).mappings().all()

        exact_cogs = {}
        latest_cogs_map = {}
        for r in cogs_rows:
            sku, inv, val = r['sku'], r['invoice'], float(r['cost_value'] or 0)
            if val > 0:
                exact_cogs[(sku, inv)] = val
                latest_cogs_map[sku] = val

        # 2. Средняя розничная цена продажи по артикулу из реальных продаж
        #    (wb_logistics, doc_type=SALE, finish_price из WB API — заполняется синхронизатором)
        retail_price_rows = db.execute(text("""
            SELECT
                TRIM(supplier_article) AS sku,
                AVG(finish_price)      AS avg_price,
                COUNT(*)               AS sale_cnt
            FROM wb_logistics
            WHERE doc_type = 'SALE'
              AND finish_price IS NOT NULL AND finish_price > 0
            GROUP BY TRIM(supplier_article)
        """)).mappings().all()

        retail_price_map = {r['sku']: float(r['avg_price']) for r in retail_price_rows if r['avg_price']}

        # 3. Возвраты — JOIN через wb_logistics.srid вместо wb_claims.income_id (исправлено)
        claims_rows = db.execute(text("""
            SELECT
                c.srid,
                TRIM(c.supplier_article)  AS sku,
                c.status,
                c.status_ex,
                c.created_dt,
                COALESCE(inv.invoice_num, 'Не указан') AS invoice,
                COALESCE(pc.class_abc, 'C') AS abc_group,
                COALESCE(pc.class_xyz, '-') AS xyz_group
            FROM wb_claims c
            -- Исправленный JOIN через wb_logistics.srid (income_id в wb_claims не заполнен с июня)
            LEFT JOIN wb_logistics l ON l.srid = c.srid
            LEFT JOIN (
                SELECT DISTINCT ON (supply_id, supplier_article)
                    supply_id, supplier_article, invoice_num
                FROM wb_invoices
            ) inv
                ON SPLIT_PART(l.income_id::text, '.', 1) = SPLIT_PART(inv.supply_id::text, '.', 1)
               AND TRIM(c.supplier_article) = TRIM(inv.supplier_article)
            LEFT JOIN product_classification pc
                ON TRIM(c.supplier_article) = TRIM(pc.article)
            WHERE c.supplier_article IS NOT NULL
              AND TRIM(c.supplier_article) NOT IN ('', 'Без артикула')
        """)).mappings().all()

        result_data = []
        for r in claims_rows:
            sku, inv = r['sku'], r['invoice']
            latest_cost = latest_cogs_map.get(sku, 0.0)
            retail_price = retail_price_map.get(sku, 0.0)

            # Себестоимость: точная (по инвойсу) → приблизительная (последняя) → нет данных
            if (sku, inv) in exact_cogs:
                cost, ctype = exact_cogs[(sku, inv)], 'exact'
            elif sku in latest_cogs_map:
                cost, ctype = latest_cogs_map[sku], 'approximate'
            else:
                cost, ctype = 0.0, 'none'

            # Дата
            dt_val = r['created_dt']
            if isinstance(dt_val, datetime):
                dt_str = dt_val.strftime('%Y-%m-%d')
            elif isinstance(dt_val, str) and len(dt_val) >= 10:
                dt_str = dt_val[:10]
            else:
                dt_str = "2026-01-01"

            # Нормализация статуса
            raw_status = str(r['status'] or '').strip().lower()
            raw_ex    = str(r['status_ex'] or '').strip().lower()
            if raw_status in ['одобрено','2','2.0','да','true'] or raw_ex in ['одобрено','2','2.0','да','true']:
                status_mapped = 'Одобрено'
            elif raw_status in ['отказ','3','3.0','нет','false'] or raw_ex in ['отказ','3','3.0','нет','false']:
                status_mapped = 'Отказ'
            elif raw_status in ['на рассмотрении','1','1.0'] or raw_ex in ['на рассмотрении','1','1.0']:
                status_mapped = 'На рассмотрении'
            else:
                status_mapped = 'Неизвестно'

            result_data.append({
                "srid": r['srid'],
                "sku": sku,
                "status": status_mapped,
                "created_dt": dt_str,
                "invoice": inv,
                "abc_group": r['abc_group'],
                "xyz_group": r['xyz_group'],
                "cost": cost,
                "cost_type": ctype,
                "latest_cost": latest_cost,
                # Данные для расчёта недополученного дохода/прибыли
                "retail_price": retail_price,
                "has_retail_price": retail_price > 0,
            })

        if platform == "wb":
            return {"status": "success", "data": result_data}

        # ── YM возвраты ──────────────────────────────────────────────────────────
        # retail_price: ym_orders.buyer_total (факт. оплата покупателя) → ym_prices → 0
        # cost:         wb_cogs (точно) → 0
        ym_rows = db.execute(text("""
            SELECT
                r.return_id::text                      AS srid,
                TRIM(r.supplier_article)               AS sku,
                COALESCE(r.status_ru, 'Неизвестно')    AS status,
                r.created_at                           AS created_dt,
                COALESCE(o.buyer_total, p.price, 0)    AS retail_price,
                CASE
                    WHEN o.buyer_total IS NOT NULL AND o.buyer_total > 0 THEN 'exact'
                    WHEN p.price IS NOT NULL AND p.price > 0             THEN 'approximate'
                    ELSE 'none'
                END AS retail_type,
                COALESCE(wc.cost_value, 0)             AS cost_value,
                CASE WHEN wc.cost_value IS NOT NULL THEN 'exact' ELSE 'none' END AS cost_type,
                COALESCE(pc.class_abc, 'N/A')          AS abc_group,
                COALESCE(pc.class_xyz, '-')            AS xyz_group
            FROM ym_returns r
            LEFT JOIN ym_orders o
                ON o.order_id = r.order_id
               AND o.supplier_article = TRIM(r.supplier_article)
            LEFT JOIN ym_prices p ON p.supplier_article = TRIM(r.supplier_article)
            LEFT JOIN (
                SELECT DISTINCT ON (TRIM(supplier_article))
                    TRIM(supplier_article) AS supplier_article, cost_value
                FROM wb_cogs
                WHERE cost_value > 0
                ORDER BY TRIM(supplier_article), id DESC
            ) wc ON wc.supplier_article = TRIM(r.supplier_article)
            LEFT JOIN product_classification pc
                ON TRIM(r.supplier_article) = TRIM(pc.article)
            WHERE r.supplier_article IS NOT NULL
        """)).mappings().all()

        ym_result = []
        for r in ym_rows:
            dt_val = r["created_dt"]
            if isinstance(dt_val, datetime):
                dt_str = dt_val.strftime("%Y-%m-%d")
            elif isinstance(dt_val, str) and len(dt_val) >= 10:
                dt_str = dt_val[:10]
            else:
                dt_str = "2026-01-01"

            retail  = float(r["retail_price"] or 0)
            cost    = float(r["cost_value"]   or 0)
            ym_result.append({
                "srid":             r["srid"],
                "sku":              r["sku"],
                "status":           r["status"],
                "created_dt":       dt_str,
                "invoice":          "ЯМ-возврат",
                "abc_group":        r["abc_group"],
                "xyz_group":        r["xyz_group"],
                "cost":             cost,
                "cost_type":        r["cost_type"],
                "latest_cost":      cost,
                "retail_price":     retail,
                "has_retail_price": retail > 0,
                "platform":         "ym",
            })

        if platform == "ym":
            return {"status": "success", "data": ym_result}

        # platform == "all"
        return {"status": "success", "data": result_data + ym_result}

    except Exception as e:
        print(f"Ошибка в финансовом модуле: {e}")
        raise HTTPException(status_code=500, detail=str(e))
