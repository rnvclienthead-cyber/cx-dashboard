"""Заявки Т-Шопинга через Telegram-бота @cxvidovito_bot.

Алерты по-прежнему уходят в чаты сотрудников. Сотрудники Т-Шопинга
оставляют заявки в боте — они попадают в раздел «Запросы „Т-Шопинг“».
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from . import centrifugo, telegram_proxy
from . import tshopping_form as form

logger = logging.getLogger("tshopping")

CHANNEL = "tshopping"
CHAT_PREFIX = "tg:"
WELCOME_TEXT = form.WELCOME_TEXT
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://cxvo.ru").rstrip("/")
WEBHOOK_PATH = "/api/v1/tshopping/telegram-webhook"


def webhook_url() -> str:
    return os.getenv("TSHOPPING_WEBHOOK_URL", f"{PUBLIC_BASE}{WEBHOOK_PATH}")


def webhook_secret() -> str:
    explicit = (os.getenv("TG_WEBHOOK_SECRET") or "").strip()
    if explicit:
        return explicit
    token = telegram_proxy.bot_token()
    if not token:
        return ""
    return hashlib.sha256(f"tshopping:{token}".encode("utf-8")).hexdigest()[:32]


def upload_dir() -> Path:
    base = Path(os.getenv("CX_UPLOAD_BASE_DIR", "/root/cx-dashboard/uploads"))
    path = base / "tshopping"
    path.mkdir(parents=True, exist_ok=True)
    return path


def public_upload_url(filename: str) -> str:
    return f"/uploads/tshopping/{filename}"


def absolute_upload_url(filename: str) -> str:
    rel = public_upload_url(filename)
    return f"{PUBLIC_BASE}{rel}"


def chat_id_for(tg_chat_id: Any) -> str:
    return f"{CHAT_PREFIX}{tg_chat_id}"


def order_key(order: str) -> str:
    raw = re.sub(r"[^0-9A-Za-zА-Яа-я-]+", "-", (order or "").strip(), flags=re.I)
    raw = re.sub(r"-{2,}", "-", raw).strip("-").lower()
    return (raw or "zakaz")[:48]


def chat_id_for_ticket(tg_chat_id: Any, order: str) -> str:
    return f"{CHAT_PREFIX}{tg_chat_id}:{order_key(order)}"


def init_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tshopping_chats (
                chat_id       TEXT PRIMARY KEY,
                tg_chat_id    BIGINT NOT NULL,
                tg_user_id    BIGINT,
                username      TEXT,
                first_name    TEXT,
                last_name     TEXT,
                display_name  TEXT,
                last_message  TEXT,
                last_sender   TEXT,
                last_ts       TIMESTAMPTZ,
                needs_answer  BOOLEAN NOT NULL DEFAULT TRUE,
                is_closed     BOOLEAN NOT NULL DEFAULT FALSE,
                unread        BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_chats DROP CONSTRAINT IF EXISTS tshopping_chats_tg_chat_id_key
        """))
        conn.execute(text("DROP INDEX IF EXISTS tshopping_chats_tg_chat_id_key"))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS tshopping_chats_tg_chat_id_idx
            ON tshopping_chats (tg_chat_id)
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_chats
                ADD COLUMN IF NOT EXISTS order_number TEXT,
                ADD COLUMN IF NOT EXISTS product TEXT,
                ADD COLUMN IF NOT EXISTS problem TEXT
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_chats
                ALTER COLUMN tg_chat_id DROP NOT NULL
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_chats
                ADD COLUMN IF NOT EXISTS close_requested BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS close_requested_by TEXT,
                ADD COLUMN IF NOT EXISTS unread_partner BOOLEAN NOT NULL DEFAULT FALSE
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tshopping_messages (
                id             BIGSERIAL PRIMARY KEY,
                chat_id        TEXT NOT NULL REFERENCES tshopping_chats(chat_id),
                tg_message_id  BIGINT,
                sender         TEXT NOT NULL,
                sender_name    TEXT,
                manager_login  TEXT,
                body           TEXT,
                attachments    JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS tshopping_messages_tg_uid
            ON tshopping_messages (chat_id, tg_message_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS tshopping_chats_queue_idx
            ON tshopping_chats (is_closed, needs_answer, last_ts DESC)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tshopping_updates (
                update_id  BIGINT PRIMARY KEY,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tshopping_form_state (
                tg_chat_id  BIGINT PRIMARY KEY,
                step        TEXT NOT NULL DEFAULT 'idle',
                draft       JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_chats
                ADD COLUMN IF NOT EXISTS source TEXT,
                ADD COLUMN IF NOT EXISTS email_from TEXT,
                ADD COLUMN IF NOT EXISTS email_reply_to TEXT,
                ADD COLUMN IF NOT EXISTS email_subject TEXT,
                ADD COLUMN IF NOT EXISTS email_message_id TEXT,
                ADD COLUMN IF NOT EXISTS last_inbound_message_id TEXT,
                ADD COLUMN IF NOT EXISTS last_outbound_message_id TEXT,
                ADD COLUMN IF NOT EXISTS email_references TEXT
        """))
        conn.execute(text("""
            UPDATE tshopping_chats SET source = CASE
                WHEN source IS NOT NULL AND source <> '' THEN source
                WHEN chat_id LIKE 'mail:%' THEN 'email'
                WHEN chat_id LIKE 'web:%' THEN 'web'
                ELSE 'telegram'
            END
            WHERE source IS NULL OR source = ''
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_messages
                ADD COLUMN IF NOT EXISTS email_message_id TEXT
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS tshopping_messages_email_uid
            ON tshopping_messages (email_message_id)
            WHERE email_message_id IS NOT NULL
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tshopping_mail_seen (
                message_id  TEXT PRIMARY KEY,
                imap_uid    BIGINT,
                status      TEXT NOT NULL DEFAULT 'ingested',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            ALTER TABLE tshopping_mail_seen
                ADD COLUMN IF NOT EXISTS mailbox TEXT
        """))
        conn.commit()
    try:
        upload_dir()
    except Exception as e:
        logger.warning("[TShopping] upload dir: %s", e)


CHAT_COLUMNS = """
    chat_id, tg_chat_id, tg_user_id, username, first_name, last_name,
    display_name, last_message, last_sender, last_ts,
    needs_answer, is_closed, unread, created_at, updated_at,
    order_number, product, problem, close_requested, close_requested_by, unread_partner,
    source, email_from, email_reply_to, email_subject, email_message_id,
    last_inbound_message_id, last_outbound_message_id, email_references
"""


def is_partner(db: Session, user: Optional[dict]) -> bool:
    """Т-Шопинг: есть раздел заявок и нет наших обращений клиентов."""
    role = str((user or {}).get("role") or "")
    if not role or role == "admin":
        return False
    rows = db.execute(text(
        "SELECT module FROM role_permissions WHERE role_name = :r"
    ), {"r": role}).fetchall()
    modules = {str(r[0]) for r in rows if r and r[0]}
    return "tshopping" in modules and "cx_responses" not in modules


def staff_chat_ids(conn: Optional[Connection] = None) -> set[str]:
    ids: set[str] = set()
    for key in ("TG_ADMIN_CHAT_ID", "TG_MODERATOR_CHAT_ID", "TG_ALERT_CHAT_ID"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            ids.add(raw)
    if conn is None:
        return ids
    try:
        rows = conn.execute(text("""
            SELECT tg_chat_id FROM users
            WHERE COALESCE(tg_alerts, false) = true
              AND tg_chat_id IS NOT NULL AND btrim(tg_chat_id) <> ''
        """)).fetchall()
        for row in rows:
            val = str(row[0]).strip()
            if val:
                ids.add(val)
    except Exception as e:
        logger.warning("[TShopping] staff_chat_ids: %s", e)
    return ids


def _display_name(user: dict) -> str:
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    username = str(user.get("username") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    if full:
        return full
    if username:
        return f"@{username}"
    return "Сотрудник"


_START_RE = re.compile(r"^/start(?:@\w+)?(?:\s|$)", re.I)


def extract_incoming(update: dict) -> Optional[dict]:
    """Разобрать update Telegram. None — событие не для поддержки."""
    if not isinstance(update, dict):
        return None

    cq = update.get("callback_query")
    if isinstance(cq, dict):
        from_user = cq.get("from") or {}
        if from_user.get("is_bot"):
            return None
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        if str(chat.get("type") or "") != "private":
            return None
        tg_chat_id = chat.get("id")
        if tg_chat_id is None:
            return None
        data = str(cq.get("data") or "")
        return {
            "update_id": update.get("update_id"),
            "edited": False,
            "callback_id": cq.get("id"),
            "command": form.command_of("", data),
            "tg_chat_id": int(tg_chat_id),
            "tg_user_id": from_user.get("id"),
            "tg_message_id": msg.get("message_id"),
            "username": from_user.get("username") or "",
            "first_name": from_user.get("first_name") or "",
            "last_name": from_user.get("last_name") or "",
            "display_name": _display_name(from_user),
            "text": "",
            "is_start": form.command_of("", data) == "start",
            "attachments": [],
            "date": msg.get("date"),
        }

    raw = update.get("message") or update.get("edited_message")
    if not isinstance(raw, dict):
        return None
    chat = raw.get("chat") or {}
    if str(chat.get("type") or "") != "private":
        return None
    from_user = raw.get("from") or {}
    if from_user.get("is_bot"):
        return None
    tg_chat_id = chat.get("id")
    if tg_chat_id is None:
        return None

    text_body = str(raw.get("text") or raw.get("caption") or "").strip()
    attachments: list[dict] = []

    photos = raw.get("photo") or []
    if isinstance(photos, list) and photos:
        best = max(photos, key=lambda p: int((p or {}).get("file_size") or 0))
        if best.get("file_id"):
            attachments.append({
                "kind": "image",
                "file_id": best["file_id"],
                "name": "photo.jpg",
            })
    document = raw.get("document") or {}
    if document.get("file_id"):
        mime = str(document.get("mime_type") or "")
        name = str(document.get("file_name") or "file")
        kind = "image" if mime.startswith("image/") else "document"
        attachments.append({
            "kind": kind,
            "file_id": document["file_id"],
            "name": name,
            "mime": mime,
        })
    for media_key, fallback_name, kind in (
        ("voice", "voice.ogg", "document"),
        ("video", "video.mp4", "document"),
        ("video_note", "video_note.mp4", "document"),
        ("audio", "audio", "document"),
        ("sticker", "sticker.webp", "image"),
    ):
        media = raw.get(media_key) or {}
        if media.get("file_id"):
            name = media.get("file_name") or fallback_name
            if media_key == "sticker" and media.get("emoji") and not text_body:
                text_body = str(media.get("emoji"))
            attachments.append({
                "kind": kind,
                "file_id": media["file_id"],
                "name": name,
                "mime": media.get("mime_type") or "",
            })

    if raw.get("contact"):
        c = raw["contact"]
        phone = str(c.get("phone_number") or "").strip()
        cname = " ".join(
            p for p in (c.get("first_name"), c.get("last_name")) if p
        ).strip()
        extra = f"Контакт: {cname} {phone}".strip()
        text_body = f"{text_body}\n{extra}".strip() if text_body else extra
    if raw.get("location"):
        loc = raw["location"]
        extra = f"Геолокация: {loc.get('latitude')}, {loc.get('longitude')}"
        text_body = f"{text_body}\n{extra}".strip() if text_body else extra

    is_start = bool(_START_RE.match(text_body))
    command = form.command_of(text_body)
    if not text_body and not attachments and not command:
        return None

    return {
        "update_id": update.get("update_id"),
        "edited": "edited_message" in update,
        "callback_id": None,
        "command": command,
        "tg_chat_id": int(tg_chat_id),
        "tg_user_id": from_user.get("id"),
        "tg_message_id": raw.get("message_id"),
        "username": from_user.get("username") or "",
        "first_name": from_user.get("first_name") or "",
        "last_name": from_user.get("last_name") or "",
        "display_name": _display_name(from_user),
        "text": "" if command else text_body,
        "is_start": is_start,
        "attachments": attachments,
        "date": raw.get("date"),
    }


def _save_tg_file(file_id: str, orig_name: str) -> Optional[dict]:
    ext = Path(orig_name or "").suffix.lower() or ".bin"
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".ogg", ".mp4", ".mp3", ".m4a"}:
        ext = ".bin"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir() / filename
    if not telegram_proxy.download_file(file_id, str(dest)):
        return None
    kind = "image" if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else "document"
    return {
        "kind": kind,
        "name": orig_name or filename,
        "url": public_upload_url(filename),
        "filename": filename,
    }


def materialize_attachments(items: list[dict]) -> list[dict]:
    out = []
    for item in items or []:
        file_id = item.get("file_id")
        saved = _save_tg_file(file_id, item.get("name") or "") if file_id else None
        if saved:
            out.append(saved)
            continue
        out.append({
            "kind": item.get("kind") or "document",
            "name": item.get("name") or "файл",
            "url": "",
            "pending": True,
            "file_id": file_id or "",
        })
    return out


def _preview(text_body: str, attachments: list[dict], is_start: bool) -> str:
    if is_start:
        return "Клиент открыл бота"
    if text_body:
        return text_body[:300]
    if attachments:
        kinds = {a.get("kind") for a in attachments}
        if kinds == {"image"}:
            return "Фото"
        return "Файл"
    return "Сообщение"


def _ts_from_unix(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _publish(chat: dict) -> None:
    try:
        centrifugo.publish(CHANNEL, {"type": "chat_updated", "chat": chat})
    except Exception as e:
        logger.debug("[TShopping] centrifugo: %s", e)


def ingest_incoming(conn: Connection, incoming: dict, staff_ids: Optional[set[str]] = None) -> dict:
    """Записать входящее в БД. staff_ids — чаты алертов, их пропускаем."""
    tg_chat_id = incoming["tg_chat_id"]
    if staff_ids is None:
        staff_ids = staff_chat_ids(conn)
    if str(tg_chat_id) in staff_ids:
        return {"status": "skipped_staff", "tg_chat_id": tg_chat_id}

    if not incoming.get("_claimed"):
        update_id = incoming.get("update_id")
        if update_id is not None:
            inserted = conn.execute(text("""
                INSERT INTO tshopping_updates (update_id)
                VALUES (:uid)
                ON CONFLICT (update_id) DO NOTHING
            """), {"uid": int(update_id)})
            if inserted.rowcount == 0 and not incoming.get("edited"):
                return {"status": "duplicate", "update_id": update_id}

    chat_id = incoming.get("ticket_chat_id") or chat_id_for(tg_chat_id)
    attachments = materialize_attachments(incoming.get("attachments") or [])
    is_start = bool(incoming.get("is_start"))
    body = incoming.get("text") or ""
    preview = _preview(body, attachments, is_start)
    created_at = _ts_from_unix(incoming.get("date"))
    needs_answer = not is_start
    unread = not is_start

    conn.execute(text("""
        INSERT INTO tshopping_chats (
            chat_id, tg_chat_id, tg_user_id, username, first_name, last_name,
            display_name, last_message, last_sender, last_ts,
            needs_answer, is_closed, unread, created_at, updated_at
        ) VALUES (
            :chat_id, :tg_chat_id, :tg_user_id, :username, :first_name, :last_name,
            :display_name, :last_message, 'client', :last_ts,
            :needs_answer, false, :unread, :created_at, :created_at
        )
        ON CONFLICT (chat_id) DO UPDATE SET
            tg_user_id = COALESCE(EXCLUDED.tg_user_id, tshopping_chats.tg_user_id),
            username = COALESCE(NULLIF(EXCLUDED.username, ''), tshopping_chats.username),
            first_name = COALESCE(NULLIF(EXCLUDED.first_name, ''), tshopping_chats.first_name),
            last_name = COALESCE(NULLIF(EXCLUDED.last_name, ''), tshopping_chats.last_name),
            display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), tshopping_chats.display_name),
            last_message = EXCLUDED.last_message,
            last_sender = 'client',
            last_ts = EXCLUDED.last_ts,
            needs_answer = tshopping_chats.needs_answer OR EXCLUDED.needs_answer,
            is_closed = CASE WHEN EXCLUDED.needs_answer THEN false ELSE tshopping_chats.is_closed END,
            unread = tshopping_chats.unread OR EXCLUDED.unread,
            unread_partner = false,
            close_requested = CASE WHEN EXCLUDED.needs_answer THEN false ELSE tshopping_chats.close_requested END,
            updated_at = now()
    """), {
        "chat_id": chat_id,
        "tg_chat_id": tg_chat_id,
        "tg_user_id": incoming.get("tg_user_id"),
        "username": incoming.get("username") or "",
        "first_name": incoming.get("first_name") or "",
        "last_name": incoming.get("last_name") or "",
        "display_name": incoming.get("display_name") or "Сотрудник",
        "last_message": preview,
        "last_ts": created_at,
        "needs_answer": needs_answer,
        "unread": unread,
        "created_at": created_at,
    })

    msg_id = None
    tg_message_id = incoming.get("tg_message_id")
    if incoming.get("edited") and tg_message_id is not None:
        row = conn.execute(text("""
            UPDATE tshopping_messages
            SET body = :body, attachments = CAST(:attachments AS jsonb)
            WHERE chat_id = :chat_id AND tg_message_id = :mid
            RETURNING id
        """), {
            "body": body,
            "attachments": _json(attachments),
            "chat_id": chat_id,
            "mid": int(tg_message_id),
        }).fetchone()
        msg_id = row[0] if row else None
    if msg_id is None:
        row = conn.execute(text("""
            INSERT INTO tshopping_messages (
                chat_id, tg_message_id, sender, sender_name, body, attachments, created_at
            ) VALUES (
                :chat_id, :mid, 'client', :sender_name, :body, CAST(:attachments AS jsonb), :created_at
            )
            ON CONFLICT (chat_id, tg_message_id)
            DO UPDATE SET body = EXCLUDED.body, attachments = EXCLUDED.attachments
            RETURNING id
        """), {
            "chat_id": chat_id,
            "mid": int(tg_message_id) if tg_message_id is not None else None,
            "sender_name": incoming.get("display_name") or "Сотрудник",
            "body": body,
            "attachments": _json(attachments),
            "created_at": created_at,
        }).fetchone()
        msg_id = row[0] if row else None

    conn.commit()
    chat = load_chat(conn, chat_id) or {"chat_id": chat_id, "tg_chat_id": tg_chat_id}
    _publish(chat)
    return {"status": "ok", "chat_id": chat_id, "message_id": msg_id, "is_start": is_start}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def latest_chat_for_user(conn: Connection, tg_chat_id: Any) -> Optional[dict]:
    row = conn.execute(text("""
        SELECT chat_id, tg_chat_id, display_name, order_number, product, problem,
               needs_answer, is_closed, last_ts, updated_at, close_requested
        FROM tshopping_chats
        WHERE tg_chat_id = :id
        ORDER BY is_closed ASC, updated_at DESC NULLS LAST
        LIMIT 1
    """), {"id": int(tg_chat_id)}).mappings().first()
    return dict(row) if row else None


def load_chat(conn: Connection, chat_id: str) -> Optional[dict]:
    row = conn.execute(text(f"""
        SELECT {CHAT_COLUMNS}
        FROM tshopping_chats WHERE chat_id = :cid
    """), {"cid": chat_id}).mappings().first()
    return dict(row) if row else None


def _infer_source(row: dict) -> str:
    source = str(row.get("source") or "").strip()
    if source:
        return source
    cid = str(row.get("chat_id") or "")
    if cid.startswith("mail:") or row.get("email_from"):
        return "email"
    if cid.startswith("web:"):
        return "web"
    if row.get("tg_chat_id"):
        return "telegram"
    return "web"


def is_email_chat(chat: Optional[dict]) -> bool:
    return _infer_source(chat or {}) == "email"


def can_staff_close(chat: Optional[dict], *, partner: bool) -> bool:
    if partner:
        return True
    return is_email_chat(chat)


def serialize_chat(row: dict) -> dict:
    last_ts = row.get("last_ts")
    created = row.get("created_at")
    source = _infer_source(row)
    contact = row.get("display_name") or "Сотрудник"
    if source == "email" and "почт" not in contact.lower():
        contact = f"{contact} · почта"
    elif source == "telegram" and "telegram" not in contact.lower():
        contact = f"{contact} · Telegram"
    elif source == "web" and "сайт" not in contact.lower():
        contact = f"{contact} · сайт"
    return {
        "id": row.get("chat_id"),
        "chat_id": row.get("chat_id"),
        "tg_chat_id": row.get("tg_chat_id"),
        "contact": contact,
        "username": row.get("username") or "",
        "last_message": row.get("last_message") or "",
        "last_sender": row.get("last_sender") or "",
        "last_ts": last_ts.isoformat() if hasattr(last_ts, "isoformat") else last_ts,
        "needs_answer": bool(row.get("needs_answer")),
        "is_closed": bool(row.get("is_closed")),
        "unread": bool(row.get("unread")),
        "unread_partner": bool(row.get("unread_partner")),
        "close_requested": bool(row.get("close_requested")),
        "close_requested_by": row.get("close_requested_by") or "",
        "waiting_for": (
            "" if row.get("is_closed")
            else ("staff" if row.get("needs_answer") else "partner")
        ),
        "order_number": row.get("order_number") or "",
        "product": row.get("product") or "",
        "problem": row.get("problem") or "",
        "source": source,
        "email_from": row.get("email_from") or "",
        "email_subject": row.get("email_subject") or "",
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def attach_ozon(db: Session, chats: list[dict], *, live: bool = False) -> None:
    from . import ozon_posting
    for chat in chats:
        order = str(chat.get("order_number") or "").strip()
        if not order:
            chat["ozon"] = None
            continue
        try:
            info = ozon_posting.lookup(db, order, live=live)
        except Exception as e:
            logger.warning("[TShopping] ozon lookup %s: %s", order, e)
            info = None
        chat["ozon"] = info
        if info and not chat.get("product"):
            names = [p.get("name") for p in (info.get("products") or []) if p.get("name")]
            if names:
                chat["product"] = ", ".join(names)[:240]


def remember_ozon_product(db: Session, chat_id: str, ozon: Optional[dict]) -> None:
    names = [p.get("name") for p in ((ozon or {}).get("products") or []) if p.get("name")]
    if not names:
        return
    db.execute(text("""
        UPDATE tshopping_chats
        SET product = COALESCE(NULLIF(product, ''), :product), updated_at = now()
        WHERE chat_id = :cid
    """), {"product": ", ".join(names)[:240], "cid": chat_id})
    db.commit()


def list_chats(db: Session, *, closed: bool = False, search: str = "") -> dict:
    params: dict[str, Any] = {}
    where = "is_closed = :closed"
    params["closed"] = closed
    q = (search or "").strip()
    if q:
        where += """ AND (
            display_name ILIKE :q OR username ILIKE :q
            OR last_message ILIKE :q OR chat_id ILIKE :q
            OR COALESCE(order_number,'') ILIKE :q
            OR COALESCE(product,'') ILIKE :q
            OR COALESCE(problem,'') ILIKE :q
        )"""
        params["q"] = f"%{q}%"
    rows = db.execute(text(f"""
        SELECT {CHAT_COLUMNS}
        FROM tshopping_chats
        WHERE {where}
        ORDER BY needs_answer DESC, unread DESC, last_ts DESC NULLS LAST
        LIMIT 300
    """), params).mappings().all()
    items = [serialize_chat(dict(r)) for r in rows]
    attach_ozon(db, items, live=False)
    counts = db.execute(text("""
        SELECT
            count(*) FILTER (WHERE NOT is_closed AND needs_answer) AS pending,
            count(*) FILTER (WHERE NOT is_closed AND unread) AS unread,
            count(*) FILTER (WHERE NOT is_closed AND unread_partner) AS unread_partner,
            count(*) FILTER (WHERE NOT is_closed AND NOT needs_answer) AS waiting_partner,
            count(*) FILTER (WHERE NOT is_closed AND close_requested) AS close_requested,
            count(*) FILTER (WHERE NOT is_closed) AS open,
            count(*) FILTER (WHERE is_closed) AS closed
        FROM tshopping_chats
    """)).mappings().first() or {}
    return {
        "items": items,
        "counts": {
            "pending": int(counts.get("pending") or 0),
            "unread": int(counts.get("unread") or 0),
            "unread_partner": int(counts.get("unread_partner") or 0),
            "waiting_partner": int(counts.get("waiting_partner") or 0),
            "close_requested": int(counts.get("close_requested") or 0),
            "open": int(counts.get("open") or 0),
            "closed": int(counts.get("closed") or 0),
        },
    }


def list_messages(db: Session, chat_id: str) -> list[dict]:
    rows = db.execute(text("""
        SELECT id, tg_message_id, sender, sender_name, manager_login, body,
               attachments, created_at
        FROM tshopping_messages
        WHERE chat_id = :cid
        ORDER BY created_at ASC, id ASC
    """), {"cid": chat_id}).mappings().all()
    out = []
    for row in rows:
        created = row["created_at"]
        atts = row["attachments"] or []
        if isinstance(atts, str):
            try:
                atts = json.loads(atts)
            except Exception:
                atts = []
        out.append({
            "id": row["id"],
            "sender": row["sender"],
            "sender_name": row["sender_name"] or "",
            "manager_login": row["manager_login"] or "",
            "text": row["body"] or "",
            "attachments": atts,
            "createdAt": created.isoformat() if hasattr(created, "isoformat") else created,
        })
    return out


def mark_read(db: Session, chat_id: str, *, partner: bool = False) -> None:
    if partner:
        db.execute(text("""
            UPDATE tshopping_chats SET unread_partner = false, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": chat_id})
    else:
        db.execute(text("""
            UPDATE tshopping_chats SET unread = false, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": chat_id})
    db.commit()


def close_chat(db: Session, chat_id: str) -> Optional[dict]:
    db.execute(text("""
        UPDATE tshopping_chats
        SET is_closed = true,
            needs_answer = false,
            unread = false,
            unread_partner = false,
            close_requested = false,
            updated_at = now()
        WHERE chat_id = :cid
    """), {"cid": chat_id})
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    return serialize_chat(chat) if chat else None


def reopen_chat(db: Session, chat_id: str) -> Optional[dict]:
    db.execute(text("""
        UPDATE tshopping_chats
        SET is_closed = false, updated_at = now()
        WHERE chat_id = :cid
    """), {"cid": chat_id})
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    return serialize_chat(chat) if chat else None


def save_manager_message(
    db: Session,
    chat_id: str,
    *,
    body: str,
    attachments: list[dict],
    manager_login: str,
    tg_message_id: Optional[int] = None,
    email_message_id: Optional[str] = None,
) -> dict:
    preview = _preview(body, attachments, False)
    mail_id = (email_message_id or "").strip() or None
    row = db.execute(text("""
        INSERT INTO tshopping_messages (
            chat_id, tg_message_id, sender, sender_name, manager_login, body, attachments,
            email_message_id
        ) VALUES (
            :chat_id, :mid, 'manager', :sender_name, :login, :body, CAST(:attachments AS jsonb),
            :email_id
        )
        RETURNING id, created_at
    """), {
        "chat_id": chat_id,
        "mid": tg_message_id,
        "sender_name": manager_login or "Менеджер",
        "login": manager_login or "",
        "body": body or "",
        "attachments": _json(attachments),
        "email_id": mail_id,
    }).fetchone()
    db.execute(text("""
        UPDATE tshopping_chats
        SET last_message = :preview,
            last_sender = 'manager',
            last_ts = now(),
            needs_answer = false,
            unread = false,
            unread_partner = true,
            is_closed = false,
            last_outbound_message_id = COALESCE(:email_id, last_outbound_message_id),
            updated_at = now()
        WHERE chat_id = :cid
    """), {"preview": preview, "cid": chat_id, "email_id": mail_id})
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    created = row[1] if row else datetime.now(timezone.utc)
    return {
        "id": row[0] if row else None,
        "sender": "manager",
        "sender_name": manager_login or "Видовито",
        "manager_login": manager_login or "",
        "text": body or "",
        "attachments": attachments,
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def save_partner_message(
    db: Session,
    chat_id: str,
    *,
    body: str,
    attachments: list[dict],
    login: str,
    display_name: str = "",
) -> dict:
    preview = _preview(body, attachments, False)
    who = (display_name or login or "Т-Шопинг").strip()
    row = db.execute(text("""
        INSERT INTO tshopping_messages (
            chat_id, sender, sender_name, manager_login, body, attachments
        ) VALUES (
            :chat_id, 'client', :sender_name, :login, :body, CAST(:attachments AS jsonb)
        )
        RETURNING id, created_at
    """), {
        "chat_id": chat_id,
        "sender_name": who,
        "login": login or "",
        "body": body or "",
        "attachments": _json(attachments),
    }).fetchone()
    db.execute(text("""
        UPDATE tshopping_chats
        SET last_message = :preview,
            last_sender = 'client',
            last_ts = now(),
            needs_answer = true,
            unread = true,
            unread_partner = false,
            close_requested = false,
            is_closed = false,
            updated_at = now()
        WHERE chat_id = :cid
    """), {"preview": preview, "cid": chat_id})
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    created = row[1] if row else datetime.now(timezone.utc)
    return {
        "id": row[0] if row else None,
        "sender": "client",
        "sender_name": who,
        "manager_login": login or "",
        "text": body or "",
        "attachments": attachments,
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def request_close(db: Session, chat_id: str, login: str) -> Optional[dict]:
    body = "Видовито просит закрыть заявку. Если вопрос решён — закройте её."
    db.execute(text("""
        INSERT INTO tshopping_messages (
            chat_id, sender, sender_name, manager_login, body, attachments
        ) VALUES (
            :chat_id, 'system', :sender_name, :login, :body, '[]'::jsonb
        )
    """), {
        "chat_id": chat_id,
        "sender_name": "Видовито",
        "login": login or "",
        "body": body,
    })
    db.execute(text("""
        UPDATE tshopping_chats
        SET close_requested = true,
            close_requested_by = :login,
            last_message = :preview,
            last_sender = 'system',
            last_ts = now(),
            unread_partner = true,
            updated_at = now()
        WHERE chat_id = :cid AND is_closed = false
    """), {"login": login or "", "preview": body, "cid": chat_id})
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    return serialize_chat(chat) if chat else None


def find_by_order(db: Session, order: str, *, include_closed: bool = False) -> Optional[dict]:
    key = (order or "").strip()
    if not key:
        return None
    closed_sql = "" if include_closed else "AND NOT is_closed"
    row = db.execute(text(f"""
        SELECT {CHAT_COLUMNS}
        FROM tshopping_chats
        WHERE lower(btrim(order_number)) = lower(btrim(:order))
          {closed_sql}
        ORDER BY is_closed ASC, last_ts DESC NULLS LAST
        LIMIT 1
    """), {"order": key}).mappings().first()
    return dict(row) if row else None


def find_open_by_order(db: Session, order: str) -> Optional[dict]:
    return find_by_order(db, order, include_closed=False)


def create_site_ticket(
    db: Session,
    *,
    order: str,
    problem: str,
    login: str,
    display_name: str = "",
) -> dict:
    order = (order or "").strip()
    problem = (problem or "").strip()
    if not order:
        raise ValueError("Нужен номер заказа")
    if not problem:
        raise ValueError("Опишите проблему")
    who = (display_name or login or "Т-Шопинг").strip()
    existing = find_open_by_order(db, order)
    if existing:
        saved = save_partner_message(
            db, existing["chat_id"],
            body=problem, attachments=[], login=login, display_name=who,
        )
        db.execute(text("""
            UPDATE tshopping_chats SET problem = :problem, updated_at = now()
            WHERE chat_id = :cid
        """), {"problem": problem, "cid": existing["chat_id"]})
        db.commit()
        with db.bind.connect() as conn:
            chat = load_chat(conn, existing["chat_id"])
        return {"chat": serialize_chat(chat) if chat else serialize_chat(existing), "message": saved, "reused": True}

    chat_id = f"web:{order_key(order)}:{uuid.uuid4().hex[:8]}"
    body = form.format_ticket({"order": order, "problem": problem}, who, "")
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO tshopping_chats (
            chat_id, tg_chat_id, display_name, last_message, last_sender, last_ts,
            needs_answer, is_closed, unread, unread_partner, created_at, updated_at,
            order_number, problem
        ) VALUES (
            :chat_id, NULL, :display_name, :preview, 'client', :now,
            true, false, true, false, :now, :now,
            :order, :problem
        )
    """), {
        "chat_id": chat_id,
        "display_name": who,
        "preview": _preview(body, [], False),
        "now": now,
        "order": order,
        "problem": problem,
    })
    row = db.execute(text("""
        INSERT INTO tshopping_messages (
            chat_id, sender, sender_name, manager_login, body, attachments
        ) VALUES (
            :chat_id, 'client', :sender_name, :login, :body, '[]'::jsonb
        )
        RETURNING id, created_at
    """), {
        "chat_id": chat_id,
        "sender_name": who,
        "login": login or "",
        "body": body,
    }).fetchone()
    db.commit()
    with db.bind.connect() as conn:
        chat = load_chat(conn, chat_id)
    if chat:
        _publish(serialize_chat(chat))
    created = row[1] if row else now
    return {
        "chat": serialize_chat(chat) if chat else {"id": chat_id, "chat_id": chat_id},
        "message": {
            "id": row[0] if row else None,
            "sender": "client",
            "sender_name": who,
            "text": body,
            "attachments": [],
            "createdAt": created.isoformat() if hasattr(created, "isoformat") else created,
        },
        "reused": False,
    }


def send_outbound(chat: dict, body: str, attachments: list[dict]) -> tuple[bool, str]:
    """Ответ в тот канал, откуда пришла заявка: почта или Telegram."""
    if is_email_chat(chat):
        from . import tshopping_mail
        return tshopping_mail.send_reply(chat, body, attachments)
    ok = send_reply_to_telegram(
        chat.get("tg_chat_id"),
        body,
        attachments,
        order_number=chat.get("order_number") or "",
    )
    return ok, ""


def send_reply_to_telegram(
    tg_chat_id: Any,
    body: str,
    attachments: list[dict],
    order_number: str = "",
) -> bool:
    if not tg_chat_id:
        return True
    send_body = body or ""
    order = (order_number or "").strip()
    if order:
        send_body = f"По заказу {order}\n\n{send_body}".strip() if send_body else f"По заказу {order}"
    ok = True
    sent_any = False
    images = [a for a in attachments if a.get("kind") == "image" and a.get("filename")]
    docs = [a for a in attachments if a.get("kind") != "image" and a.get("filename")]
    caption_used = False
    for img in images:
        caption = send_body if not caption_used else ""
        if telegram_proxy.send_photo(tg_chat_id, absolute_upload_url(img["filename"]), caption):
            sent_any = True
            caption_used = True
        else:
            ok = False
    for doc in docs:
        caption = send_body if not caption_used else ""
        if telegram_proxy.send_document(tg_chat_id, absolute_upload_url(doc["filename"]), caption):
            sent_any = True
            caption_used = True
        else:
            ok = False
    if send_body and not caption_used:
        if telegram_proxy.send_text(tg_chat_id, send_body):
            sent_any = True
        else:
            ok = False
    return ok and sent_any


def _claim_update(conn: Connection, incoming: dict) -> bool:
    """True, если событие новое и его нужно обработать."""
    update_id = incoming.get("update_id")
    if update_id is None:
        return True
    inserted = conn.execute(text("""
        INSERT INTO tshopping_updates (update_id)
        VALUES (:uid)
        ON CONFLICT (update_id) DO NOTHING
    """), {"uid": int(update_id)})
    conn.commit()
    if inserted.rowcount == 0 and not incoming.get("edited"):
        return False
    incoming["_claimed"] = True
    return True


def _load_form(conn: Connection, tg_chat_id: int) -> tuple[str, dict]:
    row = conn.execute(text("""
        SELECT step, draft FROM tshopping_form_state WHERE tg_chat_id = :id
    """), {"id": tg_chat_id}).mappings().first()
    if not row:
        return "idle", {}
    draft = row["draft"] or {}
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except Exception:
            draft = {}
    return str(row["step"] or "idle"), dict(draft)


def _save_form(conn: Connection, tg_chat_id: int, step: str, draft: dict) -> None:
    conn.execute(text("""
        INSERT INTO tshopping_form_state (tg_chat_id, step, draft, updated_at)
        VALUES (:id, :step, CAST(:draft AS jsonb), now())
        ON CONFLICT (tg_chat_id) DO UPDATE SET
            step = EXCLUDED.step, draft = EXCLUDED.draft, updated_at = now()
    """), {"id": tg_chat_id, "step": step, "draft": _json(draft or {})})
    conn.commit()


def _send_bot(tg_chat_id: Any, text_body: str, markup=None) -> bool:
    """Ответ в Telegram не держит webhook: иначе Telegram ждёт и повторяет минутами."""
    if not text_body:
        return False

    def _run() -> None:
        try:
            telegram_proxy.send_text(tg_chat_id, text_body, reply_markup=markup)
        except Exception as e:
            logger.warning("[TShopping] bot reply failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _store_ticket(conn: Connection, incoming: dict, draft: dict) -> dict:
    ticket = dict(incoming)
    ticket["text"] = form.format_ticket(
        draft, incoming.get("display_name") or "", incoming.get("username") or ""
    )
    ticket["attachments"] = list(draft.get("attachments") or [])
    ticket["is_start"] = False
    ticket["command"] = None
    ticket["ticket_chat_id"] = chat_id_for_ticket(incoming["tg_chat_id"], draft.get("order") or "")
    result = ingest_incoming(conn, ticket, staff_ids=set())
    chat_id = result.get("chat_id") or chat_id_for(incoming["tg_chat_id"])
    conn.execute(text("""
        UPDATE tshopping_chats
        SET order_number = :order, product = :product, problem = :problem, updated_at = now()
        WHERE chat_id = :cid
    """), {
        "order": draft.get("order") or "",
        "product": draft.get("product") or "",
        "problem": draft.get("problem") or "",
        "cid": chat_id,
    })
    conn.commit()
    return result


def handle_incoming(conn: Connection, incoming: dict, staff_ids: Optional[set[str]] = None) -> dict:
    tg_chat_id = incoming["tg_chat_id"]
    if staff_ids is None:
        staff_ids = staff_chat_ids(conn)
    if str(tg_chat_id) in staff_ids:
        return {"status": "skipped_staff", "tg_chat_id": tg_chat_id}

    if not _claim_update(conn, incoming):
        return {"status": "duplicate", "update_id": incoming.get("update_id")}

    callback_id = incoming.get("callback_id")
    if callback_id:
        telegram_proxy.call("answerCallbackQuery", {"callback_query_id": callback_id})

    cmd = incoming.get("command")
    step, draft = _load_form(conn, tg_chat_id)

    if step in ("", "idle") and not cmd:
        target_id = (draft or {}).get("last_chat_id")
        target = load_chat(conn, target_id) if target_id else None
        if not target:
            target = latest_chat_for_user(conn, tg_chat_id)
        if target:
            incoming = dict(incoming)
            incoming["ticket_chat_id"] = target["chat_id"]
            return ingest_incoming(conn, incoming, staff_ids=set())

    outcome = form.apply_form(step, draft, incoming)
    save_draft = dict(outcome.get("draft") or {})
    result = {"status": "form", "step": outcome["step"]}
    if outcome.get("submit"):
        stored = _store_ticket(conn, incoming, outcome["submit"])
        save_draft = {
            "last_chat_id": stored.get("chat_id"),
            "last_order": (outcome["submit"] or {}).get("order") or "",
        }
        result = stored
        result["status"] = "ticket"
    _save_form(conn, tg_chat_id, outcome["step"], save_draft)
    if outcome.get("reply"):
        result["bot_reply"] = outcome["reply"]
        result["bot_markup"] = outcome.get("markup")
        result["tg_chat_id"] = tg_chat_id
        if not incoming.get("reply_via_poller"):
            _send_bot(tg_chat_id, outcome["reply"], outcome.get("markup"))
    return result


def process_update(db: Session, update: dict, *, reply_via_poller: bool = False) -> dict:
    incoming = extract_incoming(update)
    if not incoming:
        return {"status": "ignored"}
    incoming["reply_via_poller"] = bool(reply_via_poller)
    with db.bind.connect() as conn:
        staff = staff_chat_ids(conn)
        return handle_incoming(conn, incoming, staff)


def set_webhook() -> bool:
    secret = webhook_secret()
    payload: dict[str, Any] = {
        "url": webhook_url(),
        "ip_address": os.getenv("TSHOPPING_WEBHOOK_IP", "72.56.240.22"),
        "allowed_updates": ["message", "edited_message", "callback_query"],
        "drop_pending_updates": False,
    }
    if secret:
        payload["secret_token"] = secret
    result = telegram_proxy.call("setWebhook", payload)
    if not result:
        return False
    info = telegram_proxy.call("getWebhookInfo", {}) or {}
    inner = info.get("result") if isinstance(info.get("result"), dict) else {}
    url = str((inner or {}).get("url") or "")
    ok = webhook_url() in url if url else bool(result.get("ok"))
    logger.info("[TShopping] setWebhook %s url=%s info=%s", "ok" if ok else "fail", payload["url"], url[:80])
    return ok


_poll_offset = 0


async def start_worker(get_db_func) -> None:
    """Не ставим webhook с RU-сервера. Письма Т-Шопинга забираем с IMAP.

    Telegram до 72.56.240.22 часто не достучится (минуты ожидания).
    Алерты это не ломает: они по-прежнему сервер → Cloudflare Worker → Telegram.
    Входящие заявки из бота забирает poller с Mac или webhook на Cloudflare Worker.
    """
    from . import tshopping_mail

    logger.info("[TShopping] skip setWebhook from origin")
    if not tshopping_mail.configured():
        logger.warning("[TShopping] mail not configured — IMAP worker idle")
        return
    logger.info("[TShopping] mail worker starting for %s", tshopping_mail.mail_user())
    await asyncio.sleep(15)
    while True:
        try:
            n = await asyncio.to_thread(tshopping_mail.poll_inbox_isolated)
            if n:
                logger.info("[TShopping] mail ingested %s", n)
        except Exception as e:
            logger.exception("[TShopping] mail poll: %s", e)
        await asyncio.sleep(tshopping_mail.poll_interval())


def poll_once(db: Session) -> int:
    global _poll_offset
    payload = {"offset": _poll_offset, "timeout": 0, "allowed_updates": ["message", "edited_message", "callback_query"]}
    data = telegram_proxy.call("getUpdates", payload, timeout=15)
    results = (data or {}).get("result") or []
    handled = 0
    for update in results:
        try:
            uid = int(update.get("update_id") or 0)
            _poll_offset = max(_poll_offset, uid + 1)
            process_update(db, update)
            handled += 1
        except Exception as e:
            logger.warning("[TShopping] poll update error: %s", e)
    return handled
