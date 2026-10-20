"""Теги сотрудника на возврате — те же 13 категорий, что на карте проблем."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

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


def normalize_tag_ids(raw) -> list[int]:
    out = []
    seen = set()
    for item in raw or []:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 13 and n not in seen:
            seen.add(n)
            out.append(n)
    return sorted(out)


def staff_ids_from(raw) -> list[int]:
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return []
    if isinstance(data, dict):
        return normalize_tag_ids(data.get("ids") or data.get("tags") or [])
    if isinstance(data, list):
        return normalize_tag_ids(data)
    return []


def staff_by_from(raw) -> str:
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return ""
    if isinstance(data, dict):
        return str(data.get("by") or "").strip()
    return ""


def ai_cat_ids_from_row(row: dict) -> list[int]:
    ids = []
    for i in range(1, 14):
        if row.get(f"cat_{i}"):
            ids.append(i)
    return ids


def tags_mismatch(staff_ids, ai_ids) -> bool:
    staff = normalize_tag_ids(staff_ids)
    ai = normalize_tag_ids(ai_ids)
    return bool(staff and ai and staff != ai)


def make_payload(ids, username: str) -> dict:
    return {
        "ids": normalize_tag_ids(ids),
        "by": str(username or "").strip() or "?",
        "at": datetime.now(timezone.utc).isoformat(),
    }


def attach_staff_tag_fields(row: dict) -> dict:
    d = dict(row)
    staff = staff_ids_from(d.get("staff_tags"))
    ai = ai_cat_ids_from_row(d)
    d["staff_tag_ids"] = staff
    d["staff_by"] = staff_by_from(d.get("staff_tags"))
    d["mismatch"] = tags_mismatch(staff, ai)
    return d


def save_staff_tags(db, mp: str, claim_id: str, ids, username: str) -> dict[str, Any]:
    payload = make_payload(ids, username)
    if not payload["ids"] or not str(claim_id or "").strip():
        return payload
    raw = json.dumps(payload, ensure_ascii=False)
    m = (mp or "wb").strip().lower()
    if m == "ym":
        sql = "UPDATE ym_returns SET staff_tags = CAST(:raw AS jsonb) WHERE return_id::text = :cid"
    elif m == "ozon":
        sql = "UPDATE ozon_returns SET staff_tags = CAST(:raw AS jsonb) WHERE return_id::text = :cid"
    else:
        sql = "UPDATE wb_claims SET staff_tags = CAST(:raw AS jsonb) WHERE claim_id::text = :cid"
    db.execute(text(sql), {"raw": raw, "cid": str(claim_id).strip()})
    db.commit()
    return payload
