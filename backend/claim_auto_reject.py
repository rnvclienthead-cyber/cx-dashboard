"""Автоотказы WB по правилу CX: ссылка на доотправку или предложение без ответа > 3 суток."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger("claim_auto_reject")

THREE_DAYS_SEC = 3 * 24 * 60 * 60

COMMENT_LINK = (
    "По вашей заявке уже направлена доотправка недостающей детали. "
    "Возврат товара по этой претензии не требуется."
)
COMMENT_OFFERED = (
    "Готовы отправить недостающую деталь. Напишите, пожалуйста, в чат — "
    "оформим доставку. Возврат товара по этой заявке отклоняем."
)

_FORM_REJECT_RETURN = frozenset({
    "approved", "packed", "shipped",
    "in_transit", "ready_for_pickup", "delivered",
})


def form_claim_action(form_status: str) -> str | None:
    """Что делать с возвратом WB, если анкета доотправки уже есть.

    Пока заявка только новая — возврат не трогаем: КС ещё может отказать
    в доотправке, и клиент не должен заранее читать «деталь уже в пути».
    """
    st = str(form_status or "").strip().lower()
    if st == "rejected":
        return "approve"
    if st in _FORM_REJECT_RETURN:
        return "reject"
    return None
_OFFER_HINT_RE = re.compile(
    r"(отправ\w+|вышл\w+|выслать).{0,60}детал|"
    r"недостающ\w+.{0,30}детал|"
    r"поврежденн\w+.{0,20}детал|"
    r"доотправ|"
    r"заполните\s+форму",
    re.I | re.S,
)
_DENY_HINT_RE = re.compile(
    r"не можем выслать|выслать не можем|оформите возврат|"
    r"нет возможности|невозвратн|деталью?\s+выслать\s+не",
    re.I,
)
_CLIENT_SENDERS = frozenset({"client", "buyer", "customer", "user"})


def effective_kind(kind: str, seller_texts: list[str] | None = None) -> str:
    """link / offered — с учётом формулировок «отправим деталь», не только бейджа."""
    k = str(kind or "").strip().lower()
    if k == "link":
        return "link"
    if k == "offered":
        return "offered"
    blob = "\n".join(str(t or "") for t in (seller_texts or []))
    if _DENY_HINT_RE.search(blob):
        return k or "none"
    if _OFFER_HINT_RE.search(blob):
        return "offered"
    return k or "none"


def should_auto_reject(
    kind: str,
    *,
    age_sec: float,
    client_replied: bool,
) -> str | None:
    """Почему отказываем: offered_stale | None.

    Ссылку на форму больше не считаем согласием на отказ в возврате:
    доотправку ещё может отклонить менеджер.
    """
    k = str(kind or "").strip().lower()
    if k == "offered" and not client_replied and float(age_sec or 0) > THREE_DAYS_SEC:
        return "offered_stale"
    return None


def reject_comment(reason: str) -> str:
    if reason == "link":
        return COMMENT_LINK
    return COMMENT_OFFERED


def is_auto_reject_comment(text: str) -> bool:
    """Наш автотекст в возврате WB — не комментарий площадки."""
    t = str(text or "").strip()
    if not t:
        return False
    if t in (COMMENT_LINK, COMMENT_OFFERED):
        return True
    return t.startswith("По вашей заявке уже направлена доотправка")


def _age_sec(claim_date) -> float:
    if not claim_date:
        return 0.0
    if hasattr(claim_date, "timestamp"):
        dt = claim_date
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, datetime.now(timezone.utc).timestamp() - dt.timestamp())
    raw = str(claim_date).strip()
    if not raw:
        return 0.0
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s[:32])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, datetime.now(timezone.utc).timestamp() - dt.timestamp())
    except Exception:
        return 0.0


def _client_replied(conn, chat_id: str) -> bool:
    cid = str(chat_id or "").strip()
    if not cid:
        return False
    from sqlalchemy import text
    row = conn.execute(text("""
        SELECT sender FROM wb_chat_messages
        WHERE chat_id = :cid
          AND lower(COALESCE(sender, '')) NOT IN ('wb', 'system', '')
        ORDER BY add_ts DESC NULLS LAST, id DESC
        LIMIT 1
    """), {"cid": cid}).first()
    if row and row[0]:
        return str(row[0]).strip().lower() in _CLIENT_SENDERS
    row = conn.execute(text("""
        SELECT last_sender FROM wb_chats WHERE chat_id = :cid LIMIT 1
    """), {"cid": cid}).first()
    if row and row[0]:
        return str(row[0]).strip().lower() in _CLIENT_SENDERS
    return False


def collect_reject_targets(db: Session, *, limit: int = 200) -> list[dict]:
    from .routers import cx_responses

    per_page = max(25, min(int(limit or 200), 800))
    data = cx_responses._list_wb_claims(
        db,
        active=True,
        status="На рассмотрении",
        claim_type="",
        q="",
        page=1,
        per_page=per_page,
        sort="old",
        offer="",
    )
    items = data.get("items") or []
    out: list[dict] = []
    with db.bind.connect() as conn:
        for it in items:
            raw_kind = cx_responses._offer_kind_of_item(it)
            req = it.get("reshipment") if isinstance(it.get("reshipment"), dict) else None
            form_action = form_claim_action((req or {}).get("status") or "")
            if form_action == "approve":
                continue
            texts: list[str] = []
            replied = False
            try:
                found = cx_responses._find_claim_chat(conn, "wb", it) or {}
                chat_id = str(found.get("chat_id") or "")
                if form_action != "reject":
                    replied = _client_replied(conn, chat_id)
                    texts = cx_responses._seller_texts_for_chat(conn, "wb", chat_id)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if form_action == "reject":
                kind = "link"
                reason = "link"
            else:
                kind = effective_kind(raw_kind, texts)
                reason = should_auto_reject(
                    kind,
                    age_sec=_age_sec(it.get("claim_date")),
                    client_replied=replied,
                )
            if reason == "link" and form_action != "reject":
                continue
            if not reason:
                continue
            out.append({
                "claim_id": it.get("claim_id"),
                "srid": it.get("srid"),
                "article": it.get("supplier_article"),
                "reason": reason,
                "kind": kind,
                "comment": reject_comment(reason),
            })
            if len(out) >= limit:
                break
    return out


def reject_matching_wb_claims(
    db: Session,
    *,
    limit: int = 40,
    dry_run: bool = False,
    sleep_s: float = 0.4,
) -> dict:
    """Отказы: ссылка на доотправку или предложение детали без ответа > 3 суток."""
    from . import wb_claims_api

    targets = collect_reject_targets(db, limit=limit)
    result = {
        "ok": True,
        "dry_run": dry_run,
        "targets": len(targets),
        "rejected": 0,
        "skipped": 0,
        "errors": [],
        "items": [],
    }
    for t in targets:
        row = {
            "claim_id": t["claim_id"],
            "reason": t["reason"],
            "article": t.get("article") or "",
        }
        if dry_run:
            row["status"] = "dry_run"
            result["items"].append(row)
            continue
        resp = None
        for attempt in range(3):
            try:
                resp = wb_claims_api.patch_claim(
                    t["claim_id"], "rejectcustom", t["comment"],
                )
            except Exception as e:
                logger.warning("[auto-reject] %s: %s", t["claim_id"], e)
                try:
                    db.rollback()
                except Exception:
                    pass
                row["status"] = "error"
                row["error"] = str(e)[:200]
                result["errors"].append(row)
                result["skipped"] += 1
                resp = None
                break
            code = int(resp.get("status_code") or 0)
            if code == 429 and attempt < 2:
                time.sleep(8 * (attempt + 1))
                continue
            break
        if resp is None:
            continue
        if resp.get("ok"):
            try:
                wb_claims_api.apply_local_status(
                    db,
                    claim_id=t["claim_id"],
                    srid=t.get("srid"),
                    action="rejectcustom",
                    wb_comment=t["comment"],
                )
            except Exception as e:
                logger.warning("[auto-reject] local status %s: %s", t["claim_id"], e)
            row["status"] = "rejected"
            result["rejected"] += 1
        else:
            row["status"] = "skipped"
            row["error"] = str(resp.get("text") or resp.get("error") or resp)[:200]
            result["skipped"] += 1
            result["errors"].append(row)
        result["items"].append(row)
        if sleep_s:
            time.sleep(sleep_s)
    logger.info(
        "[auto-reject] targets=%s rejected=%s skipped=%s dry=%s",
        result["targets"], result["rejected"], result["skipped"], dry_run,
    )
    return result
