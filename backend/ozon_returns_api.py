"""
Ozon — возвраты FBO (+ probe rFBS).

Источник данных: POST /v1/returns/list.
Seller API не отдаёт комментарий покупателя, фото и историю заявки
(это только в ЛК). Отдаёт: тип, статус, склады, штрихкод, цену, причину.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ozon_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ozon_returns_api")

BASE = ozon_client.BASE

# Вкладки как в ЛК Ozon «Возвраты и отмены» + отдельная очередь согласования
TAB_STATUSES = {
    "transit": {
        "WaitingShipment",
        "MovingToOzon",
        "ArrivedAtReturnPlace",
        "MovingToSeller",
    },
    # Заявки, где продавец должен ответить (лимит срока — только в ЛК, API не отдаёт)
    "approval": {
        "OnSellerApproval",
    },
    "received": {
        "ReturnedToOzon",
        "ReceivedBySeller",
    },
    "utilization": {
        "Utilizing",
        "Utilized",
        "WriteOff",
    },
    # «Решённые» — закрытые / решение продавца принято
    "resolved": {
        "Cancelled",
        "Approved",
        "Rejected",
    },
}

# Старые «активные» (совместимость)
ACTIVE_SYS = TAB_STATUSES["transit"] | TAB_STATUSES["approval"] | {"OnSellerApproval"}

TYPE_LABELS = {
    "ClientReturn": "Возврат покупателя",
    "Cancellation": "Невыкуп / отмена",
}

ACTION_KIND = {
    1: ("approve_keep", "Одобрить без возврата товара"),
    2: ("approve_return", "Одобрить с возвратом товара"),
    3: ("reject", "Отказать"),
    -1: ("reject", "Отказать"),
    -10: ("reject", "Отказать"),
}

_SCHEMA_READY = False


def _headers() -> dict:
    return ozon_client.headers()


def has_credentials() -> bool:
    return bool(ozon_client.client_id() and ozon_client.api_key())


def ensure_schema(conn_or_engine) -> None:
    """Добавляет колонки детализации, если их ещё нет."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    ddl = [
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS return_type text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS schema_code text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS barcode text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS place_name text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS target_place_name text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS price double precision",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS status_changed_at timestamptz",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS source_id bigint",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS compensation_status text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS buyer_comment text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS photos text",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS cabinet_synced_at timestamptz",
    ]

    def _run(conn):
        for q in ddl:
            conn.execute(text(q))

    # Session / Engine / Connection
    if hasattr(conn_or_engine, "begin") and not hasattr(conn_or_engine, "info"):
        # Engine
        with conn_or_engine.begin() as conn:
            _run(conn)
    elif hasattr(conn_or_engine, "bind"):
        # Session
        with conn_or_engine.bind.begin() as conn:
            _run(conn)
    else:
        _run(conn_or_engine)
        if hasattr(conn_or_engine, "commit"):
            try:
                conn_or_engine.commit()
            except Exception:
                pass
    _SCHEMA_READY = True


def type_label(return_type: str | None) -> str:
    t = str(return_type or "").strip()
    return TYPE_LABELS.get(t, t or "Возврат Ozon")


def is_active_row(is_opened: bool | None, status_sys: str | None) -> bool:
    if is_opened:
        return True
    return str(status_sys or "") in ACTIVE_SYS


def tab_for_status(status_sys: str | None) -> str:
    s = str(status_sys or "")
    for tab, statuses in TAB_STATUSES.items():
        if s in statuses:
            return tab
    return "all"


def cabinet_url(return_id: str | int) -> str:
    return f"https://seller.ozon.ru/app/returns/details/common/{return_id}"


def parse_api_item(r: dict) -> dict[str, Any]:
    """Нормализует элемент /v1/returns/list в плоские поля БД/UI."""
    product = r.get("product") or {}
    logistic = r.get("logistic") or {}
    visual = r.get("visual") or {}
    vstatus = visual.get("status") or {}
    add_info = r.get("additional_info") or {}
    place = r.get("place") or {}
    target = r.get("target_place") or {}
    price_obj = product.get("price") or {}
    status_sys = vstatus.get("sys_name") or ""
    status_ru = vstatus.get("display_name") or status_sys
    comp = r.get("compensation_status")
    if isinstance(comp, dict):
        cstat = comp.get("status") if isinstance(comp.get("status"), dict) else {}
        comp = cstat.get("sys_name") or cstat.get("display_name") or None
    elif comp is not None:
        comp = str(comp)
    return {
        "return_id": r.get("id"),
        "order_id": r.get("order_id"),
        "order_number": r.get("order_number"),
        "posting_number": r.get("posting_number"),
        "supplier_article": (product.get("offer_id") or "").strip() or None,
        "product_name": product.get("name"),
        "ozon_sku": product.get("sku"),
        "return_reason": r.get("return_reason_name"),
        "status_sys": status_sys,
        "status_ru": status_ru,
        "is_opened": add_info.get("is_opened"),
        "return_date": logistic.get("return_date"),
        "return_type": r.get("type"),
        "schema_code": r.get("schema"),
        "barcode": (logistic.get("barcode") or "").strip() or None,
        "place_name": place.get("name"),
        "target_place_name": target.get("name"),
        "price": (price_obj.get("price") if isinstance(price_obj, dict) else None),
        "status_changed_at": visual.get("change_moment"),
        "source_id": r.get("source_id"),
        "compensation_status": comp,
    }


def fetch_return_live(
    return_id: int | str,
    *,
    posting_number: str | None = None,
) -> Optional[dict]:
    """Живая карточка из Seller API. return_ids-фильтр у Ozon глючный — обходим."""
    if not has_credentials():
        return None
    rid = int(return_id)
    bodies = []
    if posting_number:
        bodies.append({
            "filter": {"posting_numbers": [str(posting_number)]},
            "limit": 10,
            "last_id": 0,
        })
    bodies.append({
        "filter": {},
        "limit": 20,
        "last_id": max(0, rid - 1),
    })
    for body in bodies:
        try:
            r = requests.post(
                f"{BASE}/v1/returns/list",
                headers=_headers(),
                json=body,
                timeout=30,
            )
            if not r.ok:
                logger.info("[Ozon Returns] list live -> %s %s", r.status_code, r.text[:200])
                continue
            for item in (r.json() or {}).get("returns") or []:
                if int(item.get("id") or 0) == rid:
                    return item
        except Exception as e:
            logger.warning("[Ozon Returns] live fetch error: %s", e)
    return None


def upsert_parsed(db: Session, parsed: dict) -> None:
    ensure_schema(db.bind)
    db.execute(text("""
        INSERT INTO ozon_returns (
            return_id, order_id, order_number, posting_number,
            supplier_article, product_name, ozon_sku,
            return_reason, status_sys, status_ru, is_opened, return_date,
            return_type, schema_code, barcode, place_name, target_place_name,
            price, status_changed_at, source_id, compensation_status, synced_at
        ) VALUES (
            :return_id, :order_id, :order_number, :posting_number,
            :supplier_article, :product_name, :ozon_sku,
            :return_reason, :status_sys, :status_ru, :is_opened, :return_date,
            :return_type, :schema_code, :barcode, :place_name, :target_place_name,
            :price, :status_changed_at, :source_id, :compensation_status, NOW()
        )
        ON CONFLICT (return_id) DO UPDATE SET
            order_id = EXCLUDED.order_id,
            order_number = EXCLUDED.order_number,
            posting_number = EXCLUDED.posting_number,
            supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_returns.supplier_article),
            product_name = COALESCE(EXCLUDED.product_name, ozon_returns.product_name),
            ozon_sku = COALESCE(EXCLUDED.ozon_sku, ozon_returns.ozon_sku),
            return_reason = COALESCE(EXCLUDED.return_reason, ozon_returns.return_reason),
            status_sys = EXCLUDED.status_sys,
            status_ru = EXCLUDED.status_ru,
            is_opened = EXCLUDED.is_opened,
            return_date = COALESCE(EXCLUDED.return_date, ozon_returns.return_date),
            return_type = EXCLUDED.return_type,
            schema_code = EXCLUDED.schema_code,
            barcode = COALESCE(EXCLUDED.barcode, ozon_returns.barcode),
            place_name = COALESCE(EXCLUDED.place_name, ozon_returns.place_name),
            target_place_name = COALESCE(EXCLUDED.target_place_name, ozon_returns.target_place_name),
            price = COALESCE(EXCLUDED.price, ozon_returns.price),
            status_changed_at = COALESCE(EXCLUDED.status_changed_at, ozon_returns.status_changed_at),
            source_id = COALESCE(EXCLUDED.source_id, ozon_returns.source_id),
            compensation_status = EXCLUDED.compensation_status,
            synced_at = NOW()
    """), parsed)
    db.commit()


def list_rfbs(limit: int = 5) -> list[dict]:
    if not has_credentials():
        return []
    try:
        r = requests.post(
            f"{BASE}/v2/returns/rfbs/list",
            headers=_headers(),
            json={"filter": {}, "limit": limit},
            timeout=25,
        )
        if not r.ok:
            return []
        data = r.json() or {}
        return list(data.get("returns") or data.get("result") or [])
    except Exception as e:
        logger.warning("[Ozon Returns] rfbs/list error: %s", e)
        return []


def fetch_rfbs(return_id: int | str) -> Optional[dict]:
    if not has_credentials():
        return None
    try:
        r = requests.post(
            f"{BASE}/v2/returns/rfbs/get",
            headers=_headers(),
            json={"return_id": int(return_id)},
            timeout=25,
        )
        if not r.ok:
            return None
        data = r.json() or {}
        return data.get("returns") or data.get("result") or data
    except Exception as e:
        logger.warning("[Ozon Returns] rfbs/get error: %s", e)
        return None


def map_actions(raw_actions: list | None) -> list[dict]:
    out = []
    seen = set()
    for a in raw_actions or []:
        if isinstance(a, dict):
            aid = a.get("id") if a.get("id") is not None else a.get("action_id")
            name = str(a.get("name") or a.get("action") or "").lower()
            label_api = str(a.get("name") or a.get("description") or "").strip()
        else:
            aid, name, label_api = a, str(a).lower(), str(a)
        kind, label = None, None
        try:
            aid_int = int(aid) if aid is not None else None
        except (TypeError, ValueError):
            aid_int = None
        if aid_int in ACTION_KIND:
            kind, label = ACTION_KIND[aid_int]
        elif "reject" in name or "отказ" in name:
            kind, label = "reject", "Отказать"
        elif "compensat" in name or "без возврат" in name:
            kind, label = "approve_keep", "Одобрить без возврата товара"
        elif "verif" in name or "approv" in name or "money" in name:
            kind, label = "approve_return", "Одобрить с возвратом товара"
        if not kind or kind in seen:
            continue
        seen.add(kind)
        out.append({
            "action": str(aid if aid is not None else kind),
            "label": label_api or label,
            "kind": kind,
            "needs_comment": kind == "reject",
            "primary": True,
            "is_change": False,
        })
    return out


def set_action(
    return_id: int | str,
    action_id: int | str,
    *,
    comment: str = "",
    rejection_reason_id: int | None = None,
) -> dict[str, Any]:
    if not has_credentials():
        return {"ok": False, "status_code": 0, "text": "Нет OZON ключей"}
    body: dict[str, Any] = {"return_id": int(return_id), "id": int(action_id)}
    if comment:
        body["comment"] = comment[:1000]
    if rejection_reason_id is not None:
        body["rejection_reason_id"] = int(rejection_reason_id)
    try:
        r = requests.post(
            f"{BASE}/v1/returns/rfbs/action/set",
            headers=_headers(),
            json=body,
            timeout=30,
        )
        return {"ok": r.status_code == 200, "status_code": r.status_code, "text": (r.text or "")[:500]}
    except Exception as e:
        return {"ok": False, "status_code": 0, "text": str(e)}


def decide_return(db: Session, return_id: str, action: str, comment: str = "") -> dict[str, Any]:
    row = db.execute(text("""
        SELECT return_id::text, status_sys, status_ru
        FROM ozon_returns WHERE return_id::text = :rid LIMIT 1
    """), {"rid": str(return_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Возврат не найден в базе", "status_code": 404}

    live = fetch_rfbs(return_id)
    if not live:
        return {
            "ok": False,
            "error": "По FBO решение принимает Ozon — из API продавца действие недоступно",
            "status_code": 409,
        }
    raw = live.get("available_actions") or live.get("availableActions") or []
    actions = map_actions(raw)
    if str(action) not in {a["action"] for a in actions}:
        return {"ok": False, "error": "Ozon не разрешает это действие", "status_code": 409, "actions": actions}

    result = set_action(return_id, action, comment=comment)
    if not result["ok"]:
        return {"ok": False, "error": result.get("text") or "Ozon отклонил запрос",
                "status_code": result.get("status_code") or 502}

    db.execute(text("""
        UPDATE ozon_returns SET synced_at = NOW() WHERE return_id::text = :rid
    """), {"rid": str(return_id)})
    db.commit()
    return {
        "ok": True,
        "claim_id": str(return_id),
        "mp": "ozon",
        "action": action,
        "status_label": row.get("status_ru") or "Обработан",
    }


def decision_capability(return_id: str | int, *, status_sys: str | None = None) -> dict[str, Any]:
    live = fetch_rfbs(return_id)
    if not live:
        note = (
            "FBO: решение и спор — в кабинете Ozon. "
            "Seller API не даёт одобрить/отказать и не отдаёт фото/комментарий покупателя."
        )
        if str(status_sys or "") == "OnSellerApproval":
            note = (
                "На согласовании: ответьте в ЛК Ozon (там же лимит срока ответа, фото и комментарий). "
                "Seller API не отдаёт дедлайн и не позволяет одобрить/отказать."
            )
        return {
            "actions": [],
            "can_decide": False,
            "rfbs": False,
            "needs_cabinet": str(status_sys or "") == "OnSellerApproval",
            "decision_note": note,
        }
    raw = live.get("available_actions") or live.get("availableActions") or []
    actions = map_actions(raw)
    if not actions:
        return {
            "actions": [],
            "can_decide": False,
            "rfbs": True,
            "needs_cabinet": False,
            "decision_note": "rFBS-заявка есть, но сейчас нет доступных действий.",
        }
    return {
        "actions": actions,
        "can_decide": True,
        "rfbs": True,
        "needs_cabinet": False,
        "decision_note": "",
    }


_RESHIP_CLOSED_SYS = {
    "Rejected", "Cancelled", "Approved", "CancelledByClient", "CancelledByOzon",
    "MoneyReturned", "Compensated",
}
_RESHIP_CLOSED_RU = {"Отказ", "Отказано", "Одобрено", "Отменено"}
RESHIPMENT_DECLINE_COMMENT = (
    "Недостающая деталь уже направлена вам отдельным отправлением. "
    "Возврат закрываем — товар можно оставить у себя."
)


def _ozon_return_row(db: Session, return_id: str | None) -> dict | None:
    rid = str(return_id or "").strip()
    if not rid:
        return None
    return db.execute(text("""
        SELECT return_id::text AS return_id,
               order_id::text AS order_id,
               order_number, posting_number,
               status_sys, status_ru, is_opened
        FROM ozon_returns WHERE return_id::text = :rid LIMIT 1
    """), {"rid": rid}).mappings().first()


def find_return_for_reshipment(
    db: Session,
    *,
    return_id: str | None = None,
    order_id: str | None = None,
    ozon_chat_id: str | None = None,
) -> dict | None:
    """Ищет возврат Ozon, который надо закрыть при одобрении доотправки."""
    row = _ozon_return_row(db, return_id)
    if row:
        return dict(row)

    chat_id = str(ozon_chat_id or "").strip()
    if chat_id:
        chat = db.execute(text("""
            SELECT order_number FROM ozon_chats WHERE chat_id = :c LIMIT 1
        """), {"c": chat_id}).mappings().first()
        if chat and chat.get("order_number"):
            order_id = order_id or chat.get("order_number")

    oid = str(order_id or "").strip()
    if not oid:
        return None
    row = db.execute(text("""
        SELECT return_id::text AS return_id,
               order_id::text AS order_id,
               order_number, posting_number,
               status_sys, status_ru, is_opened
        FROM ozon_returns
        WHERE posting_number = :oid
           OR order_number = :oid
           OR posting_number LIKE :oid || '-%'
           OR order_number LIKE :oid || '-%'
           OR order_id::text = :oid
        ORDER BY CASE
            WHEN status_sys = 'OnSellerApproval' THEN 0
            WHEN COALESCE(is_opened, FALSE) IS TRUE THEN 1
            ELSE 2
        END, return_date DESC NULLS LAST
        LIMIT 1
    """), {"oid": oid}).mappings().first()
    return dict(row) if row else None


def decline_return_for_reshipment(
    db: Session,
    *,
    return_id: str | None = None,
    order_id: str | None = None,
    ozon_chat_id: str | None = None,
) -> dict[str, Any]:
    """Отказать в возврате Ozon, когда деталь уже одобрена к отправке."""
    row = find_return_for_reshipment(
        db, return_id=return_id, order_id=order_id, ozon_chat_id=ozon_chat_id,
    )
    if not row or not row.get("return_id"):
        return {"ok": False, "skipped": "no_return"}

    st = str(row.get("status_sys") or "").strip()
    ru = str(row.get("status_ru") or "").strip()
    if st in _RESHIP_CLOSED_SYS or ru in _RESHIP_CLOSED_RU:
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
    cap = decision_capability(row["return_id"], status_sys=st)
    reject = next(
        (a for a in (cap.get("actions") or []) if a.get("kind") == "reject"),
        None,
    )
    if not reject:
        return {
            "ok": False,
            "skipped": "no_reject_action",
            "return_id": row["return_id"],
            "status": st,
        }
    result = decide_return(
        db,
        str(row["return_id"]),
        reject["action"],
        RESHIPMENT_DECLINE_COMMENT,
    )
    result.setdefault("return_id", row["return_id"])
    result.setdefault("order_id", row.get("order_number") or row.get("order_id"))
    return result

