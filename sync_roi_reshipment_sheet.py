"""Синхронизация фактической экономики доотправок с Google-таблицей ROI."""

import calendar
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread  # type: ignore
from sqlalchemy import create_engine, text  # type: ignore

from backend.routers.dashboard import RESHIPMENT_DELIVERY_COST_SQL, _parse_reshipment_economics
from backend.wb_return_logistics import fetch_global_fallback, sku_return_log_cte


DB_URL = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "/root/cx-dashboard/bot_api_key.json")
ROI_SPREADSHEET_ID = os.getenv(
    "ROI_SPREADSHEET_ID",
    "1_FeHs0Pr-9PgZd6LK_hMR0eg1HBAo9qDNkl1-cfkgxo",
)
ROI_WORKSHEET_NAME = os.getenv("ROI_WORKSHEET_NAME", "Лист1")

MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

ROW_LABELS = {
    "delivery": "Расходы на отправку деталей",
    "count": "Количество отправок",
    "avg_delivery": "Ср. стоимость отправки",
    "profit_before_delivery": "Плановая прибыль товара до доставки / отправку",
    "loss_per_shipment": "Плановая потеря при возврате / отправку",
    "benefit": "Выгода от отправки деталей:",
    "potential_result": "Потенциальный результат при возврате",
    "actual_result": "Фактический результат",
}

DELIVERY_COST_SQL = RESHIPMENT_DELIVERY_COST_SQL

ECONOMICS_SQL = f"""
    WITH {sku_return_log_cte()},
    shipped_requests AS (
        SELECT r.id, r.matched_srid, r.delivery_method, r.product_category,
               {DELIVERY_COST_SQL} AS delivery_cost
        FROM reshipment_requests r
        WHERE r.status IN ('ready_for_pickup', 'in_transit', 'shipped', 'delivered', 'returned')
          AND r.created_at >= :start AND r.created_at < :end
    ),
    sale_data AS (
        SELECT DISTINCT ON (a.id) a.id,
               COALESCE(d.ppvz_for_pay, 0) AS ppvz,
               COALESCE(d.retail_amount, 0) AS retail,
               d.sa_name
        FROM shipped_requests a
        JOIN wb_report_detail d
          ON d.srid = a.matched_srid AND d.doc_type_name = 'Продажа'
        WHERE a.matched_srid IS NOT NULL
        ORDER BY a.id, (COALESCE(d.ppvz_for_pay, 0) > 0) DESC, d.rrd_id DESC
    ),
    return_data AS (
        SELECT DISTINCT ON (a.id) a.id, COALESCE(d.acquiring_fee, 0) AS acq_return
        FROM shipped_requests a
        JOIN wb_report_detail d
          ON d.srid = a.matched_srid AND d.doc_type_name = 'Возврат'
        WHERE a.matched_srid IS NOT NULL
        ORDER BY a.id, (COALESCE(d.acquiring_fee, 0) > 0) DESC, d.rrd_id DESC
    ),
    empty_data AS (
        SELECT a.id,
               COALESCE(SUM(d.rebill_logistic_cost), 0) AS rebill,
               COALESCE(SUM(d.penalty), 0) AS penalty,
               COALESCE(SUM(d.delivery_rub), 0) AS delivery
        FROM shipped_requests a
        JOIN wb_report_detail d ON d.srid = a.matched_srid AND d.doc_type_name = ''
        WHERE a.matched_srid IS NOT NULL
        GROUP BY a.id
    ),
    sa_data AS (
        SELECT a.id,
               COALESCE(
                   NULLIF(TRIM(sd.sa_name), ''),
                   (SELECT l.supplier_article FROM wb_logistics l
                    WHERE a.matched_srid IS NOT NULL AND l.srid = a.matched_srid
                      AND l.supplier_article IS NOT NULL AND TRIM(l.supplier_article) != ''
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
        JOIN wb_cogs wc ON LOWER(TRIM(wc.supplier_article)) = LOWER(TRIM(s.sa_name))
                       AND wc.cost_value > 0
        ORDER BY s.id, wc.id DESC
    ),
    per_req AS (
        SELECT a.delivery_cost, sd.ppvz, sd.retail,
               CASE
                   WHEN COALESCE(rd.acq_return, 0) > 0 THEN rd.acq_return
                   WHEN sd.retail > 0 THEN ROUND(sd.retail * 0.04, 2)
                   ELSE 0
               END AS acq_return,
               COALESCE(ed.rebill, 0) AS rebill,
               COALESCE(ed.penalty, 0) AS penalty,
               COALESCE(ed.delivery, 0) AS wb_delivery,
               COALESCE(cd.cog, 0) AS cog,
               ((ad.article_key LIKE '2сб%' OR ad.article_key LIKE '2sb%')
                 AND ad.article_key NOT LIKE '%fullblack%') AS stays_with_client,
               COALESCE(srl.avg_delivery, :avg_return_delivery) AS return_delivery
        FROM shipped_requests a
        LEFT JOIN sale_data sd ON sd.id = a.id
        LEFT JOIN return_data rd ON rd.id = a.id
        LEFT JOIN empty_data ed ON ed.id = a.id
        LEFT JOIN article_data ad ON ad.id = a.id
        LEFT JOIN cog_data cd ON cd.id = a.id
        LEFT JOIN sku_return_log srl ON srl.sku_l = LOWER(TRIM(ad.sa_name))
        WHERE sd.ppvz IS NOT NULL
    )
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE stays_with_client),
           COUNT(*) FILTER (WHERE NOT stays_with_client),
           COALESCE(SUM(ppvz), 0), COALESCE(SUM(retail), 0),
           COALESCE(SUM(acq_return + rebill + penalty + wb_delivery), 0),
           COALESCE(SUM(cog), 0), COALESCE(SUM(delivery_cost), 0),
           COALESCE(SUM(cog) FILTER (WHERE stays_with_client), 0),
           COALESCE(SUM(cog) FILTER (WHERE NOT stays_with_client), 0),
           COALESCE(SUM(return_delivery) FILTER (WHERE NOT stays_with_client), 0)
    FROM per_req
"""


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    if month == 12:
        return start, datetime(year + 1, 1, 1)
    return start, datetime(year, month + 1, 1)


def _find_rows(worksheet) -> dict[str, int]:
    labels = worksheet.col_values(1)
    found = {}
    for key, label in ROW_LABELS.items():
        matches = [idx + 1 for idx, value in enumerate(labels) if value.strip() == label]
        if not matches:
            raise RuntimeError(f"В таблице не найдена строка «{label}»")
        found[key] = matches[-1] if key == "benefit" else matches[0]
    return found


def _fetch_month(conn, year: int, month: int, avg_return_delivery: float):
    start, end = _month_bounds(year, month)
    row = conn.execute(
        text(ECONOMICS_SQL),
        {"start": start, "end": end, "avg_return_delivery": avg_return_delivery},
    ).fetchone()
    return _parse_reshipment_economics(row, avg_return_delivery)


def sync_roi_reshipment_sheet(now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo("Europe/Moscow"))
    engine = create_engine(DB_URL)
    monthly = {}
    with engine.connect() as conn:
        avg_return_delivery = fetch_global_fallback(conn)
        for month in range(1, now.month + 1):
            monthly[month] = _fetch_month(conn, now.year, month, avg_return_delivery)

    worksheet = gspread.service_account(filename=GOOGLE_CREDS_PATH) \
        .open_by_key(ROI_SPREADSHEET_ID).worksheet(ROI_WORKSHEET_NAME)
    rows = _find_rows(worksheet)
    updates = []

    for month in range(1, now.month + 1):
        col = gspread.utils.rowcol_to_a1(1, month + 1)[:-1]
        economics = monthly[month]
        if economics:
            raw_values = {
                "delivery": economics["total_delivery_cost"],
                "count": economics["requests_with_data"],
                "potential_result": -economics["counterfactual_return_loss"],
                "actual_result": economics["net_v3"],
            }
            for key, value in raw_values.items():
                updates.append({"range": f"{col}{rows[key]}", "values": [[value]]})
            formulas = {
                "avg_delivery": f'=IFERROR(ROUND({col}{rows["delivery"]}/{col}{rows["count"]};0);"")',
                "profit_before_delivery": f'=IFERROR(ROUND(({col}{rows["delivery"]}+{col}{rows["actual_result"]})/{col}{rows["count"]};0);"")',
                "loss_per_shipment": f'=IFERROR(ROUND(ABS({col}{rows["potential_result"]}/{col}{rows["count"]});0);"")',
                "benefit": f'=IF(OR({col}{rows["count"]}="";{col}{rows["count"]}=0;{col}{rows["potential_result"]}="";{col}{rows["actual_result"]}="");"";{col}{rows["actual_result"]}-{col}{rows["potential_result"]})',
            }
            for key, formula in formulas.items():
                updates.append({"range": f"{col}{rows[key]}", "values": [[formula]]})
        else:
            for key in ROW_LABELS:
                updates.append({"range": f"{col}{rows[key]}", "values": [[""]]})

    worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    result = {
        "year": now.year,
        "months_updated": now.month,
        "current_month": MONTH_NAMES[now.month - 1],
        "current_month_economics": monthly[now.month],
    }
    print(f"✅ ROI Google Sheet обновлена: {now.year}, январь–{result['current_month']}")
    return result


if __name__ == "__main__":
    sync_roi_reshipment_sheet()
