"""
WB Returns API — решение по заявкам покупателей (claims).

GET  https://returns-api.wildberries.ru/api/v1/claims
PATCH https://returns-api.wildberries.ru/api/v1/claim
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from . import wb_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("wb_claims_api")

# Кнопки для менеджера (основные). Остальные actions WB — шаблоны/служебные.
PRIMARY_ACTIONS = {
    "autorefund1": {
        "label": "Одобрить без возврата товара",
        "kind": "approve_keep",
        "needs_comment": False,
        "default_comment": "Одобряем возврат средств. Товар остаётся у вас.",
        "local_status": "Одобрено",
        "local_ex": "Товар остается у покупателя (заявка одобрена)",
    },
    "approvecc1": {
        "label": "Одобрить без возврата товара",
        "kind": "approve_keep",
        "needs_comment": True,
        "default_comment": "Одобряем возврат средств. Товар остаётся у вас.",
        "local_status": "Одобрено",
        "local_ex": "Товар остается у покупателя (заявка одобрена)",
    },
    "approve2": {
        "label": "Одобрить с возвратом товара",
        "kind": "approve_return",
        "needs_comment": False,
        "default_comment": "",
        "local_status": "Одобрено",
        "local_ex": "Возврат продавцу (заявка одобрена)",
    },
    "approve1": {
        "label": "Одобрить с возвратом товара",
        "kind": "approve_return",
        "needs_comment": False,
        "default_comment": "",
        "local_status": "Одобрено",
        "local_ex": "Возврат продавцу (заявка одобрена)",
    },
    "rejectcustom": {
        "label": "Отказать",
        "kind": "reject",
        "needs_comment": True,
        "default_comment": "",
        "local_status": "Отказ",
        "local_ex": "Товар остается у покупателя (заявка отклонена)",
    },
}

# Шаблонные отказы WB — в «дополнительно», не в основных кнопках
TEMPLATE_REJECT = {
    "reject1": "Отказать (шаблон 1)",
    "reject2": "Отказать (шаблон 2)",
    "reject3": "Отказать (шаблон 3)",
}

# Причины для rejectcustom: менеджер выбирает, комментарий уходит в WB сам.
WB_REJECT_REASONS = [
    {
        "value": "not_confirmed",
        "label": "Проблема с товаром не подтверждена",
        "comment": "По описанию и фото проблема с товаром не подтверждена. Заявку отклоняем.",
    },
    {
        "value": "proper_quality",
        "label": "Товар надлежащего качества / не подошёл",
        "comment": "Товар надлежащего качества. Отказ от покупки по причине «не подошёл» по этой заявке не принимаем.",
    },
    {
        "value": "used",
        "label": "Товар был в использовании",
        "comment": "Товар был в использовании, возврат надлежащего качества невозможен.",
    },
    {
        "value": "appearance",
        "label": "Нарушен товарный вид или комплектация",
        "comment": "Нарушен товарный вид или комплектация. Возврат отклоняем.",
    },
    {
        "value": "nonreturnable",
        "label": "По правилам WB категория не подлежит возврату",
        "comment": "По правилам Wildberries этот товар не подлежит возврату как товар надлежащего качества. Отказаться можно было при получении.",
    },
]


def comment_for_reject_reason(reason: str, fallback: str = "") -> str:
    key = str(reason or "").strip()
    for row in WB_REJECT_REASONS:
        if row["value"] == key:
            return row["comment"]
    return (fallback or "").strip()


def _key() -> str:
    return wb_client.api_key()


def _headers() -> dict:
    return wb_client.auth_headers(_key())


def fetch_claim_from_wb(claim_id: str) -> Optional[dict]:
    """Живая карточка заявки с WB (сначала активные, потом архив)."""
    if not claim_id or not _key():
        return None
    cid = str(claim_id).strip()
    for arch in ("false", "true"):
        try:
            r = wb_client.get_claims_page_raw(
                is_archive=(arch == "true"), limit=1, claim_id=cid, key=_key(), timeout=25,
            )
            if not r.ok:
                logger.warning("[WB Claims] GET %s archive=%s -> %s %s",
                               cid, arch, r.status_code, r.text[:200])
                continue
            claims = (r.json() or {}).get("claims") or []
            if claims:
                return claims[0]
        except Exception as e:
            logger.warning("[WB Claims] GET error %s: %s", cid, e)
    return None


def map_actions(raw_actions: list | None, *, status: str | int | None = None) -> list[dict]:
    """Превращает codes WB в кнопки для UI.

    Для отклонённых заявок WB часто отдаёт actions=['autorefund1','approve2']
    (= «изменить решение»). Иногда actions пустой, но PATCH autorefund1 всё равно
    проходит — тогда подставляем те же кнопки смены решения.
    """
    out = []
    seen_kinds = set()
    raw = [str(a or "").strip() for a in (raw_actions or []) if a]
    st = str(status if status is not None else "").strip()
    is_rejected = st in ("1", "1.0", "Отказ", "Отклонено", "3")

    # Если отказ и WB не прислал actions — всё равно даём сменить на одобрение
    if is_rejected and not raw:
        raw = ["autorefund1", "approve2"]

    for code in raw:
        meta = PRIMARY_ACTIONS.get(code)
        if meta:
            if meta["kind"] in seen_kinds:
                continue
            seen_kinds.add(meta["kind"])
            label = meta["label"]
            if is_rejected and meta["kind"].startswith("approve"):
                label = "Изменить решение: " + label.lower()
            item = {
                "action": code,
                "label": label,
                "kind": meta["kind"],
                "needs_comment": meta["needs_comment"],
                "primary": True,
                "is_change": is_rejected,
            }
            if code == "rejectcustom":
                item["reason_types"] = [
                    {"value": r["value"], "label": r["label"]}
                    for r in WB_REJECT_REASONS
                ]
                item["needs_comment"] = False
            out.append(item)
            continue
        if code in TEMPLATE_REJECT and not is_rejected:
            out.append({
                "action": code,
                "label": TEMPLATE_REJECT[code],
                "kind": "reject_template",
                "needs_comment": False,
                "primary": False,
                "is_change": False,
            })
    return out


def patch_claim(claim_id: str, action: str, comment: str = "") -> dict[str, Any]:
    """
    PATCH /api/v1/claim.
    Возвращает {ok, status_code, text, action, comment}.
    """
    if not _key():
        return {"ok": False, "status_code": 0, "text": "Нет WB_API_KEY", "action": action, "comment": comment}

    meta = PRIMARY_ACTIONS.get(action) or {}
    comment = (comment or "").strip()
    if meta.get("needs_comment") and not comment:
        comment = (meta.get("default_comment") or "").strip()
    if action == "rejectcustom" and len(comment) < 10:
        return {
            "ok": False,
            "status_code": 400,
            "text": "Для отказа нужен комментарий не короче 10 символов",
            "action": action,
            "comment": comment,
        }

    body: dict[str, Any] = {"id": str(claim_id), "action": action}
    # comment только там, где WB его ждёт (docs: rejectcustom / approvecc1)
    if action in ("rejectcustom", "approvecc1"):
        if not comment and action == "approvecc1":
            comment = PRIMARY_ACTIONS["approvecc1"]["default_comment"]
        body["comment"] = (comment or PRIMARY_ACTIONS.get(action, {}).get("default_comment") or ".")[:1000]

    try:
        r = wb_client.patch_claim_raw(
            claim_id, action, comment=body.get("comment", ""), key=_key(), timeout=20,
        )
        ok = r.status_code == 200
        if not ok:
            logger.warning("[WB Claims] PATCH %s action=%s -> %s %s",
                           claim_id, action, r.status_code, r.text[:300])
        return {
            "ok": ok,
            "status_code": r.status_code,
            "text": (r.text or "")[:500],
            "action": action,
            "comment": comment,
            "body": body,
        }
    except Exception as e:
        logger.exception("[WB Claims] PATCH error")
        return {"ok": False, "status_code": 0, "text": str(e), "action": action, "comment": comment}


def apply_local_status(
    db: Session,
    *,
    claim_id: str | None = None,
    srid: str | None = None,
    action: str,
    wb_comment: str = "",
) -> None:
    """Сразу обновляет wb_claims после успешного PATCH (не ждём mini-sync)."""
    meta = PRIMARY_ACTIONS.get(action) or {}
    status = meta.get("local_status") or "Закрыта"
    ex = (wb_comment or meta.get("local_ex") or "")[:500]
    if action.startswith("reject"):
        status = "Отказ"
        if not ex:
            ex = "Товар остается у покупателя (заявка отклонена)"
    params: dict[str, Any] = {"status": status, "ex": ex}
    where = []
    if claim_id:
        where.append("claim_id::text = :cid")
        params["cid"] = str(claim_id)
    if srid:
        where.append("LOWER(srid) = LOWER(:srid)")
        params["srid"] = srid
    if not where:
        return
    db.execute(text(f"""
        UPDATE wb_claims
        SET status = :status,
            status_ex = :ex,
            is_archive = true,
            last_sync = NOW()
        WHERE {" OR ".join(where)}
    """), params)
    db.commit()
    if status == "Одобрено":
        try:
            from .chat_bot import expire_form_tokens_for_approved_return
            expire_form_tokens_for_approved_return(
                db, chat_id=None, srid=srid or None,
            )
            db.commit()
        except Exception as e:
            logger.warning("[WB Claims] expire form tokens: %s", e)


def decide_claim(
    db: Session,
    claim_id: str,
    action: str,
    comment: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """PATCH в WB + локальный UPDATE. Возвращает результат для API."""
    if action == "rejectcustom":
        comment = comment_for_reject_reason(reason, comment)
        if not comment:
            return {"ok": False, "error": "Выберите причину отказа", "status_code": 400}
    row = db.execute(text("""
        SELECT claim_id::text, srid, status, status_ex, is_archive
        FROM wb_claims WHERE claim_id::text = :cid LIMIT 1
    """), {"cid": str(claim_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Претензия не найдена в базе", "status_code": 404}

    allowed = set(PRIMARY_ACTIONS) | set(TEMPLATE_REJECT)
    if action not in allowed:
        return {"ok": False, "error": f"Неизвестное действие: {action}", "status_code": 400}

    # Сверяем с живыми actions WB. Если массив пуст, но это отказ —
    # всё равно пробуем смену решения (в ЛК есть «изменить», API иногда принимает).
    live = fetch_claim_from_wb(claim_id)
    live_actions = list(live.get("actions") or []) if live else []
    live_status = str((live or {}).get("status") if live else row.get("status") or "")
    is_rejected = live_status in ("1", "1.0", "Отказ", "Отклонено", "3") or str(row.get("status") or "") in ("Отказ", "Отклонено")
    if live_actions and action not in live_actions:
        return {
            "ok": False,
            "error": "WB больше не разрешает это действие по заявке",
            "status_code": 409,
            "actions": map_actions(live_actions, status=live_status),
        }
    if not live_actions and not is_rejected and action not in ("rejectcustom",):
        # На рассмотрении без actions — странно; на отказ без actions — ок (смена решения)
        pass

    result = patch_claim(claim_id, action, comment)
    if not result["ok"]:
        # Более понятная ошибка для смены решения
        detail = result["text"] or "WB отклонил запрос"
        if "Action not allowed" in detail or "not allowed" in detail.lower():
            detail = "WB не разрешает изменить решение по этой заявке"
        return {
            "ok": False,
            "error": detail,
            "status_code": result["status_code"] or 502,
        }

    # После успеха подтянем комментарий WB, если есть
    live_after = fetch_claim_from_wb(claim_id)
    wb_comment = ""
    if live_after:
        wb_comment = str(live_after.get("wb_comment") or live_after.get("wbComment") or "")
    apply_local_status(
        db,
        claim_id=claim_id,
        srid=row["srid"],
        action=action,
        wb_comment=wb_comment or result.get("comment") or "",
    )
    meta = PRIMARY_ACTIONS.get(action) or {}
    return {
        "ok": True,
        "claim_id": claim_id,
        "srid": row["srid"],
        "action": action,
        "status": meta.get("local_status") or ("Отказ" if action.startswith("reject") else "Одобрено"),
        "status_ex": wb_comment or meta.get("local_ex") or "",
        "status_label": meta.get("local_status") or ("Отказ" if action.startswith("reject") else "Одобрено"),
    }


def _claim_srid_candidates(
    db: Session,
    *,
    srid: str | None = None,
    wb_chat_id: str | None = None,
    order_number: str | None = None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        s = str(raw or "").strip()
        if not s:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(s)

    _add(srid)
    _add(order_number)
    cid = str(wb_chat_id or "").strip()
    if not cid:
        return out
    pending = db.execute(text(
        "SELECT rid FROM wb_chat_pending WHERE chat_id = :c"
    ), {"c": cid}).mappings().first()
    if pending:
        _add(pending.get("rid"))
    chat = db.execute(text(
        "SELECT order_rid FROM wb_chats WHERE chat_id = :c LIMIT 1"
    ), {"c": cid}).mappings().first()
    if chat:
        _add(chat.get("order_rid"))
    return out


def _find_claim_for_reshipment(
    db: Session,
    *,
    srid: str | None = None,
    wb_chat_id: str | None = None,
    order_number: str | None = None,
) -> dict[str, Any] | None:
    for cand in _claim_srid_candidates(
        db, srid=srid, wb_chat_id=wb_chat_id, order_number=order_number,
    ):
        row = db.execute(text("""
            SELECT claim_id::text AS claim_id, srid, status
            FROM wb_claims WHERE LOWER(srid) = LOWER(:s) LIMIT 1
        """), {"s": cand}).mappings().first()
        if row and row.get("claim_id"):
            return dict(row)
    return None


def reject_return_for_reshipment(
    db: Session,
    *,
    srid: str | None = None,
    wb_chat_id: str | None = None,
    order_number: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Отказать в возврате WB: клиент уже заполнил / получил доотправку."""
    row = _find_claim_for_reshipment(
        db, srid=srid, wb_chat_id=wb_chat_id, order_number=order_number,
    )
    if not row:
        return {"ok": False, "skipped": "no_claim"}
    status = str(row.get("status") or "").strip()
    if status in ("Отказ", "Отклонено"):
        return {
            "ok": True,
            "skipped": "already_rejected",
            "claim_id": row["claim_id"],
            "srid": row["srid"],
            "status": status,
        }
    text_comment = (comment or (
        "По вашей заявке оформлена доотправка недостающей детали. "
        "Возврат товара по этой претензии не требуется."
    )).strip()
    resp = patch_claim(row["claim_id"], "rejectcustom", text_comment)
    if not resp.get("ok"):
        return {
            "ok": False,
            "error": resp.get("text") or "WB отклонил запрос",
            "status_code": resp.get("status_code") or 502,
            "claim_id": row["claim_id"],
            "srid": row["srid"],
        }
    apply_local_status(
        db,
        claim_id=row["claim_id"],
        srid=row["srid"],
        action="rejectcustom",
        wb_comment=text_comment,
    )
    return {
        "ok": True,
        "claim_id": row["claim_id"],
        "srid": row["srid"],
        "action": "rejectcustom",
        "status": "Отказ",
    }


def approve_return_for_reshipment(
    db: Session,
    *,
    srid: str | None = None,
    wb_chat_id: str | None = None,
    order_number: str | None = None,
) -> dict[str, Any]:
    """Одобрить связанную заявку на возврат WB при отклонении доотправки."""
    row = _find_claim_for_reshipment(
        db, srid=srid, wb_chat_id=wb_chat_id, order_number=order_number,
    )
    if not row:
        return {"ok": False, "skipped": "no_claim"}

    status = str(row.get("status") or "").strip()
    if status == "Одобрено":
        return {
            "ok": True,
            "skipped": "already_approved",
            "claim_id": row["claim_id"],
            "srid": row["srid"],
            "status": status,
        }

    live = fetch_claim_from_wb(row["claim_id"])
    live_actions = list((live or {}).get("actions") or [])
    chosen = None
    for action in ("approve2", "approve1", "autorefund1", "approvecc1"):
        if action in live_actions:
            chosen = action
            break
    if not chosen:
        # Пустой список actions: WB иногда всё равно принимает смену решения
        chosen = "approve2"

    result = decide_claim(db, row["claim_id"], chosen)
    result.setdefault("srid", row["srid"])
    result.setdefault("claim_id", row["claim_id"])
    return result
