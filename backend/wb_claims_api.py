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
            out.append({
                "action": code,
                "label": label,
                "kind": meta["kind"],
                "needs_comment": meta["needs_comment"],
                "primary": True,
                "is_change": is_rejected,
            })
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


def decide_claim(
    db: Session,
    claim_id: str,
    action: str,
    comment: str = "",
) -> dict[str, Any]:
    """PATCH в WB + локальный UPDATE. Возвращает результат для API."""
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
