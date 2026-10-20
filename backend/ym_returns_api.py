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

# Активные (ещё не финал) — как вкладка «Ждут решения» в ЛК ЯМ
ACTIVE_STATUSES = {
    "WAITING_FOR_DECISION",
    "PREMODERATION_DECISION_WAITING",
    "PREMODERATION_DECISION_MADE",  # решение ушло, покупатель может открыть спор
    "PREMODERATION_DISPUTE",
    "PREMODERATION_SELECT_DELIVERY",
    "STARTED_BY_USER",
    "WAITING_FOR_RETURN",
    "RETURNED",
}

# Пока ЯМ держит заявку в активных статусах ЛК — не схлопываем по decisionType.

STATUS_RU = {
    "WAITING_FOR_DECISION": "Ждёт нашего решения",
    "PREMODERATION_DECISION_WAITING": "Ждёт нашего решения",
    "PREMODERATION_SELECT_DELIVERY": "Ожидает возврат",
    "STARTED_BY_USER": "Заявка создана",
    "PREMODERATION_DECISION_MADE": "Ждём Яндекс",
    "PREMODERATION_DISPUTE": "Спор",
    "WAITING_FOR_RETURN": "Ожидает возврат",
    "RETURNED": "Ожидает возврат",
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
        "local_status": "Ждём Яндекс",
        "local_code": "PREMODERATION_DECISION_MADE",
    },
    "REFUND_MONEY": {
        "label": "Одобрить с возвратом / вернуть деньги",
        "kind": "approve_return",
        "needs_comment": False,
        "local_status": "Ждём Яндекс",
        "local_code": "PREMODERATION_DECISION_MADE",
    },
    "REFUND_MONEY_INCLUDING_SHIPMENT": {
        "label": "Одобрить с возвратом и пересылкой",
        "kind": "approve_return",
        "needs_comment": True,
        "local_status": "Ждём Яндекс",
        "local_code": "PREMODERATION_DECISION_MADE",
    },
    "DECLINE_REFUND": {
        "label": "Отказать",
        "kind": "reject",
        "needs_comment": True,
        "local_status": "Ждём Яндекс",
        "local_code": "PREMODERATION_DECISION_MADE",
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

# Причины покупателя (reasonType из заявления) — только русский, без .title() на английском
REASON_TYPE_RU = {
    "BAD_QUALITY": "Брак / качество",
    "WRONG_ITEM": "Не тот товар",
    "WRONG_PARTS": "Не та комплектация",
    "DO_NOT_FIT": "Не подошёл",
    "DOES_NOT_FIT": "Не подошёл",
    "DAMAGED": "Повреждён",
    "DELIVERY_FAIL": "Проблема с доставкой",
    "UNKNOWN": "Возврат",
}

SUBREASON_TYPE_RU = {
    "UNKNOWN": "",
    "INCOMPLETENESS": "неполная комплектация",
    "NOT_WORKING": "не работает",
    "DAMAGED": "повреждён",
    "USER_DID_NOT_LIKE": "не понравился",
    "USER_CHANGED_MIND": "передумал",
    "DID_NOT_MATCH_DESCRIPTION": "не соответствует описанию",
    "WRONG_ITEM": "не тот товар",
    "WRONG_COLOR": "не тот цвет",
}

# ЯМ на длинных комментариях отказа отвечает INTERNAL_ERROR (проверено на живом API)
DECLINE_COMMENT_MAX = 250

_APPROVE_DECISIONS = {
    "REFUND_MONEY",
    "FAST_REFUND_MONEY",
    "REFUND_MONEY_INCLUDING_SHIPMENT",
}
_REJECT_DECISIONS = {"DECLINE_REFUND"}


def effective_refund_status(payload: dict | None) -> str:
    """Нормализация статуса возврата ЯМ.

    Активные статусы ЛК не трогаем.

    STARTED_BY_USER — покупатель только открыл заявку, кнопок решения ещё нет.
    Вкладка «Ждут решения» в ЛК = PREMODERATION_DECISION_WAITING.
    """
    data = payload or {}
    raw = str(data.get("refundStatus") or data.get("shipmentStatus") or "").strip()
    if raw in ACTIVE_STATUSES:
        return raw

    types = []
    for item in data.get("items") or []:
        for decision in item.get("decisions") or []:
            t = str(decision.get("decisionType") or "").strip()
            if t and t != "UNKNOWN":
                types.append(t)
    if any(t in _REJECT_DECISIONS for t in types):
        return "REJECTED"
    if any(t in _APPROVE_DECISIONS for t in types):
        if raw in ("REFUND_IN_PROGRESS", "REFUNDED"):
            return raw
        return "REFUNDED"
    return raw


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


_SCHEMA_READY = False


def ensure_schema(conn_or_engine) -> None:
    """campaign_id / placement — FBS и FBY; force_archive — скрыть висяки из активных."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    ddl = [
        "ALTER TABLE ym_returns ADD COLUMN IF NOT EXISTS campaign_id bigint",
        "ALTER TABLE ym_returns ADD COLUMN IF NOT EXISTS placement_type varchar(16)",
        "ALTER TABLE ym_returns ADD COLUMN IF NOT EXISTS force_archive boolean DEFAULT false",
    ]

    def _run(conn):
        for q in ddl:
            conn.execute(text(q))

    if hasattr(conn_or_engine, "begin") and not hasattr(conn_or_engine, "info"):
        with conn_or_engine.begin() as conn:
            _run(conn)
    elif hasattr(conn_or_engine, "bind"):
        with conn_or_engine.bind.begin() as conn:
            _run(conn)
    else:
        _run(conn_or_engine)
    _SCHEMA_READY = True


def resolve_campaign_id(row_campaign_id=None) -> str:
    cid = str(row_campaign_id or "").strip()
    if cid and cid.isdigit():
        return cid
    return _camp()


def _friendly_submit_error(status_code: int, raw: str | None) -> str:
    text = (raw or "").strip()
    if status_code == 403 and (
        "inventory-and-order-processing" in text
        or "FORBIDDEN" in text
        or "scopes" in text.lower()
    ):
        return (
            "Ключ Яндекс Маркета не умеет обрабатывать возвраты. "
            "В кабинете ЯМ откройте Настройки → API и модули → токен «Клиентский сервис» "
            "и включите право «Обработка заказов и остатков» "
            "(сейчас стоит только просмотр). Либо право «Полное управление кабинетом»."
        )
    if status_code >= 500 or "INTERNAL_ERROR" in text:
        return (
            "Яндекс не принял решение (внутренняя ошибка площадки). "
            f"Для отказа сократите комментарий до {DECLINE_COMMENT_MAX} символов "
            "и уберите лишние переносы строк — затем отправьте ещё раз."
        )
    # Не светить сырой JSON в UI
    if text.startswith("{") and "errors" in text:
        try:
            import json
            data = json.loads(text)
            errs = data.get("errors") or []
            if errs and isinstance(errs[0], dict):
                msg = str(errs[0].get("message") or errs[0].get("code") or "").strip()
                if msg:
                    return f"Яндекс отклонил запрос: {msg}"
        except Exception:
            pass
    return text[:500]


def normalize_decision_comment(comment: str, *, decision_type: str) -> tuple[str, str | None]:
    """Нормализация комментария к решению. Для отказа — лимит длины (иначе ЯМ даёт 500)."""
    text = (comment or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if decision_type == "DECLINE_REFUND":
        text = " ".join(text.split())
        if len(text) > DECLINE_COMMENT_MAX:
            return text, (
                f"Комментарий к отказу — максимум {DECLINE_COMMENT_MAX} символов "
                f"(сейчас {len(text)}). Сократите текст."
            )
    elif len(text) > 1000:
        text = text[:1000].rstrip()
    return text, None


def fetch_return(
    order_id: int | str,
    return_id: int | str,
    *,
    campaign_id: str | int | None = None,
) -> Optional[dict]:
    camp = resolve_campaign_id(campaign_id)
    if not camp or not ym_client.api_key():
        return None
    try:
        r = requests.get(
            f"{BASE}/v2/campaigns/{camp}/orders/{order_id}/returns/{return_id}",
            headers=_headers(),
            timeout=25,
        )
        if not r.ok:
            logger.warning("[YM Returns] getReturn %s/%s camp=%s -> %s %s",
                           order_id, return_id, camp, r.status_code, r.text[:200])
            return None
        data = r.json() or {}
        return data.get("result") or data
    except Exception as e:
        logger.warning("[YM Returns] getReturn error: %s", e)
        return None


def fetch_available_decisions(
    return_id: int | str,
    *,
    campaign_id: str | int | None = None,
) -> list[dict]:
    camp, biz = resolve_campaign_id(campaign_id), _biz()
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
    campaign_id: str | int | None = None,
) -> dict[str, Any]:
    camp = resolve_campaign_id(campaign_id)
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
                d["comment"] = comment
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
            logger.warning("[YM Returns] submit %s camp=%s -> %s %s",
                           return_id, camp, r.status_code, r.text[:300])
        return {
            "ok": ok,
            "status_code": r.status_code,
            "text": _friendly_submit_error(r.status_code, r.text),
            "body": body,
        }
    except Exception as e:
        logger.exception("[YM Returns] submit error")
        return {"ok": False, "status_code": 0, "text": str(e)}


def apply_local_status(db: Session, return_id: str | int, decision_type: str) -> None:
    meta = PRIMARY.get(decision_type) or {}
    code = meta.get("local_code") or "REFUNDED"
    apply_status_code(db, return_id, code)


def apply_status_code(db: Session, return_id: str | int, status_code: str) -> None:
    """Обновить локальный статус возврата ЯМ по коду API (без отправки решения)."""
    code = str(status_code or "").strip()
    if not code:
        return
    ru = status_ru(code)
    bind = getattr(db, "bind", None)
    if bind is not None:
        ensure_schema(bind)
    db.execute(text("""
        UPDATE ym_returns
        SET status = :st,
            status_ru = :ru,
            synced_at = NOW()
        WHERE return_id::text = :rid
          AND COALESCE(force_archive, false) = false
    """), {"st": code, "ru": ru, "rid": str(return_id)})
    db.commit()
    if ru == "Одобрено" or code in ("REFUNDED", "REFUND_IN_PROGRESS"):
        try:
            from .chat_bot import expire_form_tokens_for_approved_return
            expire_form_tokens_for_approved_return(
                db, ym_return_id=str(return_id),
            )
            db.commit()
        except Exception:
            pass


def decide_return(
    db: Session,
    return_id: str,
    action: str,
    comment: str = "",
    reason: str = "",
) -> dict[str, Any]:
    ensure_schema(db.bind)
    row = db.execute(text("""
        SELECT return_id::text, order_id::text, status, status_ru, campaign_id
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

    comment, comment_err = normalize_decision_comment(comment or "", decision_type=action)
    if comment_err:
        return {"ok": False, "error": comment_err, "status_code": 400}

    order_id = row["order_id"]
    camp = row.get("campaign_id")
    try:
        db.commit()
    except Exception:
        pass
    live = fetch_return(order_id, return_id, campaign_id=camp)
    item_ids = extract_return_item_ids(live)
    available = fetch_available_decisions(return_id, campaign_id=camp)
    allowed = {str(a.get("decisionType") or "") for a in available}
    if allowed and action not in allowed:
        return {
            "ok": False,
            "error": "ЯМ больше не разрешает это действие по возврату",
            "status_code": 409,
            "actions": map_actions(available),
        }

    if not item_ids:
        return {
            "ok": False,
            "error": "ЯМ не вернул позиции возврата (returnItemId)",
            "status_code": 502,
        }

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
        campaign_id=camp,
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


_RESHIP_CLOSED_STATUSES = {
    "REJECTED", "CANCELLED", "NOT_RETURNED", "FAILED",
    "REFUNDED", "REFUND_IN_PROGRESS",
}
_RESHIP_CLOSED_RU = {"Отказ", "Одобрено", "Отменено"}
RESHIPMENT_DECLINE_COMMENT = (
    "Недостающая деталь уже направлена вам отдельным отправлением. "
    "Возврат закрываем — товар можно оставить у себя."
)


def _ym_return_row(db: Session, return_id: str | None) -> dict | None:
    rid = str(return_id or "").strip()
    if not rid:
        return None
    return db.execute(text("""
        SELECT return_id::text AS return_id, order_id::text AS order_id, status, status_ru
        FROM ym_returns WHERE return_id::text = :rid LIMIT 1
    """), {"rid": rid}).mappings().first()


def find_return_for_reshipment(
    db: Session,
    *,
    return_id: str | None = None,
    order_id: str | None = None,
    ym_chat_id: str | None = None,
) -> dict | None:
    """Ищет возврат ЯМ, который надо закрыть при одобрении доотправки."""
    row = _ym_return_row(db, return_id)
    if row:
        return dict(row)

    chat_id = str(ym_chat_id or "").strip()
    if chat_id:
        chat = db.execute(text("""
            SELECT return_id::text AS return_id, order_id::text AS order_id
            FROM ym_chats WHERE chat_id = :c LIMIT 1
        """), {"c": chat_id}).mappings().first()
        if chat:
            row = _ym_return_row(db, chat.get("return_id"))
            if row:
                return dict(row)
            order_id = order_id or chat.get("order_id")

    oid = str(order_id or "").strip()
    if not oid:
        return None
    row = db.execute(text("""
        SELECT return_id::text AS return_id, order_id::text AS order_id, status, status_ru
        FROM ym_returns
        WHERE order_id::text = :oid
        ORDER BY CASE
            WHEN status IN (
                'WAITING_FOR_DECISION', 'PREMODERATION_DECISION_WAITING',
                'PREMODERATION_SELECT_DELIVERY', 'STARTED_BY_USER'
            ) THEN 0
            ELSE 1
        END, created_at DESC NULLS LAST
        LIMIT 1
    """), {"oid": oid}).mappings().first()
    return dict(row) if row else None


def decline_return_for_reshipment(
    db: Session,
    *,
    return_id: str | None = None,
    order_id: str | None = None,
    ym_chat_id: str | None = None,
) -> dict[str, Any]:
    """Отказать в возврате ЯМ, когда деталь уже одобрена к отправке."""
    row = find_return_for_reshipment(
        db, return_id=return_id, order_id=order_id, ym_chat_id=ym_chat_id,
    )
    if not row or not row.get("return_id"):
        return {"ok": False, "skipped": "no_return"}

    st = str(row.get("status") or "").strip()
    ru = str(row.get("status_ru") or "").strip()
    if st in _RESHIP_CLOSED_STATUSES or ru in _RESHIP_CLOSED_RU:
        return {
            "ok": True,
            "skipped": "already_closed",
            "return_id": row["return_id"],
            "status": st,
        }

    try:
        db.commit()
    except Exception:
        pass
    result = decide_return(
        db,
        str(row["return_id"]),
        "DECLINE_REFUND",
        RESHIPMENT_DECLINE_COMMENT,
    )
    result.setdefault("return_id", row["return_id"])
    result.setdefault("order_id", row.get("order_id"))
    return result


def reason_label(reason_type: str | None, subreason: str | None = None) -> str:
    rt = str(reason_type or "").strip().upper()
    if not rt:
        return "Возврат ЯМ"
    base = REASON_TYPE_RU.get(rt) or "Возврат ЯМ"
    sub = str(subreason or "").strip().upper()
    sub_ru = SUBREASON_TYPE_RU.get(sub, "")
    if sub_ru:
        return f"{base} ({sub_ru})"
    return base
