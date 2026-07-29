"""
ИИ-бот автоответов в WB-чатах.

Архитектура (согласована с Рустамом 2026-07-22/23, дополнена 2026-07-24):
  1. Диспетчер (YandexGPT-lite + жёсткие правила) → тема / уверенность / нужен человек
  2. Плейбук → текст + действие (форма, статус, передача менеджеру)
  3. Нет шаблона → ИИ-черновик (lite + портрет + похожие ответы менеджеров),
     зона принудительно draft (клиенту сам не шлём)
  4. Светофор темы (auto / draft / human)
  5. Сборка: [приветствие?] + суть + [подпись?]  (приветствие — раз в календарный день МСК)
  6. Shadow-режим: пишем «в стол», клиенту не отправляем

Воркер крутится рядом с синком чатов. Включается настройками:
  bot_enabled = true/false
  bot_shadow  = true/false   (по умолчанию true — безопасно)
  bot_ai_draft = true/false  (черновик без шаблона)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import cx_alert
from .routers import chat_playbook, reshipment_skus, nonreturnable_cats
from . import wb_chat
from . import chat_faq

logger = logging.getLogger("chat_bot")

MSK = ZoneInfo("Europe/Moscow")
BOT_INTERVAL_SEC = 15  # было 90; для диалога 1/2/3 нужна быстрая реакция
BOT_BATCH_LIMIT = 25
MIN_CONFIDENCE = 0.55

FORM_BASE = "https://box.vidovito.com/reshipment/form"

# Жёсткая красная зона — без ИИ (суд, угрозы, здоровье). «Деньги» — отдельно:
# если клиент просит деталь и лишь упоминает деньги как запасной вариант — это некомплект.
_HARD_RED_RE = re.compile(
    r"(роспотреб|суд\b|юрист|адвокат|обман|мошен|угроз|травм|здоровь|"
    r"отравил|аллерги|прокуратур|полици|арбитраж|"
    r"жалоб\w*\s+в\s+|напишу\s+в\s+поддержк|"
    r"отклонили\s+мо[юе]|вы\s+отклонили|"
    r"издеваетесь|шарашкин|конченые)",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(деньг|денежн|компенсац|возврат\s*денег|верн(ите|уть|у)\s+деньг|возмест|"
    r"возврат\s+средств|средств\b|верните\s+оплат|возврошени[ея]\s+денег)",
    re.IGNORECASE,
)
_NEKOMPLEKT_RE = re.compile(
    r"(не\s*хвата|нехвата|нет\s+в\s+короб|отсутств|не\s+положили|недокомплект|"
    r"некомплект|забыли\s+положить|"
    r"без\s+(болт|труб|винт|колес|крепеж|ножек|гаек|прут|наклад)|"
    r"отдельн\w*\s+отправ|до\s*отправ|дослать|дошлите|"
    r"выслать\s+(болт|детал|крепеж)|прислать\s+(болт|детал|крепеж|нужн)|"
    r"отправить\s+(болт|детал|отдельно)|"
    r"болт\w*\s+для|креплен|"
    r"в\s+комп?лекте\s+нет|в\s+комп?лекте\s+нету|нет\s+в\s+комп?лект|"
    r"болтик\w*\s+нет|нет\s+болт|нет\s+труб|нет\s+колес|нет\s+крепеж|"
    r"нет\s+в\s+наличии|не\s+достающ|недостающ|"
    r"можно\s+просто\s+прислать|есть\s+возможность\s+до\s*отправ|"
    r"только\s+два\s+колес|прислали\s+\d|"
    r"трубк\w*\s+(а|в|с)\b.{0,40}(нет|не\s+хвата|нехвата|мало|вместо))",
    re.IGNORECASE,
)

_AGREE_RE = re.compile(
    r"^(да|даа+|ок|окей|хорошо|отправляйте|отправляй|отправьте|"
    r"буду\s+благодар|актуально|согласен|согласна|нужно|"
    r"давайте|давай|конечно|ждём|ждем|"
    r"заполнила|заполнил|заполнили|"
    r"да[,\s]+актуальн\w*|да[,\s]+давайте|здравствуйте[!.]?\s*да)[\s!.]*$",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"(не\s+актуальн|не\s+надо|не\s+нужно|отказ|не\s+хочу|"
    r"уже\s+перезаказ|хочу\s+вернуть|верну\b|возврат)",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^(аа+)?\s*(спасибо|благодарю|большое\s+(вам\s+)?спасибо|"
    r"спасибо\s+большое|спасибо\s+за\s+\w+|"
    r"все\s+(нормально|отлично|супер|хорошо|подошло)|"
    r"всё\s+(нормально|отлично|супер|хорошо|подошло)|"
    r"разобрал(ись|ась|ся)|все\s+нашла|всё\s+нашла|"
    r"вопросов\s+нет|принято|понял[а]?)[\s!.❤🙏👍)]*$",
    re.IGNORECASE,
)

_SSYLKA_RE = re.compile(
    r"(ссылк\w+\s+не|не\s+открыва|перекидывает|ведёт\s+на\s+главн|"
    r"ведет\s+на\s+главн|что\s+за\s+сайт|не\s+работает\s+ссыл)",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(
    r"(как\s+долго\s+(будет\s+)?(идти|ждать)|когда\s+(прид|отправ|детал)|"
    r"что\s+с\s+моей\s+заявк|статус\s+заявк|ожидаю\s+детал|"
    r"в\s+течени[ие]\s+какого\s+срока)",
    re.IGNORECASE,
)

_VOZVRAT_RE = re.compile(
    r"(как\s+(произвести\s+)?возврат|оформить\s+возврат|хочу\s+вернуть|"
    r"можно\s+ли\s+вернуть|верн(уть|ите)\s+товар|^возврат[\s!.?]*$|"
    r"возврат\s+возможен|возвращаю\s+товар)",
    re.IGNORECASE,
)

_CHEQUE_RE = re.compile(
    r"(нужен\s+чек|где\s+чек|чек\s+на\s+|кассовый\s+чек|электронный\s+чек|"
    r"дай(те)?\s+чек|прислать\s+чек|скачать\s+чек)",
    re.IGNORECASE,
)

_STORAGE_RE = re.compile(
    r"(продлить\s+(срок\s+)?(хранени|выдач)|не\s+успеваю\s+забрать|"
    r"срок\s+хранени|продлите\s+хранен)",
    re.IGNORECASE,
)

_TECH_RE = re.compile(
    r"(не\s+открыва(ется|ется)\s+(заказ|приложен|сайт)|техн?ическ\w*\s+(сбой|ошибк|неполад)|"
    r"глючит\s+(приложение|вб|wb)|ошибка\s+приложен|сайт\s+не\s+работает)",
    re.IGNORECASE,
)

_PARAMS_RE = re.compile(
    r"(какая\s+(высота|ширина|глубина|нагрузка|длина)|какой\s+размер|"
    r"какой\s+диаметр|сколько\s+(весит|выдержив)|выдержит\s+ли|"
    r"подойд(ёт|ет)\s+ли\s+в|габарит|характеристик)",
    re.IGNORECASE,
)

_SBORKA_RE = re.compile(
    r"(как\s+собрать|не\s+собирается|трубк\w*\s+не\s+(подход|вход)|"
    r"болт\w*\s+не\s+вход|инструкц|накладк\w*\s+молот|шатается|"
    r"не\s+понятно\s+как\s+собрать|схема\s+сборк)",
    re.IGNORECASE,
)

_DOSTAVKA_RE = re.compile(
    r"(где\s+(мой\s+)?заказ|когда\s+прид(ёт|ет)\s+заказ|статус\s+заказ|"
    r"оформлен\s+\d|продавец\s+собирает|задержива(ется|ют)\s+доставк|"
    r"срок\s+доставк|где\s+посылка)",
    re.IGNORECASE,
)

_CANCEL_RE = re.compile(
    r"(отменить\s+заказ|отмена\s+заказ|отмените\s+(заказ|доставк)|"
    r"хочу\s+отменить)",
    re.IGNORECASE,
)

_VAGUE_RE = re.compile(
    r"^(здравствуйте|добрый\s+(день|вечер|утро)|привет|помогите|"
    r"подскажите|вопрос|есть\s+вопрос|нужна\s+помощь)[\s!.?]*$",
    re.IGNORECASE,
)

_MENU_CHOICE_RE = re.compile(r"^\s*([1-5])\s*[).:\-]?\s*$")

_GREETING_RE = re.compile(
    r"^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*",
    re.IGNORECASE,
)

_SHORT_NO_SIGN_RE = re.compile(
    r"^(спасибо|благодар|ок|окей|хорошо|пожалуйста|рад\s+помочь|"
    r"пожалуйста[!.,]?|понял|принято)[\s!.❤🙏👍]*$",
    re.IGNORECASE,
)


# ── Инициализация ────────────────────────────────────────────────────────────

DEFAULT_PERSONA = (
    "Ты менеджер поддержки продавца «Видовито» на Wildberries.\n"
    "Пиши коротко, вежливо, по делу, на «вы».\n"
    "Не обещай то, чего нет в правилах WB (сроки возврата денег продавцом, гарантии склада WB).\n"
    "Не спорь с клиентом и не обвиняй его. Не выдумывай трек-номера, статусы и суммы.\n"
    "Если не хватает данных — задай один короткий уточняющий вопрос "
    "или скажи, что передашь специалисту.\n"
    "Не используй эмодзи и markdown. Не ставь подпись — её добавят отдельно.\n"
    "Ответь ТОЛЬКО текстом сообщения клиенту, без пояснений и кавычек."
)

EXTRA_SETTINGS = [
    ("bot_enabled", "false",
     "Полная автоотправка бота (true/false). false = только подсказки при открытии чата / кнопка «Прогнать»."),
    ("bot_shadow", "false",
     "Устарело: теневой журнал. Оставьте false. Подсказки при открытии чата не требуют тени."),
    ("greeting_text", "Здравствуйте!",
     "Приветствие. Ставится раз в календарный день (по Москве)."),
    ("bot_confidence_min", "0.55",
     "Минимальная уверенность диспетчера. Ниже — передаём менеджеру."),
    ("bot_only_days", "2",
     "Брать только чаты за последние N дней (0 = без ограничения). Экономит токены на старом бэклоге."),
    ("bot_only_no_manager", "true",
     "Только чаты без ответа менеджера. Если в чате уже писал продавец, а бот его ещё не брал — пропуск. "
     "Чаты, которые бот уже вёл, продолжает."),
    ("bot_model", "yandexgpt-lite",
     "Модель Яндекса для классификации темы: yandexgpt-lite (дешевле) или yandexgpt (Pro, точнее)."),
    ("bot_ai_draft", "true",
     "Если нет шаблона — ИИ пишет черновик менеджеру (true/false). Клиенту сам не отправляет."),
    ("bot_ai_draft_model", "yandexgpt-lite",
     "Модель для черновика без шаблона: yandexgpt-lite (дешевле) или yandexgpt."),
    ("bot_persona", DEFAULT_PERSONA,
     "Портрет/правила тона для ИИ-черновика. Можно править под ваш стиль."),
    ("bot_suggest_on_open", "true",
     "При открытии чата подставлять шаблонный ответ в поле ввода (true/false). Менеджер сам жмёт «Отправить»."),
]

# Ключи, которые хранятся отдельно на каждую площадку (см. get_setting_mp).
BOT_MP_CONTROL_KEYS = {
    "bot_enabled", "bot_confidence_min", "bot_only_days",
    "bot_only_no_manager", "bot_suggest_on_open", "bot_ai_draft",
}


def init_bot_tables(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_state (
                chat_id            TEXT PRIMARY KEY,
                stage              TEXT DEFAULT 'new',
                last_theme         TEXT,
                last_greeted_date  DATE,
                last_bot_at        TIMESTAMP,
                closing_asked_at   TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_log (
                id              BIGSERIAL PRIMARY KEY,
                chat_id         TEXT NOT NULL,
                client_message  TEXT,
                theme           TEXT,
                confidence      REAL,
                zone            TEXT,
                branch_key      TEXT DEFAULT '',
                reply_text      TEXT,
                action          TEXT DEFAULT '',
                shadow          BOOLEAN DEFAULT TRUE,
                sent            BOOLEAN DEFAULT FALSE,
                need_human      BOOLEAN DEFAULT FALSE,
                reason          TEXT DEFAULT '',
                created_at      TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chat_bot_log_created
            ON cx_chat_bot_log (created_at DESC)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_suggest_cache (
                chat_id         TEXT PRIMARY KEY,
                client_last_ts  BIGINT,
                payload         JSONB NOT NULL DEFAULT '{}',
                created_at      TIMESTAMP DEFAULT now(),
                updated_at      TIMESTAMP DEFAULT now()
            )
        """))
        # pending_menu: JSON карта «1→nekomplekt» пока ждём ответ на меню
        try:
            conn.execute(text(
                "ALTER TABLE cx_chat_bot_state ADD COLUMN IF NOT EXISTS pending_menu TEXT"
            ))
        except Exception:
            pass
        for k, v, d in EXTRA_SETTINGS:
            conn.execute(text("""
                INSERT INTO cx_chat_settings (key, value, descr, updated_by)
                VALUES (:k, :v, :d, 'system')
                ON CONFLICT (key) DO UPDATE SET descr = EXCLUDED.descr
            """), {"k": k, "v": v, "d": d})
        conn.commit()
    from .routers.chat_playbook import ensure_mp_control_settings
    ensure_mp_control_settings(engine)


# ── Время ────────────────────────────────────────────────────────────────────

def _msk_now() -> datetime:
    return datetime.now(MSK)


def _msk_date(dt: Optional[datetime] = None):
    return (dt or _msk_now()).astimezone(MSK).date()


def _ts_to_msk_date(ts_ms: Optional[int]):
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(MSK).date()


# ── Контекст чата ────────────────────────────────────────────────────────────

def _get_article(db: Session, nm_id) -> str:
    if not nm_id:
        return ""
    try:
        n = int(nm_id)
    except (TypeError, ValueError):
        return ""
    for sql in (
        """SELECT supplier_article FROM wb_orders
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY dt DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_feedbacks
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY created_date DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_claims
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           LIMIT 1""",
    ):
        try:
            row = db.execute(text(sql), {"n": n}).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            db.rollback()
    return ""


def _get_reshipment(db: Session, chat_id: str) -> Optional[dict]:
    try:
        row = db.execute(text("""
            SELECT id, status, cdek_number, track_number, delivery_method,
                   client_pvz_address, customer_name, wb_nm_id
            FROM reshipment_requests
            WHERE wb_chat_id = :cid
            ORDER BY id DESC LIMIT 1
        """), {"cid": chat_id}).mappings().fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.warning("[ChatBot] reshipment lookup: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _history(db: Session, chat_id: str, limit: int = 8) -> List[dict]:
    rows = db.execute(text("""
        SELECT sender, text, add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND text IS NOT NULL AND text != ''
        ORDER BY add_ts DESC NULLS LAST LIMIT :lim
    """), {"cid": chat_id, "lim": limit}).mappings().all()
    return list(reversed([dict(r) for r in rows]))


def _bot_state(db: Session, chat_id: str) -> dict:
    row = db.execute(text(
        "SELECT * FROM cx_chat_bot_state WHERE chat_id = :cid"
    ), {"cid": chat_id}).mappings().fetchone()
    if row:
        return dict(row)
    db.execute(text("""
        INSERT INTO cx_chat_bot_state (chat_id, stage) VALUES (:cid, 'new')
        ON CONFLICT (chat_id) DO NOTHING
    """), {"cid": chat_id})
    db.commit()
    return {"chat_id": chat_id, "stage": "new", "last_theme": None,
            "last_greeted_date": None, "last_bot_at": None, "closing_asked_at": None}


def _set_state(db: Session, chat_id: str, **fields):
    if not fields:
        return
    # None → SQL NULL
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = {"cid": chat_id, **fields}
    db.execute(text("""
        INSERT INTO cx_chat_bot_state (chat_id, stage)
        VALUES (:cid, 'new')
        ON CONFLICT (chat_id) DO NOTHING
    """), {"cid": chat_id})
    db.execute(text(f"""
        UPDATE cx_chat_bot_state SET {sets}, updated_at = now()
        WHERE chat_id = :cid
    """), params)
    db.commit()


# ── Классификация ────────────────────────────────────────────────────────────

def _parse_menu_choice(msg: str) -> Optional[str]:
    """Из ответа на меню 1–5 → theme_key или 'human'. None если не распознали."""
    s = (msg or "").strip()
    if not s:
        return None
    m = _MENU_CHOICE_RE.match(s)
    if m:
        return chat_faq.CLARIFY_MENU_MAP.get(m.group(1))
    # «1 — не хватает» / «выберите 2»
    m2 = re.match(r"^\s*([1-5])\b", s)
    if m2 and len(s) < 80:
        return chat_faq.CLARIFY_MENU_MAP.get(m2.group(1))
    for pat, theme in chat_faq.CLARIFY_KEYWORDS:
        if re.search(pat, s, re.IGNORECASE):
            return theme
    return None


def _rule_classify(msg: str, stage: str) -> Optional[Tuple[str, float, bool, str]]:
    """Жёсткие правила до ИИ. Возвращает (theme, conf, need_human, reason) или None."""
    s = (msg or "").strip()
    if not s:
        return ("blagodarnost", 0.4, True, "пустое сообщение")

    # Ответ на меню уточнения
    if stage == "awaiting_menu":
        choice = _parse_menu_choice(s)
        if choice == "human":
            return ("dengi", 0.9, True, "меню: другое → менеджер")
        if choice:
            return (choice, 0.95, False, f"меню: выбор → {choice}")
        # не распознали — снова меню
        return ("utochnenie", 0.85, False, "меню: повтор")

    # 1) Реальная красная зона (суд/угрозы) — всегда человеку
    if _HARD_RED_RE.search(s):
        return ("otkaz_dootpravki", 0.95, True, "красная зона (правило)")

    # 2) Некомплект важнее упоминания денег («либо верните деньги»)
    if _NEKOMPLEKT_RE.search(s):
        return ("nekomplekt", 0.88, False, "некомплект (правило)")

    # 3) Деньги без запроса детали — тема dengi / человек
    if _MONEY_RE.search(s):
        return ("dengi", 0.9, True, "красная зона: деньги")

    # Короткие согласия/отказы зависят от стадии
    if stage in ("awaiting_client", "form_sent", "new") and len(s) < 100:
        if _AGREE_RE.match(s) and not _DECLINE_RE.search(s):
            # «да» без контекста доотправки на стадии new — пусть решает ИИ
            if stage == "new" and re.match(r"^да[\s!.]*$", s, re.I):
                pass
            else:
                return ("soglasie", 0.92, False, "согласие по стадии")
        if stage in ("awaiting_client", "form_sent") and _DECLINE_RE.search(s):
            return ("otkaz_dootpravki", 0.9, True, "отказ от доотправки")

    if _THANKS_RE.match(s):
        return ("blagodarnost", 0.85, False, "благодарность")

    if _SSYLKA_RE.search(s):
        return ("ssylka", 0.88, False, "ссылка")

    if _STATUS_RE.search(s):
        return ("status_zayavki", 0.82, False, "статус заявки (правило)")

    if _CHEQUE_RE.search(s):
        return ("cheque", 0.9, False, "чек (правило)")

    if _STORAGE_RE.search(s):
        return ("srok_hraneniya", 0.9, False, "хранение ПВЗ (правило)")

    if _TECH_RE.search(s):
        return ("teh_sboi", 0.85, False, "техсбой МП (правило)")

    if _CANCEL_RE.search(s):
        return ("otmena_zakaza", 0.88, False, "отмена заказа (правило)")

    if _DOSTAVKA_RE.search(s):
        return ("dostavka_wb", 0.85, False, "доставка WB (правило)")

    if _SBORKA_RE.search(s):
        return ("sborka", 0.85, False, "сборка (правило)")

    if _PARAMS_RE.search(s):
        return ("parametry", 0.82, False, "параметры (правило)")

    if len(s) < 120 and _VOZVRAT_RE.search(s) and not _MONEY_RE.search(s):
        return ("vozvrat", 0.8, False, "возврат (правило)")

    if _VAGUE_RE.match(s):
        return ("utochnenie", 0.9, False, "неясный запрос → меню")

    return None


def _themes_for_prompt(db: Session) -> List[dict]:
    rows = db.execute(text("""
        SELECT theme_key, name, when_text FROM cx_chat_themes
        WHERE active ORDER BY sort_order
    """)).mappings().all()
    return [dict(r) for r in rows]


def _yandex_classify(msg: str, history: List[dict], themes: List[dict],
                     article: str, stage: str, *, model: str = "yandexgpt-lite") -> Optional[dict]:
    api_key = os.getenv("YANDEX_API_KEY", "").strip()
    folder_id = os.getenv("FOLDER_ID", "").strip()
    if not api_key or not folder_id:
        logger.warning("[ChatBot] YANDEX_API_KEY/FOLDER_ID не заданы")
        return None

    model_name = (model or "yandexgpt-lite").strip()
    if model_name not in ("yandexgpt-lite", "yandexgpt"):
        model_name = "yandexgpt-lite"

    theme_lines = "\n".join(
        f"- {t['theme_key']}: {t['name']} — {t['when_text']}" for t in themes
    )
    hist_lines = "\n".join(
        f"{'Клиент' if h['sender']=='client' else 'Мы'}: {(h.get('text') or '')[:200]}"
        for h in history[-6:]
    ) or "(пусто)"

    system = (
        "Ты диспетчер чата продавца маркетплейса. "
        "Выбери ОДНУ тему из списка. Не пиши ответ клиенту. "
        "Если это деньги/суд/угрозы/травма — need_human=true. "
        "Ответь ТОЛЬКО JSON: "
        '{"theme":"theme_key","confidence":0.0,"need_human":false,"reason":"..."}'
    )
    user = (
        f"Стадия диалога: {stage}\n"
        f"Артикул: {article or 'неизвестен'}\n"
        f"Темы:\n{theme_lines}\n\n"
        f"История:\n{hist_lines}\n\n"
        f"Новое сообщение клиента:\n{msg}\n"
    )

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id,
    }
    payload = {
        "modelUri": f"gpt://{folder_id}/{model_name}/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": 300},
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=40)
        if r.status_code != 200:
            logger.warning("[ChatBot] Yandex %s: %s", r.status_code, r.text[:200])
            return None
        raw = r.json()["result"]["alternatives"][0]["message"]["text"]
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        return data
    except Exception as e:
        logger.warning("[ChatBot] Yandex classify error: %s", e)
        return None


def classify(db: Session, msg: str, stage: str, history: List[dict],
             article: str, nm_id=None) -> Tuple[str, float, bool, str]:
    ruled = _rule_classify(msg, stage)
    if ruled:
        theme, conf, need, reason = ruled
    else:
        themes = _themes_for_prompt(db)
        model = chat_playbook.get_setting(db, "bot_model", "yandexgpt-lite")
        ai = _yandex_classify(msg, history, themes, article, stage, model=model)
        if not ai:
            return ("utochnenie", 0.55, False, "ИИ недоступен → меню уточнения")

        keys = {t["theme_key"] for t in themes}
        theme = ai.get("theme") or ""
        if theme not in keys:
            return ("utochnenie", 0.55, False, f"неизвестная тема ИИ → меню ({theme})")

        conf = float(ai.get("confidence") or 0.5)
        need = bool(ai.get("need_human"))
        reason = str(ai.get("reason") or "ИИ")

    # Ассортимент: сушилки и др. невозвратные → черновик «невозвратный», не общий «как вернуть»
    if theme in ("vozvrat", "nevozvratny") and not need:
        if nonreturnable_cats.is_nonreturnable(db, article=article, nm_id=nm_id):
            theme = "nevozvratny"
            reason = ((reason or "") + "; категория невозвратная").strip("; ")

    min_conf = float(chat_playbook.get_setting_mp(db, "bot_confidence_min", "wb", str(MIN_CONFIDENCE))
                     or MIN_CONFIDENCE)
    if conf < min_conf and not need and theme != "utochnenie":
        reason = f"уточнение: низкая уверенность ({conf:.2f}) → меню; было: {reason}"
        theme, conf, need = "utochnenie", 0.7, False

    return theme, conf, need, reason


# ── ИИ-черновик без шаблона ──────────────────────────────────────────────────

_STOP_WORDS = {
    "и", "в", "во", "не", "на", "что", "это", "как", "а", "то", "все", "всё",
    "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы",
    "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще", "ещё",
    "нет", "о", "из", "ему", "теперь", "когда", "уже", "вам", "ни", "есть",
    "ли", "если", "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего",
    "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того",
    "потому", "этого", "какой", "совсем", "ним", "здесь", "этом", "один",
    "почти", "мой", "тем", "чтобы", "нее", "сейчас", "можно", "после", "слово",
    "где", "дело", "есть", "надо", "здравствуйте", "добрый", "день", "пожалуйста",
}


def _keywords(msg: str, limit: int = 6) -> List[str]:
    words = re.findall(r"[а-яёa-z0-9]{4,}", (msg or "").lower())
    out, seen = [], set()
    for w in words:
        if w in _STOP_WORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _similar_seller_replies(db: Session, *, nm_id, client_msg: str,
                            limit: int = 5) -> List[str]:
    """Дешёвый поиск похожих ответов менеджеров: тот же nm_id + ключевые слова."""
    keys = _keywords(client_msg)
    rows: List[str] = []
    if nm_id:
        found = db.execute(text("""
            SELECT m.text
            FROM wb_chat_messages m
            JOIN wb_chats c ON c.chat_id = m.chat_id
            WHERE m.sender = 'seller'
              AND c.nm_id = :nm
              AND m.text IS NOT NULL
              AND length(trim(m.text)) BETWEEN 40 AND 700
              AND m.text NOT ILIKE '%box.vidovito.com%'
            ORDER BY m.add_ts DESC NULLS LAST
            LIMIT 30
        """), {"nm": nm_id}).fetchall()
        rows.extend(r[0] for r in found if r and r[0])

    if keys and len(rows) < limit:
        # простой ILIKE по 1–2 словам — без эмбеддингов
        for kw in keys[:2]:
            found = db.execute(text("""
                SELECT m.text
                FROM wb_chat_messages m
                WHERE m.sender = 'seller'
                  AND m.text ILIKE :pat
                  AND length(trim(m.text)) BETWEEN 40 AND 700
                  AND m.text NOT ILIKE '%box.vidovito.com%'
                ORDER BY m.add_ts DESC NULLS LAST
                LIMIT 10
            """), {"pat": f"%{kw}%"}).fetchall()
            rows.extend(r[0] for r in found if r and r[0])
            if len(rows) >= limit * 3:
                break

    # дедуп по началу текста
    uniq, seen = [], set()
    for t in rows:
        key = re.sub(r"\s+", " ", (t or "").strip().lower())[:70]
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(t.strip())
        if len(uniq) >= limit:
            break
    return uniq


def _yandex_complete(*, system: str, user: str, model: str = "yandexgpt-lite",
                     temperature: float = 0.3, max_tokens: int = 450) -> Optional[str]:
    api_key = os.getenv("YANDEX_API_KEY", "").strip()
    folder_id = os.getenv("FOLDER_ID", "").strip()
    if not api_key or not folder_id:
        return None
    model_name = (model or "yandexgpt-lite").strip()
    if model_name not in ("yandexgpt-lite", "yandexgpt"):
        model_name = "yandexgpt-lite"
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id,
    }
    payload = {
        "modelUri": f"gpt://{folder_id}/{model_name}/latest",
        "completionOptions": {"temperature": temperature, "maxTokens": max_tokens},
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        if r.status_code != 200:
            logger.warning("[ChatBot] Yandex draft %s: %s", r.status_code, r.text[:200])
            return None
        return (r.json()["result"]["alternatives"][0]["message"]["text"] or "").strip()
    except Exception as e:
        logger.warning("[ChatBot] Yandex draft error: %s", e)
        return None


def generate_ai_draft(db: Session, *, client_msg: str, theme: str, article: str,
                      history: List[dict], nm_id=None,
                      reship: Optional[dict] = None) -> Optional[str]:
    """Черновик ответа, когда в плейбуке нет шаблона. Клиенту не отправляется сам."""
    if chat_playbook.get_setting_mp(db, "bot_ai_draft", "wb", "true").lower() != "true":
        return None

    persona = chat_playbook.get_setting(db, "bot_persona", DEFAULT_PERSONA) or DEFAULT_PERSONA
    model = chat_playbook.get_setting(db, "bot_ai_draft_model", "yandexgpt-lite")
    examples = _similar_seller_replies(db, nm_id=nm_id, client_msg=client_msg)

    theme_name = ""
    row = db.execute(text(
        "SELECT name, when_text FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).mappings().first()
    if row:
        theme_name = f"{row['name']} ({row['when_text']})"

    hist_lines = "\n".join(
        f"{'Клиент' if h['sender']=='client' else 'Мы'}: {(h.get('text') or '')[:220]}"
        for h in history[-6:]
    ) or "(пусто)"

    ex_block = "\n---\n".join(examples) if examples else "(нет похожих — пиши осторожно)"

    reship_line = "нет"
    if reship:
        reship_line = (
            f"статус={reship.get('status') or '—'}, "
            f"трек={reship.get('cdek_number') or reship.get('track_number') or '—'}"
        )

    user = (
        f"Тема (подсказка диспетчера): {theme or '—'} — {theme_name or 'без описания'}\n"
        f"Артикул/товар: {article or 'неизвестен'}\n"
        f"Заявка на доотправку: {reship_line}\n\n"
        f"Примеры ответов менеджеров (стиль, не копируй слепо):\n{ex_block}\n\n"
        f"История чата:\n{hist_lines}\n\n"
        f"Новое сообщение клиента:\n{client_msg}\n"
    )
    raw = _yandex_complete(system=persona, user=user, model=model,
                           temperature=0.35, max_tokens=450)
    if not raw:
        return None
    # убрать возможные обёртки «Ответ:» / кавычки
    raw = re.sub(r"^(ответ(\s+клиенту)?\s*[:\-]\s*)", "", raw, flags=re.I).strip()
    raw = raw.strip("«»\"'")
    raw = _strip_greeting(raw).strip()
    if len(raw) < 15:
        return None
    return raw[:900]


def _polish_reply(db: Session, body: str, state: dict,
                  last_seller_ts: Optional[int]) -> str:
    """Приветствие + подпись вокруг готового тела ответа."""
    body = _strip_greeting(body or "").strip()
    if not body:
        return ""
    parts = []
    if _needs_greeting(state, last_seller_ts):
        greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
        if greet:
            parts.append(greet)
    parts.append(body)
    text_out = "\n\n".join(p for p in parts if p).strip()
    if body and not _SHORT_NO_SIGN_RE.match(body) and len(body) > 40:
        sig = chat_playbook.get_setting(db, "signature", "").strip()
        if sig:
            text_out = f"{text_out}{sig if sig.startswith(chr(10)) else chr(10)+chr(10)+sig}"
    # На копирование/отправку — без ведущих пустых строк
    return text_out.lstrip(" \t\r\n")[:1000]


# ── Ветки и сборка ответа ────────────────────────────────────────────────────

def _make_form_url(db: Session, chat_id: str) -> str:
    # Тот же формат, что кнопка «Сформировать ссылку» в разделе доотправки.
    token = secrets.token_urlsafe(12)
    try:
        db.execute(text("""
            INSERT INTO reshipment_chat_tokens (token, chat_id, created_by, expires_at)
            VALUES (:token, :cid, 'chat_bot', NOW() + INTERVAL '7 days')
        """), {"token": token, "cid": chat_id})
        db.commit()
    except Exception as e:
        logger.warning("[ChatBot] token error: %s", e)
        db.rollback()
    return f"{FORM_BASE}?t={token}"


def _resolve_branch(db: Session, theme: str, article: str,
                    reship: Optional[dict], msg: str) -> str:
    if theme == "nekomplekt":
        return "sku_allowed" if reshipment_skus.is_allowed(db, article) else "sku_denied"
    if theme == "status_zayavki" and reship:
        st = reship.get("status") or "new"
        if st in ("new", "pending", "awaiting"):
            return "new"
        if st in ("approved", "warehouse", "ready_to_ship"):
            return "approved"
        if st in ("shipped", "in_transit"):
            return "shipped"
        if st == "ready_for_pickup":
            return "ready_for_pickup"
        if st == "delivered":
            return "delivered"
        return "new"
    if theme == "brak":
        # Пока без тонкой ветки «чинится деталью» — безопаснее вести на возврат,
        # кроме разрешённых SKU (можно предложить деталь).
        return "fixable_by_part" if reshipment_skus.is_allowed(db, article) else "needs_return"
    if theme == "blagodarnost":
        if re.search(r"(всё\s+пришло|все\s+пришло|получил|подошло|собрал)", msg or "", re.I):
            return "problem_solved"
        return "just_thanks"
    if theme == "sborka" and article and "стеллаж" in article.lower():
        return "стеллаж"
    return ""


def _fill_placeholders(body: str, *, form_url: str = "", reship: Optional[dict] = None) -> str:
    carrier = "СДЭК"
    track = ""
    if reship:
        if (reship.get("delivery_method") or "") == "yandex":
            carrier = "Яндекс Доставка"
        track = reship.get("cdek_number") or reship.get("track_number") or ""
    return (
        body.replace("{form_url}", form_url or FORM_BASE)
            .replace("{carrier}", carrier)
            .replace("{track}", track or "—")
            .replace("{storage_until}", "указанного срока")
    )


def _needs_greeting(state: dict, last_seller_ts: Optional[int]) -> bool:
    today = _msk_date()
    last_g = state.get("last_greeted_date")
    if last_g:
        if hasattr(last_g, "isoformat"):
            if last_g == today:
                return False
        elif str(last_g)[:10] == str(today):
            return False
    # Если сегодня уже писали мы (по last_ts продавца) — тоже без приветствия
    if last_seller_ts:
        if _ts_to_msk_date(last_seller_ts) == today:
            return False
    return True


def _strip_greeting(body: str) -> str:
    """Убирает приветствие в начале и ведущие пустые строки/пробелы."""
    text = body or ""
    text = _GREETING_RE.sub("", text, count=1)
    # На случай «Здравствуйте!» отдельной строкой + пустые строки после
    text = re.sub(
        r"^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*\n+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.lstrip(" \t\r\n")


def assemble_reply(db: Session, theme: str, branch: str, chat_id: str,
                   state: dict, reship: Optional[dict],
                   last_seller_ts: Optional[int]) -> Tuple[str, str]:
    """Возвращает (текст, action). Пустой текст — шаблона нет (тогда возможен ИИ-черновик)."""
    action_row = db.execute(text(
        "SELECT action FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).fetchone()
    action = (action_row[0] if action_row else "") or ""

    body = chat_playbook.pick_variant(db, theme, branch)
    if not body and branch:
        body = chat_playbook.pick_variant(db, theme, "")
    if not body:
        body = chat_faq.FAQ_FALLBACKS.get(theme, "")
    if not body:
        return "", action

    form_url = ""
    if action in ("offer_reshipment", "send_form_link", "resend_form_link", "ask_menu") or "{form_url}" in body:
        if "{form_url}" in body or action in ("offer_reshipment", "send_form_link", "resend_form_link"):
            form_url = _make_form_url(db, chat_id)

    body = _fill_placeholders(_strip_greeting(body), form_url=form_url, reship=reship)
    return _polish_reply(db, body, state, last_seller_ts), action


def _next_stage(theme: str, action: str, current: str) -> str:
    if theme == "utochnenie" or action == "ask_menu":
        return "awaiting_menu"
    if theme == "soglasie" or action == "send_form_link":
        return "form_sent"
    if theme == "nekomplekt" and action == "offer_reshipment":
        return "awaiting_client"
    if theme == "ssylka":
        return "form_sent"
    if theme == "blagodarnost":
        return "closing"
    if theme == "status_zayavki":
        return current or "in_work"
    if theme in ("otkaz_dootpravki", "dengi"):
        return "handoff"
    # После выбора из меню — сбрасываем awaiting_menu
    if current == "awaiting_menu" and theme != "utochnenie":
        if theme == "nekomplekt":
            return "awaiting_client"
        return "new"
    return current or "new"


# ── Отправка / лог ───────────────────────────────────────────────────────────

def _log(db: Session, **kw):
    db.execute(text("""
        INSERT INTO cx_chat_bot_log
          (chat_id, client_message, theme, confidence, zone, branch_key,
           reply_text, action, shadow, sent, need_human, reason)
        VALUES
          (:chat_id, :client_message, :theme, :confidence, :zone, :branch_key,
           :reply_text, :action, :shadow, :sent, :need_human, :reason)
    """), kw)
    db.commit()


def _send_to_client(db: Session, chat_id: str, reply_sign: str, text_msg: str) -> bool:
    try:
        wb_chat.send_message(reply_sign, text_msg)
    except Exception as e:
        logger.error("[ChatBot] send fail %s: %s", chat_id, e)
        return False
    now_ms = int(time.time() * 1000)
    db.execute(text("""
        UPDATE wb_chats SET needs_answer = false, last_sender = 'seller',
            last_message = :ans, last_ts = :ts, updated_at = now()
        WHERE chat_id = :cid
    """), {"cid": chat_id, "ans": text_msg[:1000], "ts": now_ms})
    exists = db.execute(text("""
        SELECT id FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller' AND text = :txt
          AND add_ts BETWEEN :t0 AND :t1
        LIMIT 1
    """), {"cid": chat_id, "txt": text_msg[:1000],
           "t0": now_ms - 180_000, "t1": now_ms + 180_000}).first()
    if not exists:
        db.execute(text("""
            INSERT INTO wb_chat_messages (chat_id, sender, text, add_ts)
            VALUES (:cid, 'seller', :txt, :ts)
            ON CONFLICT (chat_id, add_ts, sender) DO NOTHING
        """), {"cid": chat_id, "txt": text_msg[:1000], "ts": now_ms})
    db.execute(text("""
        INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)
        VALUES ('chat', :iid, 'wb', 'chat_bot', :ans)
    """), {"iid": chat_id, "ans": text_msg})
    db.commit()
    return True


def _notify_manager(db: Session, reason: str, client: str, sku: str,
                    message: str, chat_id: str):
    if chat_playbook.get_setting(db, "red_zone_notify_tg", "true").lower() != "true":
        return
    tmpl = chat_playbook.get_setting(db, "red_zone_manager_text", "")
    from urllib.parse import quote
    link = f"https://cxvo.ru/chats?chat={quote(str(chat_id), safe='')}"
    text_msg = (tmpl or "🔴 Чат {client}: {reason}\n{message}\n{link}").format(
        reason=reason or "—",
        client=client or "—",
        sku=sku or "—",
        message=(message or "")[:300],
        link=link,
    )
    try:
        cx_alert.send_tg(text_msg)
    except Exception as e:
        logger.warning("[ChatBot] TG notify: %s", e)


# ── Обработка одного чата ────────────────────────────────────────────────────

def _has_seller_reply(db: Session, chat_id: str) -> bool:
    row = db.execute(text("""
        SELECT 1 FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
          AND text IS NOT NULL AND text != ''
        LIMIT 1
    """), {"cid": chat_id}).fetchone()
    return bool(row)


def process_one(db: Session, chat: dict, *, shadow: bool) -> Optional[dict]:
    chat_id = chat["chat_id"]
    msg = (chat.get("last_message") or "").strip()
    if not msg:
        return None

    # Уже отвечали на этот же last_ts?
    state = _bot_state(db, chat_id)
    last_bot = state.get("last_bot_at")
    last_ts = chat.get("last_ts")
    if last_bot and last_ts:
        # last_bot_at храним как timestamp; если бот уже трогал чат после last_ts — пропуск
        try:
            bot_ms = int(last_bot.replace(tzinfo=timezone.utc).timestamp() * 1000) if hasattr(last_bot, "timestamp") else 0
            # грубо: если last_bot новее сообщения клиента — уже обработали
            if bot_ms and bot_ms >= int(last_ts):
                return None
        except Exception:
            pass

    # Старт: не лезем в переписки, где уже отвечал менеджер.
    # Если бот этот чат уже вёл (last_bot_at) — продолжаем (иначе после первого ответа
    # бот навсегда выпал бы из диалога).
    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", "wb", "true").lower() == "true"
    if only_no_mgr and not last_bot and _has_seller_reply(db, chat_id):
        return None

    article = _get_article(db, chat.get("nm_id"))
    reship = _get_reshipment(db, chat_id)
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))

    history = _history(db, chat_id)
    stage = state.get("stage") or "new"

    theme, conf, need_human, reason = classify(
        db, msg, stage, history, article, nm_id=chat.get("nm_id")
    )

    zone = chat_playbook.get_zone(db, theme)
    if need_human:
        zone = "human"

    branch = _resolve_branch(db, theme, article, reship, msg)

    # Последнее наше сообщение — для правила приветствия
    last_seller = db.execute(text("""
        SELECT add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
        ORDER BY add_ts DESC NULLS LAST LIMIT 1
    """), {"cid": chat_id}).fetchone()
    last_seller_ts = last_seller[0] if last_seller else None
    need_greet = _needs_greeting(state, last_seller_ts)

    if zone == "human":
        client_text = chat_playbook.get_setting(db, "red_zone_client_text", "")
        reply = client_text.strip()
        action = "handoff_to_manager"
        if reply and need_greet:
            greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
            if greet and not _GREETING_RE.match(reply):
                reply = f"{greet}\n\n{reply}"
    else:
        reply, action = assemble_reply(
            db, theme, branch, chat_id, state, reship, last_seller_ts
        )
        # Нет шаблона → ИИ-черновик менеджеру (никогда не auto-отправка)
        if not reply:
            draft = generate_ai_draft(
                db,
                client_msg=msg,
                theme=theme,
                article=article or "",
                history=history,
                nm_id=chat.get("nm_id"),
                reship=reship,
            )
            if draft:
                reply = _polish_reply(db, draft, state, last_seller_ts)
                action = "ai_draft"
                zone = "draft"
                reason = ((reason or "") + "; ИИ-черновик без шаблона").strip("; ")

    sent = False
    if zone == "auto" and reply and not shadow:
        sign = chat.get("reply_sign")
        if sign:
            sent = _send_to_client(db, chat_id, sign, reply)
        else:
            reason = (reason + "; нет reply_sign").strip("; ")

    if zone == "human" or need_human:
        _notify_manager(
            db, reason=reason or theme,
            client=chat.get("client_name") or "",
            sku=article,
            message=msg,
            chat_id=chat_id,
        )

    new_stage = _next_stage(theme, action, stage)
    state_update = {
        "stage": new_stage,
        "last_theme": theme,
        "last_bot_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if theme == "utochnenie" or action == "ask_menu":
        state_update["pending_menu"] = json.dumps(chat_faq.CLARIFY_MENU_MAP, ensure_ascii=False)
    elif stage == "awaiting_menu" and theme != "utochnenie":
        state_update["pending_menu"] = None
    if reply and need_greet:
        state_update["last_greeted_date"] = _msk_date()

    _set_state(db, chat_id, **state_update)

    _log(
        db,
        chat_id=chat_id,
        client_message=msg[:2000],
        theme=theme,
        confidence=conf,
        zone=zone,
        branch_key=branch or "",
        reply_text=reply or "",
        action=action or "",
        shadow=shadow,
        sent=sent,
        need_human=need_human or zone == "human",
        reason=reason or "",
    )

    return {
        "chat_id": chat_id,
        "theme": theme,
        "zone": zone,
        "confidence": conf,
        "sent": sent,
        "shadow": shadow,
        "reply": (reply or "")[:200],
        "reason": reason,
    }


def suggest_for_chat(db: Session, chat_id: str, *, force_ai: bool = False,
                     use_cache: bool = True) -> dict:
    """
    Подсказка шаблона при открытии чата менеджером.
    Клиенту ничего не отправляет и last_bot_at не трогает.
    Кэш по chat_id + last_ts клиента — повторный вход без новых токенов Яндекса.
    force_ai=True — принудительно сгенерировать ИИ-ответ (кнопка «Сгенерировать»).
    """
    row = db.execute(text("""
        SELECT chat_id, client_name, nm_id, good_name, last_message,
               last_sender, last_ts, reply_sign, needs_answer, is_closing
        FROM wb_chats WHERE chat_id = :cid
    """), {"cid": chat_id}).mappings().first()
    if not row:
        return {"ok": False, "reason": "чат не найден", "can_generate": False}

    chat = dict(row)
    msg = (chat.get("last_message") or "").strip()
    last_ts = int(chat.get("last_ts") or 0)

    if not msg:
        return {"ok": True, "reply_text": "", "theme": "", "confidence": 0,
                "reason": "нет текста клиента", "need_human": False,
                "can_generate": False, "skip": False, "from_cache": False}

    if chat.get("last_sender") == "seller" or not chat.get("needs_answer"):
        return {
            "ok": True, "reply_text": "", "theme": "", "confidence": 0,
            "reason": "чат не ждёт ответа", "need_human": False,
            "skip": True, "can_generate": False, "from_cache": False,
        }

    # Кэш: тот же last_ts клиента → не дергаем Яндекс снова
    if use_cache and not force_ai:
        cached = db.execute(text("""
            SELECT payload FROM cx_chat_bot_suggest_cache
            WHERE chat_id = :cid AND client_last_ts = :ts
        """), {"cid": chat_id, "ts": last_ts}).scalar()
        if cached:
            payload = cached if isinstance(cached, dict) else json.loads(cached)
            payload = dict(payload or {})
            payload["from_cache"] = True
            payload["ok"] = True
            return payload

    state = _bot_state(db, chat_id)
    article = _get_article(db, chat.get("nm_id"))
    reship = _get_reshipment(db, chat_id)
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))
    history = _history(db, chat_id)
    stage = state.get("stage") or "new"

    theme, conf, need_human, reason = classify(
        db, msg, stage, history, article, nm_id=chat.get("nm_id")
    )

    zone = chat_playbook.get_zone(db, theme)
    if need_human:
        zone = "human"

    branch = _resolve_branch(db, theme, article, reship, msg)
    last_seller = db.execute(text("""
        SELECT add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
        ORDER BY add_ts DESC NULLS LAST LIMIT 1
    """), {"cid": chat_id}).fetchone()
    last_seller_ts = last_seller[0] if last_seller else None

    reply = ""
    action = ""
    if zone == "human" and not force_ai:
        reply = (chat_playbook.get_setting(db, "red_zone_client_text", "") or "").strip()
        action = "handoff_to_manager"
        if reply and _needs_greeting(state, last_seller_ts):
            greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
            if greet and not _GREETING_RE.match(reply):
                reply = f"{greet}\n\n{reply}"
    elif not force_ai:
        reply, action = assemble_reply(
            db, theme, branch, chat_id, state, reship, last_seller_ts
        )

    # ИИ только по кнопке «Сгенерировать» — не при каждом открытии
    if force_ai:
        draft = generate_ai_draft(
            db, client_msg=msg, theme=theme, article=article or "",
            history=history, nm_id=chat.get("nm_id"), reship=reship,
        )
        if draft:
            reply = _polish_reply(db, draft, state, last_seller_ts)
            action = "ai_draft"
            reason = ((reason or "") + "; ИИ-черновик по запросу").strip("; ")
        else:
            reason = ((reason or "") + "; ИИ не смог предложить текст").strip("; ")

    theme_name = ""
    tr = db.execute(text(
        "SELECT name FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).fetchone()
    if tr:
        theme_name = tr[0] or ""

    can_generate = (not reply) and bool(msg) and zone != "human"
    # human тоже можно сгенерировать черновик менеджеру, если красный текст пустой
    if not reply and zone == "human":
        can_generate = True

    if not reply and not force_ai:
        reason = reason or "шаблон не подобран"

    result = {
        "ok": True,
        "chat_id": chat_id,
        "theme": theme,
        "theme_name": theme_name,
        "confidence": conf,
        "zone": zone,
        "branch_key": branch or "",
        "action": action or "",
        "reply_text": reply or "",
        "need_human": need_human or zone == "human",
        "reason": reason or "",
        "skip": False,
        "can_generate": can_generate,
        "from_cache": False,
    }

    # Кэшируем результат (в т.ч. «не подобран» — чтобы не жечь токены на classify снова)
    try:
        db.execute(text("""
            INSERT INTO cx_chat_bot_suggest_cache (chat_id, client_last_ts, payload, updated_at)
            VALUES (:cid, :ts, CAST(:p AS jsonb), now())
            ON CONFLICT (chat_id) DO UPDATE SET
                client_last_ts = EXCLUDED.client_last_ts,
                payload = EXCLUDED.payload,
                updated_at = now()
        """), {"cid": chat_id, "ts": last_ts, "p": json.dumps(result, ensure_ascii=False)})
        db.commit()
    except Exception as e:
        logger.warning("[ChatBot] suggest cache: %s", e)
        try:
            db.rollback()
        except Exception:
            pass

    return result


def process_batch(db: Session, limit: int = BOT_BATCH_LIMIT, *, force: bool = False) -> dict:
    """force=True — ручной прогон по кнопке, игнорирует bot_enabled (автозапуск)."""
    enabled = chat_playbook.get_setting_mp(db, "bot_enabled", "wb", "false").lower() == "true"
    if not enabled and not force:
        return {"ok": False, "reason": "bot_enabled=false (автозапуск выключен)", "processed": 0}

    # Пока нет полной автоматизации: без bot_enabled клиенту не пишем (даже с кнопки).
    shadow = True
    if enabled:
        shadow = chat_playbook.get_setting(db, "bot_shadow", "false").lower() == "true"
    try:
        only_days = int(float(chat_playbook.get_setting_mp(db, "bot_only_days", "wb", "2") or "2"))
    except ValueError:
        only_days = 2
    if only_days <= 0:
        only_days = 30  # предохранитель: не берём вечность

    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", "wb", "true").lower() == "true"

    # Чаты, где ждём ответа клиента и не в архиве — только свежие.
    # reply_sign нужен только для реальной отправки; в тени обрабатываем и без него.
    # bot_only_no_manager: пропускаем чаты, где уже писал продавец, а бот ещё не входил.
    rows = db.execute(text("""
        SELECT c.chat_id, c.client_name, c.nm_id, c.good_name, c.last_message,
               c.last_sender, c.last_ts, c.reply_sign, c.needs_answer, c.is_closing
        FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
               ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
        WHERE c.needs_answer = true
          AND COALESCE(c.is_closing, false) = false
          AND c.last_sender = 'client'
          AND d.item_id IS NULL
          AND (CAST(:shadow AS boolean) = true OR c.reply_sign IS NOT NULL)
          AND c.last_ts > EXTRACT(EPOCH FROM now() - make_interval(days => :days)) * 1000
          -- ещё не обрабатывали это сообщение клиента (иначе кнопка крутит одни и те же 15)
          AND (s.last_bot_at IS NULL
               OR (EXTRACT(EPOCH FROM s.last_bot_at) * 1000) < c.last_ts)
          AND (
                CAST(:only_no_mgr AS boolean) = false
                OR s.last_bot_at IS NOT NULL
                OR NOT EXISTS (
                    SELECT 1 FROM wb_chat_messages m
                    WHERE m.chat_id = c.chat_id AND m.sender = 'seller'
                      AND m.text IS NOT NULL AND m.text != ''
                )
              )
        ORDER BY c.last_ts DESC
        LIMIT :lim
    """), {
        "lim": limit, "days": only_days, "shadow": shadow,
        "only_no_mgr": only_no_mgr,
    }).mappings().all()

    results = []
    skipped_empty = 0
    for row in rows:
        try:
            r = process_one(db, dict(row), shadow=shadow)
            if r:
                results.append(r)
            else:
                skipped_empty += 1
        except Exception as e:
            logger.exception("[ChatBot] process %s: %s", row.get("chat_id"), e)
            try:
                db.rollback()
            except Exception:
                pass

    by_zone = {"auto": 0, "draft": 0, "human": 0}
    for r in results:
        z = r.get("zone") or "human"
        by_zone[z] = by_zone.get(z, 0) + 1

    return {
        "ok": True,
        "shadow": shadow,
        "processed": len(results),
        "candidates": len(rows),
        "skipped_empty": skipped_empty,
        "by_zone": by_zone,
        "items": results,
    }


# ── Воркер ───────────────────────────────────────────────────────────────────

async def start_chat_bot_worker(get_db_func):
    logger.info("[ChatBot] worker started, interval=%ss", BOT_INTERVAL_SEC)
    await asyncio.sleep(20)  # дать синку чатов прогреться
    while True:
        try:
            db = next(get_db_func())
            try:
                stats = await asyncio.to_thread(process_batch, db, BOT_BATCH_LIMIT)
                if stats.get("processed"):
                    logger.info("[ChatBot] batch: %s", stats)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[ChatBot] worker error: %s", e)
        await asyncio.sleep(BOT_INTERVAL_SEC)
