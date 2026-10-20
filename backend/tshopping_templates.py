"""Шаблоны ответов Т-Шопингу: возвраты и темы из чатов Ozon."""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

RETURN_TEMPLATES = [
    {
        "id": "return_pickup",
        "theme_key": "return_pickup",
        "theme_name": "Одобрение с забором",
        "title": "Одобрение возврата с забором товара",
        "branch_key": "",
        "branch_label": "",
        "body": (
            "Здравствуйте! Согласовываем возврат. "
            "Просим организовать забор товара у клиента."
        ),
    },
    {
        "id": "return_keep",
        "theme_key": "return_keep",
        "theme_name": "Одобрение без забора",
        "title": "Одобрение возврата без забора товара",
        "branch_key": "",
        "branch_label": "",
        "body": (
            "Здравствуйте! Согласовываем возврат по заказу без забора товара — "
            "клиент может оставить товар у себя."
        ),
    },
    {
        "id": "return_reject",
        "theme_key": "return_reject",
        "theme_name": "Отказ по возврату",
        "title": "Отказ по возврату",
        "branch_key": "",
        "branch_label": "",
        "body": (
            "Здравствуйте! По заказу возврат не согласовываем: "
            "по описанию и фото не видим оснований для возврата."
        ),
    },
]

_PICKUP_HINTS = (
    "забор", "заберите", "забрать товар", "курьер", "брак", "сломан",
    "трещин", "дефект", "некомплект", "не хватает", "помят", "царапин",
)
_KEEP_HINTS = (
    "без забора", "оставьте товар", "не забирать", "без возврата товара",
)
_SIGNED_RE = re.compile(r"команда\s+видовито|из\s+команды\s+видовито", re.I)


def fill_order(body: str, order: str = "") -> str:
    text = (body or "").strip()
    token = (order or "").strip() or "этому заказу"
    return text.replace("{order}", token)


def suggest_return_id(text: str) -> Optional[str]:
    blob = (text or "").lower()
    if any(h in blob for h in _KEEP_HINTS):
        return "return_keep"
    if any(h in blob for h in _PICKUP_HINTS):
        return "return_pickup"
    return None


def polish(db: Session, body: str, *, order: str = "", already_signed: bool = False) -> str:
    from .routers import chat_playbook

    text = fill_order(body, order)
    if not text:
        return ""
    greeting = (chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!") or "").strip()
    signature = (chat_playbook.get_setting(db, "signature", "") or "").strip()
    parts = []
    if greeting and not text.lower().startswith("здравствуйте"):
        parts.append(greeting)
    parts.append(text)
    result = "\n\n".join(parts).strip()
    if (
        signature
        and len(text) > 40
        and not already_signed
        and signature not in result
        and not _SIGNED_RE.search(result)
    ):
        result = f"{result}\n\n{signature}"
    return result


def list_return_templates(order: str = "") -> list[dict]:
    out = []
    for item in RETURN_TEMPLATES:
        row = dict(item)
        row["body"] = fill_order(item["body"], order)
        row["preview_body"] = row["body"]
        row["mp"] = "tshopping"
        out.append(row)
    return out


def staff_already_signed(messages: list[dict]) -> bool:
    for msg in messages or []:
        if (msg or {}).get("sender") != "manager":
            continue
        text = str((msg or {}).get("text") or "")
        if _SIGNED_RE.search(text):
            return True
    return False
