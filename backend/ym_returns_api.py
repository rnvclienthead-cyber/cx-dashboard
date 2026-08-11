"""
Яндекс Маркет — решения по возвратам.

GET  /v2/campaigns/{campaignId}/orders/{orderId}/returns/{returnId}
POST /v1/businesses/{businessId}/returns/decisions
POST /v2/campaigns/{campaignId}/orders/{orderId}/returns/{returnId}/decision/submit
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ym_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ym_returns_api")

BASE = ym_client.BASE

# Активные (ещё не финал) — для вкладки «Активные»
ACTIVE_STATUSES = {
    "WAITING_FOR_DECISION",
    "PREMODERATION_DECISION_WAITING",
    "PREMODERATION_SELECT_DELIVERY",
    "STARTED_BY_USER",
    "WAITING_FOR_RETURN",
    "RETURNED",
}

STATUS_RU = {
    "WAITING_FOR_DECISION": "На рассмотрении",
    "PREMODERATION_DECISION_WAITING": "На рассмотрении",
    "PREMODERATION_SELECT_DELIVERY": "На рассмотрении",
    "STARTED_BY_USER": "На рассмотрении",
    "WAITING_FOR_RETURN": "На рассмотрении",
    "RETURNED": "На рассмотрении",
    "REFUND_IN_PROGRESS": "Одобрено",
    "REFUNDED": "Одобрено",
    "NOT_RETURNED": "Отказ",
    "FAILED": "Отказ",
    "CANCELLED": "Отменено",
    "REJECTED": "Отказ",
}

PRIMARY = {
    "FAST_REFUND_MONEY": {
        "label": "Одобрить без возврата товара",
        "kind": "approve_keep",
        "needs_comment": False,
        "local_status": "Одобрено",
        "local_code": "REFUNDED",
    },
    "REFUND_MONEY": {
        "label": "Одобрить с возвратом / вернуть деньги",
        "kind": "approve_return",
        "needs_comment": False,
        "local_status": "Одобрено",
        "local_code": "REFUNDED",
    },
    "REFUND_MONEY_INCLUDING_SHIPMENT": {
        "label": "Одобрить с возвратом и пересылкой",
        "kind": "approve_return",
        "needs_comment": True,
        "local_status": "Одобрено",
        "local_code": "REFUNDED",
    },
    "DECLINE_REFUND": {
        "label": "Отказать",
        "kind": "reject",
        "needs_comment": True,
        "local_status": "Отказ",
        "local_code": "REJECTED",
    },
}

REASON_LABELS = {
    "ISSUE_WITH_THE_PRODUCT_WAS_NOT_CONFIRMED": "Проблема с товаром не подтверждена",
    "MECHANICAL_DAMAGE": "Механические повреждения",
    "WARRANTY_PERIOD_HAS_EXPIRED": "Истёк гарантийный срок",
    "CONFIGURATION_OR_PACKAGING_COMPROMISED": "Нарушена комплектация или упаковка",
    "PRODUCT_APPEARANCE_COMPROMISED": "Нарушен товарный вид",
    "WARRANTY_TERMS_VIOLATED": "Нарушены условия гарантии",
    "DEVICE_ACTIVATED": "Устройство активировано",
}

REASON_TYPE_RU = {
    "BAD_QUALITY": "Брак / качество",
    "WRONG_ITEM": "Не тот товар",
    "WRONG_PARTS": "Не та комплектация",
    "DO_NOT_FIT": "Не подошёл",
    "DAMAGED": "Повреждён",
}


def status_ru(code: str | None, fallback: str | None = None) -> str:
    c = str(code or "").strip()
    if c in STATUS_RU:
        return STATUS_RU[c]
    return (fallback or c or "—").strip() or "—"


def is_active_status(code: str | None) -> bool:
    return str(code or "").strip() in ACTIVE_STATUSES


def _headers() -> dict:
    return ym_client.headers()


def _camp() -> str:
    return ym_client.campaign_id()


def _biz() -> str:
    return ym_client.business_id()


def fetch_return(order_id: int | str, return_id: int | str) -> Optional[dict]:
    camp = _camp()
    if not camp or not ym_client.api_key():
        return None
    try:
        r = requests.get(
            f"{BASE}/v2/campaigns/{camp}/orders/{order_id}/returns/{return_id}",
            headers=_headers(),
            timeout=25,
        )
        if not r.ok:
            logger.warning("[YM Returns] getReturn %s/%s -> %s %s",
                           order_id, return_id, r.status_code, r.text[:200])
            return None
        data = r.json() or {}
        return data.get("result") or data
    except Exception as e:
        logger.warning("[YM Returns] getReturn error: %s", e)
        return None


def fetch_available_decisions(return_id: int | str) -> list[dict]:
    camp, biz = _camp(), _biz()
    if not camp or not biz or not ym_client.api_key():
        return []
    try:
        r = requests.post(
            f"{BASE}/v1/businesses/{biz}/returns/decisions",
            headers=_headers(),
            json={"campaignId": int(camp), "returnId": int(return_id)},
            timeout=25,
        )
        if not r.ok:
            logger.warning("[YM Returns] decisions %s -> %s %s",
                           return_id, r.status_code, r.text[:200])
            return []
        data = r.json() or {}
        result = data.get("result") or data
        return list(result.get("availableDecisions") or [])
    except Exception as e:
        logger.warning("[YM Returns] decisions error: %s", e)
        return []


def extract_return_item_ids(live: dict | None) -> list[int]:
    out: list[int] = []
    if not live:
        return out
    for item in live.get("items") or []:
        for dec in item.get("decisions") or []:
            rid = dec.get("returnItemId")
            if rid is not None:
                try:
                    out.append(int(rid))
                except (TypeError, ValueError):
                    pass
    return out


def map_actions(available: list[dict] | None) -> list[dict]:
    """Кнопки для UI из availableDecisions ЯМ."""
    out = []
    seen_kinds = set()
    for row in available or []:
        dtype = str(row.get("decisionType") or "").strip()
        meta = PRIMARY.get(dtype)
        if not meta:
            continue
        if meta["kind"] in seen_kinds and dtype != "REFUND_MONEY_INCLUDING_SHIPMENT":
            continue
        seen_kinds.add(meta["kind"])
        reasons = [str(x) for x in (row.get("decisionReasonTypes") or []) if x]
        out.append({
            "action": dtype,
            "label": meta["label"],
            "kind": meta["kind"],
            "needs_comment": meta["needs_comment"] or dtype == "DECLINE_REFUND",
            "primary": True,
            "is_change": False,
            "reason_types": [
                {"value": r, "label": REASON_LABELS.get(r, r)} for r in reasons
            ],
        })
    return out


def submit_decision(
    order_id: int | str,
    return_id: int | str,
    *,
    decision_type: str,
    return_item_ids: list[int],
    comment: str = "",
    reason_type: str = "",
) -> dict[str, Any]:
    camp = _camp()
    if not camp or not ym_client.api_key():
        return {"ok": False, "status_code": 0, "text": "Нет YM_API_KEY / YM_CAMPAIGN_ID"}
    if not return_item_ids:
        return {"ok": False, "status_code": 400, "text": "Нет returnItemId для решения"}

    decisions = []
    for item_id in return_item_ids:
        d: dict[str, Any] = {
            "returnItemId": int(item_id),
            "decisionType": decision_type,
        }
        if decision_type == "DECLINE_REFUND":
            d["decisionReasonType"] = reason_type or "ISSUE_WITH_THE_PRODUCT_WAS_NOT_CONFIRMED"
            if comment:
                d["comment"] = comment[:1000]
        elif comment and decision_type == "REFUND_MONEY_INCLUDING_SHIPMENT":
            d["comment"] = comment[:1000]
        decisions.append(d)

    body = {"returnItemDecisions": decisions}
    try:
        r = requests.post(
            f"{BASE}/v2/campaigns/{camp}/orders/{order_id}/returns/{return_id}/decision/submit",
            headers=_headers(),
            json=body,
            timeout=30,
        )
        ok = r.status_code in (200, 204)
        if not ok:
            logger.warning("[YM Returns] submit %s -> %s %s",
                           return_id, r.status_code, r.text[:300])
        return {
            "ok": ok,
            "status_code": r.status_code,
            "text": (r.text or "")[:500],
            "body": body,
        }
    except Exception as e:
        logger.exception("[YM Returns] submit error")
        return {"ok": False, "status_code": 0, "text": str(e)}


def apply_local_status(db: Session, return_id: str | int, decision_type: str) -> None:
    meta = PRIMARY.get(decision_type) or {}
    code = meta.get("local_code") or "REFUNDED"
    ru = meta.get("local_status") or status_ru(code)
    db.execute(text("""
        UPDATE ym_returns
        SET status = :st,
            status_ru = :ru,
            synced_at = NOW()
        WHERE return_id::text = :rid
    """), {"st": code, "ru": ru, "rid": str(return_id)})
    db.commit()


def decide_return(
    db: Session,
    return_id: str,
    action: str,
    comment: str = "",
    reason: str = "",
) -> dict[str, Any]:
    row = db.execute(text("""
        SELECT return_id::text, order_id::text, status, status_ru
        FROM ym_returns WHERE return_id::text = :rid LIMIT 1
    """), {"rid": str(return_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Возврат не найден в базе", "status_code": 404}

    action = (action or "").strip()
    if action not in PRIMARY:
        return {"ok": False, "error": f"Неизвестное действие: {action}", "status_code": 400}

    if action == "DECLINE_REFUND" and len((comment or "").strip()) < 5:
        return {
            "ok": False,
            "error": "Для отказа укажите комментарий (от 5 символов)",
            "status_code": 400,
        }

    order_id = row["order_id"]
    live = fetch_return(order_id, return_id)
    item_ids = extract_return_item_ids(live)
    available = fetch_available_decisions(return_id)
    allowed = {str(a.get("decisionType") or "") for a in available}
    if allowed and action not in allowed:
        return {
            "ok": False,
            "error": "ЯМ больше не разрешает это действие по возврату",
            "status_code": 409,
            "actions": map_actions(available),
        }

    # Если ЯМ не отдал item id — пробуем один раз из live; иначе ошибка
    if not item_ids:
        return {
            "ok": False,
            "error": "ЯМ не вернул позиции возврата (returnItemId)",
            "status_code": 502,
        }

    # Для отказа — взять первую доступную причину, если не передали
    if action == "DECLINE_REFUND" and not reason:
        for a in available:
            if str(a.get("decisionType")) == "DECLINE_REFUND":
                rts = [x for x in (a.get("decisionReasonTypes") or []) if x]
                if rts:
                    reason = str(rts[0])
                break
        reason = reason or "ISSUE_WITH_THE_PRODUCT_WAS_NOT_CONFIRMED"

    result = submit_decision(
        order_id,
        return_id,
        decision_type=action,
        return_item_ids=item_ids,
        comment=comment or "",
        reason_type=reason or "",
    )
    if not result["ok"]:
        return {
            "ok": False,
            "error": result.get("text") or "ЯМ отклонил запрос",
            "status_code": result.get("status_code") or 502,
        }

    apply_local_status(db, return_id, action)
    meta = PRIMARY[action]
    return {
        "ok": True,
        "claim_id": str(return_id),
        "mp": "ym",
        "action": action,
        "status": meta["local_status"],
        "status_label": meta["local_status"],
        "order_id": order_id,
    }


def reason_label(reason_type: str | None, subreason: str | None = None) -> str:
    rt = str(reason_type or "").strip()
    if not rt:
        return "Возврат ЯМ"
    base = REASON_TYPE_RU.get(rt, rt.replace("_", " ").title())
    if subreason and str(subreason).strip() not in ("", "UNKNOWN"):
        return f"{base}"
    return base
