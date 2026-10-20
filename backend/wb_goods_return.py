"""Отчёт WB «Возвраты товаров продавцу» — стикер/штрихкод с коробки → SRID.

Клиент на возврате ничего не клеит: на коробке остаются этикетки WB
(розовый стикер partA/partB, TRBX, сортировка). Розовый номер — stickerId
в GET /api/v1/analytics/goods-return, оттуда берём srid и приёмку на ПВЗ.
"""
from __future__ import annotations

from datetime import date, timedelta

import requests

from . import wb_client

ANALYTICS_URL = "https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return"
STICKERS_URL = "https://marketplace-api.wildberries.ru/api/v3/orders/stickers"
MAX_DAYS = 31
STICKER_BATCH = 100


def ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_goods_returns (
            srid TEXT PRIMARY KEY,
            shk_id BIGINT,
            sticker_id TEXT,
            barcode TEXT,
            nm_id BIGINT,
            order_id BIGINT,
            vendor_code TEXT,
            subject_name TEXT,
            status TEXT,
            reason TEXT,
            return_type TEXT,
            dst_office_id BIGINT,
            dst_office_address TEXT,
            order_dt DATE,
            ready_to_return_dt TIMESTAMP,
            completed_dt TIMESTAMP,
            expired_dt TIMESTAMP,
            is_status_active INT,
            sticker_barcode TEXT,
            last_sync TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("ALTER TABLE wb_goods_returns ADD COLUMN IF NOT EXISTS sticker_barcode TEXT"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_wb_goods_returns_sticker ON wb_goods_returns (sticker_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_wb_goods_returns_shk ON wb_goods_returns (shk_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_wb_goods_returns_barcode ON wb_goods_returns (barcode)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_wb_goods_returns_sticker_barcode ON wb_goods_returns (sticker_barcode)"))
    db.commit()


def parse_report(payload) -> list[dict]:
    if not payload:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("report") or payload.get("data") or payload.get("items") or []
    else:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        srid = str(raw.get("srid") or "").strip()
        if not srid:
            continue
        shk = raw.get("shkId") if "shkId" in raw else raw.get("shk_id")
        try:
            shk_id = int(shk) if shk not in (None, "") else None
        except (TypeError, ValueError):
            shk_id = None
        sticker = str(raw.get("stickerId") or raw.get("sticker_id") or "").strip() or None
        out.append({
            "srid": srid,
            "shk_id": shk_id,
            "sticker_id": sticker,
            "barcode": str(raw.get("barcode") or "").strip() or None,
            "nm_id": raw.get("nmId") if raw.get("nmId") is not None else raw.get("nm_id"),
            "order_id": raw.get("orderId") if raw.get("orderId") is not None else raw.get("order_id"),
            "vendor_code": str(raw.get("vendorCode") or raw.get("vendor_code") or "").strip() or None,
            "subject_name": str(raw.get("subjectName") or raw.get("subject_name") or "").strip() or None,
            "status": str(raw.get("status") or "").strip() or None,
            "reason": str(raw.get("reason") or "").strip() or None,
            "return_type": str(raw.get("returnType") or raw.get("return_type") or "").strip() or None,
            "dst_office_id": raw.get("dstOfficeId") if raw.get("dstOfficeId") is not None else raw.get("dst_office_id"),
            "dst_office_address": str(raw.get("dstOfficeAddress") or raw.get("dst_office_address") or "").strip() or None,
            "order_dt": raw.get("orderDt") or raw.get("order_dt"),
            "ready_to_return_dt": raw.get("readyToReturnDt") or raw.get("ready_to_return_dt"),
            "completed_dt": raw.get("completedDt") or raw.get("completed_dt"),
            "expired_dt": raw.get("expiredDt") or raw.get("expired_dt"),
            "is_status_active": raw.get("isStatusActive") if raw.get("isStatusActive") is not None else raw.get("is_status_active"),
        })
    return out


def fetch_report(date_from: str, date_to: str, *, timeout: int = 60) -> list[dict]:
    resp = requests.get(
        ANALYTICS_URL,
        headers=wb_client.auth_headers(),
        params={"dateFrom": date_from, "dateTo": date_to},
        timeout=timeout,
    )
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    return parse_report(resp.json())


def parse_stickers(payload) -> list[dict]:
    rows = (payload or {}).get("stickers") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        oid = raw.get("orderId") or raw.get("order_id") or raw.get("id")
        try:
            order_id = int(oid) if oid not in (None, "") else None
        except (TypeError, ValueError):
            order_id = None
        part_a = str(raw.get("partA") or raw.get("part_a") or "").strip()
        part_b = str(raw.get("partB") or raw.get("part_b") or "").strip()
        barcode = str(raw.get("barcode") or "").strip()
        if not barcode and not (part_a or part_b):
            continue
        out.append({
            "order_id": order_id,
            "sticker_barcode": barcode or None,
            "sticker_id": f"{part_a}{part_b}" if (part_a or part_b) else None,
        })
    return out


def fetch_stickers_meta(order_ids: list[int], *, timeout: int = 60) -> list[dict]:
    if not order_ids:
        return []
    params_try = [
        {"type": "svg", "width": 40, "height": 30},
        {"type": "png", "width": 40, "height": 30},
    ]
    last = None
    for params in params_try:
        resp = requests.post(
            STICKERS_URL,
            headers=wb_client.auth_headers(),
            json={"orders": [int(x) for x in order_ids[:STICKER_BATCH]]},
            params=params,
            timeout=timeout,
        )
        last = resp
        if resp.status_code != 400:
            break
    resp = last
    resp.raise_for_status()
    rows = parse_stickers(resp.json())
    requested = [int(x) for x in order_ids[:STICKER_BATCH]]
    if len(rows) == 1 and rows[0].get("order_id") is None and requested:
        rows[0]["order_id"] = requested[0]
    elif rows and requested and all(r.get("order_id") is None for r in rows) and len(rows) == len(requested):
        for row, oid in zip(rows, requested):
            row["order_id"] = oid
    return rows


def sync_sticker_barcodes(db, *, sleep_s: float = 0.35, limit: int = 0) -> dict:
    """Добирает QR стикера (*XXXX) по order_id — камера читает его, а не печатный номер."""
    import time
    from sqlalchemy import text

    ensure_table(db)
    sql = """
        SELECT DISTINCT order_id
        FROM wb_goods_returns
        WHERE order_id > 0
          AND (sticker_barcode IS NULL OR sticker_barcode = '')
        ORDER BY order_id
    """
    ids = [int(r[0]) for r in db.execute(text(sql)).fetchall()]
    if limit:
        ids = ids[: int(limit)]
    updated = 0
    errors = []
    for i in range(0, len(ids), STICKER_BATCH):
        chunk = ids[i:i + STICKER_BATCH]
        if i and sleep_s:
            time.sleep(sleep_s)
        try:
            rows = fetch_stickers_meta(chunk)
        except Exception as exc:
            errors.append(f"{chunk[0]}:{exc}")
            try:
                db.rollback()
            except Exception:
                pass
            continue
        for row in rows:
            if not row.get("sticker_barcode") and not row.get("sticker_id"):
                continue
            row.setdefault("order_id", None)
            result = db.execute(text("""
                UPDATE wb_goods_returns
                SET sticker_barcode = COALESCE(:sticker_barcode, sticker_barcode),
                    sticker_id = COALESCE(sticker_id, :sticker_id),
                    last_sync = NOW()
                WHERE (:order_id IS NOT NULL AND order_id = :order_id)
                   OR (:sticker_id IS NOT NULL AND sticker_id = :sticker_id)
            """), row)
            updated += result.rowcount or 0
        db.commit()
    return {"ok": not errors, "pending": len(ids), "updated": updated, "errors": errors[:5]}


def upsert_rows(db, rows: list[dict]) -> int:
    if not rows:
        return 0
    from sqlalchemy import text
    sql = text("""
        INSERT INTO wb_goods_returns (
            srid, shk_id, sticker_id, barcode, nm_id, order_id, vendor_code, subject_name,
            status, reason, return_type, dst_office_id, dst_office_address,
            order_dt, ready_to_return_dt, completed_dt, expired_dt, is_status_active, last_sync
        ) VALUES (
            :srid, :shk_id, :sticker_id, :barcode, :nm_id, :order_id, :vendor_code, :subject_name,
            :status, :reason, :return_type, :dst_office_id, :dst_office_address,
            :order_dt, :ready_to_return_dt, :completed_dt, :expired_dt, :is_status_active, NOW()
        )
        ON CONFLICT (srid) DO UPDATE SET
            shk_id = COALESCE(EXCLUDED.shk_id, wb_goods_returns.shk_id),
            sticker_id = COALESCE(EXCLUDED.sticker_id, wb_goods_returns.sticker_id),
            barcode = COALESCE(EXCLUDED.barcode, wb_goods_returns.barcode),
            nm_id = COALESCE(EXCLUDED.nm_id, wb_goods_returns.nm_id),
            order_id = COALESCE(EXCLUDED.order_id, wb_goods_returns.order_id),
            vendor_code = COALESCE(EXCLUDED.vendor_code, wb_goods_returns.vendor_code),
            subject_name = COALESCE(EXCLUDED.subject_name, wb_goods_returns.subject_name),
            status = COALESCE(EXCLUDED.status, wb_goods_returns.status),
            reason = COALESCE(EXCLUDED.reason, wb_goods_returns.reason),
            return_type = COALESCE(EXCLUDED.return_type, wb_goods_returns.return_type),
            dst_office_id = COALESCE(EXCLUDED.dst_office_id, wb_goods_returns.dst_office_id),
            dst_office_address = COALESCE(EXCLUDED.dst_office_address, wb_goods_returns.dst_office_address),
            order_dt = COALESCE(EXCLUDED.order_dt, wb_goods_returns.order_dt),
            ready_to_return_dt = COALESCE(EXCLUDED.ready_to_return_dt, wb_goods_returns.ready_to_return_dt),
            completed_dt = COALESCE(EXCLUDED.completed_dt, wb_goods_returns.completed_dt),
            expired_dt = COALESCE(EXCLUDED.expired_dt, wb_goods_returns.expired_dt),
            is_status_active = COALESCE(EXCLUDED.is_status_active, wb_goods_returns.is_status_active),
            last_sync = NOW()
    """)
    for row in rows:
        db.execute(sql, row)
    db.commit()
    return len(rows)


def _windows(days_back: int) -> list[tuple[str, str]]:
    end = date.today()
    start = end - timedelta(days=max(1, int(days_back)))
    windows = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_DAYS - 1), end)
        windows.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return windows


def sync_goods_returns(db, *, days_back: int = 31, sleep_s: float = 0) -> dict:
    """Забирает отчёт за days_back дней (кусками по 31)."""
    import time

    ensure_table(db)
    total = 0
    errors = []
    windows = _windows(days_back)
    for i, (date_from, date_to) in enumerate(windows):
        if i and sleep_s:
            time.sleep(sleep_s)
        try:
            rows = fetch_report(date_from, date_to)
            total += upsert_rows(db, rows)
        except Exception as exc:
            errors.append(f"{date_from}:{date_to}:{exc}")
            try:
                db.rollback()
            except Exception:
                pass
    stickers = {"ok": True, "pending": 0, "updated": 0, "errors": []}
    try:
        stickers = sync_sticker_barcodes(db, sleep_s=max(sleep_s, 0.35) if sleep_s else 0.35)
    except Exception as exc:
        stickers = {"ok": False, "errors": [str(exc)]}
    return {
        "ok": not errors and stickers.get("ok", True),
        "saved": total,
        "windows": len(windows),
        "errors": errors[:5],
        "stickers": stickers,
    }
