"""Раздел «Запросы „Т-Шопинг“»: заявки сотрудников из Telegram-бота."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from .. import tshopping, tshopping_templates
from .auth import get_current_user

logger = logging.getLogger("tshopping")

ALLOWED_ATTACH = {
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".png": ("image/png", "image"),
    ".pdf": ("application/pdf", "document"),
}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 6


def _access(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    role = current_user.get("role", "")
    if role == "admin":
        return current_user
    row = db.execute(
        text("SELECT 1 FROM role_permissions WHERE role_name = :r AND module = 'tshopping'"),
        {"r": role},
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Нет доступа к разделу Запросы «Т-Шопинг»")
    return current_user


router = APIRouter(
    prefix="/api/v1/tshopping",
    tags=["T-Shopping"],
    dependencies=[Depends(_access)],
)

public_router = APIRouter(
    prefix="/api/v1/tshopping",
    tags=["T-Shopping webhook"],
)


class ReplyPayload(BaseModel):
    text: str = ""


class CreateTicketPayload(BaseModel):
    order: str = ""
    problem: str = ""


def _require_chat(db: Session, chat_id: str) -> dict:
    with db.bind.connect() as conn:
        chat = tshopping.load_chat(conn, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return chat


def _login(user: dict) -> str:
    return (user or {}).get("username") or ""


@router.get("/me")
def get_me(db: Session = Depends(get_db), current_user: dict = Depends(_access)):
    partner = tshopping.is_partner(db, current_user)
    return {"actor": "partner" if partner else "staff", "username": _login(current_user)}


@router.post("/chats")
def create_chat(
    payload: CreateTicketPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    if not tshopping.is_partner(db, current_user):
        raise HTTPException(status_code=403, detail="Новую заявку создаёт Т-Шопинг")
    try:
        return tshopping.create_site_ticket(
            db,
            order=payload.order,
            problem=payload.problem,
            login=_login(current_user),
            display_name=_login(current_user) or "Т-Шопинг",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/chats")
def get_chats(
    closed: bool = Query(False),
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    return tshopping.list_chats(db, closed=closed, search=q)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    data = tshopping.list_chats(db, closed=False)
    return data.get("counts") or {}


@router.get("/chats/{chat_id}")
def get_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    chat = _require_chat(db, chat_id)
    partner = tshopping.is_partner(db, current_user)
    tshopping.mark_read(db, chat_id, partner=partner)
    chat["unread"] = False if not partner else chat.get("unread")
    chat["unread_partner"] = False if partner else chat.get("unread_partner")
    payload = tshopping.serialize_chat(chat)
    tshopping.attach_ozon(db, [payload], live=True)
    if payload.get("ozon"):
        tshopping.remember_ozon_product(db, chat_id, payload["ozon"])
    messages = tshopping.list_messages(db, chat_id)
    last_in = next((m.get("text") or "" for m in reversed(messages) if m.get("sender") == "client"), "")
    suggested = tshopping_templates.suggest_return_id(last_in)
    return {
        "chat": payload,
        "messages": messages,
        "ozon": payload.get("ozon"),
        "suggested_template": suggested,
    }


@router.get("/templates")
def get_templates(
    chat_id: str = Query(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    chat = tshopping.load_chat(db, chat_id) if chat_id else None
    order = str((chat or {}).get("order_number") or "")
    templates = []
    for item in tshopping_templates.list_return_templates(order):
        row = dict(item)
        row["preview_body"] = row["body"]
        templates.append(row)
    greeting = ""
    signature = ""
    try:
        from . import chat_playbook
        greeting = (chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!") or "").strip()
        signature = (chat_playbook.get_setting(db, "signature", "") or "").strip()
    except Exception:
        pass
    return {
        "templates": templates,
        "greeting_text": greeting,
        "signature": signature,
    }


@router.post("/chats/{chat_id}/close")
def close_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    chat = _require_chat(db, chat_id)
    partner = tshopping.is_partner(db, current_user)
    if not tshopping.can_staff_close(chat, partner=partner):
        raise HTTPException(status_code=403, detail="Закрыть заявку может Т-Шопинг или сотрудник по письму")
    chat = tshopping.close_chat(db, chat_id)
    return {"status": "ok", "chat": chat}


@router.post("/chats/{chat_id}/reopen")
def reopen_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    chat = _require_chat(db, chat_id)
    partner = tshopping.is_partner(db, current_user)
    if not tshopping.can_staff_close(chat, partner=partner):
        raise HTTPException(status_code=403, detail="Вернуть заявку может Т-Шопинг или сотрудник по письму")
    chat = tshopping.reopen_chat(db, chat_id)
    return {"status": "ok", "chat": chat}


@router.post("/chats/{chat_id}/request-close")
def request_close(
    chat_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    if tshopping.is_partner(db, current_user):
        raise HTTPException(status_code=403, detail="Просьбу закрыть отправляет Видовито")
    chat = _require_chat(db, chat_id)
    if chat.get("is_closed"):
        raise HTTPException(status_code=400, detail="Заявка уже закрыта")
    updated = tshopping.request_close(db, chat_id, _login(current_user))
    return {"status": "ok", "chat": updated}


@router.post("/chats/{chat_id}/read")
def mark_chat_read(
    chat_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    _require_chat(db, chat_id)
    tshopping.mark_read(db, chat_id, partner=tshopping.is_partner(db, current_user))
    return {"status": "ok"}


@public_router.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    x_tshopping_poller: Optional[str] = Header(default=None),
):
    expected = tshopping.webhook_secret()
    if expected and (x_telegram_bot_api_secret_token or "") != expected:
        raise HTTPException(status_code=403, detail="Bad webhook secret")
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Invalid update")
    try:
        result = tshopping.process_update(
            db, update, reply_via_poller=(x_tshopping_poller or "") == "1",
        )
    except Exception as e:
        logger.exception("[TShopping] webhook failed: %s", e)
        # Telegram должен получить 200, иначе будет ретраить одно и то же.
        return {"ok": True, "status": "error"}
    return {"ok": True, **result}


def _after_staff_send(db: Session, chat: dict) -> None:
    if not tshopping.is_email_chat(chat):
        return
    try:
        from .. import tshopping_mail
        tshopping_mail.mark_thread_read(db, chat)
    except Exception as e:
        logger.warning("[TShopping] mark inbox read: %s", e)


def _store_upload(file: UploadFile) -> dict:
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_ATTACH:
        raise HTTPException(status_code=400, detail="Разрешены только JPEG, PNG и PDF")
    mime, kind = ALLOWED_ATTACH[ext]
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="Файл больше 5 МиБ")
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = tshopping.upload_dir() / filename
    dest.write_bytes(data)
    return {
        "kind": kind,
        "name": name,
        "url": tshopping.public_upload_url(filename),
        "filename": filename,
        "mime": mime,
    }


@router.post("/chats/{chat_id}/message")
def send_message(chat_id: str, payload: ReplyPayload, db: Session = Depends(get_db),
                 current_user: dict = Depends(_access)):
    text_body = (payload.text or "").strip()
    if not text_body:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    chat = _require_chat(db, chat_id)
    if chat.get("is_closed"):
        raise HTTPException(status_code=400, detail="Заявка закрыта")
    partner = tshopping.is_partner(db, current_user)
    if partner:
        saved = tshopping.save_partner_message(
            db, chat_id, body=text_body, attachments=[],
            login=_login(current_user),
            display_name=_login(current_user) or "Т-Шопинг",
        )
        return {"status": "ok", "message": saved}
    ok, mail_id = tshopping.send_outbound(chat, text_body, [])
    if not ok:
        raise HTTPException(status_code=502, detail="Не удалось отправить сообщение")
    saved = tshopping.save_manager_message(
        db, chat_id, body=text_body, attachments=[],
        manager_login=_login(current_user),
        email_message_id=mail_id or None,
    )
    _after_staff_send(db, chat)
    return {"status": "ok", "message": saved}


@router.post("/chats/{chat_id}/message-with-files")
def send_message_with_files(
    chat_id: str,
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_access),
):
    text_body = (text or "").strip()
    uploads = list(files or [])
    if len(uploads) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Можно прикрепить не больше {MAX_FILES} файлов")
    if not text_body and not uploads:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    chat = _require_chat(db, chat_id)
    if chat.get("is_closed"):
        raise HTTPException(status_code=400, detail="Заявка закрыта")
    attachments = [_store_upload(f) for f in uploads]
    partner = tshopping.is_partner(db, current_user)
    if partner:
        saved = tshopping.save_partner_message(
            db, chat_id, body=text_body, attachments=attachments,
            login=_login(current_user),
            display_name=_login(current_user) or "Т-Шопинг",
        )
        return {"status": "ok", "message": saved}
    ok, mail_id = tshopping.send_outbound(chat, text_body, attachments)
    if not ok:
        raise HTTPException(status_code=502, detail="Не удалось отправить сообщение")
    saved = tshopping.save_manager_message(
        db, chat_id, body=text_body, attachments=attachments,
        manager_login=_login(current_user),
        email_message_id=mail_id or None,
    )
    _after_staff_send(db, chat)
    return {"status": "ok", "message": saved}
