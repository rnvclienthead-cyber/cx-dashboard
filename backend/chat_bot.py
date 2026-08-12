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
from .routers import chat_playbook, reshipment_skus, nonreturnable_cats, question_playbook
from . import wb_chat
from . import chat_faq

logger = logging.getLogger("chat_bot")

MSK = ZoneInfo("Europe/Moscow")
BOT_INTERVAL_SEC = 15  # было 90; для диалога 1/2/3 нужна быстрая реакция
BOT_BATCH_LIMIT = 25
MIN_CONFIDENCE = 0.55
# Ждём пачку сообщений клиента (фото + текст), не отвечаем мгновенно на первое фото
BOT_DEBOUNCE_SEC = 90

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

# Причина уже названа — дальше возврат разбирает менеджер, бот не выдаёт
# инструкцию оформления. Намеренно не включает слова «причина»/«возврат» сами
# по себе: вопрос «как указать причину возврата?» всё ещё требует уточнения.
_RETURN_REASON_RE = re.compile(
    r"(брак|дефект|поврежд|слом|трещин|царап|вмят|не\s+подош[её]л|"
    r"не\s+подходит|размер|лишн|передум|не\s+понрав|не\s+нужен|"
    r"не\s+устраива|не\s+хвата|неполн\w*\s+комплект|не\s+тот\s+товар|"
    r"другой\s+(товар|цвет|модель)|не\s+соответств|качество|запах)",
    re.IGNORECASE,
)


def _has_return_reason(msg: str) -> bool:
    return bool(_RETURN_REASON_RE.search(msg or ""))


def _apply_return_flow(theme: str, msg: str, stage: str, need_human: bool,
                       reason: str) -> Tuple[str, bool, str]:
    """Применяет двухшаговый поток только к обычному ``vozvrat``.

    ``nevozvratny`` сюда не попадает: WB-правило категории остаётся отдельным.
    """
    if theme != "vozvrat":
        return theme, need_human, reason
    if need_human:
        return theme, True, reason
    if stage == "awaiting_return_reason" or _has_return_reason(msg):
        note = "причина возврата получена → менеджер"
        return theme, True, f"{reason}; {note}".strip("; ")
    note = "возврат без причины → уточнить причину"
    return theme, False, f"{reason}; {note}".strip("; ")


def _return_reason_text(mp: str) -> str:
    """Короткий вопрос причины с названием раздела конкретной площадки."""
    mp = (mp or "wb").lower()
    section = "«Покупки»" if mp == "wb" else "«Заказы»"
    return (
        "Подскажите, пожалуйста, почему решили вернуть товар? "
        "Например: не подошёл размер, обнаружили брак, товар оказался лишним "
        f"или вы передумали. Товар можно найти в разделе {section}."
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
    r"хочу\s+отменить\s+заказ|отмените\s+пожалуйста\s+заказ)",
    re.IGNORECASE,
)

# Клиент закрывает тему / отменяет обращение к боту (не заказ)
_RESOLVED_RE = re.compile(
    r"(все?\s+хорошо|всё\s+хорошо|все\s+ок|разобрал|нашли|нашла|нашёл|"
    r"не\s+буду(\s+уже)?|уже\s+собрал|вопрос\s+реш|проблема\s+реш|"
    r"спасибо[\s!,.]*$|благодар)",
    re.IGNORECASE,
)
_CANCEL_BOT_RE = re.compile(
    r"^(отмена|отменить|cancel|стоп|хватит|не\s+надо)[\s!.?]*$",
    re.IGNORECASE,
)

_VAGUE_RE = re.compile(
    r"^(здравствуйте|добрый\s+(день|вечер|утро)|привет|помогите|"
    r"подскажите|вопрос|есть\s+вопрос|нужна\s+помощь)[\s!.?]*$",
    re.IGNORECASE,
)

# Только сменные насадки/тряпки к швабре — НЕ заглушки/крепёж
_NASADKI_RE = re.compile(
    r"(насадк\w*|сменн\w*\s+(тряпк\w*|насадк\w*)|тряпк\w*\s+к\s+швабр|"
    r"(купить|где\s+(купить|взять|заказать)|приобрести|докапит\w*)\s+"
    r"(сменн\w*\s+)?(насадк\w*|тряпк\w*))",
    re.IGNORECASE,
)

# Заглушки, крепёж, пробки — отдельно не продаём
_KREPEZH_RE = re.compile(
    r"(заглушк\w*|защ[её]лк\w*|пробк\w*|креп[её]ж\w*|сливн\w*\s+отверст|"
    r"заглушк\w*\s+для\s+слив|мелк\w*\s+детал)",
    re.IGNORECASE,
)

# Ozon Playbook 2.0 (логики Альфреда)
_WRONG_ITEM_RE = re.compile(
    r"(не\s+тот\s+товар|прислали\s+не\s+тот|пришёл\s+не\s+тот|пришел\s+не\s+тот|"
    r"другой\s+(товар|цвет|модель|артикул)|не\s+тот\s+цвет|пересорт|"
    r"вместо\s+\w+\s+прислал|это\s+не\s+(ваш|наш)\s+товар)",
    re.IGNORECASE,
)
_EXPIRED_RETURN_RE = re.compile(
    r"(срок\s+(возврата|верн\w*)\s+ист[её]к|ист[её]к\s+срок\s+(возврата|верн)|"
    r"не\s+получается\s+(оформить\s+)?возврат|не\s+могу\s+(оформить\s+)?возврат|"
    r"нет\s+кнопк\w*\s*[«\"]?верн|кнопк\w*\s*[«\"]?верн\w*[»\"]?\s+нет|"
    r"возврат\s+уже\s+недоступ|не\s+дает\s+вернуть)",
    re.IGNORECASE,
)
_TUBE_CHECK_RE = re.compile(
    r"(трубк|перекладин|палки|боков\w*\s+(креплени|перекладин)|"
    r"не\s+хвата\w*\s+\w*\s*труб|труб\w*\s+не\s+хвата)",
    re.IGNORECASE,
)
_KEPT_ANYWAY_RE = re.compile(
    r"(остав(лю|или|им|ляю)\s+(товар|сушилку|стеллаж|обувниц)|"
    r"буду\s+пользова|пользуюсь|оставила?\s+себе)",
    re.IGNORECASE,
)

# Короткий «другое» / «5» вне меню — не гоняем снова в уточнение
_OTHER_SHORT_RE = re.compile(
    r"^(другое|другое\s+пожалуйста|иное|5\s*[-–—.]?\s*(другое)?|"
    r"пункт\s*5|вариант\s*5|нужна\s+другая\s+деталь)[\s!.?]*$",
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
    ("bot_test_chat_ids", "",
     "Тестовый allowlist: chat_id через запятую/пробел. Если не пусто — бот обрабатывает ТОЛЬКО эти чаты. Пусто = все."),
]

# Ключи, которые хранятся отдельно на каждую площадку (см. get_setting_mp).
BOT_MP_CONTROL_KEYS = {
    "bot_enabled", "bot_confidence_min", "bot_only_days",
    "bot_only_no_manager", "bot_suggest_on_open", "bot_ai_draft",
    "bot_test_chat_ids",
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
        try:
            conn.execute(text(
                "ALTER TABLE cx_chat_bot_log ADD COLUMN IF NOT EXISTS mp TEXT DEFAULT 'wb'"
            ))
        except Exception:
            pass
        # Fast handoff check after a sent auto-reply.  Do not put COALESCE(mp,
        # 'wb') in the predicate: old WB rows with mp=NULL must stay covered.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chat_bot_log_sent_auto_lookup
            ON cx_chat_bot_log (chat_id, mp, created_at DESC, id DESC)
            WHERE sent = true AND zone = 'auto'
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

def _remap_theme_for_mp(theme: str, mp: str, reason: str = "",
                        client_msg: str = "") -> Tuple[str, str]:
    """На Ozon/ЯМ доотправки деталей нет — ветки некомплекта без формы WB."""
    mp = (mp or "wb").lower()
    msg = client_msg or ""
    # ИИ часто путает «заглушки» с темой насадок — не шлём ссылку на насадки
    if theme == "zapchasti" and not _NASADKI_RE.search(msg):
        note = "→krepezh (не насадки)"
        reason = f"{reason}; {note}".strip("; ") if reason else note
        theme = "krepezh"
    if mp == "wb" or not theme:
        return theme, reason
    # Невозвратность сушилок — правило WB; на Ozon/ЯМ обычный возврат площадки
    if theme == "nevozvratny":
        note = f"→vozvrat ({mp}: nevozvratny только WB)"
        reason = f"{reason}; {note}".strip("; ") if reason else note
        return "vozvrat", reason
    # Согласие/ссылка/статус заявки на WB-доотправку → некомплект без досылки
    if theme in ("soglasie", "ssylka", "status_zayavki"):
        note = f"→nekomplekt ({mp}: без доотправки)"
        reason = f"{reason}; {note}".strip("; ") if reason else note
        return "nekomplekt", reason
    # nekomplekt оставляем — ветки check_tubes / no_reship_return
    return theme, reason


_MOP_ARTICLE_RE = re.compile(r"^mop[\-_]", re.I)


def _is_mop_or_bucket(db: Session, *, article: str = "", nm_id=None) -> bool:
    """Тема ekspluataciya (видео швабры) — только артикулы Mop-* / категория швабры|вёдра."""
    art = (article or "").strip()
    if art and _MOP_ARTICLE_RE.match(art):
        return True
    cat = ""
    try:
        cat = nonreturnable_cats.get_category(db, article=art or None, nm_id=nm_id) or ""
    except Exception:
        cat = ""
    cl = cat.lower()
    if any(x in cl for x in ("швабр", "ведр", "ведер")):
        return True
    if not art and nm_id is not None:
        resolved = _get_article(db, nm_id)
        if resolved and _MOP_ARTICLE_RE.match(resolved.strip()):
            return True
    return False


_PARTS_REQ_RE = re.compile(
    r"не\s*хвата|некомплект|доотправ|дослат|прислать|выслать|детал|"
    r"боков(ые|ина)|палки|трубк|крепеж|колес|комплект",
    re.I,
)


def _looks_like_missing_parts(msg: str = "", reason: str = "") -> bool:
    return bool(_PARTS_REQ_RE.search(f"{msg or ''} {reason or ''}"))


def _clarify_menu_text(mp: str = "wb") -> str:
    """Меню 1–5: на Ozon/ЯМ пункт 1 — некомплект без предложения досылки."""
    if (mp or "wb").lower() in ("ozon", "ym"):
        mp_name = "Ozon" if mp == "ozon" else "Яндекс Маркет"
        return (
            "Подскажите, пожалуйста, что вам нужно — ответьте цифрой или коротко своими словами:\n"
            "1 — не хватает детали / неполная комплектация\n"
            "2 — брак или повреждение товара\n"
            "3 — вопрос по сборке, размерам или нагрузке\n"
            f"4 — хочу оформить возврат на {mp_name}\n"
            "5 — другое (подключу коллегу)"
        )
    return chat_faq.CLARIFY_MENU_TEXT


def _return_only_text(mp: str) -> str:
    """Ответ при запросе детали на площадке без доотправки: сначала «не можем выслать», потом возврат."""
    if mp == "ym":
        where = (
            "в личном кабинете Яндекс Маркета: откройте заказ → "
            "оформите возврат с указанием причины «неполная комплектация» или «брак»"
        )
    else:
        where = (
            "в личном кабинете Ozon: раздел «Заказы» → выберите товар → "
            "нажмите «Вернуть» и укажите причину «неполная комплектация» или «брак». "
            "К заявке желательно приложить фото полученного комплекта"
        )
    return (
        "Здравствуйте! К сожалению, отдельно прислать или продать недостающую деталь "
        "мы не можем. "
        f"Рекомендуем оформить возврат {where}. "
        "Окончательное решение и дальнейшие инструкции предоставит маркетплейс."
    )


def _get_article(db: Session, nm_id) -> str:
    if not nm_id:
        return ""
    try:
        n = str(int(nm_id))
    except (TypeError, ValueError):
        return ""
    for sql in (
        """SELECT supplier_article FROM wb_feedbacks
           WHERE TRIM(COALESCE(nm_id, '')) = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY created_date DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_claims
           WHERE TRIM(COALESCE(nm_id, '')) = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY claim_date DESC NULLS LAST, created_dt DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_logistics
           WHERE TRIM(COALESCE(nm_id, '')) = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY last_change_date DESC NULLS LAST, dt DESC NULLS LAST LIMIT 1""",
    ):
        try:
            row = db.execute(text(sql), {"n": n}).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            db.rollback()
    return ""


def _norm_sender(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("customer", "buyer", "client", "user"):
        return "client"
    if s in ("seller", "courier"):
        return "seller"
    return s or "system"


def _load_chat_for_suggest(db: Session, chat_id: str, mp: str) -> Optional[dict]:
    """Единый вид чата для подсказок: wb / ozon / ym."""
    mp = (mp or "wb").lower()
    if mp == "wb":
        cid = chat_id
        row = db.execute(text("""
            SELECT chat_id, client_name, nm_id, good_name, last_message,
                   last_sender, last_ts, reply_sign, needs_answer, is_closing
            FROM wb_chats WHERE chat_id = :cid
        """), {"cid": cid}).mappings().first()
        if not row and ":" not in (cid or ""):
            row = db.execute(text("""
                SELECT chat_id, client_name, nm_id, good_name, last_message,
                       last_sender, last_ts, reply_sign, needs_answer, is_closing
                FROM wb_chats
                WHERE chat_id = :p1 OR chat_id LIKE :suf
                ORDER BY CASE WHEN chat_id = :p1 THEN 0 ELSE 1 END
                LIMIT 1
            """), {"p1": f"1:{cid}", "suf": f"%:{cid}"}).mappings().first()
        return dict(row) if row else None

    if mp == "ozon":
        row = db.execute(text("""
            SELECT chat_id,
                   COALESCE(NULLIF(TRIM(context_article), ''), order_number, 'Покупатель')
                     AS client_name,
                   context_sku AS nm_id,
                   context_article AS good_name,
                   last_message, last_sender, needs_answer,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                      WHERE m.chat_id = ozon_chats.chat_id
                        AND m.sender_type IN ('customer','buyer','client')),
                     waiting_since, updated_at, created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ozon_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not row:
            return None
        d = dict(row)
        d["is_closing"] = False
        d["reply_sign"] = None
        d["last_sender"] = _norm_sender(d.get("last_sender"))
        # Если превью — фото, берём последний текст клиента
        msg = (d.get("last_message") or "").strip()
        if not msg or msg.startswith("📷") or msg.startswith("!["):
            t = db.execute(text("""
                SELECT text FROM ozon_chat_messages
                WHERE chat_id = :cid
                  AND sender_type IN ('customer','buyer','client')
                  AND text IS NOT NULL AND TRIM(text) <> ''
                  AND text NOT LIKE '![](%'
                ORDER BY created_at DESC NULLS LAST LIMIT 1
            """), {"cid": chat_id}).scalar()
            if t:
                d["last_message"] = t
        return d

    if mp == "ym":
        row = db.execute(text("""
            SELECT chat_id,
                   COALESCE(NULLIF(TRIM(customer_name), ''), 'Покупатель') AS client_name,
                   NULL::bigint AS nm_id,
                   COALESCE(
                     NULLIF(TRIM(context_article), ''),
                     (SELECT o.supplier_article FROM ym_orders o
                      WHERE ym_chats.order_id IS NOT NULL
                        AND o.order_id::text = ym_chats.order_id
                        AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL
                      LIMIT 1),
                     (SELECT r.supplier_article FROM ym_returns r
                      WHERE (
                        (ym_chats.return_id IS NOT NULL AND r.return_id::text = ym_chats.return_id)
                        OR (ym_chats.order_id IS NOT NULL AND r.order_id::text = ym_chats.order_id)
                      )
                      AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                      ORDER BY r.created_at DESC NULLS LAST LIMIT 1)
                   ) AS good_name,
                   last_message, last_sender, needs_answer,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ym_chat_messages m
                      WHERE m.chat_id = ym_chats.chat_id
                        AND lower(COALESCE(m.sender_type,'')) IN ('customer','buyer','client','user')),
                     waiting_since, updated_at, created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ym_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not row:
            return None
        d = dict(row)
        d["is_closing"] = False
        d["reply_sign"] = None
        d["last_sender"] = _norm_sender(d.get("last_sender"))
        return d

    return None


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


def _history(db: Session, chat_id: str, limit: int = 8, mp: str = "wb") -> List[dict]:
    mp = (mp or "wb").lower()
    if mp == "ozon":
        rows = db.execute(text("""
            SELECT sender_type AS sender, text,
                   EXTRACT(EPOCH FROM created_at)::bigint * 1000 AS add_ts
            FROM ozon_chat_messages
            WHERE chat_id = :cid AND text IS NOT NULL AND text != ''
            ORDER BY created_at DESC NULLS LAST LIMIT :lim
        """), {"cid": chat_id, "lim": limit}).mappings().all()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["sender"] = _norm_sender(d.get("sender"))
            out.append(d)
        return out
    if mp == "ym":
        rows = db.execute(text("""
            SELECT sender_type AS sender, text,
                   EXTRACT(EPOCH FROM created_at)::bigint * 1000 AS add_ts
            FROM ym_chat_messages
            WHERE chat_id = :cid AND text IS NOT NULL AND text != ''
            ORDER BY created_at DESC NULLS LAST LIMIT :lim
        """), {"cid": chat_id, "lim": limit}).mappings().all()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["sender"] = _norm_sender(d.get("sender"))
            out.append(d)
        return out
    rows = db.execute(text("""
        SELECT sender, text, add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND text IS NOT NULL AND text != ''
        ORDER BY add_ts DESC NULLS LAST LIMIT :lim
    """), {"cid": chat_id, "lim": limit}).mappings().all()
    return list(reversed([dict(r) for r in rows]))


def _last_seller_ts(db: Session, chat_id: str, mp: str = "wb"):
    mp = (mp or "wb").lower()
    if mp == "ozon":
        row = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM created_at)::bigint * 1000
            FROM ozon_chat_messages
            WHERE chat_id = :cid AND sender_type IN ('seller', 'courier')
            ORDER BY created_at DESC NULLS LAST LIMIT 1
        """), {"cid": chat_id}).fetchone()
        return row[0] if row else None
    if mp == "ym":
        row = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM created_at)::bigint * 1000
            FROM ym_chat_messages
            WHERE chat_id = :cid
              AND lower(COALESCE(sender_type, '')) IN ('seller', 'partner', 'merchant')
            ORDER BY created_at DESC NULLS LAST LIMIT 1
        """), {"cid": chat_id}).fetchone()
        return row[0] if row else None
    row = db.execute(text("""
        SELECT add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
        ORDER BY add_ts DESC NULLS LAST LIMIT 1
    """), {"cid": chat_id}).fetchone()
    return row[0] if row else None


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


_POST_AUTO_REPLY_HANDOFF_REASON = (
    "клиент ответил на автоответ → передано менеджеру; "
    "повторный автоответ отключён"
)


def _client_replied_after_sent_auto(
    db: Session, chat_id: str, client_last_ts, mp: str = "wb"
) -> bool:
    """Есть ли новое сообщение клиента после реально отправленного автоответа.

    ``last_bot_at`` здесь не подходит: он обновляется и в shadow/draft-сценариях.
    Берём только запись журнала с ``sent=true`` и зоной ``auto``.
    """
    try:
        client_ms = int(client_last_ts or 0)
    except (TypeError, ValueError):
        return False
    if client_ms <= 0:
        return False

    row = db.execute(text("""
        SELECT EXTRACT(EPOCH FROM created_at)::bigint * 1000
        FROM cx_chat_bot_log
        WHERE chat_id = :cid
          AND COALESCE(mp, 'wb') = :mp
          AND sent = true
          AND zone = 'auto'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """), {"cid": chat_id, "mp": (mp or "wb").lower()}).fetchone()
    if not row or row[0] is None:
        return False
    try:
        return client_ms > int(row[0])
    except (TypeError, ValueError):
        return False


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

    # Уже передано менеджеру — не предлагаем снова меню 1–5
    if stage == "handoff":
        return ("dengi", 0.95, True, "уже на менеджере")

    # Любой ответ на наш вопрос о причине сразу передаём менеджеру.
    if stage == "awaiting_return_reason":
        return ("vozvrat", 0.95, True, "получен ответ с причиной возврата")

    # Клиент закрыл тему / отменил обращение (не заказ)
    if _CANCEL_BOT_RE.match(s) or (_RESOLVED_RE.search(s) and len(s) < 180):
        return ("blagodarnost", 0.9, False, "клиент закрыл вопрос")

    # Ответ на меню уточнения
    if stage == "awaiting_menu":
        choice = _parse_menu_choice(s)
        if choice == "human":
            return ("dengi", 0.9, True, "меню: другое → менеджер")
        if choice:
            return (choice, 0.95, False, f"меню: выбор → {choice}")
        # Фото / «здравствуйте» без выбора — не долбим меню снова
        if s.startswith("📷") or s.startswith("![") or len(s) < 2:
            return ("utochnenie", 0.4, False, "меню: ждём выбор (фото/пусто)")
        # не распознали — снова меню только если похоже на текст, не на повтор приветствия
        if re.match(r"^здравствуйте", s, re.I) and len(s) > 40:
            return ("utochnenie", 0.4, False, "меню: ждём выбор (шаблонное приветствие)")
        return ("utochnenie", 0.85, False, "меню: повтор")

    # «Другое» вне меню (клиент отвечает словами после меню, а stage уже сменился)
    if _OTHER_SHORT_RE.match(s):
        return ("dengi", 0.9, True, "другое → менеджер")

    # Заглушки/крепёж — до насадок (иначе ИИ путает с zapchasti→насадки)
    if _KREPEZH_RE.search(s):
        return ("krepezh", 0.93, False, "заглушки/крепёж (правило)")

    # Только явный запрос насадок/тряпок
    if _NASADKI_RE.search(s):
        return ("zapchasti", 0.92, False, "насадки (правило)")

    # 1) Реальная красная зона (суд/угрозы) — всегда человеку
    if _HARD_RED_RE.search(s):
        return ("otkaz_dootpravki", 0.95, True, "красная зона (правило)")

    # Срок возврата истёк / нет кнопки «Вернуть» — до некомплекта/возврата
    if _EXPIRED_RETURN_RE.search(s):
        return ("return_expired", 0.92, False, "срок возврата истёк (правило)")

    # Прислали не тот товар / цвет
    if _WRONG_ITEM_RE.search(s):
        return ("wrong_item", 0.9, False, "не тот товар (правило)")

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
             article: str, nm_id=None, mp: str = "wb") -> Tuple[str, float, bool, str]:
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

    # Ассортимент: сушилки и др. невозвратные — только WB
    if (mp or "wb").lower() == "wb" and theme in ("vozvrat", "nevozvratny") and not need:
        if nonreturnable_cats.is_nonreturnable(db, article=article, nm_id=nm_id):
            theme = "nevozvratny"
            reason = ((reason or "") + "; категория невозвратная").strip("; ")
    elif (mp or "wb").lower() != "wb" and theme == "nevozvratny":
        theme = "vozvrat"
        reason = ((reason or "") + "; nevozvratny только WB → vozvrat").strip("; ")

    # Только после WB-проверки невозвратной категории: nevozvratny не меняем.
    theme, need, reason = _apply_return_flow(theme, msg, stage, need, reason)

    # «Товар не работает» — только швабры/вёдра (Mop-*), иначе меню уточнения
    if theme == "ekspluataciya" and not _is_mop_or_bucket(db, article=article, nm_id=nm_id):
        theme, conf = "utochnenie", max(conf, 0.7)
        reason = ((reason or "") + "; ekspluataciya только швабры/вёдра → меню").strip("; ")

    min_conf = float(chat_playbook.get_setting_mp(db, "bot_confidence_min", mp or "wb", str(MIN_CONFIDENCE))
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
                            limit: int = 5, mp: str = "wb") -> List[str]:
    """Дешёвый поиск похожих ответов менеджеров по площадке."""
    keys = _keywords(client_msg)
    rows: List[str] = []
    mp = (mp or "wb").lower()

    if mp == "ozon":
        found = db.execute(text("""
            SELECT m.text
            FROM ozon_chat_messages m
            WHERE m.sender_type IN ('seller','courier')
              AND m.text IS NOT NULL
              AND length(trim(m.text)) BETWEEN 40 AND 700
            ORDER BY m.created_at DESC NULLS LAST
            LIMIT 40
        """)).fetchall()
        rows.extend(r[0] for r in found if r and r[0])
        if keys and len(rows) < limit * 2:
            for kw in keys[:2]:
                found = db.execute(text("""
                    SELECT m.text
                    FROM ozon_chat_messages m
                    WHERE m.sender_type IN ('seller','courier')
                      AND m.text ILIKE :pat
                      AND length(trim(m.text)) BETWEEN 40 AND 700
                    ORDER BY m.created_at DESC NULLS LAST
                    LIMIT 10
                """), {"pat": f"%{kw}%"}).fetchall()
                rows.extend(r[0] for r in found if r and r[0])
                if len(rows) >= limit * 3:
                    break
    elif mp == "ym":
        found = db.execute(text("""
            SELECT m.text
            FROM ym_chat_messages m
            WHERE lower(coalesce(m.sender_type,'')) IN ('seller','partner','merchant')
              AND m.text IS NOT NULL
              AND length(trim(m.text)) BETWEEN 40 AND 700
            ORDER BY m.created_at DESC NULLS LAST
            LIMIT 40
        """)).fetchall()
        rows.extend(r[0] for r in found if r and r[0])
    else:
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
                      reship: Optional[dict] = None, mp: str = "wb") -> Optional[str]:
    """Черновик ответа, когда в плейбуке нет шаблона. Клиенту не отправляется сам."""
    if chat_playbook.get_setting_mp(db, "bot_ai_draft", mp or "wb", "true").lower() != "true":
        return None

    persona = chat_playbook.get_setting(db, "bot_persona", DEFAULT_PERSONA) or DEFAULT_PERSONA
    model = chat_playbook.get_setting(db, "bot_ai_draft_model", "yandexgpt-lite")
    examples = _similar_seller_replies(db, nm_id=nm_id, client_msg=client_msg, mp=mp)

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
        f"Площадка: {('Ozon' if mp == 'ozon' else 'Яндекс Маркет' if mp == 'ym' else 'Wildberries')}\n"
        f"Тема (подсказка диспетчера): {theme or '—'} — {theme_name or 'без описания'}\n"
        f"Артикул/товар: {article or 'неизвестен'}\n"
        f"Заявка на доотправку: {reship_line}\n"
    )
    if mp in ("ozon", "ym"):
        support = (
            "профиль → «Сообщения» → «Поддержка Ozon»"
            if mp == "ozon"
            else "поддержка Яндекс Маркета в личном кабинете"
        )
        orders = "раздел «Заказы» → товар → «Вернуть»" if mp == "ozon" else "заказ → оформить возврат"
        user += (
            f"Важно: на этой площадке доотправка деталей НЕ делается — "
            f"предлагай только возврат ({orders}), без формы досылки.\n"
            f"По доставке/срокам/деньгам направляй в поддержку площадки: {support}. "
            f"Не пиши про WB, «Покупки» WB или «Задать вопрос» WB.\n\n"
        )
    else:
        user += "\n"
    user += (
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
                  last_seller_ts: Optional[int],
                  *, force_signature: bool = False) -> str:
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
    add_sig = force_signature or (
        body and not _SHORT_NO_SIGN_RE.match(body) and len(body) > 40
    )
    if add_sig:
        sig = chat_playbook.get_setting(db, "signature", "").strip()
        if sig and sig not in text_out:
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


def _resolve_branch_ozon(theme: str, msg: str, stage: str = "new",
                         last_theme: str = "") -> str:
    """Ветки Ozon Playbook 2.0 (по паттернам Альфреда)."""
    s = msg or ""
    if theme == "nekomplekt":
        # После check_tubes клиент подтвердил нехватку → сразу возврат без досылки
        if stage in ("awaiting_client", "in_work") and last_theme == "nekomplekt":
            if _RESOLVED_RE.search(s) or _KEPT_ANYWAY_RE.search(s):
                return ""  # blagodarnost handled elsewhere
            return "no_reship_return"
        # Трубки/перекладины — сначала «разложите по длине»
        if _TUBE_CHECK_RE.search(s):
            return "check_tubes"
        # Явный запрос «доотправьте / пришлите деталь» или меню «1» без трубок
        return "no_reship_return"
    if theme == "brak":
        if _WRONG_ITEM_RE.search(s):
            return "wrong_item"
        return "needs_return"
    if theme == "blagodarnost":
        if _KEPT_ANYWAY_RE.search(s):
            return "kept_anyway"
        if re.search(
            r"(всё\s+пришло|все\s+пришло|получил|подошло|собрал|разобрал|"
            r"нашли|нашла|полный\s+комплект|всё\s+на\s+месте)",
            s, re.I,
        ):
            return "problem_solved"
        return "just_thanks"
    if theme == "sborka" and _TUBE_CHECK_RE.search(s) and _NEKOMPLEKT_RE.search(s):
        # «не хватает трубки при сборке» — ближе к check_tubes; тема уже sborka
        return ""
    return ""


def _resolve_branch(db: Session, theme: str, article: str,
                    reship: Optional[dict], msg: str,
                    mp: str = "wb", stage: str = "new",
                    last_theme: str = "") -> str:
    mp = (mp or "wb").lower()
    if theme == "vozvrat" and stage != "awaiting_return_reason" and not _has_return_reason(msg):
        return "ask_reason"
    if mp in ("ozon", "ym"):
        return _resolve_branch_ozon(theme, msg, stage=stage, last_theme=last_theme)
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
                   last_seller_ts: Optional[int],
                   mp: str = "wb",
                   source_theme: str = "",
                   client_msg: str = "",
                   reason: str = "") -> Tuple[str, str]:
    """Возвращает (текст, action). Пустой текст — шаблона нет (тогда возможен ИИ-черновик)."""
    mp = (mp or "wb").lower()
    source_theme = (source_theme or theme or "").lower()
    action_row = db.execute(text(
        "SELECT action FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).fetchone()
    action = (action_row[0] if action_row else "") or ""

    # Первый шаг возврата: только спросить причину, без инструкции оформления.
    if theme == "vozvrat" and branch == "ask_reason":
        body = chat_playbook.pick_variant(db, theme, branch, mp=mp)
        if not body:
            body = _return_reason_text(mp)
        return _polish_reply(db, body, state, last_seller_ts), "ask_reason"

    # Ozon/ЯМ: некомплект — ветки Альфреда (check_tubes / no_reship_return)
    if mp in ("ozon", "ym") and theme == "nekomplekt":
        body = (
            chat_playbook.pick_variant(db, theme, branch, mp=mp)
            if branch else None
        )
        if not body:
            body = chat_playbook.pick_variant(db, theme, "no_reship_return", mp=mp)
        if body:
            action = "check_tubes" if branch == "check_tubes" else "explain_return"
            return _polish_reply(db, body, state, last_seller_ts), action
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts), "explain_return"

    # Ozon/ЯМ: не тот товар
    if mp in ("ozon", "ym") and theme == "wrong_item":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts), "explain_return"
        return "", action

    # Ozon/ЯМ: срок возврата истёк
    if mp in ("ozon", "ym") and theme == "return_expired":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts), "explain_support"
        return "", action

    # Legacy: remap раньше уводил некомплект в vozvrat — оставляем фолбэк
    if mp in ("ozon", "ym") and theme == "vozvrat":
        from_parts = (
            source_theme in ("nekomplekt", "soglasie", "ssylka", "status_zayavki")
            or "без доотправки" in (reason or "")
            or _looks_like_missing_parts(client_msg, reason)
        )
        if from_parts:
            body = chat_playbook.pick_variant(db, "nekomplekt", "no_reship_return", mp=mp)
            if body:
                return _polish_reply(db, body, state, last_seller_ts), "explain_return"
            return (
                _polish_reply(db, _return_only_text(mp), state, last_seller_ts),
                "explain_return",
            )
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts), "explain_return"
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts), "explain_return"
    if mp in ("ozon", "ym") and theme == "utochnenie":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if not body:
            body = _clarify_menu_text(mp)
        return _polish_reply(db, body, state, last_seller_ts), "ask_menu"
    if mp in ("ozon", "ym") and theme == "brak":
        br = branch or "needs_return"
        if br == "wrong_item":
            body = (
                chat_playbook.pick_variant(db, "wrong_item", "", mp=mp)
                or chat_playbook.pick_variant(db, theme, "needs_return", mp=mp)
            )
        else:
            body = (
                chat_playbook.pick_variant(db, theme, "needs_return", mp=mp)
                or chat_playbook.pick_variant(db, theme, "", mp=mp)
            )
        if body:
            return _polish_reply(db, body, state, last_seller_ts), "explain_return"
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts), "explain_return"

    body = chat_playbook.pick_variant(db, theme, branch, mp=mp)
    if not body and branch:
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
    if not body:
        body = chat_faq.FAQ_FALLBACKS.get(theme, "")
    if not body:
        return "", action

    form_url = ""
    # Форма доотправки — только WB
    if mp == "wb" and (
        action in ("offer_reshipment", "send_form_link", "resend_form_link", "ask_menu")
        or "{form_url}" in body
    ):
        if "{form_url}" in body or action in ("offer_reshipment", "send_form_link", "resend_form_link"):
            form_url = _make_form_url(db, chat_id)

    # На Ozon/ЯМ не подставляем ссылку формы и не предлагаем досылку из шаблона
    if mp in ("ozon", "ym"):
        if "{form_url}" in (body or "") or action in (
            "offer_reshipment", "send_form_link", "resend_form_link"
        ):
            return _polish_reply(db, _return_only_text(mp), state, last_seller_ts), "explain_return"
        body = body.replace("{form_url}", "").strip()

    body = _fill_placeholders(_strip_greeting(body), form_url=form_url, reship=reship if mp == "wb" else None)
    return _polish_reply(db, body, state, last_seller_ts), action


def _next_stage(theme: str, action: str, current: str) -> str:
    if action == "handoff_to_manager":
        return "handoff"
    if theme == "utochnenie" or action == "ask_menu":
        return "awaiting_menu"
    if theme == "vozvrat" and action == "ask_reason":
        return "awaiting_return_reason"
    if theme == "soglasie" or action == "send_form_link":
        return "form_sent"
    if theme == "nekomplekt" and action == "offer_reshipment":
        return "awaiting_client"
    if theme == "nekomplekt" and action == "check_tubes":
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
    kw.setdefault("mp", "wb")
    db.execute(text("""
        INSERT INTO cx_chat_bot_log
          (chat_id, mp, client_message, theme, confidence, zone, branch_key,
           reply_text, action, shadow, sent, need_human, reason)
        VALUES
          (:chat_id, :mp, :client_message, :theme, :confidence, :zone, :branch_key,
           :reply_text, :action, :shadow, :sent, :need_human, :reason)
    """), kw)
    db.commit()


def _send_to_client(db: Session, chat_id: str, text_msg: str, *,
                    mp: str = "wb", reply_sign: str | None = None) -> bool:
    mp = (mp or "wb").lower()
    txt = (text_msg or "")[:1000]
    try:
        if mp == "wb":
            if not reply_sign:
                logger.error("[ChatBot] WB send without reply_sign %s", chat_id)
                return False
            wb_chat.send_message(reply_sign, txt)
        elif mp == "ozon":
            from . import ozon_chat_sync
            ozon_chat_sync.send_message(chat_id, txt)
        elif mp == "ym":
            from . import ym_chat_sync
            ym_chat_sync.send_message(chat_id, txt)
        else:
            return False
    except Exception as e:
        logger.error("[ChatBot] send fail %s/%s: %s", mp, chat_id, e)
        return False

    now_ms = int(time.time() * 1000)
    if mp == "wb":
        db.execute(text("""
            UPDATE wb_chats SET needs_answer = false, last_sender = 'seller',
                last_message = :ans, last_ts = :ts, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": chat_id, "ans": txt, "ts": now_ms})
        exists = db.execute(text("""
            SELECT id FROM wb_chat_messages
            WHERE chat_id = :cid AND sender = 'seller' AND text = :txt
              AND add_ts BETWEEN :t0 AND :t1
            LIMIT 1
        """), {"cid": chat_id, "txt": txt,
               "t0": now_ms - 180_000, "t1": now_ms + 180_000}).first()
        if not exists:
            db.execute(text("""
                INSERT INTO wb_chat_messages (chat_id, sender, text, add_ts)
                VALUES (:cid, 'seller', :txt, :ts)
                ON CONFLICT (chat_id, add_ts, sender) DO NOTHING
            """), {"cid": chat_id, "txt": txt, "ts": now_ms})
    elif mp == "ozon":
        db.execute(text("""
            UPDATE ozon_chats SET needs_answer = false, last_sender = 'seller',
                last_message = :ans, unread_count = 0, waiting_since = NULL, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": chat_id, "ans": txt[:500]})
        db.execute(text("""
            INSERT INTO ozon_chat_messages (message_id, chat_id, sender_type, text, created_at)
            VALUES (:mid, :cid, 'seller', :txt, now())
            ON CONFLICT (message_id) DO NOTHING
        """), {"mid": f"bot-{chat_id}-{now_ms}", "cid": chat_id, "txt": txt[:4000]})
    elif mp == "ym":
        db.execute(text("""
            UPDATE ym_chats SET needs_answer = false, last_sender = 'seller',
                last_message = :ans, waiting_since = NULL,
                status = 'WAITING_FOR_CUSTOMER', updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": chat_id, "ans": txt[:500]})
        db.execute(text("""
            INSERT INTO ym_chat_messages (message_id, chat_id, sender_type, text, created_at)
            VALUES (:mid, :cid, 'seller', :txt, now())
            ON CONFLICT (message_id) DO NOTHING
        """), {"mid": f"bot-{chat_id}-{now_ms}", "cid": chat_id, "txt": txt[:4000]})

    db.execute(text("""
        INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)
        VALUES ('chat', :iid, :mp, 'chat_bot', :ans)
    """), {"iid": chat_id, "mp": mp, "ans": txt})
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

def _has_seller_reply(db: Session, chat_id: str, mp: str = "wb") -> bool:
    mp = (mp or "wb").lower()
    if mp == "ozon":
        row = db.execute(text("""
            SELECT 1 FROM ozon_chat_messages
            WHERE chat_id = :cid AND sender_type IN ('seller', 'courier')
              AND (
                (text IS NOT NULL AND text != '')
                OR COALESCE(is_image, false) = true
                OR NULLIF(TRIM(COALESCE(attachments, '')), '') IS NOT NULL
              )
            LIMIT 1
        """), {"cid": chat_id}).fetchone()
        return bool(row)
    if mp == "ym":
        row = db.execute(text("""
            SELECT 1 FROM ym_chat_messages
            WHERE chat_id = :cid
              AND lower(COALESCE(sender_type, '')) IN ('seller', 'partner', 'merchant')
              AND text IS NOT NULL AND text != ''
            LIMIT 1
        """), {"cid": chat_id}).fetchone()
        return bool(row)
    row = db.execute(text("""
        SELECT 1 FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
          AND text IS NOT NULL AND text != ''
        LIMIT 1
    """), {"cid": chat_id}).fetchone()
    return bool(row)


def _has_human_seller_reply(db: Session, chat_id: str, mp: str = "ozon") -> bool:
    """
    Есть ответ менеджера не от chat_bot (из ЛК Ozon / ручная отправка в CX).
    Бот после такого ответа в чат не вмешивается.
    """
    mp = (mp or "ozon").lower()
    if mp != "ozon":
        # Для WB/YM пока достаточно факта seller-сообщения вне бота:
        return _has_seller_reply(db, chat_id, mp=mp)

    row = db.execute(text("""
        SELECT 1
        FROM ozon_chat_messages m
        WHERE m.chat_id = :cid
          AND m.sender_type IN ('seller', 'courier')
          AND m.message_id NOT LIKE 'bot-%'
          AND m.message_id NOT LIKE 'local-%'
          AND (
            (m.text IS NOT NULL AND m.text != '')
            OR COALESCE(m.is_image, false) = true
          )
          AND NOT EXISTS (
            SELECT 1 FROM cx_chat_bot_log l
            WHERE l.chat_id = m.chat_id
              AND l.mp = 'ozon'
              AND l.sent = true
              AND l.created_at BETWEEN m.created_at - interval '3 minutes'
                                   AND m.created_at + interval '3 minutes'
          )
        LIMIT 1
    """), {"cid": chat_id}).fetchone()
    return bool(row)


def _customer_burst_too_fresh(db: Session, chat_id: str, mp: str, *,
                              debounce_sec: int = BOT_DEBOUNCE_SEC) -> bool:
    """True = последнее сообщение клиента слишком свежее, ждём пачку (фото+текст)."""
    if debounce_sec <= 0:
        return False
    mp = (mp or "wb").lower()
    if mp == "ozon":
        age = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at)))
            FROM ozon_chat_messages
            WHERE chat_id = :cid
              AND sender_type IN ('customer', 'buyer', 'client')
        """), {"cid": chat_id}).scalar()
    elif mp == "ym":
        age = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at)))
            FROM ym_chat_messages
            WHERE chat_id = :cid
              AND lower(COALESCE(sender_type, '')) IN ('customer','buyer','client','user')
        """), {"cid": chat_id}).scalar()
    else:
        age = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(MAX(add_ts)/1000.0)))
            FROM wb_chat_messages
            WHERE chat_id = :cid AND sender = 'client'
        """), {"cid": chat_id}).scalar()
    if age is None:
        return False
    try:
        return float(age) < float(debounce_sec)
    except (TypeError, ValueError):
        return False


def _best_client_text(db: Session, chat_id: str, mp: str, fallback: str) -> str:
    """
    Текст для классификации: последний содержательный клиентский текст
    после ответа продавца (игнорируем голые фото и шаблонный «Здравствуйте! У меня вопрос»).
    """
    mp = (mp or "wb").lower()
    hist = _history(db, chat_id, limit=20, mp=mp)
    # Берём хвост после последнего seller
    tail = []
    for h in reversed(hist):
        if h.get("sender") == "seller":
            break
        tail.append(h)
    tail.reverse()
    ozon_hello = re.compile(
        r"^здравствуйте!\s*у меня вопрос по вашему товару",
        re.IGNORECASE,
    )
    candidates = []
    for h in tail:
        t = (h.get("text") or "").strip()
        if not t or t.startswith("![") or t.startswith("📷"):
            continue
        if ozon_hello.match(t) and len(t) < 220:
            continue
        candidates.append(t)
    if candidates:
        # Цифра меню / короткое решение важнее длинного шума
        for t in reversed(candidates):
            if _MENU_CHOICE_RE.match(t) or _OTHER_SHORT_RE.match(t) or _CANCEL_BOT_RE.match(t):
                return t
            if _RESOLVED_RE.search(t) and len(t) < 160:
                return t
        return candidates[-1]
    fb = (fallback or "").strip()
    if fb.startswith("📷") or fb.startswith("!["):
        return ""
    return fb


def _parse_test_allowlist(raw: str) -> List[str]:
    """chat_id через запятую/пробел/перевод строки. Пусто = без ограничения."""
    if not (raw or "").strip():
        return []
    return [x.strip() for x in re.split(r"[,;\s]+", raw.strip()) if x.strip()]


def _fetch_allowlist_extras(
    db: Session, mp: str, chat_ids: List[str], *,
    only_days: int, only_no_mgr: bool, shadow: bool,
) -> List[Any]:
    """Добирает чаты из тестового allowlist, если их не было в топе очереди."""
    if not chat_ids:
        return []
    # Без жёстких фильтров очереди — только needs_answer + не в архиве,
    # чтобы тест реально попал в выбранные чаты.
    if mp == "ozon":
        q = text("""
            SELECT c.chat_id,
                   COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_number, 'Покупатель')
                     AS client_name,
                   c.context_sku AS nm_id,
                   c.context_article AS good_name,
                   c.last_message, c.last_sender, c.needs_answer,
                   NULL::text AS reply_sign,
                   false AS is_closing,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                      WHERE m.chat_id = c.chat_id
                        AND m.sender_type IN ('customer','buyer','client')),
                     c.waiting_since, c.updated_at, c.created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ozon_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
        """)
    elif mp == "ym":
        q = text("""
            SELECT c.chat_id,
                   COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель') AS client_name,
                   NULL::bigint AS nm_id,
                   COALESCE(
                     NULLIF(TRIM(c.context_article), ''),
                     (SELECT o.supplier_article FROM ym_orders o
                      WHERE c.order_id IS NOT NULL AND o.order_id::text = c.order_id
                        AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL LIMIT 1),
                     (SELECT r.supplier_article FROM ym_returns r
                      WHERE (
                        (c.return_id IS NOT NULL AND r.return_id::text = c.return_id)
                        OR (c.order_id IS NOT NULL AND r.order_id::text = c.order_id)
                      ) AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                      ORDER BY r.created_at DESC NULLS LAST LIMIT 1)
                   ) AS good_name,
                   c.last_message, c.last_sender, c.needs_answer,
                   NULL::text AS reply_sign,
                   false AS is_closing,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ym_chat_messages m
                      WHERE m.chat_id = c.chat_id
                        AND lower(COALESCE(m.sender_type,'')) IN ('customer','buyer','client','user')),
                     c.waiting_since, c.updated_at, c.created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ym_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
        """)
    else:
        q = text("""
            SELECT c.chat_id, c.client_name, c.nm_id, c.good_name, c.last_message,
                   c.last_sender, c.last_ts, c.reply_sign, c.needs_answer, c.is_closing
            FROM wb_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
              AND (CAST(:shadow AS boolean) = true OR c.reply_sign IS NOT NULL)
        """)
        return db.execute(q, {"ids": chat_ids, "shadow": shadow}).mappings().all()
    return db.execute(q, {"ids": chat_ids}).mappings().all()


def process_one(db: Session, chat: dict, *, shadow: bool, mp: str = "wb") -> Optional[dict]:
    mp = (mp or "wb").lower()
    chat_id = chat["chat_id"]
    allowlist = _parse_test_allowlist(
        chat_playbook.get_setting_mp(db, "bot_test_chat_ids", mp, "") or ""
    )
    # Тестовый режим: вне allowlist не трогаем (и клиенту не пишем).
    if allowlist and str(chat_id) not in set(allowlist):
        return None

    # Ручной ответ менеджера (ЛК / не bot) — бот в этот чат больше не пишет
    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", mp, "true").lower() == "true"
    if only_no_mgr and _has_human_seller_reply(db, chat_id, mp=mp):
        return None

    # Debounce: ждём пачку сообщений (фото → цифра/текст)
    if mp in ("ozon", "ym") and _customer_burst_too_fresh(db, chat_id, mp):
        return None

    msg = _best_client_text(db, chat_id, mp, chat.get("last_message") or "")
    if not msg:
        msg = (chat.get("last_message") or "").strip()
    if not msg:
        return None
    # Превью-фото без текста — не шлём меню, ждём текст (debounce уже выше)
    if mp in ("ozon", "ym") and (msg.startswith("📷") or msg.startswith("![")):
        hist = _history(db, chat_id, limit=15, mp=mp)
        for h in reversed(hist):
            t = (h.get("text") or "").strip()
            if t and h.get("sender") == "client" and not t.startswith("![") and not t.startswith("📷"):
                msg = t
                break
        else:
            return None

    # Уже отвечали на этот же last_ts?
    state = _bot_state(db, chat_id)
    last_bot = state.get("last_bot_at")
    last_ts = chat.get("last_ts")
    if last_bot and last_ts:
        try:
            bot_ms = int(last_bot.replace(tzinfo=timezone.utc).timestamp() * 1000) if hasattr(last_bot, "timestamp") else 0
            if bot_ms and bot_ms >= int(last_ts):
                return None
        except Exception:
            pass

    # Старое правило: если seller есть и бот ещё не писал — skip.
    # (human-seller выше жёстче; это страховка для WB)
    if only_no_mgr and not last_bot and _has_seller_reply(db, chat_id, mp=mp):
        return None

    article = (chat.get("good_name") or "").strip() if mp in ("ozon", "ym") else ""
    if not article:
        article = _get_article(db, chat.get("nm_id"))
    reship = _get_reshipment(db, chat_id) if mp == "wb" else None
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))

    stage = state.get("stage") or "new"
    replied_after_auto = _client_replied_after_sent_auto(db, chat_id, last_ts, mp)
    if replied_after_auto:
        # Второе сообщение после реально отправленного ответа всегда берёт человек.
        # В частности, это сохраняет поток возврата: причина -> менеджер.
        theme = state.get("last_theme") or "dengi"
        source_theme = theme
        conf = 1.0
        need_human = True
        reason = _POST_AUTO_REPLY_HANDOFF_REASON
        zone = "human"
        branch = ""
    else:
        history = _history(db, chat_id, mp=mp)
        theme, conf, need_human, reason = classify(
            db, msg, stage, history, article,
            nm_id=chat.get("nm_id") if mp == "wb" else None,
            mp=mp,
        )
        source_theme = theme
        theme, reason = _remap_theme_for_mp(theme, mp, reason, client_msg=msg)

        # Не долбим меню, пока клиент шлёт фото/приветствие без выбора
        if "ждём выбор" in (reason or ""):
            return None

        zone = chat_playbook.get_zone(db, theme)
        if need_human:
            zone = "human"

        branch = _resolve_branch(
            db, theme, article, reship, msg,
            mp=mp, stage=stage, last_theme=state.get("last_theme") or "",
        )
    last_seller_ts = _last_seller_ts(db, chat_id, mp=mp)
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
            db, theme, branch, chat_id, state, reship, last_seller_ts,
            mp=mp, source_theme=source_theme, client_msg=msg, reason=reason,
        )
        if not reply:
            draft = generate_ai_draft(
                db,
                client_msg=msg,
                theme=theme,
                article=article or "",
                history=history,
                nm_id=chat.get("nm_id") if mp == "wb" else None,
                reship=reship,
                mp=mp,
            )
            if draft:
                reply = _polish_reply(db, draft, state, last_seller_ts)
                action = "ai_draft"
                zone = "draft"
                reason = ((reason or "") + "; ИИ-черновик без шаблона").strip("; ")

    sent = False
    if zone == "auto" and reply and not shadow:
        if mp == "wb":
            sign = chat.get("reply_sign")
            if sign:
                sent = _send_to_client(db, chat_id, reply, mp="wb", reply_sign=sign)
            else:
                reason = (reason + "; нет reply_sign").strip("; ")
        else:
            sent = _send_to_client(db, chat_id, reply, mp=mp)
            if not sent and mp == "ozon":
                # Часто: «access period has expired» — писать в чат уже нельзя
                zone = "draft"
                reason = (
                    (reason or "")
                    + "; Ozon не принял отправку (окно ответа истекло?) — на проверку менеджеру"
                ).strip("; ")

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
    if reply and need_greet and sent:
        # Только если реально ушло клиенту — иначе shadow/suggest «съедает» приветствие на весь день
        state_update["last_greeted_date"] = _msk_date()

    _set_state(db, chat_id, **state_update)

    _log(
        db,
        chat_id=chat_id,
        mp=mp,
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

    # Чтобы при открытии чата не всплывал старый/чужой черновик (меню после handoff и т.п.)
    try:
        last_ts = int(chat.get("last_ts") or 0)
        theme_name = ""
        tr = db.execute(text(
            "SELECT name FROM cx_chat_themes WHERE theme_key = :k"
        ), {"k": theme}).fetchone()
        if tr:
            theme_name = tr[0] or ""
        suggest_payload = {
            "ok": True,
            "chat_id": chat_id,
            "mp": mp,
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
            "can_generate": not bool(reply),
            "from_cache": False,
            "from_worker": True,
        }
        db.execute(text("""
            INSERT INTO cx_chat_bot_suggest_cache (chat_id, client_last_ts, payload, updated_at)
            VALUES (:cid, :ts, CAST(:p AS jsonb), now())
            ON CONFLICT (chat_id) DO UPDATE SET
                client_last_ts = EXCLUDED.client_last_ts,
                payload = EXCLUDED.payload,
                updated_at = now()
        """), {
            "cid": chat_id,
            "ts": last_ts,
            "p": json.dumps(suggest_payload, ensure_ascii=False),
        })
        db.commit()
    except Exception:
        logger.exception("[ChatBot] suggest cache update fail %s", chat_id)

    return {
        "chat_id": chat_id,
        "mp": mp,
        "theme": theme,
        "zone": zone,
        "confidence": conf,
        "sent": sent,
        "shadow": shadow,
        "reply": (reply or "")[:200],
        "reason": reason,
    }


def suggest_for_chat(db: Session, chat_id: str, *, force_ai: bool = False,
                     use_cache: bool = True, mp: str = "wb") -> dict:
    """
    Подсказка шаблона при открытии чата менеджером.
    Клиенту ничего не отправляет и last_bot_at не трогает.
    Кэш по chat_id + last_ts клиента — повторный вход без новых токенов Яндекса.
    force_ai=True — принудительно сгенерировать ИИ-ответ (кнопка «Сгенерировать»).
    mp: wb | ozon | ym
    """
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"

    chat = _load_chat_for_suggest(db, chat_id, mp)
    if not chat:
        return {"ok": False, "reason": "чат не найден", "can_generate": False}

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

    state = _bot_state(db, chat_id)
    if _client_replied_after_sent_auto(db, chat_id, last_ts, mp):
        # Не показываем новый шаблон после автоответа: чат уже ждёт человека.
        return {
            "ok": True,
            "chat_id": chat_id,
            "mp": mp,
            "theme": state.get("last_theme") or "",
            "theme_name": "",
            "confidence": 1.0,
            "zone": "human",
            "branch_key": "",
            "action": "handoff_to_manager",
            "reply_text": "",
            "need_human": True,
            "reason": _POST_AUTO_REPLY_HANDOFF_REASON,
            "skip": False,
            "can_generate": False,
            "from_cache": False,
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

    # Артикул: у Ozon/ЯМ уже в good_name; у WB — через nm_id
    article = (chat.get("good_name") or "").strip() if mp in ("ozon", "ym") else ""
    if not article:
        article = _get_article(db, chat.get("nm_id"))
    reship = _get_reshipment(db, chat_id) if mp == "wb" else None
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))
    history = _history(db, chat_id, mp=mp)
    stage = state.get("stage") or "new"

    theme, conf, need_human, reason = classify(
        db, msg, stage, history, article,
        nm_id=chat.get("nm_id") if mp == "wb" else None,
        mp=mp,
    )
    source_theme = theme
    theme, reason = _remap_theme_for_mp(theme, mp, reason, client_msg=msg)

    zone = chat_playbook.get_zone(db, theme)
    if need_human:
        zone = "human"

    branch = _resolve_branch(
        db, theme, article, reship, msg,
        mp=mp, stage=stage, last_theme=state.get("last_theme") or "",
    )
    last_seller_ts = _last_seller_ts(db, chat_id, mp=mp)

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
            db, theme, branch, chat_id, state, reship, last_seller_ts,
            mp=mp, source_theme=source_theme, client_msg=msg, reason=reason,
        )

    # ИИ только по кнопке «Сгенерировать» — не при каждом открытии
    if force_ai:
        draft = generate_ai_draft(
            db, client_msg=msg, theme=theme, article=article or "",
            history=history, nm_id=chat.get("nm_id") if mp == "wb" else None,
            reship=reship, mp=mp,
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
    if theme == "nevozvratny":
        theme_name = "Хочу вернуть товар · WB невозвратный"
    # Для менеджера понятнее: ветки Playbook 2.0
    if mp in ("ozon", "ym"):
        if theme == "nekomplekt" and branch == "check_tubes":
            theme_name = "Некомплект → проверить трубки"
        elif theme == "nekomplekt" and branch == "no_reship_return":
            theme_name = "Некомплект → возврат (без досылки)"
        elif theme == "wrong_item":
            theme_name = theme_name or "Прислали не тот товар"
        elif theme == "return_expired":
            theme_name = theme_name or "Срок возврата истёк"
        elif (
            theme == "vozvrat"
            and (
                source_theme in ("nekomplekt", "soglasie", "ssylka", "status_zayavki")
                or _looks_like_missing_parts(msg, reason)
            )
        ):
            theme_name = "Некомплект → возврат (без досылки)"

    can_generate = (not reply) and bool(msg) and zone != "human"
    # human тоже можно сгенерировать черновик менеджеру, если красный текст пустой
    if not reply and zone == "human":
        can_generate = True

    if not reply and not force_ai:
        reason = reason or "шаблон не подобран"

    result = {
        "ok": True,
        "chat_id": chat_id,
        "mp": mp,
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


def suggest_for_question(
    db: Session,
    question_text: str,
    *,
    mp: str = "wb",
    article: str = "",
    nm_id: int | None = None,
    force_ai: bool = False,
) -> dict:
    """Подбор шаблона ответа на вопрос покупателя — тот же классификатор/плейбук, что в чатах.

    Не создаёт чат и ничего не отправляет. form_url для доотправки в вопросах не подставляем.
    """
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    msg = (question_text or "").strip()
    if not msg:
        return {
            "ok": True, "reply_text": "", "theme": "", "theme_name": "",
            "confidence": 0, "reason": "нет текста вопроса",
            "need_human": False, "can_generate": False, "skip": False,
        }

    theme, conf, need_human, reason = classify(
        db, msg, "new", [], article or "",
        nm_id=nm_id if mp == "wb" else None,
        mp=mp,
    )
    source_theme = theme
    theme, reason = _remap_theme_for_mp(theme, mp, reason, client_msg=msg)
    zone = question_playbook.get_zone(db, theme) if theme else "human"
    if need_human:
        zone = "human"

    branch = _resolve_branch(
        db, theme, article or "", None, msg,
        mp=mp, stage="new", last_theme="",
    )
    fake_state = {"stage": "new"}

    reply = ""
    action = ""
    if zone == "human" and not force_ai:
        reply = (chat_playbook.get_setting(db, "red_zone_client_text", "") or "").strip()
        action = "handoff_to_manager"
    elif not force_ai:
        # Плейбук вопросов (отдельная копия); без form_url
        body = question_playbook.pick_variant(db, theme, branch or "", mp=mp)
        if not body and branch:
            body = question_playbook.pick_variant(db, theme, "", mp=mp)
        if not body:
            body = chat_faq.FAQ_FALLBACKS.get(theme, "")
        if body:
            body = re.sub(r"\{form_url\}", "", body).strip()
            body = re.sub(
                r"(?im)^.*(?:форм[аыуе]|ссылк\w*\s+на\s+доотправ|доотправк\w*).*$\n?",
                "",
                body,
            ).strip()
            reply = _polish_reply(db, body, fake_state, None, force_signature=True) if body else ""
            action = "question_template"

    if force_ai:
        draft = generate_ai_draft(
            db, client_msg=msg, theme=theme, article=article or "",
            history=[], nm_id=nm_id if mp == "wb" else None,
            reship=None, mp=mp,
        )
        if draft:
            reply = _polish_reply(db, draft, fake_state, None, force_signature=True)
            action = "ai_draft"
            reason = ((reason or "") + "; ИИ-черновик по запросу").strip("; ")
        else:
            reason = ((reason or "") + "; ИИ не смог предложить текст").strip("; ")

    theme_name = ""
    tr = db.execute(text(
        "SELECT name FROM cx_question_themes WHERE theme_key = :k"
    ), {"k": theme}).fetchone()
    if not tr:
        tr = db.execute(text(
            "SELECT name FROM cx_chat_themes WHERE theme_key = :k"
        ), {"k": theme}).fetchone()
    if tr:
        theme_name = tr[0] or ""

    if not reply and not force_ai:
        reason = reason or "шаблон не подобран"

    can_generate = (not reply) and bool(msg)

    return {
        "ok": True,
        "mp": mp,
        "theme": theme or "",
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
        "source_theme": source_theme or "",
    }


def process_batch(db: Session, limit: int = BOT_BATCH_LIMIT, *,
                  force: bool = False, mp: str = "wb") -> dict:
    """force=True — ручной прогон по кнопке, игнорирует bot_enabled (автозапуск)."""
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"

    enabled = chat_playbook.get_setting_mp(db, "bot_enabled", mp, "false").lower() == "true"
    if not enabled and not force:
        return {"ok": False, "reason": f"bot_enabled_{mp}=false", "processed": 0, "mp": mp}

    # Без bot_enabled клиенту не пишем (даже с кнопки) — только журнал.
    shadow = True
    if enabled:
        shadow = chat_playbook.get_setting(db, "bot_shadow", "false").lower() == "true"
    try:
        only_days = int(float(chat_playbook.get_setting_mp(db, "bot_only_days", mp, "2") or "2"))
    except ValueError:
        only_days = 2
    if only_days <= 0:
        only_days = 30

    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", mp, "true").lower() == "true"
    allowlist = _parse_test_allowlist(
        chat_playbook.get_setting_mp(db, "bot_test_chat_ids", mp, "") or ""
    )

    if mp == "ozon":
        rows = db.execute(text("""
            SELECT c.chat_id,
                   COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_number, 'Покупатель')
                     AS client_name,
                   c.context_sku AS nm_id,
                   c.context_article AS good_name,
                   c.last_message, c.last_sender, c.needs_answer,
                   NULL::text AS reply_sign,
                   false AS is_closing,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                      WHERE m.chat_id = c.chat_id
                        AND m.sender_type IN ('customer','buyer','client')),
                     c.waiting_since, c.updated_at, c.created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ozon_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.needs_answer = true
              AND lower(COALESCE(c.last_sender, '')) IN ('customer', 'buyer', 'client')
              AND d.item_id IS NULL
              AND COALESCE(
                    (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
                    c.waiting_since, c.updated_at, c.created_at, now()
                  ) > now() - make_interval(days => :days)
              AND (s.last_bot_at IS NULL
                   OR s.last_bot_at < COALESCE(
                        (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                         WHERE m.chat_id = c.chat_id
                           AND m.sender_type IN ('customer','buyer','client')),
                        c.waiting_since, c.updated_at, c.created_at
                      ))
              AND (
                    CAST(:only_no_mgr AS boolean) = false
                    OR NOT EXISTS (
                        -- ручной ответ менеджера (не chat_bot) — бот не берёт чат
                        SELECT 1 FROM ozon_chat_messages m
                        WHERE m.chat_id = c.chat_id
                          AND m.sender_type IN ('seller','courier')
                          AND m.message_id NOT LIKE 'bot-%'
                          AND m.message_id NOT LIKE 'local-%'
                          AND (
                            (m.text IS NOT NULL AND m.text != '')
                            OR COALESCE(m.is_image, false) = true
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM cx_chat_bot_log l
                            WHERE l.chat_id = m.chat_id
                              AND l.mp = 'ozon'
                              AND l.sent = true
                              AND l.created_at BETWEEN m.created_at - interval '3 minutes'
                                                   AND m.created_at + interval '3 minutes'
                          )
                    )
                  )
              -- debounce: не трогаем чат, если клиент писал < 90 сек назад
              AND COALESCE(
                    (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                     WHERE m.chat_id = c.chat_id
                       AND m.sender_type IN ('customer','buyer','client')),
                    c.waiting_since, c.updated_at, c.created_at
                  ) < now() - interval '90 seconds'
            ORDER BY last_ts DESC NULLS LAST
            LIMIT :lim
        """), {
            "lim": max(limit, 500) if allowlist else limit,
            "days": only_days, "only_no_mgr": only_no_mgr,
        }).mappings().all()
    elif mp == "ym":
        rows = db.execute(text("""
            SELECT c.chat_id,
                   COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель') AS client_name,
                   NULL::bigint AS nm_id,
                   COALESCE(
                     NULLIF(TRIM(c.context_article), ''),
                     (SELECT o.supplier_article FROM ym_orders o
                      WHERE c.order_id IS NOT NULL AND o.order_id::text = c.order_id
                        AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL LIMIT 1),
                     (SELECT r.supplier_article FROM ym_returns r
                      WHERE (
                        (c.return_id IS NOT NULL AND r.return_id::text = c.return_id)
                        OR (c.order_id IS NOT NULL AND r.order_id::text = c.order_id)
                      ) AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                      ORDER BY r.created_at DESC NULLS LAST LIMIT 1)
                   ) AS good_name,
                   c.last_message, c.last_sender, c.needs_answer,
                   NULL::text AS reply_sign,
                   false AS is_closing,
                   EXTRACT(EPOCH FROM COALESCE(
                     (SELECT MAX(m.created_at) FROM ym_chat_messages m
                      WHERE m.chat_id = c.chat_id
                        AND lower(COALESCE(m.sender_type,'')) IN ('customer','buyer','client','user')),
                     c.waiting_since, c.updated_at, c.created_at, now()
                   ))::bigint * 1000 AS last_ts
            FROM ym_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.needs_answer = true
              AND lower(COALESCE(c.last_sender, '')) IN ('customer', 'buyer', 'client', 'user')
              AND d.item_id IS NULL
              AND COALESCE(
                    (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                    c.waiting_since, c.updated_at, c.created_at, now()
                  ) > now() - make_interval(days => :days)
              AND (s.last_bot_at IS NULL
                   OR s.last_bot_at < COALESCE(
                        (SELECT MAX(m.created_at) FROM ym_chat_messages m
                         WHERE m.chat_id = c.chat_id
                           AND lower(COALESCE(m.sender_type,'')) IN ('customer','buyer','client','user')),
                        c.waiting_since, c.updated_at, c.created_at
                      ))
              AND (
                    CAST(:only_no_mgr AS boolean) = false
                    OR s.last_bot_at IS NOT NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM ym_chat_messages m
                        WHERE m.chat_id = c.chat_id
                          AND lower(COALESCE(m.sender_type,'')) IN ('seller','partner','merchant')
                          AND m.text IS NOT NULL AND m.text != ''
                    )
                  )
            ORDER BY last_ts DESC NULLS LAST
            LIMIT :lim
        """), {
            "lim": max(limit, 500) if allowlist else limit,
            "days": only_days, "only_no_mgr": only_no_mgr,
        }).mappings().all()
    else:
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
            "lim": max(limit, 500) if allowlist else limit,
            "days": only_days, "shadow": shadow,
            "only_no_mgr": only_no_mgr,
        }).mappings().all()

    if allowlist:
        allow_set = set(allowlist)
        rows = [r for r in rows if str(r.get("chat_id")) in allow_set]
        have = {str(r.get("chat_id")) for r in rows}
        missing = [cid for cid in allowlist if cid not in have]
        if missing:
            extra = _fetch_allowlist_extras(
                db, mp, missing,
                only_days=only_days, only_no_mgr=only_no_mgr, shadow=shadow,
            )
            rows = list(rows) + list(extra)

    results = []
    skipped_empty = 0
    for row in rows:
        try:
            chat = dict(row)
            if mp in ("ozon", "ym"):
                chat["last_sender"] = _norm_sender(chat.get("last_sender"))
            r = process_one(db, chat, shadow=shadow, mp=mp)
            if r:
                results.append(r)
            else:
                skipped_empty += 1
        except Exception as e:
            logger.exception("[ChatBot] process %s/%s: %s", mp, row.get("chat_id"), e)
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
        "mp": mp,
        "shadow": shadow,
        "enabled": enabled,
        "processed": len(results),
        "candidates": len(rows),
        "skipped_empty": skipped_empty,
        "by_zone": by_zone,
        "items": results,
        "test_allowlist": allowlist or None,
    }


def process_batch_all(db: Session, limit: int = BOT_BATCH_LIMIT, *, force: bool = False) -> dict:
    """Прогон wb+ozon+ym (воркер и run-once?mp=all)."""
    out = {}
    total = 0
    for m in ("wb", "ozon", "ym"):
        r = process_batch(db, limit=limit, force=force, mp=m)
        out[m] = r
        total += int(r.get("processed") or 0)
    return {"ok": True, "processed": total, "by_mp": out}


# ── Воркер ───────────────────────────────────────────────────────────────────

async def start_chat_bot_worker(get_db_func):
    logger.info("[ChatBot] worker started, interval=%ss (wb/ozon/ym)", BOT_INTERVAL_SEC)
    await asyncio.sleep(20)
    while True:
        try:
            db = next(get_db_func())
            try:
                stats = await asyncio.to_thread(process_batch_all, db, BOT_BATCH_LIMIT)
                if stats.get("processed"):
                    logger.info("[ChatBot] batch: %s", stats)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[ChatBot] worker error: %s", e)
        await asyncio.sleep(BOT_INTERVAL_SEC)
