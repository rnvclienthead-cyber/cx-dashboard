"""
Синхронизация чатов Яндекс Маркета (Partner API).

Polling: WAITING_FOR_PARTNER + недавние WAITING_FOR_CUSTOMER.
Отправка сообщений требует scope communication / all-methods у токена.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import text

from . import ym_client

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


def _map_sender(sender: str) -> str:
    s = str(sender or "").upper()
    if s == "PARTNER":
        return "seller"
    if s == "CUSTOMER":
        return "customer"
    if s in ("MARKET", "SUPPORT"):
        return "system"
    return s.lower() or "unknown"


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
            needs_answer        = EXCLUDED.needs_answer,
            waiting_since       = CASE
                WHEN EXCLUDED.needs_answer AND ym_chats.waiting_since IS NULL THEN NOW()
                WHEN NOT EXCLUDED.needs_answer THEN NULL
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
    # Клиент ждёт ответа — вернуть чат из архива
    if needs_answer:
        conn.execute(text("""
            DELETE FROM cx_dismissed_items
            WHERE mp = 'ym' AND item_type = 'chat' AND item_id = :cid
        """), {"cid": chat_id})
    return chat_id


def _sync_history(conn, chat_id: str) -> int:
    data = _fetch_history(chat_id)
    ctx = _context_fields(data.get("context"))
    messages = data.get("messages") or []
    stored = 0
    last_text = ""
    last_sender = ""

    for m in messages:
        mid = str(m.get("messageId") or m.get("id") or "").strip()
        if not mid:
            continue
        sender = _map_sender(m.get("sender"))
        body = str(m.get("message") or m.get("text") or "").strip()
        created = _parse_ts(m.get("createdAt"))
        # payload[] = вложения {name, url, size}
        att_urls = []
        for p in (m.get("payload") or []):
            if isinstance(p, dict):
                u = str(p.get("url") or "").strip()
                if u.startswith("http"):
                    att_urls.append(u)
            elif isinstance(p, str) and p.startswith("http"):
                att_urls.append(p)
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
            "att": "\n".join(att_urls) if att_urls else None,
        })
        # Убираем local-/bot- дубль: ЯМ часто дописывает предупреждение к тексту
        if sender == "seller" and body and not str(mid).startswith(("local-", "bot-")):
            _delete_local_seller_dupes(conn, chat_id, body)
        stored += 1
        # messages обычно от старых к новым — берём последнее
        if body or att_urls:
            last_text = body or (f"[файл] {att_urls[0]}" if att_urls else "")
            last_sender = sender

    conn.execute(text("""
        UPDATE ym_chats SET
            last_message = COALESCE(NULLIF(:msg, ''), last_message),
            last_sender  = COALESCE(NULLIF(:sender, ''), last_sender),
            context_type = COALESCE(NULLIF(:ctype, ''), context_type),
            customer_name = COALESCE(NULLIF(:cname, ''), customer_name),
            customer_public_id = COALESCE(:cpid, customer_public_id),
            order_id = COALESCE(:oid, order_id),
            return_id = COALESCE(:rid, return_id),
            updated_at = NOW()
        WHERE chat_id = :cid
    """), {
        "cid": chat_id,
        "msg": (last_text or "")[:500],
        "sender": last_sender,
        "ctype": ctx["context_type"],
        "cname": ctx["customer_name"],
        "cpid": ctx["customer_public_id"],
        "oid": ctx["order_id"],
        "rid": ctx["return_id"],
    })
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
        waiting = _list_chats(["WAITING_FOR_PARTNER"], limit=50)
        # ответы покупателю уже отправлены — держим в списке короткий хвост
        answered = _list_chats(["WAITING_FOR_CUSTOMER"], limit=20)
    except Exception as e:
        logger.error("[YmChat] list error: %s", e)
        return 0

    synced = 0
    hist = 0
    with db.bind.connect() as conn:
        for chat in waiting:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=True)
                if not cid:
                    continue
                synced += 1
                if hist < HISTORY_LIMIT_CHATS:
                    try:
                        _sync_history(conn, cid)
                        hist += 1
                    except Exception as he:
                        logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                logger.error("[YmChat] upsert: %s", e)

        for chat in answered:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=False)
                if not cid:
                    continue
                synced += 1
                if hist < HISTORY_LIMIT_CHATS:
                    try:
                        _sync_history(conn, cid)
                        hist += 1
                    except Exception as he:
                        logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                logger.error("[YmChat] upsert answered: %s", e)

        conn.commit()

        archived = conn.execute(text("""
            WITH targets AS (
              SELECT c.chat_id
              FROM ym_chats c
              LEFT JOIN cx_dismissed_items d
                ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
              WHERE d.item_id IS NULL
                AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'partner', 'shop')
                AND COALESCE(
                  (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                  c.waiting_since, c.updated_at, c.created_at
                ) < now() - interval '2 days'
            ),
            ins AS (
              INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
              SELECT 'chat', chat_id, 'ym', 'ym_chat_sync',
                     'Авто: нет ответа клиента >2 дн после нашего'
              FROM targets
              ON CONFLICT (item_type, item_id, mp) DO NOTHING
              RETURNING item_id
            )
            SELECT count(*) FROM ins
        """)).scalar() or 0
        if archived:
            conn.commit()
            logger.info("[YmChat] auto-archived seller-silent>2d: %s", archived)

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


def link_reshipment_tokens(db) -> int:
    """Привязывает токены формы доотправки к чатам ЯМ (как MiniSync для WB).

    1) Неиспользованные токены без chat_id — ищем ссылку в ym_chat_messages.
    2) Уже заполненные заявки без чата — только если сообщение со ссылкой
       было до/около used_at (чтобы не привязать чужой повторно использованный токен).
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
        hit = db.execute(sql_text("""
            SELECT chat_id FROM ym_chat_messages
            WHERE text ILIKE :pat
            ORDER BY created_at DESC
            LIMIT 1
        """), {"pat": f"%t={tok}%"}).mappings().first()
        if not hit:
            continue
        db.execute(sql_text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid, mp = 'ym'
            WHERE token = :token AND (chat_id IS NULL OR chat_id = '')
        """), {"cid": str(hit["chat_id"]), "token": tok})
        linked += 1
        logger.info("[YmChat] token linked (unused): %s → %s", tok, hit["chat_id"])

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
        hit = db.execute(sql_text("""
            SELECT chat_id FROM ym_chat_messages
            WHERE text ILIKE :pat
              AND created_at <= (:used_at::timestamp + INTERVAL '2 hours')
            ORDER BY created_at DESC
            LIMIT 1
        """), {"pat": f"%t={row['token']}%", "used_at": row["used_at"]}).mappings().first()
        if not hit:
            continue
        cid = str(hit["chat_id"])
        db.execute(sql_text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid, mp = 'ym'
            WHERE token = :token AND (chat_id IS NULL OR chat_id = '')
        """), {"cid": cid, "token": row["token"]})
        ym = db.execute(sql_text("""
            SELECT order_id, context_article FROM ym_chats WHERE chat_id = :cid
        """), {"cid": cid}).mappings().first() or {}
        db.execute(sql_text("""
            UPDATE reshipment_requests SET
                ym_chat_id = :cid,
                marketplace = 'ym',
                order_number = COALESCE(NULLIF(order_number, ''), :oid),
                product_category = COALESCE(NULLIF(product_category, ''), :art)
            WHERE id = :id
              AND (ym_chat_id IS NULL OR ym_chat_id = '')
              AND (wb_chat_id IS NULL OR wb_chat_id = '')
        """), {
            "cid": cid,
            "oid": ym.get("order_id"),
            "art": ym.get("context_article"),
            "id": row["req_id"],
        })
        linked += 1
        logger.info("[YmChat] token linked (used→req #%s): %s → %s",
                    row["req_id"], row["token"], cid)

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


async def start_ym_chat_sync_worker(get_db_func) -> None:
    logger.info("[YmChat] worker starting (interval=%ds)", SYNC_INTERVAL)
    await asyncio.sleep(50)

    if not _key() or not _business_id():
        logger.warning("[YmChat] YM_API_KEY / YM_BUSINESS_ID не заданы — idle")
        return

    while True:
        try:
            db = next(get_db_func())
            try:
                await asyncio.to_thread(sync_chats, db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[YmChat] cycle error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL)
