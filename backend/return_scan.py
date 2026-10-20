"""Поиск возврата по коду с этикетки (камера телефона или сканер-пистолет).

Сначала ищем в нашей базе: SRID, номер претензии, штрихкод стикера WB (shk),
партию (income_id), штрихкод Ozon, заказ Яндекса. Расшифровка «розового» QR
через отдельный API WB не нужна, если на наклейке есть читаемый номер.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

CATEGORIES = {
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

MATCH_LABELS = {
    "srid": "номер заказа WB (SRID)",
    "wb_mp_srid": "номер из приёмки WB (не претензия)",
    "claim_id": "номер претензии WB",
    "shk": "штрихкод стикера на коробке",
    "income_id": "QR партии возвратов",
    "wb_return_sticker": "стикер возврата WB (розовая наклейка)",
    "wb_return_shk": "штрихкод возврата WB",
    "trbx": "QR короба WB (TRBX)",
    "ozon_barcode": "штрихкод возврата Ozon",
    "ozon_posting": "номер отправления Ozon",
    "ozon_id": "номер возврата Ozon",
    "ym_order": "номер заказа Яндекс Маркета",
    "ym_id": "номер возврата Яндекс Маркета",
}

WB_CLAIM_SQL = """
    SELECT c.srid, c.claim_id, c.supplier_article, c.user_comment,
           c.status, c.claim_type, c.photos, c.nm_id, c.claim_date, c.income_id,
           c.cat_1, c.cat_2, c.cat_3, c.cat_4, c.cat_5, c.cat_6,
           c.cat_7, c.cat_8, c.cat_9, c.cat_10, c.cat_11, c.cat_12, c.cat_13,
           a.name_ru,
           pp.photo_url AS product_photo
    FROM wb_claims c
    LEFT JOIN wb_assortment a ON a.supplier_article = c.supplier_article
    LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
"""
_NOT_PURGED_SQL = "NOT EXISTS (SELECT 1 FROM cx_purged_claims p WHERE p.srid = c.srid)"

_QUERY_KEYS = (
    "srid", "sticker", "stickerId", "sticker_id", "shk", "shkId", "shk_id",
    "barcode", "incomeId", "income_id", "claimId", "claim_id",
)
_goods_table_ready = False


def _sql(sql: str):
    try:
        from sqlalchemy import text
        return text(sql)
    except ImportError:
        return sql


def normalize_scan_code(raw: str) -> str:
    """Достаёт полезный номер из сырого ответа сканера (URL, JSON, текст)."""
    s = (raw or "").strip()
    if not s:
        return ""
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            data = json.loads(s)
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in _QUERY_KEYS:
                val = data.get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
        if isinstance(data, list) and data:
            return normalize_scan_code(str(data[0]))
    if "://" in s or s.lower().startswith("www."):
        url = s if "://" in s else "https://" + s
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            for key in _QUERY_KEYS:
                vals = qs.get(key) or qs.get(key.lower())
                if vals and str(vals[0]).strip():
                    return str(vals[0]).strip()
            parts = [p for p in parsed.path.split("/") if p]
            if parts:
                return parts[-1]
        except Exception:
            pass
    return s


def expand_scan_codes(raw: str) -> list[str]:
    """Варианты одного скана: TRBX, розовый QR (*XXXX), $1:1:шк:офис, partA+partB."""
    code = normalize_scan_code(raw)
    if not code:
        return []
    out: list[str] = []

    def add(value: str):
        v = (value or "").strip()
        if v and v not in out:
            out.append(v)

    add(code)
    parts = [p for p in code.replace("/", " ").replace("-", " ").split() if p]
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        add(parts[0] + parts[1])
        add(parts[0] + parts[1].zfill(4))
    upper = code.upper()
    if upper.startswith("WB-TRBX-"):
        rest = code[8:]
        add(rest)
        add("TRBX" + rest)
        add("WB-TRBX-" + rest)
    elif upper.startswith("TRBX"):
        rest = code[4:].lstrip("-")
        add(rest)
        add("TRBX" + rest)
        add("WB-TRBX-" + rest)
    if code[:1] in "*!":
        body = code[1:]
        add("*" + body)
        add("!" + body)
        add(body)
    elif len(code) >= 6 and code.isalnum() and not code.isdigit():
        add("*" + code)
        add("!" + code)
    low = code.lower()
    if low.startswith("mp.") and low.endswith(".r") and len(code) > 5:
        add(code[3:-2])
    if "$" in code or (":" in code and any(ch.isdigit() for ch in code)):
        for part in "".join(ch if ch.isdigit() else " " for ch in code).split():
            if len(part) >= 6:
                add(part)
    return out


def _is_numeric(code: str) -> bool:
    return bool(code) and code.isdigit()


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _photos_from_row(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        items = [str(u) for u in raw if u]
    else:
        items = str(raw).split()
    out = []
    for url in items:
        url = (url or "").strip()
        if not url:
            continue
        if "|" in url:
            url = url.split("|")[-1].strip()
        if url.startswith("//"):
            url = "https:" + url
        if url:
            out.append(url)
    return out


def build_wb_card(row: dict, *, match_by: str) -> dict:
    active_cats = [CATEGORIES[i] for i in range(1, 14) if row.get(f"cat_{i}")]
    return {
        "marketplace": "wb",
        "match_by": match_by,
        "match_label": MATCH_LABELS.get(match_by, match_by),
        "srid": row.get("srid") or "",
        "claim_id": row.get("claim_id"),
        "supplier_article": row.get("supplier_article") or "",
        "product_name": row.get("name_ru") or "",
        "product_photo": row.get("product_photo") or "",
        "nm_id": row.get("nm_id") or "",
        "status": row.get("status") or "",
        "claim_type": row.get("claim_type"),
        "user_comment": row.get("user_comment") or "",
        "claim_date": _iso(row.get("claim_date")),
        "income_id": row.get("income_id") or "",
        "barcode": str(row.get("shk_id") or ""),
        "categories": active_cats,
        "photos": _photos_from_row(row.get("photos")),
        "has_claim": True,
    }


def fetch_wb_claim(db, where_sql: str, params: dict):
    row = db.execute(
        _sql(f"{WB_CLAIM_SQL} WHERE {_NOT_PURGED_SQL} AND {where_sql} LIMIT 1"),
        params,
    ).mappings().first()
    return dict(row) if row else None


def fetch_wb_batch(db, income_id: str) -> list[dict]:
    rows = db.execute(
        _sql(
            f"{WB_CLAIM_SQL} WHERE {_NOT_PURGED_SQL} AND c.income_id = CAST(:income_id AS TEXT) "
            "ORDER BY c.claim_date DESC LIMIT 200"
        ),
        {"income_id": income_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_order_by_shk(db, code: str):
    if not _is_numeric(code):
        return None
    try:
        shk = int(code)
    except ValueError:
        return None
    row = db.execute(
        _sql("""
            SELECT srid, supplier_article, shk_id
            FROM wb_orders
            WHERE shk_id = :shk
            ORDER BY last_change_date DESC NULLS LAST
            LIMIT 1
        """),
        {"shk": shk},
    ).mappings().first()
    return dict(row) if row else None


def _claim_via_order_shk(db, *raw_ids):
    """Поставка на склад и претензия связаны через shk заказа, не через srid приёмки."""
    seen = set()
    for raw in raw_ids:
        if raw in (None, ""):
            continue
        code = str(raw).strip()
        if not _is_numeric(code) or len(code) < 10 or code in seen:
            continue
        seen.add(code)
        order = _fetch_order_by_shk(db, code)
        if not order or not order.get("srid"):
            continue
        claim = fetch_wb_claim(db, "LOWER(c.srid) = LOWER(:code)", {"code": order["srid"]})
        if claim:
            return claim, order
    return None, None


def _ozon_select():
    return """
        SELECT c.return_id, c.posting_number, c.barcode, c.supplier_article,
               c.product_name, c.return_reason, c.status_ru, c.status_sys,
               c.photos, c.buyer_comment, c.place_name, c.target_place_name,
               c.return_date, c.ozon_sku, c.return_type, c.schema_code,
               pp.photo_url AS product_photo
        FROM ozon_returns c
        LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
    """


def _ym_select():
    return """
        SELECT c.return_id, c.order_id, c.supplier_article, c.return_comment,
               c.photos, c.status, c.status_ru, c.reason_type, c.subreason_type,
               c.created_at, pp.photo_url AS product_photo
        FROM ym_returns c
        LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
    """


def _build_ozon_card(row: dict, *, match_by: str) -> dict:
    reason = (row.get("return_reason") or "").strip()
    comment = (row.get("buyer_comment") or "").strip()
    categories = [reason] if reason else []
    return {
        "marketplace": "ozon",
        "match_by": match_by,
        "match_label": MATCH_LABELS.get(match_by, match_by),
        "srid": str(row.get("posting_number") or row.get("return_id") or ""),
        "claim_id": row.get("return_id"),
        "supplier_article": row.get("supplier_article") or "",
        "product_name": row.get("product_name") or "",
        "product_photo": row.get("product_photo") or "",
        "nm_id": row.get("ozon_sku") or "",
        "status": row.get("status_ru") or row.get("status_sys") or "",
        "claim_type": row.get("return_type"),
        "user_comment": comment or reason,
        "claim_date": _iso(row.get("return_date")),
        "income_id": "",
        "barcode": row.get("barcode") or "",
        "place_name": row.get("place_name") or "",
        "target_place_name": row.get("target_place_name") or "",
        "categories": categories,
        "photos": _photos_from_row(row.get("photos")),
        "has_claim": True,
    }


def _build_ym_card(row: dict, *, match_by: str) -> dict:
    reason = " ".join(
        part for part in (row.get("reason_type"), row.get("subreason_type")) if part
    ).strip()
    return {
        "marketplace": "ym",
        "match_by": match_by,
        "match_label": MATCH_LABELS.get(match_by, match_by),
        "srid": str(row.get("order_id") or row.get("return_id") or ""),
        "claim_id": row.get("return_id"),
        "supplier_article": row.get("supplier_article") or "",
        "product_name": "",
        "product_photo": row.get("product_photo") or "",
        "nm_id": "",
        "status": row.get("status_ru") or row.get("status") or "",
        "claim_type": row.get("reason_type"),
        "user_comment": (row.get("return_comment") or "").strip(),
        "claim_date": _iso(row.get("created_at")),
        "income_id": "",
        "barcode": "",
        "categories": [reason] if reason else [],
        "photos": _photos_from_row(row.get("photos")),
        "has_claim": True,
    }


def _query_one(db, sql: str, params: dict):
    try:
        row = db.execute(_sql(sql), params).mappings().first()
        return dict(row) if row else None
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _claim_result(item: dict) -> dict:
    return {"kind": "claim", "item": item}


def _batch_result(income_id, rows: list[dict]) -> dict:
    return {
        "kind": "batch",
        "income_id": str(income_id),
        "count": len(rows),
        "match_by": "income_id",
        "match_label": MATCH_LABELS["income_id"],
        "marketplace": "wb",
        "items": [build_wb_card(r, match_by="income_id") for r in rows],
    }


def _goods_return_row(db, code: str):
    row = _query_one(
        db,
        """
            SELECT srid, shk_id, sticker_id, sticker_barcode, barcode, nm_id, vendor_code, subject_name,
                   status, reason, return_type, dst_office_address, completed_dt
            FROM wb_goods_returns
            WHERE LOWER(srid) = LOWER(:code)
               OR sticker_id = :code
               OR barcode = :code
               OR sticker_barcode = :code
               OR (:pat IS NOT NULL AND srid ILIKE :pat)
               OR (:num IS NOT NULL AND shk_id = :num)
            ORDER BY last_sync DESC NULLS LAST
            LIMIT 1
        """,
        {
            "code": code,
            "num": int(code) if _is_numeric(code) else None,
            "pat": f"%{code}%" if (not _is_numeric(code) and len(code) >= 16) else None,
        },
    )
    if row:
        return row
    return _query_one(
        db,
        """
            SELECT srid, sa_name AS vendor_code, nm_id, barcode,
                   shk_id, sticker_id, trbx_id, gi_id
            FROM wb_report_detail
            WHERE trbx_id = :code
               OR sticker_id = :code
               OR barcode = :code
               OR (:num IS NOT NULL AND shk_id = :num)
            ORDER BY rr_dt DESC NULLS LAST
            LIMIT 1
        """,
        {"code": code, "num": int(code) if _is_numeric(code) else None},
    )


def _goods_by_sticker_prefix(db, code: str):
    """Верхнее число розовой наклейки (partA) — уникальный префикс sticker_id."""
    if not (_is_numeric(code) and 6 <= len(code) <= 8):
        return None
    try:
        rows = db.execute(
            _sql("""
                SELECT srid, shk_id, sticker_id, sticker_barcode, barcode, nm_id, vendor_code, subject_name,
                       status, reason, return_type, dst_office_address, completed_dt
                FROM wb_goods_returns
                WHERE sticker_id LIKE :pat
                ORDER BY last_sync DESC NULLS LAST
                LIMIT 2
            """),
            {"pat": f"{code}%"},
        ).mappings().all()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if len(rows) != 1:
        return None
    return dict(rows[0])


def _card_from_goods_return(db, row: dict, *, match_by: str) -> dict:
    claim = None
    srid = row.get("srid") or ""
    if srid:
        claim = fetch_wb_claim(db, "LOWER(c.srid) = LOWER(:code)", {"code": srid})
    if not claim:
        claim, _order = _claim_via_order_shk(db, row.get("shk_id"), row.get("sticker_id"))
        if claim and match_by in ("wb_return_sticker", "wb_return_shk", "wb_mp_srid"):
            match_by = "shk"
    if claim:
        card = build_wb_card(claim, match_by=match_by)
        if row.get("sticker_id"):
            card["barcode"] = str(row.get("sticker_id") or card.get("barcode") or "")
        elif row.get("shk_id"):
            card["barcode"] = str(row.get("shk_id") or card.get("barcode") or "")
        if row.get("dst_office_address") and not card.get("place_name"):
            card["place_name"] = row["dst_office_address"]
        return card
    article = (row.get("vendor_code") or row.get("supplier_article") or "").strip()
    name = (row.get("subject_name") or row.get("product_name") or "").strip()
    photo = ""
    if row.get("nm_id"):
        extra = _query_one(
            db,
            """
                SELECT a.supplier_article, a.subject AS name_ru, pp.photo_url
                FROM wb_logistics a
                LEFT JOIN product_photos pp ON pp.supplier_article = a.supplier_article
                WHERE a.nm_id::text = :nm
                LIMIT 1
            """,
            {"nm": str(row["nm_id"])},
        )
        if extra:
            article = article or (extra.get("supplier_article") or "")
            name = name or (extra.get("name_ru") or "")
            photo = extra.get("photo_url") or ""
    title, text = _no_claim_story(row)
    extra = " ".join(
        part for part in (row.get("return_type"), row.get("reason")) if part
    ).strip()
    return {
        "marketplace": "wb",
        "match_by": match_by,
        "match_label": MATCH_LABELS.get(match_by, match_by),
        "srid": srid,
        "claim_id": None,
        "has_claim": False,
        "no_claim_title": title,
        "no_claim_text": text,
        "supplier_article": article,
        "product_name": name,
        "product_photo": photo,
        "nm_id": str(row.get("nm_id") or ""),
        "status": row.get("status") or "На складе",
        "claim_type": row.get("return_type"),
        "user_comment": "",
        "claim_date": _iso(row.get("completed_dt") or row.get("order_dt")),
        "income_id": str(row.get("gi_id") or ""),
        "barcode": str(row.get("sticker_id") or row.get("shk_id") or row.get("barcode") or ""),
        "place_name": row.get("dst_office_address") or "",
        "categories": [extra] if extra else [],
        "photos": [],
    }


def _no_claim_story(row: dict | None = None) -> tuple[str, str]:
    blob = " ".join(
        str(x) for x in (
            (row or {}).get("return_type"),
            (row or {}).get("reason"),
            (row or {}).get("status"),
        ) if x
    ).lower()
    if any(word in blob for word in ("отмен", "cancel", "невыкуп", "unredeem")):
        return (
            "Это отмена или невыкуп",
            "Претензии покупателя нет — так и должно быть. Причины дефекта здесь не будет.",
        )
    if any(word in blob for word in ("брак", "defect", "приемк", "приёмк")):
        return (
            "Вернули без претензии",
            "Похоже на отбраковку на приёмке маркетплейса. Отдельной заявки покупателя нет — это нормально.",
        )
    return (
        "Претензии нет — это нормально",
        "Коробка есть в поставке на склад. Заявки покупателя может не быть: отмена, невыкуп или отбраковка на приёмке WB.",
    )


def _order_without_claim_card(order: dict, scanned: str) -> dict:
    title, text = _no_claim_story(None)
    return {
        "marketplace": "wb",
        "match_by": "shk",
        "match_label": MATCH_LABELS["shk"],
        "srid": order.get("srid") or "",
        "claim_id": None,
        "has_claim": False,
        "no_claim_title": title,
        "no_claim_text": text,
        "supplier_article": order.get("supplier_article") or "",
        "product_name": "",
        "product_photo": "",
        "nm_id": "",
        "status": "Без претензии",
        "claim_type": None,
        "user_comment": "",
        "claim_date": None,
        "income_id": "",
        "barcode": str(order.get("shk_id") or scanned),
        "categories": [],
        "photos": [],
    }


def _match_by_for_goods_row(row: dict, code: str) -> str:
    sticker = str(row.get("sticker_id") or "")
    encoded = str(row.get("sticker_barcode") or "")
    shk = str(row.get("shk_id") or "")
    srid = str(row.get("srid") or "")
    trbx = str(row.get("trbx_id") or "")
    if code.upper().startswith("TRBX") or (trbx and code in (trbx, trbx.replace("WB-TRBX-", "").replace("TRBX", ""))):
        return "trbx"
    if srid and (code.lower() == srid.lower() or (len(code) >= 16 and code.lower() in srid.lower())):
        return "wb_mp_srid"
    if encoded and code in (encoded, encoded.replace("!", "*"), encoded.replace("*", "!")):
        return "wb_return_sticker"
    if sticker and code == sticker:
        return "wb_return_sticker"
    if shk and code == shk:
        return "wb_return_shk"
    if trbx:
        return "trbx"
    if sticker:
        return "wb_return_sticker"
    return "wb_return_shk"


def lookup_scan(db, raw: str) -> dict | None:
    """Находит возврат по сырому коду сканера. None — ничего не нашли."""
    codes = expand_scan_codes(raw)
    if not codes:
        return None
    global _goods_table_ready
    if not _goods_table_ready:
        try:
            from .wb_goods_return import ensure_table
            ensure_table(db)
            _goods_table_ready = True
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    code = codes[0]

    for variant in codes:
        row = fetch_wb_claim(db, "LOWER(c.srid) = LOWER(:code)", {"code": variant})
        if row:
            return _claim_result(build_wb_card(row, match_by="srid"))
        if len(variant) >= 16 and not _is_numeric(variant):
            row = fetch_wb_claim(db, "c.srid ILIKE :pat", {"pat": f"%{variant}%"})
            if row:
                return _claim_result(build_wb_card(row, match_by="srid"))

    row = fetch_wb_claim(db, "c.claim_id::text = :code", {"code": code})
    if row:
        return _claim_result(build_wb_card(row, match_by="claim_id"))

    pending_order = None
    for variant in codes:
        if not (_is_numeric(variant) and len(variant) >= 10):
            continue
        order = _fetch_order_by_shk(db, variant)
        if not order or not order.get("srid"):
            continue
        claim = fetch_wb_claim(db, "LOWER(c.srid) = LOWER(:code)", {"code": order["srid"]})
        if claim:
            card = build_wb_card(claim, match_by="shk")
            card["barcode"] = str(order.get("shk_id") or variant)
            return _claim_result(card)
        if pending_order is None:
            pending_order = (order, variant)

    for variant in codes:
        goods = _goods_return_row(db, variant)
        if goods:
            match_by = _match_by_for_goods_row(goods, variant)
            return _claim_result(_card_from_goods_return(db, goods, match_by=match_by))

    if _is_numeric(code):
        batch = fetch_wb_batch(db, code)
        if batch:
            if len(batch) == 1:
                return _claim_result(build_wb_card(batch[0], match_by="income_id"))
            return _batch_result(code, batch)

    for variant in codes:
        if not (_is_numeric(variant) and 6 <= len(variant) <= 8):
            continue
        goods = _goods_by_sticker_prefix(db, variant)
        if goods:
            match_by = _match_by_for_goods_row(goods, str(goods.get("sticker_id") or variant))
            return _claim_result(_card_from_goods_return(db, goods, match_by=match_by))

    if pending_order:
        order, scanned = pending_order
        return _claim_result(_order_without_claim_card(order, scanned))

    ozon = _query_one(
        db,
        f"{_ozon_select()} WHERE c.barcode = :code ORDER BY c.return_date DESC NULLS LAST LIMIT 1",
        {"code": code},
    )
    if ozon:
        return _claim_result(_build_ozon_card(ozon, match_by="ozon_barcode"))

    ozon = _query_one(db, f"{_ozon_select()} WHERE c.posting_number = :code LIMIT 1", {"code": code})
    if ozon:
        return _claim_result(_build_ozon_card(ozon, match_by="ozon_posting"))

    ozon = _query_one(db, f"{_ozon_select()} WHERE c.return_id::text = :code LIMIT 1", {"code": code})
    if ozon:
        return _claim_result(_build_ozon_card(ozon, match_by="ozon_id"))

    ym = _query_one(
        db,
        f"{_ym_select()} WHERE c.order_id::text = :code ORDER BY c.created_at DESC NULLS LAST LIMIT 1",
        {"code": code},
    )
    if ym:
        return _claim_result(_build_ym_card(ym, match_by="ym_order"))

    ym = _query_one(db, f"{_ym_select()} WHERE c.return_id::text = :code LIMIT 1", {"code": code})
    if ym:
        return _claim_result(_build_ym_card(ym, match_by="ym_id"))

    return None
