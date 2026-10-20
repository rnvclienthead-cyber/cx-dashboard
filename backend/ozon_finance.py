"""Фактическая стоимость Ozon Доставки из Seller API (начисления по дням).

Номер постинга доотправки лежит в unit_number строки CONTAINER_FEES.
Это не продажи с витрины: у них другие номера и типы Logistic/LastMile.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ozon_client

logger = logging.getLogger("ozon_finance")

PAGES_PER_DAY = 40
LOOKBACK_DAYS = 21
# Пока посылка не выдана, в начислениях часто только страховка ~10 ₽.
# Это ещё не факт доставки — в отчёт такое не пишем.
MIN_ACTUAL_RUB = 20.0


def _unit_key(value) -> str:
    return str(value or "").strip().upper()


def _amount(value) -> float:
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return abs(float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def accrual_unit_totals(accruals: list) -> dict[str, float]:
    """Сумма |total_amount| по unit_number за пачку начислений."""
    out: dict[str, float] = {}
    for row in accruals or []:
        unit = _unit_key(row.get("unit_number"))
        if not unit:
            continue
        out[unit] = round(out.get(unit, 0.0) + _amount(row.get("total_amount")), 2)
    return out


def fetch_accruals_for_day(day: date) -> list[dict]:
    if not ozon_client.client_id() or not ozon_client.api_key():
        return []
    rows: list[dict] = []
    last = ""
    seen: set[str] = set()
    day_s = day.isoformat()
    for _ in range(PAGES_PER_DAY):
        body = {"date": day_s}
        if last:
            body["last_id"] = last
        data = ozon_client.post(
            "/v1/finance/accrual/by-day", body, timeout=60, idempotent=True,
        )
        chunk = data.get("accruals") or []
        rows.extend(chunk)
        new_last = str(data.get("last_id") or "")
        if not chunk or not new_last or new_last in seen:
            break
        seen.add(new_last)
        last = new_last
    return rows


def fetch_unit_totals(start: date, end: date) -> dict[str, float]:
    totals: dict[str, float] = {}
    day = start
    while day <= end:
        part = accrual_unit_totals(fetch_accruals_for_day(day))
        for unit, amount in part.items():
            totals[unit] = round(totals.get(unit, 0.0) + amount, 2)
        day += timedelta(days=1)
    return totals


def apply_unit_totals(db: Session, totals: dict[str, float]) -> int:
    """Пишет ozon_cost_actual заявкам, чей постинг/заказ есть в totals."""
    if not totals:
        return 0
    rows = db.execute(text("""
        SELECT id, ozon_posting_number, ozon_order_number, ozon_cost_actual
        FROM reshipment_requests
        WHERE delivery_method = 'ozon'
          AND (
            COALESCE(ozon_posting_number, '') <> ''
            OR COALESCE(ozon_order_number, '') <> ''
          )
    """)).mappings().all()
    updated = 0
    for row in rows:
        posting = _unit_key(row.get("ozon_posting_number"))
        order = _unit_key(row.get("ozon_order_number"))
        amount = totals.get(posting) or totals.get(order)
        if amount is None or amount < MIN_ACTUAL_RUB:
            continue
        prev = row.get("ozon_cost_actual")
        try:
            prev_n = float(prev) if prev is not None else None
        except (TypeError, ValueError):
            prev_n = None
        if prev_n is not None and abs(prev_n - amount) < 0.005:
            continue
        db.execute(text("""
            UPDATE reshipment_requests
            SET ozon_cost_actual = :amt, updated_at = NOW()
            WHERE id = :id
        """), {"amt": round(amount, 2), "id": row["id"]})
        updated += 1
    if updated:
        db.commit()
    return updated


def sync_reshipment_ozon_costs(
    db: Session,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict:
    """Подтягивает начисления Ozon и проставляет факт в заявки доотправки."""
    if not ozon_client.client_id() or not ozon_client.api_key():
        return {"updated": 0, "days": 0, "reason": "no_key"}
    today = date.today()
    if end is None:
        end = today
    if start is None:
        earliest = earliest_ozon_ship_date(db)
        start = earliest or (end - timedelta(days=LOOKBACK_DAYS))
        floor = end - timedelta(days=120)
        if start < floor:
            start = floor
    if start > end:
        start, end = end, start
    try:
        db.rollback()
    except Exception:
        pass
    totals = fetch_unit_totals(start, end)
    # Долгий запрос к Ozon держит сессию idle — Postgres рвёт SSL.
    apply_db = Session(db.get_bind())
    try:
        updated = apply_unit_totals(apply_db, totals)
    finally:
        apply_db.close()
    stats = {
        "updated": updated,
        "days": (end - start).days + 1,
        "units": len(totals),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    if updated:
        logger.info("ozon finance costs: %s", stats)
    return stats


def earliest_ozon_ship_date(db: Session) -> Optional[date]:
    raw = db.execute(text("""
        SELECT MIN(shipped_at)::date
        FROM reshipment_requests
        WHERE delivery_method = 'ozon' AND shipped_at IS NOT NULL
    """)).scalar()
    return raw
