"""Почта Т-Шопинга: письма с help@vidovito.team.

Забираем только письма от lifestyle_service@emails.tinkoff.ru.
Ответ из раздела «Запросы Т-Шопинг» уходит с той же почты в ту же переписку.
"""
from __future__ import annotations

import base64
import imaplib
import logging
import os
import re
import smtplib
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("tshopping.mail")

DEFAULT_IMAP_USER = "vidovito.team/mursalimov/help"
DEFAULT_SMTP_USER = "mursalimov@vidovito.team"
DEFAULT_FROM = "help@vidovito.team"
DEFAULT_REPLY_TO = "lifestyle_service@tbank.ru"
DEFAULT_ALLOWED = (
    "lifestyle_service@emails.tinkoff.ru,"
    "lifestyle_service@tbank.ru,"
    "ls_service@tbank.ru"
)
DEFAULT_FROM_NAME = "Видовито"
DEFAULT_FOLDERS = "INBOX,Т-шопинг"

LABELED_ORDER_RE = re.compile(
    r"номер\s+заказа\s*[:№#]?\s*([0-9A-Za-z][0-9A-Za-z\-]{3,})",
    re.I,
)
SHOP_ORDER_RE = re.compile(r"\b(\d{6,}-\d{2,}-\d+)\b")
SUBJECT_PAIR_RE = re.compile(r"заказу?\s+\d+\s*/\s*([0-9][0-9A-Za-z\-]+)", re.I)
ORDER_RES = (
    LABELED_ORDER_RE,
    SUBJECT_PAIR_RE,
    SHOP_ORDER_RE,
    re.compile(r"заказ(?:а|у|ом|е)?\s*[:№#]\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.I),
    re.compile(r"заказ(?:а|у|ом|е)?\s+№?\s*([A-Za-z0-9][A-Za-z0-9\-]{5,})", re.I),
    re.compile(r"order(?:\s*(?:id|number))?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.I),
    re.compile(r"№\s*([0-9]{6,})", re.I),
)
FOOTER_MARKERS = (
    "Просим рассмотреть и предоставить решение",
    "В ответном письме сохраните",
    "Это электронное письмо строго конфиденциально",
)

SKIP_ORDER = {"заказ", "заказа", "номер", "order", "number", "id"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
KEEP_EXT = IMAGE_EXT | {".pdf"}
KEEP_MIME = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
    "application/pdf",
}


def mail_user() -> str:
    return (os.getenv("TSHOPPING_MAIL_USER") or DEFAULT_IMAP_USER).strip()


def smtp_user() -> str:
    return (os.getenv("TSHOPPING_SMTP_USER") or DEFAULT_SMTP_USER).strip()


def mail_pass() -> str:
    return (os.getenv("TSHOPPING_MAIL_PASS") or "").replace(" ", "").strip()


def mail_from() -> str:
    return (os.getenv("TSHOPPING_MAIL_FROM") or DEFAULT_FROM).strip()


def configured() -> bool:
    return bool(mail_user() and mail_pass())


def poll_interval() -> int:
    try:
        return max(20, int(os.getenv("TSHOPPING_MAIL_POLL_SEC") or "45"))
    except ValueError:
        return 45


def lookback_days() -> int:
    try:
        return max(1, int(os.getenv("TSHOPPING_MAIL_LOOKBACK_DAYS") or "21"))
    except ValueError:
        return 21


def allowed_senders() -> set[str]:
    raw = os.getenv("TSHOPPING_MAIL_ALLOWED_FROM") or DEFAULT_ALLOWED
    extra = {p.strip().lower() for p in raw.split(",") if p.strip()}
    builtin = {p.strip().lower() for p in DEFAULT_ALLOWED.split(",") if p.strip()}
    return extra | builtin


def imap_utf7_encode(name: str) -> str:
    """Имя папки Яндекса в modified UTF-7, как в IMAP LIST."""
    parts: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").replace("/", ",").rstrip("=")
        parts.append("&" + b64 + "-")
        buf.clear()

    for ch in name or "":
        code = ord(ch)
        if ch == "&":
            flush()
            parts.append("&-")
        elif 0x20 <= code <= 0x7E:
            flush()
            parts.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(parts)


def encode_mailbox(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "INBOX"
    if raw.startswith("&") and raw.endswith("-"):
        return raw
    if all(ord(ch) < 128 for ch in raw):
        return raw
    return imap_utf7_encode(raw)


def mail_folders() -> list[str]:
    raw = os.getenv("TSHOPPING_MAIL_FOLDERS") or DEFAULT_FOLDERS
    names = [p.strip() for p in raw.split(",") if p.strip()]
    encoded = []
    for name in names:
        box = encode_mailbox(name)
        if box not in encoded:
            encoded.append(box)
    return encoded or ["INBOX"]


def _decode_header(value: Any) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        value = str(value)
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addr(value: str) -> str:
    _, addr = parseaddr(_decode_header(value) or "")
    return (addr or "").strip().lower()


def sender_allowed(from_addr: str, sender_addr: str = "") -> bool:
    allowed = allowed_senders()
    return _addr(from_addr) in allowed or _addr(sender_addr) in allowed


def extract_order_number(*parts: str) -> str:
    blob = "\n".join(p for p in parts if p)
    for rx in ORDER_RES:
        for match in rx.finditer(blob):
            raw = (match.group(1) or "").strip(".,;:()[]<>\"'")
            if raw.lower() in SKIP_ORDER:
                continue
            if len(raw) >= 4:
                return raw
    return ""


def strip_tbank_footer(text: str) -> str:
    cut = len(text or "")
    for marker in FOOTER_MARKERS:
        idx = (text or "").find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return (text or "")[:cut].strip()


def normalize_message_id(mid: str) -> str:
    inner = (mid or "").strip().strip("<>").strip()
    if inner.lower().startswith("bcc."):
        inner = inner[4:]
    return f"<{inner}>" if inner else ""


def expand_message_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in ids or []:
        for variant in (item, normalize_message_id(item)):
            key = (variant or "").strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def outbound_to(chat: dict) -> str:
    to_addr = (
        chat.get("email_reply_to") or chat.get("email_from") or DEFAULT_REPLY_TO
    ).strip().lower()
    if to_addr.endswith("@emails.tinkoff.ru") or to_addr.endswith("@emails.tbank.ru"):
        return DEFAULT_REPLY_TO
    return to_addr or DEFAULT_REPLY_TO


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.skip += 1
        if tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"}:
            self.parts.append("\n")
        if tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.skip:
            self.skip -= 1
        if tag in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        text = re.sub(r"[ \t]+", " ", data)
        if text.strip():
            self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def strip_quoted(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^from:\s", stripped, re.I):
            break
        if re.match(r"^[-_]{2,}\s*(original message|исходное сообщение)", stripped, re.I):
            break
        if "пересылаемое сообщение" in stripped.lower():
            break
        if re.match(r"^on .+ wrote:$", stripped, re.I):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        data = part.get_payload()
        return data if isinstance(data, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _is_skippable_inline(part, data: bytes) -> bool:
    disp = (part.get_content_disposition() or "").lower()
    mime = (part.get_content_type() or "").lower()
    if disp == "inline" and mime.startswith("image/") and len(data) < 4096:
        return True
    name = (part.get_filename() or "").lower()
    if name.endswith((".gif", ".png")) and len(data) < 2048:
        return True
    return False


def parse_rfc822(raw: bytes) -> Optional[dict]:
    if not raw:
        return None
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    from_addr = _addr(msg.get("From") or "")
    sender_addr = _addr(msg.get("Sender") or "")
    message_id = (msg.get("Message-ID") or "").strip()
    our = {mail_from().lower(), smtp_user().lower()}
    if from_addr in our:
        return {"skip": "self", "from_addr": from_addr, "message_id": message_id}
    if not sender_allowed(from_addr, sender_addr):
        return {
            "skip": "sender",
            "from_addr": from_addr,
            "message_id": message_id,
            "subject": _decode_header(msg.get("Subject") or ""),
        }

    auto = (msg.get("Auto-Submitted") or "").lower()
    if auto and auto not in {"no"}:
        return {"skip": "auto", "from_addr": from_addr, "message_id": message_id}

    subject = _decode_header(msg.get("Subject") or "")
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    references = (msg.get("References") or "").strip()
    reply_to = _addr(msg.get("Reply-To") or "") or from_addr

    texts: list[str] = []
    htmls: list[str] = []
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        mime = (part.get_content_type() or "application/octet-stream").lower()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""
        if mime == "text/plain" and disp != "attachment":
            texts.append(_part_text(part))
            continue
        if mime == "text/html" and disp != "attachment":
            htmls.append(_part_text(part))
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload or _is_skippable_inline(part, payload):
            continue
        ext = Path(filename or "").suffix.lower()
        if mime not in KEEP_MIME and ext not in KEEP_EXT:
            continue
        attachments.append({
            "name": filename or ("file" + (ext or ".bin")),
            "mime": mime,
            "data": payload,
        })

    plain = strip_quoted("\n\n".join(t.strip() for t in texts if t.strip()))
    html_text = html_to_text("\n".join(htmls)) if htmls else ""
    if html_text and (not plain or len(html_text) > len(plain) + 10):
        body = strip_quoted(html_text)
    else:
        body = plain or strip_quoted(html_text)
    body = strip_tbank_footer(body)
    order = extract_order_number(subject, body, html_text)

    created = None
    try:
        created = parsedate_to_datetime(msg.get("Date") or "")
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except Exception:
        created = None

    return {
        "skip": "",
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "from_addr": from_addr,
        "reply_to": reply_to,
        "subject": subject,
        "body": body,
        "order_number": order,
        "attachments": attachments,
        "date": created or datetime.now(timezone.utc),
    }


def reference_ids(parsed: dict) -> list[str]:
    blob = " ".join([
        parsed.get("in_reply_to") or "",
        parsed.get("references") or "",
    ])
    found = re.findall(r"<[^>]+>", blob)
    if parsed.get("in_reply_to") and parsed["in_reply_to"] not in found:
        found.insert(0, parsed["in_reply_to"].strip())
    out = []
    seen = set()
    for item in found:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return expand_message_ids(out)


def reply_subject(subject: str) -> str:
    raw = (subject or "").strip() or "Заявка Т-Шопинг"
    if re.match(r"^(re|aw|fw|fwd)\s*:", raw, re.I):
        return raw
    return f"Re: {raw}"


def build_reply_message(
    *,
    chat: dict,
    body: str,
    attachments: list[dict],
    upload_root: Path,
) -> EmailMessage:
    msg = EmailMessage()
    from_addr = mail_from()
    to_addr = outbound_to(chat)
    inbound = (chat.get("last_inbound_message_id") or chat.get("email_message_id") or "").strip()
    refs = (chat.get("email_references") or "").strip()
    order = (chat.get("order_number") or "").strip()
    text_body = (body or "").strip()
    if order and order.lower() not in text_body.lower():
        text_body = f"По заказу {order}\n\n{text_body}".strip() if text_body else f"По заказу {order}"

    msg["From"] = formataddr((DEFAULT_FROM_NAME, from_addr))
    msg["To"] = to_addr
    msg["Subject"] = reply_subject(chat.get("email_subject") or "")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="vidovito.team")
    if inbound:
        msg["In-Reply-To"] = inbound
        chain = f"{refs} {inbound}".strip() if refs else inbound
        msg["References"] = chain
    msg.set_content(text_body or " ")

    for item in attachments or []:
        filename = item.get("filename") or ""
        path = upload_root / filename if filename else None
        if not path or not path.is_file():
            continue
        raw = path.read_bytes()
        mime = (item.get("mime") or "application/octet-stream").split(";")[0].strip()
        main, _, sub = mime.partition("/")
        if not sub:
            main, sub = "application", "octet-stream"
        msg.add_attachment(
            raw,
            maintype=main,
            subtype=sub,
            filename=item.get("name") or filename,
        )
    return msg


def _save_attachments(items: list[dict]) -> list[dict]:
    from . import tshopping
    out = []
    for item in items or []:
        data = item.get("data") or b""
        if not data:
            continue
        name = item.get("name") or "file.bin"
        ext = Path(name).suffix.lower()
        mime = (item.get("mime") or "").lower()
        if ext not in KEEP_EXT:
            if mime in {"image/jpeg", "image/jpg"}:
                ext = ".jpg"
            elif mime == "image/png":
                ext = ".png"
            elif mime == "application/pdf":
                ext = ".pdf"
            else:
                ext = ".bin"
        filename = f"{uuid.uuid4().hex}{ext}"
        dest = tshopping.upload_dir() / filename
        dest.write_bytes(data)
        kind = "image" if ext in IMAGE_EXT else "document"
        out.append({
            "kind": kind,
            "name": name,
            "url": tshopping.public_upload_url(filename),
            "filename": filename,
            "mime": mime or ("image/jpeg" if kind == "image" else "application/octet-stream"),
        })
    return out


def _already_seen(db: Session, message_id: str) -> bool:
    if not message_id:
        return False
    row = db.execute(text("""
        SELECT 1 FROM tshopping_mail_seen WHERE message_id = :mid
        UNION ALL
        SELECT 1 FROM tshopping_messages WHERE email_message_id = :mid
        LIMIT 1
    """), {"mid": message_id}).first()
    return bool(row)


def _mark_seen(db: Session, message_id: str, imap_uid: Optional[int], status: str, mailbox: str = "INBOX") -> None:
    if not message_id:
        message_id = f"uid:{imap_uid or uuid.uuid4().hex}"
    db.execute(text("""
        INSERT INTO tshopping_mail_seen (message_id, imap_uid, status, mailbox)
        VALUES (:mid, :uid, :status, :box)
        ON CONFLICT (message_id) DO UPDATE SET
            status = EXCLUDED.status,
            imap_uid = COALESCE(EXCLUDED.imap_uid, tshopping_mail_seen.imap_uid),
            mailbox = COALESCE(EXCLUDED.mailbox, tshopping_mail_seen.mailbox)
    """), {"mid": message_id, "uid": imap_uid, "status": status, "box": mailbox or "INBOX"})


def _find_by_refs(db: Session, ids: list[str]) -> Optional[dict]:
    from . import tshopping
    if not ids:
        return None
    row = db.execute(text(f"""
        SELECT {tshopping.CHAT_COLUMNS}
        FROM tshopping_chats
        WHERE last_inbound_message_id = ANY(:ids)
           OR last_outbound_message_id = ANY(:ids)
           OR email_message_id = ANY(:ids)
        ORDER BY last_ts DESC NULLS LAST
        LIMIT 1
    """), {"ids": ids}).mappings().first()
    if row:
        return dict(row)
    msg = db.execute(text("""
        SELECT chat_id FROM tshopping_messages
        WHERE email_message_id = ANY(:ids)
        ORDER BY id DESC
        LIMIT 1
    """), {"ids": ids}).first()
    if not msg:
        return None
    with db.bind.connect() as conn:
        chat = tshopping.load_chat(conn, msg[0])
    return chat


def ingest_parsed(db: Session, parsed: dict, *, imap_uid: Optional[int] = None, mailbox: str = "INBOX") -> dict:
    from . import tshopping

    if parsed.get("skip"):
        _mark_seen(db, parsed.get("message_id") or "", imap_uid, parsed["skip"], mailbox)
        db.commit()
        return {"status": "skipped", "reason": parsed["skip"]}

    message_id = (parsed.get("message_id") or "").strip()
    if message_id and _already_seen(db, message_id):
        return {"status": "duplicate", "message_id": message_id}

    attachments = _save_attachments(parsed.get("attachments") or [])
    order = (parsed.get("order_number") or "").strip()
    body = (parsed.get("body") or "").strip()
    subject = (parsed.get("subject") or "").strip()
    if not body and not attachments:
        _mark_seen(db, message_id, imap_uid, "empty", mailbox)
        db.commit()
        return {"status": "skipped", "reason": "empty"}
    if not body:
        body = subject or "Письмо без текста"
    preview_body = body

    chat = _find_by_refs(db, reference_ids(parsed))
    if chat is None and order:
        chat = tshopping.find_by_order(db, order, include_closed=True)

    now = parsed.get("date") or datetime.now(timezone.utc)
    who = "Т-Шопинг"
    if chat:
        chat_id = chat["chat_id"]
        db.execute(text("""
            UPDATE tshopping_chats SET
                source = 'email',
                display_name = COALESCE(NULLIF(display_name, ''), :who),
                email_from = :email_from,
                email_reply_to = :email_reply_to,
                email_subject = COALESCE(NULLIF(:subject, ''), email_subject),
                email_message_id = COALESCE(NULLIF(:mid, ''), email_message_id),
                last_inbound_message_id = COALESCE(NULLIF(:mid, ''), last_inbound_message_id),
                email_references = CASE
                    WHEN COALESCE(email_references, '') = '' THEN :refs
                    ELSE email_references
                END,
                order_number = COALESCE(NULLIF(:order, ''), order_number),
                problem = COALESCE(NULLIF(:problem, ''), problem),
                last_message = :preview,
                last_sender = 'client',
                last_ts = :now,
                needs_answer = true,
                unread = true,
                unread_partner = false,
                close_requested = false,
                is_closed = false,
                updated_at = now()
            WHERE chat_id = :cid
        """), {
            "who": who,
            "email_from": parsed.get("from_addr") or "",
            "email_reply_to": DEFAULT_REPLY_TO,
            "subject": subject,
            "mid": message_id,
            "refs": parsed.get("references") or parsed.get("in_reply_to") or "",
            "order": order,
            "problem": body[:500],
            "preview": tshopping._preview(preview_body, attachments, False),
            "now": now,
            "cid": chat_id,
        })
        row = db.execute(text("""
            INSERT INTO tshopping_messages (
                chat_id, sender, sender_name, body, attachments, created_at, email_message_id
            ) VALUES (
                :chat_id, 'client', :who, :body, CAST(:attachments AS jsonb), :now, :mid
            )
            RETURNING id
        """), {
            "chat_id": chat_id,
            "who": who,
            "body": body,
            "attachments": tshopping._json(attachments),
            "now": now,
            "mid": message_id or None,
        }).fetchone()
        _mark_seen(db, message_id, imap_uid, "ingested", mailbox)
        db.commit()
        with db.bind.connect() as conn:
            updated = tshopping.load_chat(conn, chat_id)
        if updated:
            tshopping._publish(tshopping.serialize_chat(updated))
        return {"status": "ok", "chat_id": chat_id, "message_id": row[0] if row else None, "reused": True}

    chat_id = f"mail:{tshopping.order_key(order or subject or 'pismo')}:{uuid.uuid4().hex[:8]}"
    db.execute(text("""
        INSERT INTO tshopping_chats (
            chat_id, tg_chat_id, display_name, last_message, last_sender, last_ts,
            needs_answer, is_closed, unread, unread_partner, created_at, updated_at,
            order_number, problem, source, email_from, email_reply_to, email_subject,
            email_message_id, last_inbound_message_id, email_references
        ) VALUES (
            :chat_id, NULL, :who, :preview, 'client', :now,
            true, false, true, false, :now, :now,
            :order, :problem, 'email', :email_from, :email_reply_to, :subject,
            :mid, :mid, :refs
        )
    """), {
        "chat_id": chat_id,
        "who": who,
        "preview": tshopping._preview(preview_body, attachments, False),
        "now": now,
        "order": order or None,
        "problem": body[:500],
        "email_from": parsed.get("from_addr") or "",
        "email_reply_to": DEFAULT_REPLY_TO,
        "subject": subject,
        "mid": message_id or None,
        "refs": parsed.get("references") or parsed.get("in_reply_to") or "",
    })
    row = db.execute(text("""
        INSERT INTO tshopping_messages (
            chat_id, sender, sender_name, body, attachments, created_at, email_message_id
        ) VALUES (
            :chat_id, 'client', :who, :body, CAST(:attachments AS jsonb), :now, :mid
        )
        RETURNING id
    """), {
        "chat_id": chat_id,
        "who": who,
        "body": body,
        "attachments": tshopping._json(attachments),
        "now": now,
        "mid": message_id or None,
    }).fetchone()
    _mark_seen(db, message_id, imap_uid, "ingested", mailbox)
    db.commit()
    with db.bind.connect() as conn:
        created_chat = tshopping.load_chat(conn, chat_id)
    if created_chat:
        tshopping._publish(tshopping.serialize_chat(created_chat))
    return {"status": "ok", "chat_id": chat_id, "message_id": row[0] if row else None, "reused": False}


def send_reply(chat: dict, body: str, attachments: list[dict]) -> tuple[bool, str]:
    from . import tshopping
    if not configured():
        logger.warning("[TShopping] mail send skipped: no credentials")
        return False, ""
    msg = build_reply_message(
        chat=chat, body=body, attachments=attachments, upload_root=tshopping.upload_dir(),
    )
    host = os.getenv("TSHOPPING_SMTP_HOST") or "smtp.yandex.ru"
    port = int(os.getenv("TSHOPPING_SMTP_PORT") or "465")
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as smtp:
            smtp.login(smtp_user(), mail_pass())
            refused = smtp.send_message(msg)
    except Exception as e:
        logger.exception("[TShopping] SMTP send failed: %s", e)
        return False, ""
    if refused:
        logger.warning("[TShopping] SMTP refused recipients: %s", refused)
        return False, ""
    if not append_sent_copy(msg):
        logger.warning("[TShopping] sent copy was not saved to Отправленные")
    return True, str(msg["Message-ID"] or "")


def sent_mailbox(list_lines: list) -> str:
    for item in list_lines or []:
        raw = item.decode(errors="replace") if isinstance(item, (bytes, bytearray)) else str(item)
        if "\\Sent" not in raw:
            continue
        match = re.search(r'"([^"]+)"\s*$', raw)
        if match:
            return match.group(1)
        return raw.split()[-1].strip('"')
    return "Sent"


def append_sent_copy(msg: EmailMessage) -> bool:
    """SMTP сам не кладёт письмо в «Отправленные» — копируем через IMAP."""
    if not configured() or msg is None:
        return False
    client = None
    try:
        client = _login_imap()
        typ, folders = client.list()
        folder = sent_mailbox(folders if typ == "OK" else [])
        raw = msg.as_bytes()
        typ, data = client.append(
            folder,
            r"(\Seen)",
            imaplib.Time2Internaldate(datetime.now().timestamp()),
            raw,
        )
        if typ != "OK":
            logger.warning("[TShopping] IMAP APPEND %s failed: %s %s", folder, typ, data)
            return False
        return True
    except Exception as e:
        logger.warning("[TShopping] IMAP append Sent: %s", e)
        return False
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def _imap_since() -> str:
    day = datetime.now(timezone.utc) - timedelta(days=lookback_days())
    return day.strftime("%d-%b-%Y")


def _search_uids(client: imaplib.IMAP4, sender: str = "") -> list[int]:
    """Яндекс ломает SEARCH FROM/CHARSET UTF-8. Берём SINCE и фильтруем отправителя сами."""
    del sender
    since = _imap_since()
    last_error = None
    for criteria in (("SINCE", since), ("ALL",)):
        try:
            typ, data = client.uid("SEARCH", *criteria)
        except Exception as e:
            last_error = e
            continue
        if typ != "OK":
            continue
        uids = []
        for part in (data[0] or b"").split():
            try:
                uids.append(int(part))
            except ValueError:
                continue
        return uids
    if last_error:
        raise last_error
    return []


def fetch_raw(client: imaplib.IMAP4, uid: int) -> Optional[bytes]:
    typ, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if typ != "OK" or not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _login_imap():
    host = os.getenv("TSHOPPING_IMAP_HOST") or "imap.yandex.ru"
    port = int(os.getenv("TSHOPPING_IMAP_PORT") or "993")
    last_err = None
    for attempt in range(3):
        ctx = ssl.create_default_context()
        try:
            client = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
            client.login(mail_user(), mail_pass())
            return client
        except Exception as e:
            last_err = e
            time.sleep(1.2 * (attempt + 1))
    raise last_err


def _connect_imap(*, writable: bool = False, folder: str = "INBOX"):
    client = _login_imap()
    box = folder or "INBOX"
    typ, _ = client.select(box, readonly=not writable)
    if typ != "OK":
        try:
            client.logout()
        except Exception:
            pass
        raise RuntimeError(f"IMAP select {box} failed: {typ}")
    return client


def flag_uids_seen(uids: list[int], *, mailbox: str = "INBOX") -> int:
    clean = sorted({int(u) for u in uids if u})
    if not clean or not configured():
        return 0
    client = _connect_imap(writable=True, folder=mailbox or "INBOX")
    try:
        typ, _ = client.uid("STORE", ",".join(str(u) for u in clean), "+FLAGS.SILENT", r"(\Seen)")
        if typ != "OK":
            logger.warning("[TShopping] IMAP STORE Seen failed: %s uids=%s box=%s", typ, clean, mailbox)
            return 0
        return len(clean)
    except Exception as e:
        logger.warning("[TShopping] IMAP mark seen: %s", e)
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


def thread_imap_uids(db: Session, chat: dict) -> list[tuple[str, int]]:
    chat_id = str((chat or {}).get("chat_id") or (chat or {}).get("id") or "")
    mids = []
    for key in ("email_message_id", "last_inbound_message_id", "last_outbound_message_id"):
        mids.append(str((chat or {}).get(key) or ""))
    if chat_id:
        rows = db.execute(text("""
            SELECT email_message_id FROM tshopping_messages
            WHERE chat_id = :cid AND email_message_id IS NOT NULL
        """), {"cid": chat_id}).fetchall()
        mids.extend(str(r[0] or "") for r in rows)
    ids = expand_message_ids(mids)
    if not ids:
        return []
    rows = db.execute(text("""
        SELECT DISTINCT COALESCE(mailbox, 'INBOX') AS mailbox, imap_uid
        FROM tshopping_mail_seen
        WHERE imap_uid IS NOT NULL AND message_id = ANY(:ids)
    """), {"ids": ids}).fetchall()
    return [(str(r[0] or "INBOX"), int(r[1])) for r in rows if r and r[1]]


def mark_thread_read(db: Session, chat: dict) -> int:
    """После ответа из CX помечаем письма треда прочитанными в help@."""
    pairs = thread_imap_uids(db, chat)
    if not pairs:
        return 0
    marked = 0
    by_box: dict[str, list[int]] = {}
    for mailbox, uid in pairs:
        by_box.setdefault(mailbox or "INBOX", []).append(uid)
    for mailbox, uids in by_box.items():
        marked += flag_uids_seen(uids, mailbox=mailbox)
    return marked


def _poll_folder(session: Session, folder: str) -> int:
    client = _connect_imap(writable=False, folder=folder)
    try:
        uids = sorted(set(_search_uids(client)))
        count = 0
        for uid in uids:
            try:
                raw = fetch_raw(client, uid)
                parsed = parse_rfc822(raw or b"")
                if not parsed:
                    continue
                result = ingest_parsed(session, parsed, imap_uid=uid, mailbox=folder)
                if result.get("status") == "ok":
                    count += 1
            except Exception as e:
                logger.warning("[TShopping] mail uid %s box=%s: %s", uid, folder, e)
                try:
                    session.rollback()
                except Exception:
                    pass
        return count
    finally:
        try:
            client.logout()
        except Exception:
            pass


def poll_inbox(db: Optional[Session] = None) -> int:
    from .database import db_session

    if not configured():
        return 0

    def _run(session: Session) -> int:
        session.execute(text(
            "ALTER TABLE tshopping_mail_seen ADD COLUMN IF NOT EXISTS mailbox TEXT"
        ))
        session.commit()
        total = 0
        for folder in mail_folders():
            try:
                n = _poll_folder(session, folder)
                if n:
                    logger.info("[TShopping] ingested %s from %s", n, folder)
                total += n
            except Exception as e:
                logger.warning("[TShopping] folder %s: %s", folder, e)
        return total

    if db is not None:
        return _run(db)
    with db_session() as session:
        return _run(session)


def poll_inbox_isolated() -> int:
    try:
        return poll_inbox()
    except Exception as e:
        logger.exception("[TShopping] IMAP poll failed: %s", e)
        return 0
