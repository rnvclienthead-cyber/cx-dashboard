"""
Синхронизация чатов Яндекс Маркета (Partner API).

Polling: WAITING_FOR_PARTNER + недавние WAITING_FOR_CUSTOMER.
Отправка сообщений требует scope communication / all-methods у токена.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import text

from . import ym_client
from .database import db_session

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ym_chat_sync")

SYNC_INTERVAL = 45  # секунд
HISTORY_LIMIT_CHATS = 30

# DIRECT-чат: «у меня вопрос по товару {название}…»
_DIRECT_PRODUCT_RE = re.compile(
    r"вопрос\s+по\s+товару\s+(.+?)(?:\.|\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_DIM_RE = re.compile(
    r"(\d{2,3})\s*[хx×]\s*(\d{2,3})\s*[хx×]\s*(\d{2,3})",
    re.IGNORECASE,
)
# (ключ в названии каталога, варианты в тексте клиента)
_COLOR_TOKENS = (
    ("бел", ("бел", "white")),
    ("черн", ("черн", "black")),
    ("сер", ("сер", "grey", "gray")),
    ("зелен", ("зелен", "green")),
    ("син", ("син", "blue")),
    ("корич", ("корич", "brown")),
    ("роз", ("роз", "pink")),
)


def _key() -> str:
    return ym_client.api_key()


def _business_id() -> str:
    return ym_client.business_id()


def _hdr() -> dict:
    return ym_client.headers(_key())


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _list_chats(statuses: list[str], *, limit: int = 50) -> list[dict]:
    return ym_client.list_chats(statuses, limit=limit, biz=_business_id(), key=_key())


def _fetch_history(chat_id: int | str) -> dict:
    return ym_client.fetch_chat_history(chat_id, biz=_business_id(), key=_key())


def _context_fields(ctx: dict | None) -> dict:
    ctx = ctx or {}
    ctype = str(ctx.get("type") or "")
    customer = ctx.get("customer") or {}
    name = customer.get("name") or ""
    public_id = customer.get("publicId") or ""
    order_id = None
    return_id = None
    order = ctx.get("order") or {}
    if isinstance(order, dict):
        order_id = order.get("orderId") or order.get("id")
    ret = ctx.get("return") or {}
    if isinstance(ret, dict):
        return_id = ret.get("returnId") or ret.get("id")
    # иногда orderId на верхнем уровне контекста
    order_id = order_id or ctx.get("orderId")
    return_id = return_id or ctx.get("returnId")
    return {
        "context_type": ctype,
        "customer_name": name,
        "customer_public_id": public_id,
        "order_id": str(order_id) if order_id is not None else None,
        "return_id": str(return_id) if return_id is not None else None,
    }


_CUSTOMER_SENDERS = frozenset({"customer", "buyer", "client", "user"})
_SELLER_SENDERS = frozenset({"seller", "partner", "shop", "merchant"})


def _map_sender(sender: str) -> str:
    s = str(sender or "").upper()
    if s == "PARTNER":
        return "seller"
    if s == "CUSTOMER":
        return "customer"
    if s in ("MARKET", "SUPPORT"):
        return "system"
    return s.lower() or "unknown"


def _human_role(sender: str) -> str | None:
    s = str(sender or "").lower()
    if s in _CUSTOMER_SENDERS:
        return "customer"
    if s in _SELLER_SENDERS:
        return "seller"
    return None


def last_human_message(messages: list[dict]) -> dict | None:
    """Самое позднее сообщение покупателя или продавца, без MARKET/system."""
    best = None
    best_key = None
    for m in messages:
        role = _human_role(m.get("sender_type") or m.get("sender"))
        if not role:
            continue
        created = m.get("created_at")
        mid = str(m.get("message_id") or m.get("id") or "")
        key = (created is not None, created or datetime.min, mid)
        if best_key is None or key > best_key:
            best_key = key
            best = {**m, "role": role}
    return best


def needs_answer_from_messages(messages: list[dict]) -> bool:
    last = last_human_message(messages)
    return bool(last and last["role"] == "customer")


def _apply_human_state(conn, chat_ids: list[str] | None = None, *, only_clear_stale: bool = False) -> list[str]:
    """needs_answer / last_sender по последнему человеческому сообщению, включая local-."""
    params: dict = {}
    id_filter = ""
    if chat_ids is not None:
        ids = [str(x).strip() for x in chat_ids if str(x).strip()]
        if not ids:
            return []
        params["ids"] = ids
        id_filter = "AND c.chat_id = ANY(:ids)"
    stale_filter = ""
    if only_clear_stale:
        stale_filter = "AND c.needs_answer = true"
        customer_branch = "c.needs_answer"
    else:
        customer_branch = "true"
    rows = conn.execute(text(f"""
        WITH last_human AS (
          SELECT DISTINCT ON (m.chat_id)
            m.chat_id, m.sender_type, m.text, m.created_at
          FROM ym_chat_messages m
          WHERE lower(COALESCE(m.sender_type, '')) IN (
                'customer', 'buyer', 'client', 'user',
                'seller', 'partner', 'shop', 'merchant'
              )
          ORDER BY m.chat_id, m.created_at DESC NULLS LAST, m.message_id DESC
        )
        UPDATE ym_chats c SET
            last_sender = CASE
              WHEN lower(h.sender_type) IN ('seller', 'partner', 'shop', 'merchant')
                THEN 'seller'
              ELSE 'customer'
            END,
            last_message = COALESCE(NULLIF(left(COALESCE(h.text, ''), 500), ''), c.last_message),
            needs_answer = CASE
              WHEN lower(h.sender_type) IN ('seller', 'partner', 'shop', 'merchant')
                THEN false
              ELSE {customer_branch}
            END,
            waiting_since = CASE
              WHEN lower(h.sender_type) IN ('seller', 'partner', 'shop', 'merchant') THEN NULL
              WHEN {customer_branch} THEN COALESCE(c.waiting_since, h.created_at, now())
              ELSE c.waiting_since
            END,
            updated_at = now()
        FROM last_human h
        WHERE c.chat_id = h.chat_id
          {id_filter}
          {stale_filter}
        RETURNING c.chat_id, c.needs_answer
    """), params).mappings().all()
    waiting_ids = [str(r["chat_id"]) for r in rows if r.get("needs_answer")]
    if waiting_ids and not only_clear_stale:
        conn.execute(text("""
            DELETE FROM cx_dismissed_items
            WHERE mp = 'ym' AND item_type = 'chat' AND item_id = ANY(:ids)
        """), {"ids": waiting_ids})
    return [str(r["chat_id"]) for r in rows]


def _ensure_chat_columns(conn) -> None:
    conn.execute(text(
        "ALTER TABLE ym_chats ADD COLUMN IF NOT EXISTS context_article TEXT"
    ))


def _delete_local_seller_dupes(conn, chat_id: str, api_text: str) -> None:
    """Убрать local-/bot- дубль после синка реального seller-сообщения.

    ЯМ дописывает к тексту предупреждение про ссылки — поэтому кроме точного
    совпадения ловим и префикс: локальный текст = начало API-текста.
    """
    if not api_text:
        return
    conn.execute(text("""
        DELETE FROM ym_chat_messages
        WHERE chat_id = :cid
          AND sender_type = 'seller'
          AND (message_id LIKE 'local-%' OR message_id LIKE 'bot-%')
          AND (
            regexp_replace(lower(coalesce(text,'')), '\\s+', ' ', 'g')
              = regexp_replace(lower(:txt), '\\s+', ' ', 'g')
            OR position(
                 regexp_replace(lower(coalesce(text,'')), '\\s+', ' ', 'g')
                 in regexp_replace(lower(:txt), '\\s+', ' ', 'g')
               ) = 1
            OR position(
                 regexp_replace(lower(:txt), '\\s+', ' ', 'g')
                 in regexp_replace(lower(coalesce(text,'')), '\\s+', ' ', 'g')
               ) = 1
          )
    """), {"cid": chat_id, "txt": api_text[:4000]})


def _match_article_by_product_name(conn, product_name: str) -> str | None:
    """Сопоставить название из DIRECT-сообщения с ym_assortment / ym_orders."""
    name = re.sub(r"\s+", " ", (product_name or "").strip())[:280]
    if len(name) < 10:
        return None

    row = conn.execute(text("""
        SELECT supplier_article FROM ym_assortment
        WHERE NULLIF(TRIM(supplier_article), '') IS NOT NULL
          AND (
            lower(name) = lower(:n)
            OR lower(name) LIKE lower(:n) || '%'
            OR lower(:n) LIKE lower(name) || '%'
          )
        ORDER BY length(name) ASC
        LIMIT 1
    """), {"n": name}).fetchone()
    if row and row[0]:
        return str(row[0]).strip()

    dim = _DIM_RE.search(name)
    dim_a = dim_b = dim_c = None
    if dim:
        dim_a, dim_b, dim_c = dim.group(1), dim.group(2), dim.group(3)

    low = name.lower()
    color_key = None
    for key, variants in _COLOR_TOKENS:
        if any(v in low for v in variants):
            color_key = key
            break

    stem = name.split(",")[0].strip()[:50]
    if len(stem) < 8:
        stem = name[:50]

    candidates = conn.execute(text("""
        SELECT supplier_article, name FROM ym_assortment
        WHERE NULLIF(TRIM(supplier_article), '') IS NOT NULL
          AND name ILIKE :stem
        LIMIT 60
    """), {"stem": f"%{stem[:35]}%"}).mappings().all()

    if not candidates and dim_a:
        candidates = conn.execute(text("""
            SELECT supplier_article, name FROM ym_assortment
            WHERE NULLIF(TRIM(supplier_article), '') IS NOT NULL
              AND name ILIKE :dim
            LIMIT 60
        """), {"dim": f"%{dim_a}%{dim_b}%{dim_c}%"}).mappings().all()

    best = None
    best_score = 0
    for c in candidates:
        cname = (c.get("name") or "").lower()
        score = 0
        if dim_a and dim_a in cname and dim_b in cname and dim_c in cname:
            score += 5
        if color_key and color_key in cname:
            score += 3
        # общие значимые слова (≥4 букв)
        words = [w for w in re.split(r"[^\wа-яё]+", low) if len(w) >= 4]
        score += sum(1 for w in words[:12] if w in cname)
        if score > best_score:
            best_score = score
            best = c.get("supplier_article")

    if best and best_score >= 4:
        return str(best).strip()

    # Фоллбек: sku_name в заказах (больше вариантов названий)
    if dim_a:
        row = conn.execute(text("""
            SELECT supplier_article FROM ym_orders
            WHERE NULLIF(TRIM(supplier_article), '') IS NOT NULL
              AND sku_name ILIKE :dim
              AND (:color = '' OR sku_name ILIKE :color_pat)
            GROUP BY supplier_article
            ORDER BY count(*) DESC
            LIMIT 1
        """), {
            "dim": f"%{dim_a}%{dim_b}%{dim_c}%",
            "color": color_key or "",
            "color_pat": f"%{color_key}%" if color_key else "%",
        }).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    return None


def _resolve_article(conn, chat_id: str) -> bool:
    """Заполнить context_article из заказа/возврата или названия в тексте DIRECT."""
    _ensure_chat_columns(conn)
    row = conn.execute(text("""
        SELECT context_article, order_id, return_id, context_type
        FROM ym_chats WHERE chat_id = :cid
    """), {"cid": chat_id}).mappings().first()
    if not row:
        return False
    if (row.get("context_article") or "").strip():
        return True

    conn.execute(text("""
        UPDATE ym_chats c SET
            context_article = COALESCE(
                NULLIF(TRIM(c.context_article), ''),
                (
                  SELECT o.supplier_article FROM ym_orders o
                  WHERE c.order_id IS NOT NULL
                    AND o.order_id::text = c.order_id
                    AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL
                  LIMIT 1
                ),
                (
                  SELECT r.supplier_article FROM ym_returns r
                  WHERE (
                    (c.return_id IS NOT NULL AND r.return_id::text = c.return_id)
                    OR (c.order_id IS NOT NULL AND r.order_id::text = c.order_id)
                  )
                  AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                  ORDER BY r.created_at DESC NULLS LAST
                  LIMIT 1
                )
            )
        WHERE c.chat_id = :cid
          AND NULLIF(TRIM(c.context_article), '') IS NULL
    """), {"cid": chat_id})

    art = conn.execute(text(
        "SELECT context_article FROM ym_chats WHERE chat_id = :cid"
    ), {"cid": chat_id}).scalar()
    if (art or "").strip():
        return True

    # DIRECT: парсим «вопрос по товару …» из сообщений клиента
    texts = conn.execute(text("""
        SELECT text FROM ym_chat_messages
        WHERE chat_id = :cid
          AND lower(COALESCE(sender_type,'')) IN ('customer','buyer','client','user')
          AND text IS NOT NULL AND TRIM(text) <> ''
        ORDER BY created_at ASC NULLS LAST
        LIMIT 8
    """), {"cid": chat_id}).fetchall()
    for (txt,) in texts:
        m = _DIRECT_PRODUCT_RE.search(txt or "")
        if not m:
            continue
        product = m.group(1).strip()
        # обрезать хвост «Получил…» если точка потерялась
        product = re.split(r"\s+Получил|\s+Приобр|\s+Заказ", product, maxsplit=1)[0].strip()
        matched = _match_article_by_product_name(conn, product)
        if matched:
            conn.execute(text("""
                UPDATE ym_chats SET context_article = :art, updated_at = NOW()
                WHERE chat_id = :cid AND NULLIF(TRIM(context_article), '') IS NULL
            """), {"cid": chat_id, "art": matched})
            return True
    return False


def _upsert_chat(conn, chat: dict, *, needs_answer: bool) -> str | None:
    chat_id = str(chat.get("chatId") or chat.get("id") or "").strip()
    if not chat_id:
        return None
    _ensure_chat_columns(conn)
    ctx = _context_fields(chat.get("context"))
    status = str(chat.get("status") or "")
    created = _parse_ts(chat.get("createdAt"))
    updated = _parse_ts(chat.get("updatedAt"))

    conn.execute(text("""
        INSERT INTO ym_chats
            (chat_id, status, context_type, customer_name, customer_public_id,
             order_id, return_id, needs_answer, waiting_since, created_at, updated_at)
        VALUES
            (:cid, :status, :ctype, :cname, :cpid,
             :oid, :rid, :needs,
             CASE WHEN :needs THEN COALESCE(
                 (SELECT waiting_since FROM ym_chats WHERE chat_id = :cid), NOW()
             ) ELSE NULL END,
             COALESCE(:created, NOW()), COALESCE(:updated, NOW()))
        ON CONFLICT (chat_id) DO UPDATE SET
            status              = EXCLUDED.status,
            context_type        = COALESCE(EXCLUDED.context_type, ym_chats.context_type),
            customer_name       = COALESCE(NULLIF(EXCLUDED.customer_name, ''), ym_chats.customer_name),
            customer_public_id  = COALESCE(EXCLUDED.customer_public_id, ym_chats.customer_public_id),
            order_id            = COALESCE(EXCLUDED.order_id, ym_chats.order_id),
            return_id           = COALESCE(EXCLUDED.return_id, ym_chats.return_id),
            -- Статус ЯМ (WAITING_FOR_PARTNER) часто врёт после нашей ссылки.
            -- Не затираем точный needs_answer, если последнее известное — наше.
            needs_answer        = CASE
                WHEN NOT EXCLUDED.needs_answer THEN false
                WHEN lower(COALESCE(ym_chats.last_sender, '')) IN
                     ('seller', 'partner', 'shop', 'merchant')
                    THEN ym_chats.needs_answer
                ELSE true
            END,
            waiting_since       = CASE
                WHEN NOT EXCLUDED.needs_answer THEN NULL
                WHEN lower(COALESCE(ym_chats.last_sender, '')) IN
                     ('seller', 'partner', 'shop', 'merchant')
                    THEN ym_chats.waiting_since
                WHEN ym_chats.waiting_since IS NULL THEN NOW()
                ELSE ym_chats.waiting_since
            END,
            updated_at          = COALESCE(EXCLUDED.updated_at, NOW())
    """), {
        "cid": chat_id,
        "status": status,
        "ctype": ctx["context_type"],
        "cname": ctx["customer_name"],
        "cpid": ctx["customer_public_id"],
        "oid": ctx["order_id"],
        "rid": ctx["return_id"],
        "needs": needs_answer,
        "created": created,
        "updated": updated,
    })
    return chat_id


def _sync_history(conn, chat_id: str) -> int:
    data = _fetch_history(chat_id)
    ctx = _context_fields(data.get("context"))
    messages = sorted(
        data.get("messages") or [],
        key=lambda m: (
            _parse_ts(m.get("createdAt")) or datetime.min,
            str(m.get("messageId") or m.get("id") or ""),
        ),
    )
    stored = 0

    for m in messages:
        mid = str(m.get("messageId") or m.get("id") or "").strip()
        if not mid:
            continue
        sender = _map_sender(m.get("sender"))
        body = str(m.get("message") or m.get("text") or "").strip()
        created = _parse_ts(m.get("createdAt"))
        att_items = []
        for p in (m.get("payload") or []):
            if isinstance(p, dict):
                u = str(p.get("url") or "").strip()
                if u.startswith("http"):
                    att_items.append({
                        "url": u,
                        "name": str(p.get("name") or ""),
                        "size": p.get("size") or 0,
                    })
            elif isinstance(p, str) and p.startswith("http"):
                att_items.append({"url": p, "name": "", "size": 0})
        att_raw = json.dumps(att_items, ensure_ascii=False) if att_items else None
        conn.execute(text("""
            INSERT INTO ym_chat_messages
                (message_id, chat_id, sender_type, text, created_at, attachments)
            VALUES (:mid, :cid, :sender, :txt, :created, :att)
            ON CONFLICT (message_id) DO UPDATE SET
                text = EXCLUDED.text,
                attachments = COALESCE(EXCLUDED.attachments, ym_chat_messages.attachments)
        """), {
            "mid": mid,
            "cid": chat_id,
            "sender": sender,
            "txt": body[:4000],
            "created": created,
            "att": att_raw,
        })
        # Убираем local-/bot- дубль: ЯМ часто дописывает предупреждение к тексту
        if sender == "seller" and body and not str(mid).startswith(("local-", "bot-")):
            _delete_local_seller_dupes(conn, chat_id, body)
        stored += 1

    conn.execute(text("""
        UPDATE ym_chats SET
            context_type = COALESCE(NULLIF(:ctype, ''), context_type),
            customer_name = COALESCE(NULLIF(:cname, ''), customer_name),
            customer_public_id = COALESCE(:cpid, customer_public_id),
            order_id = COALESCE(:oid, order_id),
            return_id = COALESCE(:rid, return_id),
            updated_at = NOW()
        WHERE chat_id = :cid
    """), {
        "cid": chat_id,
        "ctype": ctx["context_type"],
        "cname": ctx["customer_name"],
        "cpid": ctx["customer_public_id"],
        "oid": ctx["order_id"],
        "rid": ctx["return_id"],
    })
    _apply_human_state(conn, [chat_id])
    try:
        _resolve_article(conn, chat_id)
    except Exception as e:
        logger.warning("[YmChat] resolve article %s: %s", chat_id, e)
    return stored


def cleanup_local_message_dupes(db, *, days: int = 30) -> int:
    """Разовая чистка уже накопившихся local-/bot- дублей ЯМ."""
    with db.bind.connect() as conn:
        deleted = conn.execute(text("""
            WITH dups AS (
              SELECT l.message_id
              FROM ym_chat_messages l
              JOIN ym_chat_messages a
                ON a.chat_id = l.chat_id
               AND a.message_id <> l.message_id
               AND a.sender_type = 'seller'
               AND a.message_id NOT LIKE 'local-%'
               AND a.message_id NOT LIKE 'bot-%'
               AND l.sender_type = 'seller'
               AND (l.message_id LIKE 'local-%' OR l.message_id LIKE 'bot-%')
               AND l.created_at > now() - make_interval(days => :days)
               AND (
                 regexp_replace(lower(coalesce(l.text,'')), '\\s+', ' ', 'g')
                   = regexp_replace(lower(coalesce(a.text,'')), '\\s+', ' ', 'g')
                 OR position(
                      regexp_replace(lower(coalesce(l.text,'')), '\\s+', ' ', 'g')
                      in regexp_replace(lower(coalesce(a.text,'')), '\\s+', ' ', 'g')
                    ) = 1
                 OR position(
                      regexp_replace(lower(coalesce(a.text,'')), '\\s+', ' ', 'g')
                      in regexp_replace(lower(coalesce(l.text,'')), '\\s+', ' ', 'g')
                    ) = 1
               )
            )
            DELETE FROM ym_chat_messages
            WHERE message_id IN (SELECT message_id FROM dups)
            RETURNING message_id
        """), {"days": int(days)}).fetchall()
        conn.commit()
        n = len(deleted)
        if n:
            logger.info("[YmChat] cleaned local/bot dupes: %d", n)
        return n


def backfill_chat_articles(db, limit: int = 40) -> int:
    """Дозаполнить context_article у чатов без артикула."""
    with db.bind.connect() as conn:
        _ensure_chat_columns(conn)
        rows = conn.execute(text("""
            SELECT chat_id FROM ym_chats
            WHERE NULLIF(TRIM(context_article), '') IS NULL
              AND updated_at > now() - interval '30 days'
            ORDER BY updated_at DESC NULLS LAST
            LIMIT :lim
        """), {"lim": limit}).fetchall()
        done = 0
        for (cid,) in rows:
            try:
                if _resolve_article(conn, cid):
                    done += 1
            except Exception as e:
                logger.warning("[YmChat] backfill %s: %s", cid, e)
        conn.commit()
    logger.info("[YmChat] backfill articles: %d / %d", done, len(rows))
    return done


def sync_chats(db) -> int:
    if not _key() or not _business_id():
        return 0

    try:
        waiting = _list_chats(["WAITING_FOR_PARTNER"], limit=100)
        # ответы покупателю уже отправлены — держим в списке короткий хвост
        answered = _list_chats(["WAITING_FOR_CUSTOMER"], limit=50)
    except Exception as e:
        logger.error("[YmChat] list error: %s", e)
        return 0

    synced = 0
    hist = 0
    touched_ids: list[str] = []
    with db.bind.connect() as conn:
        for chat in waiting:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=True)
                if not cid:
                    continue
                synced += 1
                touched_ids.append(cid)
                conn.commit()
                try:
                    _sync_history(conn, cid)
                    conn.commit()
                    hist += 1
                except Exception as he:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.error("[YmChat] upsert: %s", e)

        for chat in answered:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=False)
                if not cid:
                    continue
                synced += 1
                touched_ids.append(cid)
                conn.commit()
                if hist < HISTORY_LIMIT_CHATS:
                    try:
                        _sync_history(conn, cid)
                        conn.commit()
                        hist += 1
                    except Exception as he:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.error("[YmChat] upsert answered: %s", e)

        if touched_ids:
            try:
                _apply_human_state(conn, list(set(touched_ids)))
            except Exception as e:
                logger.warning("[YmChat] apply human state: %s", e)
        try:
            _apply_human_state(conn, only_clear_stale=True)
        except Exception as e:
            logger.warning("[YmChat] clear stale waiting: %s", e)

        conn.commit()

        if touched_ids:
            try:
                from . import chat_realtime
                rows = conn.execute(text("""
                    SELECT chat_id, customer_name, context_article, last_message,
                           last_sender, needs_answer,
                           EXTRACT(epoch FROM COALESCE(
                             (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                             c.waiting_since, c.updated_at, c.created_at, now()
                           ))::bigint * 1000 AS last_ts
                    FROM ym_chats c
                    WHERE chat_id = ANY(:ids)
                """), {"ids": list(set(touched_ids))}).mappings().all()
                for row in rows:
                    chat_realtime.publish_chat_updated({
                        "chat_id": row["chat_id"],
                        "mp": "ym",
                        "client_name": row.get("customer_name"),
                        "sku": row.get("context_article") or "",
                        "last_message": row.get("last_message"),
                        "last_sender": row.get("last_sender"),
                        "last_ts": row.get("last_ts"),
                        "needs_answer": row.get("needs_answer"),
                    })
                    if row.get("needs_answer") and str(row.get("last_sender") or "").lower() in (
                        "customer", "buyer", "client", "user", ""
                    ):
                        chat_realtime.schedule_suggest_precompute("ym", row["chat_id"])
            except Exception as e:
                logger.warning("[YmChat] realtime publish: %s", e)

        archived_ids = [
            str(r[0]) for r in conn.execute(text("""
            WITH targets AS (
              SELECT c.chat_id
              FROM ym_chats c
              LEFT JOIN cx_dismissed_items d
                ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
              WHERE d.item_id IS NULL
                AND COALESCE(
                  (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                  c.waiting_since, c.updated_at, c.created_at
                ) < now() - interval '3 days'
                AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'partner', 'shop', 'merchant')
                AND EXISTS (
                  SELECT 1 FROM ym_chat_messages s
                  WHERE s.chat_id = c.chat_id
                    AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'partner', 'shop', 'merchant')
                )
            ),
            ins AS (
              INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
              SELECT 'chat', chat_id, 'ym', 'ym_chat_sync',
                     'Авто: нет общения >3 дн после нашего ответа'
              FROM targets
              ON CONFLICT (item_type, item_id, mp) DO NOTHING
              RETURNING item_id
            )
            SELECT item_id FROM ins
        """)).fetchall()
        ]
        if archived_ids:
            conn.commit()
            logger.info("[YmChat] auto-archived silent>3d: %s", len(archived_ids))
            try:
                from .chat_bot import set_bot_disabled
                for cid in archived_ids:
                    set_bot_disabled(conn, cid)
                conn.commit()
            except Exception as e:
                logger.warning("[YmChat] bot disable: %s", e)
            try:
                from . import chat_realtime
                for cid in archived_ids:
                    chat_realtime.publish_chat_removed(cid, "ym")
            except Exception as e:
                logger.warning("[YmChat] realtime remove: %s", e)

        try:
            closed_ret = archive_waiting_chats_with_approved_returns(conn)
            if closed_ret:
                conn.commit()
                logger.info("[YmChat] auto-archived approved returns >1d: %s", closed_ret)
        except Exception as e:
            logger.warning("[YmChat] archive approved returns: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        backfill_chat_articles(db, limit=25)
    except Exception as e:
        logger.warning("[YmChat] article backfill: %s", e)

    try:
        linked = link_reshipment_tokens(db)
        if linked:
            logger.info("[YmChat] reshipment tokens linked: %s", linked)
    except Exception as e:
        logger.warning("[YmChat] reshipment token link: %s", e)

    logger.info("[YmChat] synced=%d history=%d waiting=%d", synced, hist, len(waiting))
    return synced


def archive_waiting_chats_with_approved_returns(conn) -> int:
    """Чаты ЯМ без переписки >1 дня и с одобренным возвратом — в архив."""
    note = "Авто: возврат одобрен, нет переписки >1 дн"
    ids = [
        str(r[0]) for r in conn.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id, c.order_id, c.return_id
          FROM ym_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND c.needs_answer = true
            AND COALESCE(
                  (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                  c.waiting_since, c.updated_at, c.created_at
                ) < now() - interval '1 day'
        ),
        matched AS (
          SELECT DISTINCT w.chat_id
          FROM waiting w
          JOIN ym_returns r ON (
            (NULLIF(TRIM(w.return_id), '') IS NOT NULL AND r.return_id::text = w.return_id)
            OR (NULLIF(TRIM(w.order_id), '') IS NOT NULL AND r.order_id::text = w.order_id)
          )
          WHERE upper(COALESCE(r.status, '')) IN ('REFUND_IN_PROGRESS', 'REFUNDED')
             OR COALESCE(r.status_ru, '') ILIKE '%одобр%'
        ),
        ins AS (
          INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
          SELECT 'chat', chat_id, 'ym', 'ym_chat_sync', :note
          FROM matched
          ON CONFLICT (item_type, item_id, mp) DO NOTHING
          RETURNING item_id
        )
        SELECT item_id FROM ins
        """), {"note": note}).fetchall()
    ]
    if not ids:
        return 0
    conn.execute(text("""
        UPDATE ym_chats c SET needs_answer = false, waiting_since = NULL, updated_at = now()
        WHERE c.chat_id = ANY(:ids)
    """), {"ids": ids})
    try:
        from .chat_bot import set_bot_disabled
        for cid in ids:
            set_bot_disabled(conn, cid)
    except Exception as e:
        logger.warning("[YmChat] bot disable approved: %s", e)
    try:
        from . import chat_realtime
        for cid in ids:
            chat_realtime.publish_chat_removed(cid, "ym")
    except Exception as e:
        logger.warning("[YmChat] realtime approved: %s", e)
    return len(ids)


def _ym_chat_where_form_token_sent(db, token: str, used_at=None):
    """Чат ЯМ, куда ушла ссылка с токеном. used_at — не брать сообщения сильно позже заполнения."""
    from sqlalchemy import text as sql_text

    tok = (token or "").strip()
    if not tok:
        return None
    if used_at is None:
        row = db.execute(sql_text("""
            SELECT chat_id FROM ym_chat_messages
            WHERE text ILIKE :pat
            ORDER BY CASE
                       WHEN lower(COALESCE(sender_type, '')) IN ('seller', 'partner', 'merchant')
                       THEN 0 ELSE 1
                     END,
                     created_at DESC NULLS LAST
            LIMIT 1
        """), {"pat": f"%t={tok}%"}).mappings().first()
    else:
        row = db.execute(sql_text("""
            SELECT chat_id FROM ym_chat_messages
            WHERE text ILIKE :pat
              AND created_at <= (CAST(:used_at AS timestamp) + INTERVAL '2 hours')
            ORDER BY CASE
                       WHEN lower(COALESCE(sender_type, '')) IN ('seller', 'partner', 'merchant')
                       THEN 0 ELSE 1
                     END,
                     created_at DESC NULLS LAST
            LIMIT 1
        """), {"pat": f"%t={tok}%", "used_at": used_at}).mappings().first()
    if row and row.get("chat_id"):
        return str(row["chat_id"])
    return None


def _apply_ym_chat_to_reshipment(db, req_id: int, cid: str, *, only_if_chat=None) -> None:
    from sqlalchemy import text as sql_text

    ym = db.execute(sql_text("""
        SELECT order_id, context_article, context_type FROM ym_chats WHERE chat_id = :cid
    """), {"cid": cid}).mappings().first() or {}
    ctx = (ym.get("context_type") or "").strip().upper()
    oid = ym.get("order_id") if ctx in ("ORDER", "RETURN") else None
    art = (ym.get("context_article") or "").strip() or None if ctx in ("ORDER", "RETURN") else None
    db.execute(sql_text("""
        UPDATE reshipment_requests SET
            ym_chat_id = :cid,
            marketplace = 'ym',
            order_number = COALESCE(NULLIF(order_number, ''), :oid),
            supplier_article = CASE
                WHEN :art IS NULL OR btrim(:art) = '' THEN supplier_article
                WHEN supplier_article IS NULL OR btrim(supplier_article) = '' THEN :art
                WHEN ym_chat_id IS DISTINCT FROM :cid THEN :art
                ELSE supplier_article
            END,
            product_category = COALESCE(NULLIF(product_category, ''), :art)
        WHERE id = :id
          AND (wb_chat_id IS NULL OR wb_chat_id = '')
          AND (
            :only_if IS NULL
            OR ym_chat_id IS NULL OR ym_chat_id = ''
            OR ym_chat_id = :only_if
          )
    """), {
        "cid": cid,
        "oid": oid,
        "art": art,
        "id": req_id,
        "only_if": only_if_chat,
    })


def link_reshipment_tokens(db) -> int:
    """Привязывает токены формы доотправки к чатам ЯМ (как MiniSync для WB).

    1) Неиспользованные токены без chat_id — ищем ссылку в ym_chat_messages.
    2) Уже заполненные заявки без чата — только если сообщение со ссылкой
       было до/около used_at (чтобы не привязать чужой повторно использованный токен).
    3) Ссылка создана в одном чате, а ушла клиенту в другом — перепривязываем.
    """
    from sqlalchemy import text as sql_text

    linked = 0

    unused = db.execute(sql_text("""
        SELECT token FROM reshipment_chat_tokens
        WHERE (chat_id IS NULL OR chat_id = '')
          AND used_at IS NULL
          AND COALESCE(expired, FALSE) IS NOT TRUE
    """)).fetchall()

    for (tok,) in unused:
        cid = _ym_chat_where_form_token_sent(db, tok)
        if not cid:
            continue
        db.execute(sql_text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid, mp = 'ym'
            WHERE token = :token AND used_at IS NULL
        """), {"cid": cid, "token": tok})
        linked += 1
        logger.info("[YmChat] token linked (unused): %s → %s", tok, cid)

    used = db.execute(sql_text("""
        SELECT t.token, t.used_at, r.id AS req_id
        FROM reshipment_chat_tokens t
        JOIN reshipment_requests r ON r.id = t.request_id
        WHERE (t.chat_id IS NULL OR t.chat_id = '')
          AND t.used_at IS NOT NULL
          AND (r.ym_chat_id IS NULL OR r.ym_chat_id = '')
          AND (r.wb_chat_id IS NULL OR r.wb_chat_id = '')
    """)).mappings().fetchall()

    for row in used:
        cid = _ym_chat_where_form_token_sent(db, row["token"], used_at=row["used_at"])
        if not cid:
            continue
        db.execute(sql_text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid, mp = 'ym'
            WHERE token = :token AND (chat_id IS NULL OR chat_id = '')
        """), {"cid": cid, "token": row["token"]})
        _apply_ym_chat_to_reshipment(db, row["req_id"], cid)
        linked += 1
        logger.info("[YmChat] token linked (used→req #%s): %s → %s",
                    row["req_id"], row["token"], cid)

    mismatched = db.execute(sql_text("""
        SELECT t.token, t.chat_id, t.used_at, t.request_id
        FROM reshipment_chat_tokens t
        WHERE NULLIF(TRIM(t.chat_id), '') IS NOT NULL
          AND COALESCE(t.expired, FALSE) IS NOT TRUE
          AND t.created_at > NOW() - INTERVAL '21 days'
    """)).mappings().fetchall()

    for row in mismatched:
        cid = _ym_chat_where_form_token_sent(db, row["token"], used_at=row["used_at"])
        if not cid or cid == str(row["chat_id"] or ""):
            continue
        old_cid = str(row["chat_id"])
        db.execute(sql_text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid, mp = 'ym'
            WHERE token = :token AND chat_id = :old
        """), {"cid": cid, "token": row["token"], "old": old_cid})
        if row["request_id"]:
            _apply_ym_chat_to_reshipment(
                db, row["request_id"], cid, only_if_chat=old_cid,
            )
        linked += 1
        logger.info("[YmChat] token rebound %s: %s → %s (req #%s)",
                    row["token"], old_cid, cid, row["request_id"])

    if linked:
        db.commit()
    return linked


def send_message(chat_id: str, text: str) -> None:
    """Отправка текста. Требует scope communication у YM_API_KEY."""
    ym_client.send_chat_message(chat_id, text, biz=_business_id(), key=_key())


def send_file(
    chat_id: str,
    filename: str,
    content: bytes,
    *,
    content_type: str | None = None,
) -> None:
    """Отправка файла в чат ЯМ (multipart Partner API)."""
    ym_client.send_chat_file(
        chat_id,
        filename,
        content,
        content_type=content_type,
        biz=_business_id(),
        key=_key(),
    )


def _sync_chats_isolated():
    with db_session() as db:
        return sync_chats(db)


async def start_ym_chat_sync_worker(get_db_func) -> None:
    logger.info("[YmChat] worker starting (interval=%ds)", SYNC_INTERVAL)
    await asyncio.sleep(50)

    if not _key() or not _business_id():
        logger.warning("[YmChat] YM_API_KEY / YM_BUSINESS_ID не заданы — idle")
        return

    while True:
        try:
            await asyncio.to_thread(_sync_chats_isolated)
        except Exception as e:
            logger.error("[YmChat] cycle error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL)
