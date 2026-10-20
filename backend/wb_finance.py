"""Детальный финотчёт WB (sales reports).

С 15.07.2026 Wildberries отключил
GET statistics-api /api/v5/supplier/reportDetailByPeriod.
Живой метод: POST finance-api /api/finance/v1/sales-reports/detailed.

Поля camelCase, деньги — строки. В БД пишем прежние snake_case колонки.
"""
from __future__ import annotations

import requests

from . import wb_client

FINANCE_BASE = "https://finance-api.wildberries.ru"
SALES_REPORTS_DETAILED_URL = f"{FINANCE_BASE}/api/finance/v1/sales-reports/detailed"

# Колонки wb_report_detail ← поля v1 (и запасной ключ v5, если вдруг придёт).
_ROW_FIELDS = (
    ("rrd_id", ("rrdId", "rrd_id"), "int"),
    ("realizationreport_id", ("reportId", "realizationreport_id"), "raw"),
    ("date_from", ("dateFrom", "date_from"), "raw"),
    ("date_to", ("dateTo", "date_to"), "raw"),
    ("rr_dt", ("rrDate", "rr_dt"), "raw"),
    ("doc_type_name", ("docTypeName", "doc_type_name"), "str"),
    ("sa_name", ("vendorCode", "sa_name"), "str"),
    ("nm_id", ("nmId", "nm_id"), "raw"),
    ("barcode", ("sku", "barcode"), "str"),
    ("srid", ("srid",), "str"),
    ("quantity", ("quantity",), "int"),
    ("retail_price", ("retailPrice", "retail_price"), "money"),
    ("retail_amount", ("retailAmount", "retail_amount"), "money"),
    ("ppvz_for_pay", ("forPay", "ppvz_for_pay"), "money"),
    ("ppvz_sales_commission", ("ppvzSalesCommission", "ppvz_sales_commission"), "money"),
    ("delivery_rub", ("deliveryService", "delivery_rub"), "money"),
    ("storage_fee", ("paidStorage", "storage_fee"), "money"),
    ("penalty", ("penalty",), "money"),
    ("deduction", ("deduction",), "money"),
    ("acceptance", ("paidAcceptance", "acceptance"), "money"),
    ("rebill_logistic_cost", ("rebillLogisticCost", "rebill_logistic_cost"), "money"),
    ("acquiring_fee", ("acquiringFee", "acquiring_fee"), "money"),
    ("supplier_promo", ("sellerPromo", "supplier_promo"), "money"),
    ("additional_payment", ("additionalPayment", "additional_payment"), "money"),
    ("commission_percent", ("commissionPercent", "commission_percent"), "money"),
    ("uuid_promocode", ("uuidPromocode", "uuid_promocode"), "opt_str"),
    ("sale_price_promocode_discount_prc", ("salePricePromocodeDiscountPrc", "sale_price_promocode_discount_prc"), "money"),
    ("shk_id", ("shkId", "shk_id"), "int"),
    ("sticker_id", ("stickerId", "sticker_id"), "opt_str"),
    ("trbx_id", ("trbxId", "trbx_id"), "opt_str"),
    ("gi_id", ("giId", "gi_id"), "int"),
)


def parse_money(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pick(row: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def request_body(
    date_from: str,
    date_to: str,
    *,
    rrd_id: int = 0,
    limit: int = 10000,
    period: str = "weekly",
) -> dict:
    return {
        "dateFrom": date_from,
        "dateTo": date_to,
        "limit": int(limit),
        "rrdId": int(rrd_id),
        "period": period,
    }


def normalize_row(row: dict | None) -> dict | None:
    """Одна строка v1 → запись для wb_report_detail. Без rrd_id — пропускаем."""
    src = row or {}
    out: dict = {}
    for col, keys, kind in _ROW_FIELDS:
        raw = _pick(src, keys)
        if kind == "int":
            try:
                out[col] = int(raw or 0)
            except (TypeError, ValueError):
                out[col] = 0
        elif kind == "money":
            out[col] = parse_money(raw)
        elif kind == "str":
            out[col] = str(raw or "")
        elif kind == "opt_str":
            txt = str(raw or "").strip()
            out[col] = txt or None
        else:
            out[col] = raw
    if not out.get("rrd_id"):
        return None
    return out


def parse_rows(payload, status_code: int = 200) -> list[dict]:
    """204 / пустой ответ — конец пагинации. Иначе список строк отчёта."""
    if status_code == 204:
        return []
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("reports") or payload.get("items") or []
        if not isinstance(rows, list):
            rows = []
    else:
        return []
    return [mapped for row in rows if (mapped := normalize_row(row))]


def sales_reports_detailed_raw(
    *,
    date_from: str,
    date_to: str,
    rrd_id: int = 0,
    limit: int = 10000,
    period: str = "weekly",
    key: str | None = None,
    timeout: int = 120,
) -> requests.Response:
    """POST без raise_for_status: воркер сам разбирает 204/429."""
    return requests.post(
        SALES_REPORTS_DETAILED_URL,
        headers=wb_client.auth_headers(key),
        json=request_body(date_from, date_to, rrd_id=rrd_id, limit=limit, period=period),
        timeout=timeout,
    )
