"""Журнал приёмки на складе: каждое пробитие стикера — строка с товаром и временем."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

def is_sticker_qr(code: str) -> bool:
    s = (code or "").strip()
    if not s or "TRBX" in s.upper():
        return False
    if s[:1] in "*!":
        return True
    if 6 <= len(s) <= 12 and s.isalnum() and any(ch.isalpha() for ch in s):
        return True
    return False


def is_linear_sticker(code: str) -> bool:
    s = (code or "").strip()
    parts = [p for p in "".join(ch if ch.isdigit() or ch.isspace() else " " for ch in s).split() if p]
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        glued = parts[0] + parts[1]
    else:
        glued = "".join(ch for ch in s if ch.isdigit())
        if len(glued) < 7:
            glued = ""
    return glued.isdigit() and 7 <= len(glued) <= 14

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS warehouse_intake (
        id                SERIAL PRIMARY KEY,
        dedupe_key        TEXT UNIQUE NOT NULL,
        scan_code         TEXT NOT NULL,
        barcode           TEXT,
        marketplace       TEXT,
        supplier_article  TEXT,
        product_name      TEXT,
        nm_id             TEXT,
        srid              TEXT,
        claim_id          TEXT,
        status            TEXT,
        has_claim         BOOLEAN,
        match_by          TEXT,
        category          TEXT,
        categories        JSONB,
        product_photo     TEXT,
        payload           JSONB,
        found             BOOLEAN NOT NULL DEFAULT FALSE,
        received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_scan_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        scan_count        INTEGER NOT NULL DEFAULT 1
    )
"""

_PERIOD_FILTER = """
    received_at >= (
        (date_trunc('day', timezone('Europe/Moscow', now()))
         - make_interval(days => :span))
        AT TIME ZONE 'Europe/Moscow'
    )
"""


def _sql(sql: str):
    try:
        from sqlalchemy import text as sql_text
        return sql_text(sql)
    except ImportError:
        return sql


def ensure_table(db) -> None:
    global _table_ready
    if _table_ready:
        return
    db.execute(_sql(_CREATE_SQL))
    db.execute(_sql(
        "CREATE INDEX IF NOT EXISTS idx_warehouse_intake_received "
        "ON warehouse_intake (received_at DESC)"
    ))
    db.execute(_sql(
        "CREATE INDEX IF NOT EXISTS idx_warehouse_intake_article "
        "ON warehouse_intake (supplier_article)"
    ))
    _table_ready = True


def init_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text(_CREATE_SQL))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_warehouse_intake_received "
            "ON warehouse_intake (received_at DESC)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_warehouse_intake_article "
            "ON warehouse_intake (supplier_article)"
        ))
        conn.commit()
    global _table_ready
    _table_ready = True


def normalize_intake_code(raw: str) -> str:
    """Один стикер — один номер: 5690311 8348 и 56903118348 это одно и то же."""
    s = str(raw or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 7:
        return digits
    return ""


def make_dedupe_key(scan_code: str, item: dict | None) -> str:
    item = item or {}
    barcode = str(item.get("barcode") or "").strip()
    unit = normalize_intake_code(barcode) or normalize_intake_code(scan_code)
    if unit:
        return f"unit|{unit}"
    srid = str(item.get("srid") or "").strip()
    if srid:
        return f"srid|{srid.lower()}"
    return f"raw|{(scan_code or '').strip().lower()}"


def _find_existing(db, *, code: str, item: dict) -> dict | None:
    barcode = str(item.get("barcode") or "").strip()
    srid = str(item.get("srid") or "").strip()
    norm = normalize_intake_code(code) or normalize_intake_code(barcode)
    row = db.execute(
        _sql("""
            SELECT * FROM warehouse_intake
            WHERE
              (:code <> '' AND scan_code = :code)
              OR (:norm <> '' AND (
                    scan_code = :norm
                    OR regexp_replace(COALESCE(scan_code, ''), '\\D', '', 'g') = :norm
                    OR barcode = :norm
                    OR regexp_replace(COALESCE(barcode, ''), '\\D', '', 'g') = :norm
              ))
              OR (:barcode <> '' AND (barcode = :barcode OR scan_code = :barcode))
              OR (:srid <> '' AND LOWER(srid) = LOWER(:srid))
            ORDER BY received_at ASC
            LIMIT 1
        """),
        {"code": code, "norm": norm, "barcode": barcode, "srid": srid},
    ).mappings().first()
    return dict(row) if row else None


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _serialize(row: dict, *, duplicate: bool = False) -> dict:
    cats = row.get("categories")
    if isinstance(cats, str):
        try:
            cats = json.loads(cats)
        except Exception:
            cats = []
    if not isinstance(cats, list):
        cats = list(cats) if cats else []
    return {
        "id": row.get("id"),
        "scan_code": row.get("scan_code") or "",
        "barcode": row.get("barcode") or "",
        "marketplace": row.get("marketplace") or "",
        "supplier_article": row.get("supplier_article") or "",
        "product_name": row.get("product_name") or "",
        "nm_id": row.get("nm_id") or "",
        "srid": row.get("srid") or "",
        "claim_id": row.get("claim_id"),
        "status": row.get("status") or "",
        "has_claim": bool(row.get("has_claim")),
        "match_by": row.get("match_by") or "",
        "category": row.get("category") or "",
        "categories": cats,
        "product_photo": row.get("product_photo") or "",
        "found": bool(row.get("found")),
        "received_at": _iso(row.get("received_at")),
        "last_scan_at": _iso(row.get("last_scan_at")),
        "scan_count": int(row.get("scan_count") or 1),
        "duplicate": duplicate,
    }


def _enrich_article(db, article: str) -> dict:
    art = (article or "").strip()
    if not art:
        return {}
    try:
        row = db.execute(
            _sql("""
                SELECT supplier_article, name_ru, category_1
                FROM wb_assortment
                WHERE LOWER(TRIM(supplier_article)) = LOWER(TRIM(:art))
                LIMIT 1
            """),
            {"art": art},
        ).mappings().first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}
    return dict(row) if row else {}


def _claim_id_text(item: dict) -> str | None:
    raw = item.get("claim_id")
    if raw in (None, ""):
        return None
    return str(raw)


def record_intake(db, scan_code: str, item: dict | None = None) -> dict | None:
    """Пишет приёмку. Повтор того же стикера не создаёт вторую строку."""
    code = (scan_code or "").strip()
    if not code:
        return None
    if is_sticker_qr(code) or not is_linear_sticker(code):
        return None
    try:
        ensure_table(db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None

    item = dict(item or {})
    extra = _enrich_article(db, item.get("supplier_article") or "")
    if extra:
        item.setdefault("supplier_article", extra.get("supplier_article") or "")
        if not item.get("product_name"):
            item["product_name"] = extra.get("name_ru") or ""
        item.setdefault("category", extra.get("category_1") or "")

    found = bool(item)
    key = make_dedupe_key(code, item if found else None)
    cats = item.get("categories") if isinstance(item.get("categories"), list) else []
    params = {
        "dedupe_key": key,
        "scan_code": code,
        "barcode": str(item.get("barcode") or ""),
        "marketplace": str(item.get("marketplace") or ""),
        "supplier_article": str(item.get("supplier_article") or ""),
        "product_name": str(item.get("product_name") or ""),
        "nm_id": str(item.get("nm_id") or ""),
        "srid": str(item.get("srid") or ""),
        "claim_id": _claim_id_text(item),
        "status": str(item.get("status") or ""),
        "has_claim": bool(item.get("has_claim")) if item else False,
        "match_by": str(item.get("match_by") or ""),
        "category": str(item.get("category") or ""),
        "categories": json.dumps(cats, ensure_ascii=False),
        "product_photo": str(item.get("product_photo") or ""),
        "payload": json.dumps(item, ensure_ascii=False, default=str) if item else None,
        "found": found,
    }
    existing = None
    try:
        existing = _find_existing(db, code=code, item=item if found else {})
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    if existing:
        return _bump_existing(db, existing, params)

    inserted = db.execute(
        _sql("""
            INSERT INTO warehouse_intake (
                dedupe_key, scan_code, barcode, marketplace, supplier_article,
                product_name, nm_id, srid, claim_id, status, has_claim, match_by,
                category, categories, product_photo, payload, found
            ) VALUES (
                :dedupe_key, :scan_code, :barcode, :marketplace, :supplier_article,
                :product_name, :nm_id, :srid, :claim_id, :status, :has_claim, :match_by,
                :category, CAST(:categories AS JSONB), :product_photo,
                CAST(:payload AS JSONB), :found
            )
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING *
        """),
        params,
    ).mappings().first()
    if inserted:
        db.commit()
        return _serialize(dict(inserted), duplicate=False)

    raced = db.execute(
        _sql("SELECT * FROM warehouse_intake WHERE dedupe_key = :k LIMIT 1"),
        {"k": key},
    ).mappings().first()
    if not raced:
        db.commit()
        return None
    return _bump_existing(db, dict(raced), params)


def _bump_existing(db, existing: dict, params: dict) -> dict:
    """Повтор: время первой приёмки не меняем, вторую строку не создаём."""
    updated = db.execute(
        _sql("""
            UPDATE warehouse_intake
            SET last_scan_at = NOW(),
                scan_count = scan_count + 1,
                barcode = CASE WHEN COALESCE(barcode, '') = '' THEN :barcode ELSE barcode END,
                marketplace = CASE WHEN COALESCE(marketplace, '') = '' THEN :marketplace ELSE marketplace END,
                supplier_article = CASE WHEN :found AND :supplier_article <> '' THEN :supplier_article ELSE supplier_article END,
                product_name = CASE WHEN :found AND :product_name <> '' THEN :product_name ELSE product_name END,
                nm_id = CASE WHEN :found AND :nm_id <> '' THEN :nm_id ELSE nm_id END,
                srid = CASE WHEN :found AND :srid <> '' THEN :srid ELSE srid END,
                claim_id = CASE WHEN :found AND :claim_id IS NOT NULL THEN :claim_id ELSE claim_id END,
                status = CASE WHEN :found AND :status <> '' THEN :status ELSE status END,
                has_claim = CASE WHEN :found THEN :has_claim ELSE has_claim END,
                match_by = CASE WHEN :found AND :match_by <> '' THEN :match_by ELSE match_by END,
                category = CASE WHEN :found AND :category <> '' THEN :category ELSE category END,
                categories = CASE WHEN :found THEN CAST(:categories AS JSONB) ELSE categories END,
                product_photo = CASE WHEN :found AND :product_photo <> '' THEN :product_photo ELSE product_photo END,
                payload = CASE WHEN :found THEN CAST(:payload AS JSONB) ELSE payload END,
                found = found OR :found
            WHERE id = :id
            RETURNING *
        """),
        {**params, "id": existing["id"]},
    ).mappings().first()
    db.commit()
    row = dict(updated or existing)
    if not updated:
        row["scan_count"] = int(row.get("scan_count") or 1) + 1
        row["last_scan_at"] = datetime.now(timezone.utc)
    return _serialize(row, duplicate=True)


def latest_intake(db, *, srid: str = "", barcode: str = "") -> dict | None:
    srid = (srid or "").strip()
    barcode = (barcode or "").strip()
    if not srid and not barcode:
        return None
    try:
        ensure_table(db)
        row = db.execute(
            _sql("""
                SELECT * FROM warehouse_intake
                WHERE found = TRUE
                  AND (
                    (:srid <> '' AND LOWER(srid) = LOWER(:srid))
                    OR (:barcode <> '' AND barcode = :barcode)
                  )
                ORDER BY received_at DESC
                LIMIT 1
            """),
            {"srid": srid, "barcode": barcode},
        ).mappings().first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    return _serialize(dict(row)) if row else None


def attach_intake(db, card: dict) -> dict:
    if not card:
        return card
    row = latest_intake(db, srid=str(card.get("srid") or ""), barcode=str(card.get("barcode") or ""))
    if row:
        card["intake"] = row
        card["received_at"] = row.get("received_at")
    return card


def period_span_days(days) -> int:
    try:
        value = int(days or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 31))


def list_today(db, days: int = 1) -> dict:
    span = period_span_days(days) - 1
    params = {"span": span}
    try:
        ensure_table(db)
        items = db.execute(
            _sql(f"""
                SELECT * FROM warehouse_intake
                WHERE {_PERIOD_FILTER}
                ORDER BY received_at DESC
                LIMIT 500
            """),
            params,
        ).mappings().all()
        articles = db.execute(
            _sql(f"""
                SELECT COALESCE(NULLIF(BTRIM(supplier_article), ''), 'без артикула') AS supplier_article,
                       MAX(NULLIF(product_name, '')) AS product_name,
                       COUNT(*) AS qty
                FROM warehouse_intake
                WHERE {_PERIOD_FILTER}
                GROUP BY 1
                ORDER BY qty DESC, supplier_article
            """),
            params,
        ).mappings().all()
        total_row = db.execute(
            _sql(f"SELECT COUNT(*) AS n FROM warehouse_intake WHERE {_PERIOD_FILTER}"),
            params,
        ).mappings().first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {"day": _moscow_day(), "days": span + 1, "total": 0, "articles": [], "items": []}

    rows = [_serialize(dict(r)) for r in items]
    arts = [
        {
            "supplier_article": r["supplier_article"],
            "product_name": r.get("product_name") or "",
            "qty": int(r["qty"] or 0),
        }
        for r in articles
    ]
    return {
        "day": _moscow_day(),
        "days": span + 1,
        "total": int((total_row or {}).get("n") or 0),
        "articles": arts,
        "items": rows,
    }


def _moscow_day() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()
