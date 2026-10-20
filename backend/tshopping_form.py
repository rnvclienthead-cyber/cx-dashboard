"""Анкета заявки в боте: меню → номер заказа → проблема."""
from __future__ import annotations

import re
from typing import Any, Optional

SKIP_RE = re.compile(r"^(нет|не|н|no|skip|-|готово|дальше|нет фото)$", re.I)
NEW_RE = re.compile(
    r"^(/(new|novaia|новая)(?:@\w+)?|оставить заявку|заявка)$",
    re.I,
)
CANCEL_RE = re.compile(r"^/(cancel|otmena|отмена)(?:@\w+)?(?:\s|$)|^отмена$", re.I)
START_RE = re.compile(r"^/start(?:@\w+)?(?:\s|$)", re.I)

PROMPT_ORDER = "Напишите номер заказа."
PROMPT_PROBLEM = "В чём проблема?"
WELCOME_TEXT = (
    "Нажмите кнопку «Оставить заявку» внизу экрана."
)
CANCEL_TEXT = "Отменил. Чтобы начать — нажмите «Оставить заявку»."
DONE_TEXT = (
    "Заявка ушла. Ответ придёт сюда.\n"
    "Ещё одна — снова нажмите «Оставить заявку»."
)
MENU_HINT = "Нажмите «Оставить заявку» внизу экрана."

BTN_MENU = {
    "keyboard": [[{"text": "Оставить заявку"}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Нажмите «Оставить заявку»",
}
BTN_CANCEL = {
    "keyboard": [[{"text": "Отмена"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def command_of(text: str, callback: str = "") -> Optional[str]:
    data = (callback or "").strip()
    if data == "form:new":
        return "new"
    if data in {"form:cancel"}:
        return "cancel"
    if data == "form:skip":
        return "skip"
    raw = (text or "").strip()
    if START_RE.match(raw):
        return "start"
    if NEW_RE.match(raw):
        return "new"
    if CANCEL_RE.match(raw):
        return "cancel"
    return None


def format_ticket(draft: dict, display_name: str, username: str = "") -> str:
    who = (display_name or "Сотрудник").strip()
    if username:
        who = f"{who} (@{username.lstrip('@')})"
    photos = draft.get("attachments") or []
    lines = [
        "Заявка",
        "",
        f"Сотрудник: {who}",
        f"Заказ: {draft.get('order') or 'не указан'}",
        f"Проблема: {draft.get('problem') or '—'}",
    ]
    if draft.get("product"):
        lines.append(f"Товар: {draft['product']}")
    if photos:
        lines.append(f"Фото: {len(photos)} шт.")
    return "\n".join(lines)


def begin_menu() -> dict:
    return {
        "step": "idle",
        "draft": {},
        "reply": WELCOME_TEXT,
        "markup": BTN_MENU,
        "submit": None,
    }


def begin_form(draft: Optional[dict] = None) -> dict:
    keep = {}
    if isinstance(draft, dict):
        if draft.get("last_chat_id"):
            keep["last_chat_id"] = draft["last_chat_id"]
        if draft.get("last_order"):
            keep["last_order"] = draft["last_order"]
    return {
        "step": "order",
        "draft": keep,
        "reply": PROMPT_ORDER,
        "markup": BTN_CANCEL,
        "submit": None,
    }


def apply_form(step: str, draft: dict, incoming: dict) -> dict:
    """Следующий шаг анкеты. submit — готовая заявка или None."""
    step = step or "idle"
    draft = dict(draft or {})
    cmd = incoming.get("command")
    text = str(incoming.get("text") or "").strip()
    atts = list(incoming.get("attachments") or [])

    if cmd == "start":
        return begin_menu()
    if cmd == "new":
        return begin_form(draft)
    if cmd == "cancel":
        kept = {}
        if draft.get("last_chat_id"):
            kept["last_chat_id"] = draft["last_chat_id"]
        if draft.get("last_order"):
            kept["last_order"] = draft["last_order"]
        return {
            "step": "idle",
            "draft": kept,
            "reply": CANCEL_TEXT,
            "markup": BTN_MENU,
            "submit": None,
        }

    if step == "idle":
        return {
            "step": "idle",
            "draft": draft,
            "reply": MENU_HINT,
            "markup": BTN_MENU,
            "submit": None,
            "follow_up": True,
        }

    if step == "order":
        if not text or SKIP_RE.match(text):
            return {
                "step": "order",
                "draft": draft,
                "reply": "Нужен номер заказа.",
                "markup": BTN_CANCEL,
                "submit": None,
            }
        draft["order"] = text
        return {
            "step": "problem",
            "draft": draft,
            "reply": PROMPT_PROBLEM,
            "markup": BTN_CANCEL,
            "submit": None,
        }

    if step == "problem":
        if atts:
            draft.setdefault("attachments", []).extend(atts)
        if not text:
            return {
                "step": "problem",
                "draft": draft,
                "reply": "Опишите проблему текстом.",
                "markup": BTN_CANCEL,
                "submit": None,
            }
        draft["problem"] = text
        return {
            "step": "idle",
            "draft": {},
            "reply": DONE_TEXT,
            "markup": BTN_MENU,
            "submit": draft,
        }

    if step in {"product", "photos"}:
        return {
            "step": "problem",
            "draft": draft,
            "reply": PROMPT_PROBLEM,
            "markup": BTN_CANCEL,
            "submit": None,
        }

    return begin_menu()
