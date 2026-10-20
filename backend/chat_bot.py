"""
ИИ-бот автоответов в WB-чатах.

Архитектура (согласована с Рустамом 2026-07-22/23, дополнена 2026-07-24):
  1. Диспетчер: жёсткие правила и правки менеджеров (без Qwen на каждый чат/вопрос)
  2. Плейбук → текст + действие (форма, статус, передача менеджеру)
    3. Нет шаблона → кнопка «Сгенерировать» у менеджера (клиенту само не уходит).
       Тумблер bot_ai_draft — только автобот, не прячет кнопки в чате.
  4. Светофор темы (auto / draft / human)
  5. Сборка: [приветствие?] + суть + [подпись?]
     (приветствие — раз в 12 часов с нашего последнего «здравствуйте»;
      подпись — один раз за чат, если уже представлялись)
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
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from . import cx_alert, telegram_proxy, telegram_webapp
from .routers import chat_playbook, reshipment_skus, nonreturnable_cats, question_playbook
from . import wb_chat
from .database import db_session
from . import chat_faq
from . import chat_fulfillment
from .review_policy import is_paint_hole_question

logger = logging.getLogger("chat_bot")

MSK = ZoneInfo("Europe/Moscow")
BOT_INTERVAL_SEC = 15  # было 90; для диалога 1/2/3 нужна быстрая реакция
BOT_BATCH_LIMIT = 25
MIN_CONFIDENCE = 0.65
AUTO_SEND_MIN_CONF = 0.90
def _quota_paused() -> bool:
    from .qwen_quota import all_qwen_models_exhausted
    return all_qwen_models_exhausted()


def _quota_wait_sec() -> float:
    from .qwen_quota import qwen_wait_sec
    return qwen_wait_sec()


def _mark_quota_pause(err: str) -> None:
    from .qwen_quota import is_quota_or_model_error
    if not is_quota_or_model_error(err):
        return
    # complete_sync уже переключил модели и пометил лимиты.
    # Общую паузу на 30 мин не ставим — как в отзывах, берём следующую свободную.
    logger.warning("[ChatBot] Qwen лимит: %s", (err or "")[:200])
# Ждём пачку сообщений клиента (фото + текст), не отвечаем мгновенно на первое фото
BOT_DEBOUNCE_SEC = 90

FORM_BASE = "https://box.vidovito.com/reshipment/form"
FORM_TOKEN_RE = re.compile(
    r"(?:https?://)?box\.vidovito\.com(?:/reshipment/form)?\?t=([\w\-]+)",
    re.IGNORECASE,
)

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
    r"нет\s+шуруп|нет\s+винт|нет\s+заглуш|нет\s+гаек|"
    r"нет\s+в\s+наличии|не\s+достающ|недостающ|"
    r"можно\s+просто\s+прислать|есть\s+возможность\s+до\s*отправ|"
    r"только\s+два\s+колес|прислали\s+\d|"
    r"трубк\w*\s+(а|в|с)\b.{0,40}(нет|не\s+хвата|нехвата|мало|вместо))",
    re.IGNORECASE,
)

_AGREE_RE = re.compile(
    r"^(?:(?:здравствуйте|добрый\s+(?:день|вечер|утро))[!.]?\s+)?"
    r"(?:да|даа+|ок|окей|хорошо|отправляйте|отправляй|отправьте|"
    r"буду\s+благодар|будем\s+благодар|актуально|согласен|согласна|нужно|"
    r"давайте|давай|конечно|ждём|ждем|"
    r"заполнила|заполнил|заполнили|"
    r"да[,\s]+актуальн\w*|да[,\s]+давайте)[\s!.]*$",
    re.IGNORECASE,
)

# Согласие внутри фразы («Здравствуйте. Конечно актуально, будем благодарны»)
_AGREE_HINT_RE = re.compile(
    r"(актуальн\w*|согласен|согласна|конечно(\s+актуальн)?|"
    r"давайте\s+отправ|отправляйте|отправьте\s+детал|"
    r"буду\s+благодар|будем\s+очень\s+благодар|будем\s+благодар)",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"(не\s+актуальн|не\s+надо|не\s+нужно|отказ|не\s+хочу|"
    r"уже\s+перезаказ|хочу\s+вернуть|верну\b|возврат)",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^(аа+)?\s*(спасибо|благодарю|спс|ок|окей)[\s!.❤🙏👍)]*$",
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
                       reason: str, mp: str = "wb") -> Tuple[str, bool, str]:
    """Применяет двухшаговый поток только к обычному ``vozvrat``.

    ``nevozvratny`` сюда не попадает: WB-правило категории остаётся отдельным.
    ЯМ: сразу ветка возврата, без вопроса «почему», кроме срока >14 дней.
    """
    if theme != "vozvrat":
        return theme, need_human, reason
    if need_human:
        return theme, True, reason
    mp = (mp or "wb").lower()
    late = bool(re.search(
        r"(14\s*дн|более\s+четырнадцати|больше\s+(двух\s+)?недель|месяц\s+назад)",
        msg or "", re.I,
    ))
    if mp == "ym" and not late and stage != "awaiting_return_reason" and not _has_return_reason(msg):
        note = "ЯМ: возврат без уточнения причины"
        return theme, False, f"{reason}; {note}".strip("; ")
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
    r"(отмен\w*\s+(заказ|доставк)|отмена\s+заказ|"
    r"отмените\s+(пожалуйста\s+)?(заказ|доставк)|"
    r"хочу\s+отменить\s+заказ|"
    r"отметите\s+заказ|отметить\s+заказ|"
    r"нет\s+в\s+надобност|не\s+надо\s+(его\s+)?привозить|"
    r"если\s+(ещ[её]\s+)?не\s+отправил)",
    re.IGNORECASE,
)

# Шаблон WB «посмотрите моё сообщение — жду ответа» — не тема и не обучение.
_MARKETPLACE_NUDGE_RE = re.compile(
    r"^(?:здравствуйте|добрый\s+(?:день|вечер|утро))[!.,]?\s+"
    r"пожалуйста,?\s+посмотрите\s+мо[её]\s+сообщен\w*"
    r"(?:\s*[—\-–]\s*жду\s+ответа)?[\s!.]*$",
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
_WHEEL_MISS_RE = re.compile(
    r"(не\s+хвата\w*.{0,24}кол[её]с|нет\s+кол[её]с|без\s+кол[её]с|кол[её]с\w*\s+не\s+(пришл|хвата|долож))",
    re.IGNORECASE | re.DOTALL,
)
_BOLT_MISS_RE = re.compile(
    r"(не\s+хвата\w*.{0,24}(болт|винт|крепёж|крепеж|гайк)|"
    r"нет\s+(болт|винт|крепёж|крепеж)|без\s+(болт|винт|крепеж|крепёж))",
    re.IGNORECASE | re.DOTALL,
)
_LEG_MISS_RE = re.compile(
    r"(не\s+хвата\w*.{0,24}нож|нет\s+нож|без\s+нож|ножк\w*\s+не\s+(пришл|хвата|долож))",
    re.IGNORECASE | re.DOTALL,
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

# Уже представлялись в чате («Александр, команда Видовито» и варианты)
_SIGN_MARK_RE = re.compile(
    r"(команда\s+видовито|из\s+команды\s+видовито)",
    re.IGNORECASE,
)

# Ozon: «Здравствуйте! У меня вопрос по вашему товару "…", артикул X. …»
# ЯМ DIRECT: «Здравствуйте, у меня вопрос по товару Стеллаж …, 173×43×30 см.»
# Название карточки общее — без срезки «полок/хранения/вешалка» путается с некомплектом.
_OZON_PRODUCT_QUESTION_RE = re.compile(
    r"^\s*(?:(?:здравствуйте|добрый\s+(?:день|вечер|утро)|привет)[!.,]?\s+)?"
    r"у\s+меня\s+вопрос\s+по\s+вашему\s+товару\s+"
    r"(?:«[^»]{1,400}»|\"[^\"]{1,400}\"|“[^”]{1,400}”)\s*"
    r"(?:,?\s*артикул\s*:?\s*[A-Za-z0-9._\-]+)?\s*[.!]?\s*",
    re.IGNORECASE,
)
_YM_PRODUCT_QUESTION_RE = re.compile(
    r"^\s*(?:(?:здравствуйте|добрый\s+(?:день|вечер|утро)|привет)[!.,]?\s+)?"
    r"у\s+меня\s+вопрос\s+по\s+товару\s+"
    r"(?!«|\"|“)"
    r".+?"
    r"(?:[.!](?:\s|$)|$)",
    re.IGNORECASE,
)
_PRODUCT_QUESTION_HEAD_RE = re.compile(
    r"у\s+меня\s+вопрос\s+по\s+(?:вашему\s+)?товару\b",
    re.IGNORECASE,
)
_PRODUCT_QUESTION_LLM_NOTE = (
    "Шаблон площадки «у меня вопрос по товару {название карточки}» "
    "без своего вопроса — это не некомплект и не запрос детали. "
    "Тема utochnenie: спроси, что именно нужно."
)

_SKIDKA_RE = re.compile(
    r"(как\s+(мне\s+)?(получить|взять)\s+скидк|получить\s+скидк|"
    r"есть\s+(ли\s+)?скидк|какая\s+скидк|сделайте\s+скидк|"
    r"можете\s+скидк|скидк\w*\s+(пожалуйста|на\s+(этот\s+)?товар)|"
    r"промокод|купон\s+на\s+скидк)",
    re.IGNORECASE,
)


def _unwrap_client_question(msg: str) -> str:
    """Оставляет вопрос клиента, без шапки Ozon/ЯМ с названием карточки."""
    raw = (msg or "").strip()
    if not raw:
        return ""
    stripped = _OZON_PRODUCT_QUESTION_RE.sub("", raw, count=1).strip()
    if stripped == raw:
        stripped = _YM_PRODUCT_QUESTION_RE.sub("", raw, count=1).strip()
    return stripped


def _is_product_question_wrapper_only(msg: str) -> bool:
    """Шаблон площадки «вопрос по товару {карточка}» без своей сути."""
    raw = (msg or "").strip()
    if not raw or not _PRODUCT_QUESTION_HEAD_RE.search(raw):
        return False
    return not _unwrap_client_question(raw)


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
    ("nasadki_in_stock", "true",
     "Сменные насадки в наличии (true/false). Выкл — бот отвечает, что сейчас нет."),
    ("bot_enabled", "false",
     "Полная автоотправка бота (true/false). false = только подсказки при открытии чата / кнопка «Прогнать»."),
    ("bot_shadow", "false",
     "Устарело: теневой журнал. Оставьте false. Подсказки при открытии чата не требуют тени."),
    ("greeting_text", "Здравствуйте!",
     "Приветствие. Ставится, если с нашего последнего приветствия прошло 12 часов."),
    ("bot_confidence_min", "0.65",
     "Минимальная уверенность для подстановки шаблона в окно ответа. Ниже — поле пустое, менеджер выбирает сам (без меню 1–5 и без ИИ-заполнения)."),
    ("bot_auto_confidence_min", "0.90",
     "Автоответ клиенту только при уверенности не ниже этого порога. Иначе — черновик на согласование. "
     "Похожий выбор менеджера в прошлом чате автоответ не даёт — только черновик."),
    ("bot_only_days", "2",
     "Брать только чаты за последние N дней (0 = без ограничения). Экономит токены на старом бэклоге."),
    ("bot_only_no_manager", "true",
     "Только чаты без ответа менеджера. Если в чате уже писал продавец, а бот его ещё не брал — пропуск. "
     "Чаты, которые бот уже вёл, продолжает."),
    ("bot_model", "qwen-plus",
     "Модель для классификации темы: qwen-plus (основная) или yandexgpt-lite (запасной)."),
    ("bot_ai_draft", "false",
     "Кнопка «Сгенерировать»: ИИ пишет черновик, если нет шаблона. Выкл — Qwen не вызываем."),
    ("bot_ai_draft_model", "qwen-plus",
     "Модель для черновика без шаблона: qwen-plus (основная) или yandexgpt-lite (запасной)."),
    ("bot_persona", DEFAULT_PERSONA,
     "Портрет/правила тона для ИИ-черновика. Можно править под ваш стиль."),
    ("bot_suggest_on_open", "true",
     "При открытии чата подставлять шаблонный ответ в поле ввода (true/false). Менеджер сам жмёт «Отправить»."),
    ("bot_test_chat_ids", "",
     "Тестовый allowlist: chat_id через запятую/пробел. Если не пусто — бот обрабатывает ТОЛЬКО эти чаты. Пусто = все."),
]

# Ключи, которые хранятся отдельно на каждую площадку (см. get_setting_mp).
BOT_MP_CONTROL_KEYS = {
    "bot_enabled", "bot_confidence_min", "bot_auto_confidence_min", "bot_only_days",
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
            conn.rollback()
        # Fast handoff check after a sent auto-reply.  Do not put COALESCE(mp,
        # 'wb') in the predicate: old WB rows with mp=NULL must stay covered.
        try:
            conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chat_bot_log_sent_auto_lookup
            ON cx_chat_bot_log (chat_id, mp, created_at DESC, id DESC)
            WHERE sent = true AND zone = 'auto'
        """))
        except Exception:
            conn.rollback()
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
            conn.rollback()
        try:
            conn.execute(text(
                "ALTER TABLE cx_chat_bot_state "
                "ADD COLUMN IF NOT EXISTS bot_disabled BOOLEAN DEFAULT false"
            ))
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "ALTER TABLE cx_chat_bot_state "
                "ADD COLUMN IF NOT EXISTS last_greeted_at TIMESTAMP"
            ))
        except Exception:
            conn.rollback()
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_corrections (
                id                SERIAL PRIMARY KEY,
                chat_id           TEXT NOT NULL,
                mp                TEXT NOT NULL DEFAULT 'wb',
                client_message    TEXT NOT NULL DEFAULT '',
                suggested_theme   TEXT NOT NULL DEFAULT '',
                suggested_branch  TEXT NOT NULL DEFAULT '',
                chosen_theme      TEXT NOT NULL,
                chosen_branch     TEXT NOT NULL DEFAULT '',
                chosen_variant_id INTEGER,
                created_by        TEXT NOT NULL DEFAULT '',
                created_at        TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chat_bot_corrections_mp
            ON cx_chat_bot_corrections (mp, created_at DESC)
        """))
        for k, v, d in EXTRA_SETTINGS:
            conn.execute(text("""
                INSERT INTO cx_chat_settings (key, value, descr, updated_by)
                VALUES (:k, :v, :d, 'system')
                ON CONFLICT (key) DO UPDATE SET descr = EXCLUDED.descr
            """), {"k": k, "v": v, "d": d})
        conn.execute(text("""
            UPDATE cx_chat_settings
               SET value = 'qwen-plus', updated_at = now()
             WHERE key IN ('bot_model', 'bot_ai_draft_model')
               AND value IN (
                    'yandexgpt-lite', 'yandexgpt', 'yandex-lite', 'yandex-pro',
                    'claude-haiku', 'claude-sonnet'
               )
        """))
        conn.execute(text("""
            DELETE FROM cx_chat_bot_corrections
            WHERE client_message ~* 'посмотрите[[:space:]]+мо[её][[:space:]]+сообщен'
        """))
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
    """На Ozon и ЯМ доотправка есть. Невозвратность сушилок — только WB."""
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
    # На Ozon доотправка есть: согласие, ссылка и статус заявки остаются своими темами.
    if mp != "ym" and theme == "kupit_detal":
        note = "→nekomplekt (покупка детали только ЯМ)"
        reason = f"{reason}; {note}".strip("; ") if reason else note
        return "nekomplekt", reason
    return theme, reason


_MOP_ARTICLE_RE = re.compile(r"^mop[\-_]", re.I)
# Высокие вёдра 2S — другое изделие, видео «товар не работает» туда не подходит.
_TWO_S_BUCKET_RE = re.compile(
    r"(?:^|[\s\-_/])2s(?:$|[\s\-_/])|2-s\b|2сб|высок\w*\s+ведр|ведр\w*\s+высок",
    re.I,
)


def _is_two_s_or_high_bucket(article: str = "", name: str = "") -> bool:
    blob = f"{article or ''} {name or ''}"
    return bool(_TWO_S_BUCKET_RE.search(blob))


def _is_mop_or_bucket(db: Session, *, article: str = "", nm_id=None) -> bool:
    """Тема ekspluataciya — швабры/вёдра 1S. Высокие 2S сюда не идут."""
    art = (article or "").strip()
    if _is_two_s_or_high_bucket(art):
        return False
    if art and _MOP_ARTICLE_RE.match(art):
        return True
    cat = ""
    try:
        cat = nonreturnable_cats.get_category(db, article=art or None, nm_id=nm_id) or ""
    except Exception:
        cat = ""
    cl = cat.lower()
    if _is_two_s_or_high_bucket(art, cat):
        return False
    if any(x in cl for x in ("швабр", "ведр", "ведер")):
        return True
    if not art and nm_id is not None:
        resolved = _get_article(db, nm_id)
        if resolved and _is_two_s_or_high_bucket(resolved):
            return False
        if resolved and _MOP_ARTICLE_RE.match(resolved.strip()):
            return True
    return False


_PARTS_REQ_RE = re.compile(
    r"не\s*хвата|некомплект|доотправ|дослат|прислать|выслать|детал|"
    r"боков(ые|ина)|палки|трубк|крепеж|колес|комплект",
    re.I,
)


def _looks_like_missing_parts(msg: str = "", reason: str = "") -> bool:
    if _is_product_question_wrapper_only(msg):
        return False
    s = _unwrap_client_question(msg or "")
    return bool(_PARTS_REQ_RE.search(f"{s} {reason or ''}"))


def _clarify_menu_text(mp: str = "wb") -> str:
    """Меню 1–5: на Ozon пункт 1 без досылки; на ЯМ — с доотправкой, как на WB."""
    if (mp or "wb").lower() == "ozon":
        return (
            "Подскажите, пожалуйста, что вам нужно — ответьте цифрой или коротко своими словами:\n"
            "1 — не хватает детали / неполная комплектация\n"
            "2 — брак или повреждение товара\n"
            "3 — вопрос по сборке, размерам или нагрузке\n"
            "4 — хочу оформить возврат на Ozon\n"
            "5 — другое (подключу коллегу)"
        )
    if (mp or "wb").lower() == "ym":
        return (
            "Подскажите, пожалуйста, что вам нужно — ответьте цифрой или коротко своими словами:\n"
            "1 — не хватает детали / нужна доотправка\n"
            "2 — брак или повреждение товара\n"
            "3 — вопрос по сборке, размерам или нагрузке\n"
            "4 — хочу оформить возврат на Яндекс Маркете\n"
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


def _article_for_chat(db: Session, chat: Optional[dict], mp: str) -> str:
    """WB: артикул по nm_id. Название карточки («Сушилка…») в список SKU не подходит."""
    chat = chat or {}
    mp = (mp or "wb").lower()
    if mp in ("ozon", "ym"):
        return (chat.get("good_name") or "").strip()
    article = _get_article(db, chat.get("nm_id"))
    if article:
        return article
    reship = _get_reshipment(db, chat.get("chat_id") or "") if mp == "wb" else None
    if reship and reship.get("wb_nm_id"):
        return _get_article(db, reship.get("wb_nm_id"))
    return ""


def _plain_client_text(msg: str) -> str:
    s = _unwrap_client_question(msg or "")
    return re.sub(r"[\u00a0\u202f]+", " ", s).strip()


def _is_marketplace_nudge(msg: str) -> bool:
    s = _plain_client_text(msg)
    if not s:
        return False
    return bool(_MARKETPLACE_NUDGE_RE.match(s))


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
            WHERE wb_chat_id = :cid OR ym_chat_id = :cid OR ozon_chat_id = :cid
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


def set_bot_disabled(conn, chat_id: str, disabled: bool = True) -> None:
    """После архива чата автоответы больше не пишут, даже если клиент снова написал."""
    cid = (chat_id or "").strip()
    if not cid:
        return
    conn.execute(text("""
        INSERT INTO cx_chat_bot_state (chat_id, stage, bot_disabled, updated_at)
        VALUES (:cid, 'new', :off, now())
        ON CONFLICT (chat_id) DO UPDATE SET
            bot_disabled = EXCLUDED.bot_disabled,
            updated_at = now()
    """), {"cid": cid, "off": bool(disabled)})


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
            "last_greeted_date": None, "last_greeted_at": None,
            "last_bot_at": None, "closing_asked_at": None,
            "bot_disabled": False}


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


def _ym_parts_dialog_open(mp: str, stage: str) -> bool:
    """ЯМ: после предложения купить / бесплатной доотправки ждём да/нет, не менеджера."""
    return (mp or "").lower() == "ym" and (stage or "") in ("awaiting_buy", "awaiting_client")


def _wants_return(msg: str) -> bool:
    s = msg or ""
    return bool(_VOZVRAT_RE.search(s) or re.search(r"хочу\s+вернуть", s, re.I))


def _rule_classify(msg: str, stage: str, mp: str = "wb") -> Optional[Tuple[str, float, bool, str]]:
    """Жёсткие правила до ИИ. Возвращает (theme, conf, need_human, reason) или None."""
    s = (msg or "").strip()
    mp = (mp or "wb").lower()
    if not s:
        return ("blagodarnost", 0.4, True, "пустое сообщение")
    if _is_product_question_wrapper_only(s):
        return ("utochnenie", 0.9, False, "шапка площадки «вопрос по товару», без сути")

    # Отмена заказа — раньше handoff и согласия (опечатки «отметите заказ»).
    if _CANCEL_RE.search(s):
        return ("otmena_zakaza", 0.9, False, "отмена заказа (правило)")

    # Уже передано менеджеру — не предлагаем снова меню 1–5
    if stage == "handoff":
        return ("dengi", 0.95, True, "уже на менеджере")

    # Любой ответ на наш вопрос о причине сразу передаём менеджеру.
    if stage == "awaiting_return_reason":
        return ("vozvrat", 0.95, True, "получен ответ с причиной возврата")

    # Клиент закрыл тему / отменил обращение (не заказ)
    # Но «будем благодарны» + «актуально» — это согласие на доотправку, не закрытие.
    if _CANCEL_BOT_RE.match(s) or (
        _RESOLVED_RE.search(s) and len(s) < 180 and not _AGREE_HINT_RE.search(s)
    ):
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

    # ЯМ: ветка «купить со скидкой → бесплатно дольше → возврат»
    agreed = (
        (_AGREE_RE.match(s) or _AGREE_HINT_RE.search(s))
        and not _DECLINE_RE.search(s)
    )
    if mp == "ym" and stage == "awaiting_buy":
        if _wants_return(s):
            return ("nekomplekt", 0.93, False, "ЯМ: сразу возврат")
        if agreed:
            return ("kupit_detal", 0.93, False, "ЯМ: согласие купить деталь")
        if _DECLINE_RE.search(s):
            return ("nekomplekt", 0.92, False, "ЯМ: отказ от покупки → бесплатная доотправка")
    if mp == "ym" and stage == "awaiting_client":
        if agreed:
            return ("soglasie", 0.93, False, "ЯМ: согласие на бесплатную доотправку")
        if _DECLINE_RE.search(s) or _wants_return(s):
            return ("nekomplekt", 0.93, False, "ЯМ: отказ от доотправки → возврат")
    if mp == "ym" and stage == "form_sent" and (_DECLINE_RE.search(s) or _wants_return(s)):
        return ("nekomplekt", 0.9, False, "ЯМ: отказ после формы → возврат")

    # «Другое» вне меню (клиент отвечает словами после меню, а stage уже сменился)
    if _OTHER_SHORT_RE.match(s):
        return ("dengi", 0.9, True, "другое → менеджер")

    # Заглушки/крепёж — до насадок (иначе ИИ путает с zapchasti→насадки).
    # Но «не положили заглушки/шурупы» — это некомплект, не «купите в магазине».
    if _KREPEZH_RE.search(s) and not _NEKOMPLEKT_RE.search(s):
        return ("krepezh", 0.93, False, "заглушки/крепёж (правило)")

    # Только явный запрос насадок/тряпок
    if _NASADKI_RE.search(s):
        return ("zapchasti", 0.92, False, "насадки (правило)")

    # Дырки на полке/подносе — линия покраски, не некомплект и не брак
    if is_paint_hole_question(s):
        return ("tech_otverstiya", 0.93, False, "технологические отверстия (правило)")

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

    if _SKIDKA_RE.search(s):
        return ("dengi", 0.9, True, "скидка/акция → менеджер")

    # Короткие согласия/отказы зависят от стадии
    if stage in ("awaiting_client", "form_sent", "new") and len(s) < 120:
        # «актуально / конечно / будем благодарны» — согласие на доотправку,
        # даже если фраза начинается с «Здравствуйте» (иначе уходит в благодарность).
        if (
            (_AGREE_RE.match(s) or _AGREE_HINT_RE.search(s))
            and not _DECLINE_RE.search(s)
        ):
            if stage == "new" and re.match(r"^да[\s!.]*$", s, re.I):
                pass
            else:
                return ("soglasie", 0.92, False, "согласие на доотправку")
        if (
            mp != "ym"
            and stage in ("awaiting_client", "form_sent")
            and _DECLINE_RE.search(s)
        ):
            return ("otkaz_dootpravki", 0.9, True, "отказ от доотправки")

    if _THANKS_RE.match(s):
        return ("blagodarnost", 0.85, False, "благодарность")

    if _SSYLKA_RE.search(s):
        return ("ssylka", 0.88, False, "ссылка")

    if re.search(r"(что\s+с\s+мо(им|ей)\s+заказ|где\s+(мой\s+)?заказ|где\s+посылк)", s, re.I) \
            and not re.search(r"(заявк|детал)", s, re.I):
        return ("dostavka_wb", 0.86, False, "статус заказа, не заявки на деталь")

    if _STATUS_RE.search(s):
        return ("status_zayavki", 0.82, False, "статус заявки (правило)")

    if _CHEQUE_RE.search(s):
        return ("cheque", 0.9, False, "чек (правило)")

    if _STORAGE_RE.search(s):
        return ("srok_hraneniya", 0.9, False, "хранение ПВЗ (правило)")

    if _TECH_RE.search(s):
        return ("teh_sboi", 0.85, False, "техсбой МП (правило)")

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


def _msg_tokens(msg: str) -> set:
    return set(re.findall(r"[а-яёa-z0-9]{4,}", (msg or "").lower()))


def _correction_overlap(msg: str, sample: str) -> int:
    a, b = _msg_tokens(msg), _msg_tokens(sample)
    if not a or not b:
        return 0
    return len(a & b)


def _correction_jaccard(msg: str, sample: str) -> float:
    a, b = _msg_tokens(msg), _msg_tokens(sample)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter < 2:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


_MANAGER_CORRECTION_REASON = "исправление менеджера"


def _is_manager_correction_reason(reason: str) -> bool:
    return _MANAGER_CORRECTION_REASON in (reason or "")


def _best_correction(db: Session, msg: str, mp: str, chat_id: str = "") -> Optional[dict]:
    """Похожее исправление менеджера → тема для черновика (не для автоотправки)."""
    text_msg = _unwrap_client_question(msg)
    if (
        len(text_msg) < 8
        or _is_marketplace_nudge(text_msg)
        or _is_product_question_wrapper_only(msg)
    ):
        return None
    skip_chat = (chat_id or "").strip()
    try:
        rows = db.execute(text("""
            SELECT chosen_theme, chosen_branch, client_message, chat_id
            FROM cx_chat_bot_corrections
            WHERE coalesce(mp, 'wb') = :mp
              AND chosen_theme <> ''
            ORDER BY created_at DESC
            LIMIT 400
        """), {"mp": (mp or "wb").lower()}).mappings().all()
    except Exception:
        return None
    best = None
    best_score = 0.0
    for row in rows:
        if skip_chat and (row.get("chat_id") or "").strip() == skip_chat:
            continue
        sample = _unwrap_client_question(row.get("client_message") or "")
        if (
            len(sample) < 8
            or _is_marketplace_nudge(sample)
            or _is_product_question_wrapper_only(row.get("client_message") or "")
        ):
            continue
        score = _correction_jaccard(text_msg, sample)
        if score > best_score:
            best_score = score
            best = dict(row)
    if best and best_score >= 0.38:
        return best
    return None


def _correction_hints_for_prompt(db: Session, mp: str) -> str:
    try:
        rows = db.execute(text("""
            SELECT client_message, chosen_theme
            FROM cx_chat_bot_corrections
            WHERE coalesce(mp, 'wb') = :mp
              AND chosen_theme <> ''
            ORDER BY created_at DESC
            LIMIT 8
        """), {"mp": (mp or "wb").lower()}).mappings().all()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for row in rows:
        sample = _unwrap_client_question(row.get("client_message") or "")
        sample = re.sub(r"\s+", " ", sample).strip()[:120]
        theme = row.get("chosen_theme") or ""
        if sample and theme:
            lines.append(f"- «{sample}» → {theme}")
    if not lines:
        return ""
    return (
        "Исправления менеджеров (если похоже — выбирай ту же тему):\n"
        + "\n".join(lines) + "\n\n"
    )


def record_correction(
    db: Session, *,
    chat_id: str,
    mp: str,
    client_message: str,
    suggested_theme: str = "",
    suggested_branch: str = "",
    chosen_theme: str,
    chosen_branch: str = "",
    chosen_variant_id: Optional[int] = None,
    created_by: str = "",
) -> dict:
    chosen = (chosen_theme or "").strip()
    if not chosen:
        return {"ok": False, "reason": "нет выбранной темы"}
    suggested = (suggested_theme or "").strip()
    sug_branch = (suggested_branch or "").strip()
    ch_branch = (chosen_branch or "").strip()
    msg = _unwrap_client_question(client_message)[:2000]
    if (
        not msg
        or _is_marketplace_nudge(msg)
        or _is_product_question_wrapper_only(client_message)
    ):
        return {"ok": True, "saved": False, "learned": False, "reason": "шаблон площадки, не учим"}
    mp_key = (mp or "wb").lower()
    differs = suggested != chosen or sug_branch != ch_branch
    try:
        dup = db.execute(text("""
            SELECT 1 FROM cx_chat_bot_corrections
            WHERE coalesce(mp, 'wb') = :mp
              AND chosen_theme = :chosen
              AND coalesce(chosen_branch, '') = :branch
              AND client_message = :msg
              AND created_at > now() - interval '7 days'
            LIMIT 1
        """), {
            "mp": mp_key, "chosen": chosen, "branch": ch_branch, "msg": msg,
        }).fetchone()
    except Exception:
        dup = None
    if dup:
        return {"ok": True, "saved": False, "learned": False, "reason": "уже есть такой пример"}
    db.execute(text("""
        INSERT INTO cx_chat_bot_corrections (
            chat_id, mp, client_message, suggested_theme, suggested_branch,
            chosen_theme, chosen_branch, chosen_variant_id, created_by
        ) VALUES (
            :chat_id, :mp, :client_message, :suggested_theme, :suggested_branch,
            :chosen_theme, :chosen_branch, :chosen_variant_id, :created_by
        )
    """), {
        "chat_id": (chat_id or "").strip(),
        "mp": mp_key,
        "client_message": msg,
        "suggested_theme": suggested,
        "suggested_branch": sug_branch,
        "chosen_theme": chosen,
        "chosen_branch": ch_branch,
        "chosen_variant_id": chosen_variant_id,
        "created_by": (created_by or "").strip()[:120],
    })
    db.commit()
    return {"ok": True, "saved": differs, "learned": True}


def _auto_min_confidence(db: Session, mp: str) -> float:
    raw = chat_playbook.get_setting_mp(
        db, "bot_auto_confidence_min", mp or "wb", str(AUTO_SEND_MIN_CONF),
    )
    try:
        val = float(raw or AUTO_SEND_MIN_CONF)
    except (TypeError, ValueError):
        val = AUTO_SEND_MIN_CONF
    return min(0.99, max(0.5, val))


def gate_auto_by_confidence(
    zone: str, theme: str, conf: float, reason: str, db: Session, mp: str,
) -> Tuple[str, str, bool]:
    """Тема «Авто» уходит клиенту только при высокой уверенности. Иначе — согласование.

    Выбор шаблона менеджером — подтверждение для человека, не автоотправка.
    """
    if (zone or "") != "auto" or (theme or "") == "utochnenie":
        return zone, reason, False
    if _is_manager_correction_reason(reason):
        note = "выбор менеджера — черновик, без автоотправки"
        return "draft", f"{reason}; {note}".strip("; "), True
    min_auto = _auto_min_confidence(db, mp)
    try:
        score = float(conf or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= min_auto:
        return zone, reason, False
    note = f"уверенность {score:.0%} < {min_auto:.0%} → согласование"
    return "draft", f"{reason}; {note}".strip("; "), True


def _llm_classify(msg: str, history: List[dict], themes: List[dict],
                  article: str, stage: str, *, model: str = "qwen-plus",
                  correction_hints: str = "") -> Optional[dict]:
    from .llm import complete_sync, is_yandex_lite

    if _quota_paused():
        return None

    theme_lines = "\n".join(
        f"- {t['theme_key']}: {t['name']} — {t['when_text']}" for t in themes
    )
    hist_lines = "\n".join(
        f"{'Клиент' if h['sender']=='client' else 'Мы'}: "
        f"{(_unwrap_client_question(h.get('text') or '') or h.get('text') or '')[:200]}"
        for h in history[-6:]
    ) or "(пусто)"

    system = (
        "Ты диспетчер чата продавца маркетплейса. "
        "Выбери ОДНУ тему из списка. Не пиши ответ клиенту. "
        "Если это деньги/суд/угрозы/травма — need_human=true. "
        f"{_PRODUCT_QUESTION_LLM_NOTE} "
        "Ответь ТОЛЬКО JSON: "
        '{"theme":"theme_key","confidence":0.0,"need_human":false,"reason":"..."}'
    )
    shown = _unwrap_client_question(msg) or msg
    user = (
        f"Стадия диалога: {stage}\n"
        f"Артикул: {article or 'неизвестен'}\n"
        f"Темы:\n{theme_lines}\n\n"
        f"{correction_hints}"
        f"История:\n{hist_lines}\n\n"
        f"Новое сообщение клиента:\n{shown}\n"
    )
    try:
        raw = complete_sync(
            system=system,
            user=user,
            model_key=model,
            temperature=0.1,
            max_tokens=300,
            response_format=None if is_yandex_lite(model) else {"type": "json_object"},
        )
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception as e:
        logger.warning("[ChatBot] classify error: %s", e)
        _mark_quota_pause(str(e))
        return None


def _branch_or_correction(db: Session, theme: str, article: str, reship,
                          msg: str, *, mp: str, stage: str, last_theme: str) -> str:
    """Если менеджер уже правил похожий случай — берём его ветку, иначе обычный подбор."""
    forced = getattr(classify, "_forced_branch", None)
    if forced is not None:
        return forced
    return _resolve_branch(
        db, theme, article, reship, msg,
        mp=mp, stage=stage, last_theme=last_theme or "",
    )


def _dostavka_branch_from_fulfillment(db: Session, theme: str, branch: str,
                                      mp: str, chat_id: str) -> str:
    """FBS → «Наша задержка», FBO → обычный шаблон площадки.

    Тип заказа важнее похожего прошлого чата: иначе старые правки «ведите в WB»
    продолжат уходить на FBS.
    """
    if (theme or "") != "dostavka_wb":
        return branch
    try:
        with db.bind.connect() as conn:
            payload = chat_fulfillment.resolve_chat_fulfillment(conn, mp, chat_id) or {}
        mapped = chat_fulfillment.dostavka_branch_for_fulfillment(payload.get("code"))
        if mapped is None:
            return branch
        return mapped
    except Exception:
        return branch


def classify(db: Session, msg: str, stage: str, history: List[dict],
             article: str, nm_id=None, mp: str = "wb",
             chat_id: str = "") -> Tuple[str, float, bool, str]:
    classify._forced_branch = None
    raw = msg or ""
    if _is_product_question_wrapper_only(raw):
        return ("utochnenie", 0.9, False, "шапка площадки «вопрос по товару», без сути")
    msg = _unwrap_client_question(raw)
    hit = _best_correction(db, msg, mp or "wb", chat_id=chat_id)
    if hit:
        theme = hit["chosen_theme"]
        classify._forced_branch = (hit.get("chosen_branch") or "").strip()
        conf, need, reason = 0.95, False, f"{_MANAGER_CORRECTION_REASON} → {theme}"
    else:
        ruled = _rule_classify(msg, stage, mp=mp or "wb")
        if ruled:
            theme, conf, need, reason = ruled
        else:
            # Классификация через Qwen жрала лимит на каждом открытии чата/страницы
            # вопросов. Тема — только правилами и правками менеджеров.
            # ИИ остаётся на кнопке «Сгенерировать» (generate_ai_draft).
            return ("utochnenie", 0.55, False, "без ИИ → шаблон или кнопка «Сгенерировать»")

    chosen_from_correction = theme if hit else None

    # Ассортимент: сушилки и др. невозвратные — только WB
    if (mp or "wb").lower() == "wb" and theme in ("vozvrat", "nevozvratny") and not need:
        if nonreturnable_cats.is_nonreturnable(db, article=article, nm_id=nm_id):
            theme = "nevozvratny"
            reason = ((reason or "") + "; категория невозвратная").strip("; ")
    elif (mp or "wb").lower() != "wb" and theme == "nevozvratny":
        theme = "vozvrat"
        reason = ((reason or "") + "; nevozvratny только WB → vozvrat").strip("; ")

    # Только после WB-проверки невозвратной категории: nevozvratny не меняем.
    theme, need, reason = _apply_return_flow(theme, msg, stage, need, reason, mp=mp or "wb")

    # «Товар не работает» — швабры/вёдра 1S; высокие 2S → меню уточнения
    if theme == "ekspluataciya" and not _is_mop_or_bucket(db, article=article, nm_id=nm_id):
        theme, conf = "utochnenie", max(conf, 0.7)
        reason = ((reason or "") + "; ekspluataciya только швабры/вёдра 1S → меню").strip("; ")

    min_conf = float(chat_playbook.get_setting_mp(db, "bot_confidence_min", mp or "wb", str(MIN_CONFIDENCE))
                     or MIN_CONFIDENCE)
    # Низкая уверенность: тему оставляем как подсказку для чипов, но не уводим в меню 1–5.
    # Подстановку текста в окно режет suggest_for_chat / process по этому же порогу.
    if conf < min_conf and not need and theme != "utochnenie":
        reason = f"низкая уверенность ({conf:.2f}) — без автоподстановки; было: {reason}"

    if chosen_from_correction is not None and theme != chosen_from_correction:
        classify._forced_branch = None

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
                            limit: int = 5, mp: str = "wb",
                            skip_keywords: bool = False) -> List[str]:
    """Дешёвый поиск похожих ответов менеджеров по площадке."""
    keys = [] if skip_keywords else _keywords(client_msg)
    rows: List[str] = []
    mp = (mp or "wb").lower()
    recent_limit = 8 if skip_keywords else 40

    if mp == "ozon":
        found = db.execute(text("""
            SELECT m.text
            FROM ozon_chat_messages m
            WHERE m.sender_type IN ('seller','courier')
              AND m.text IS NOT NULL
              AND length(trim(m.text)) BETWEEN 40 AND 700
            ORDER BY m.created_at DESC NULLS LAST
            LIMIT :lim
        """), {"lim": recent_limit}).fetchall()
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
            LIMIT :lim
        """), {"lim": recent_limit}).fetchall()
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
                LIMIT :lim
            """), {"nm": nm_id, "lim": 8 if skip_keywords else 30}).fetchall()
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


def _llm_complete(*, system: str, user: str, model: str = "qwen-plus",
                  temperature: float = 0.3, max_tokens: int = 450,
                  timeout: Optional[float] = None,
                  max_models: int = 0) -> Optional[str]:
    from .llm import complete_sync

    if _quota_paused():
        return None
    try:
        raw = complete_sync(
            system=system,
            user=user,
            model_key=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_models=max_models,
        )
        return (raw or "").strip() or None
    except Exception as e:
        logger.warning("[ChatBot] draft error: %s", e)
        _mark_quota_pause(str(e))
        return None


def generate_ai_draft(db: Session, *, client_msg: str, theme: str, article: str,
                      history: List[dict], nm_id=None,
                      reship: Optional[dict] = None, mp: str = "wb",
                      for_manager: bool = False) -> Optional[str]:
    """Черновик ответа, когда в плейбуке нет шаблона. Клиенту не отправляется сам.

    for_manager=True — кнопка в чате/вопросе: работает даже если тумблер
    bot_ai_draft выключен (он только для автобота).
    """
    if not for_manager and chat_playbook.get_setting_mp(db, "bot_ai_draft", mp or "wb", "false").lower() != "true":
        return None

    persona = chat_playbook.get_setting(db, "bot_persona", DEFAULT_PERSONA) or DEFAULT_PERSONA
    model = chat_playbook.get_setting(db, "bot_ai_draft_model", "qwen-plus")
    examples = _similar_seller_replies(
        db, nm_id=nm_id, client_msg=_unwrap_client_question(client_msg), mp=mp,
        skip_keywords=for_manager, limit=5,
    )
    hints = _correction_hints_for_prompt(db, mp or "wb")

    theme_name = ""
    row = db.execute(text(
        "SELECT name, when_text FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).mappings().first()
    if row:
        theme_name = f"{row['name']} ({row['when_text']})"

    hist_lines = "\n".join(
        f"{'Клиент' if h['sender']=='client' else 'Мы'}: "
        f"{(_unwrap_client_question(h.get('text') or '') or h.get('text') or '')[:220]}"
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
            f"Не пиши про WB, «Покупки» WB или «Задать вопрос» WB.\n"
            f"{_PRODUCT_QUESTION_LLM_NOTE}\n\n"
        )
    else:
        user += "\n"
    if hints:
        user += hints
    shown_msg = _unwrap_client_question(client_msg)
    if _is_product_question_wrapper_only(client_msg):
        shown_msg = "(клиент прислал только шапку площадки «вопрос по товару», своей сути нет)"
    elif not shown_msg:
        shown_msg = client_msg
    user += (
        f"Примеры ответов менеджеров (стиль, не копируй слепо):\n{ex_block}\n\n"
        f"История чата:\n{hist_lines}\n\n"
        f"Новое сообщение клиента:\n{shown_msg}\n"
    )
    llm_kw = dict(system=persona, user=user, model=model,
                  temperature=0.35, max_tokens=450)
    if for_manager:
        llm_kw.update(timeout=20, max_models=2, max_tokens=280)
    raw = _llm_complete(**llm_kw)
    if not raw:
        return None
    # убрать возможные обёртки «Ответ:» / кавычки
    raw = re.sub(r"^(ответ(\s+клиенту)?\s*[:\-]\s*)", "", raw, flags=re.I).strip()
    raw = raw.strip("«»\"'")
    raw = _strip_greeting(raw).strip()
    if len(raw) < 15:
        return None
    return raw[:900]


def _text_has_signature(text: str, signature: str = "") -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    sig = (signature or "").strip()
    if sig and sig in raw:
        return True
    return bool(_SIGN_MARK_RE.search(raw))


def _seller_already_signed(db: Session, chat_id: str, mp: str = "wb",
                           signature: str = "") -> bool:
    """В этом чате мы уже представлялись — повторная подпись не нужна."""
    if not chat_id:
        return False
    # Берём шире обычной истории подсказки: представление могло быть раньше
    for h in _history(db, chat_id, limit=40, mp=mp):
        sender = (h.get("sender") or "").lower()
        if sender not in ("seller", "partner", "merchant", "courier"):
            continue
        if _text_has_signature(h.get("text") or "", signature):
            return True
    return False


def _polish_reply(db: Session, body: str, state: dict,
                  last_seller_ts: Optional[int],
                  *, force_signature: bool = False,
                  client_msg: str = "",
                  chat_id: str = "",
                  mp: str = "wb") -> str:
    """Приветствие + подпись вокруг готового тела ответа."""
    body = _strip_greeting(body or "").strip()
    if not body:
        return ""
    parts = []
    if _needs_greeting(
        state, last_seller_ts, client_msg=client_msg,
        last_seller_greeting_ts=_last_seller_greeting_ts(db, chat_id, mp) if chat_id else None,
    ):
        greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
        if greet:
            parts.append(greet)
    parts.append(body)
    text_out = "\n\n".join(p for p in parts if p).strip()
    sig = chat_playbook.get_setting(db, "signature", "").strip()
    already = False
    if not force_signature and chat_id:
        already = _seller_already_signed(db, chat_id, mp, sig)
    add_sig = force_signature or (
        body and not _SHORT_NO_SIGN_RE.match(body) and len(body) > 40 and not already
    )
    if add_sig and sig and not _text_has_signature(text_out, sig):
        text_out = f"{text_out}{sig if sig.startswith(chr(10)) else chr(10)+chr(10)+sig}"
    # На копирование/отправку — без ведущих пустых строк
    return text_out.lstrip(" \t\r\n")[:1000]


# ── Ветки и сборка ответа ────────────────────────────────────────────────────

def extract_form_tokens(text_msg: str) -> list[str]:
    return FORM_TOKEN_RE.findall(text_msg or "")


def bind_form_tokens_to_chat(db, text_msg: str, chat_id: str, mp: str = "") -> int:
    """Привязать токен формы к чату, куда ссылку реально отправили.

    Ссылка могла быть создана в другом чате (черновик / гонка при переключении),
    а уйти клиенту уже здесь — иначе заявка цепляется не туда.
    """
    cid = str(chat_id or "").strip()
    tokens = extract_form_tokens(text_msg)
    if not cid or not tokens:
        return 0
    mp_val = (mp or "").strip().lower() or None
    n = 0
    for tok in tokens:
        db.execute(text("""
            UPDATE reshipment_chat_tokens
            SET chat_id = :cid,
                mp = COALESCE(:mp, mp)
            WHERE token = :token
              AND used_at IS NULL
              AND COALESCE(expired, FALSE) IS NOT TRUE
        """), {"cid": cid, "mp": mp_val, "token": tok})
        n += 1
    return n


def chat_id_for_form_token(db, token: str) -> str | None:
    """Чат, где ссылку с этим токеном реально отправили — не тот, где её создали."""
    tok = (token or "").strip()
    if not tok:
        return None
    pat = f"%t={tok}%"
    row = db.execute(text("""
        SELECT chat_id
        FROM ym_chat_messages
        WHERE text ILIKE :pat
        ORDER BY CASE
                   WHEN lower(COALESCE(sender_type, '')) IN ('seller', 'partner', 'merchant')
                   THEN 0 ELSE 1
                 END,
                 created_at DESC NULLS LAST
        LIMIT 1
    """), {"pat": pat}).mappings().first()
    if row and row.get("chat_id"):
        return str(row["chat_id"])
    row = db.execute(text("""
        SELECT chat_id
        FROM ozon_chat_messages
        WHERE text ILIKE :pat
        ORDER BY CASE
                   WHEN lower(COALESCE(sender_type, '')) IN ('seller', 'partner')
                   THEN 0 ELSE 1
                 END,
                 created_at DESC NULLS LAST
        LIMIT 1
    """), {"pat": pat}).mappings().first()
    if row and row.get("chat_id"):
        return str(row["chat_id"])
    row = db.execute(text("""
        SELECT chat_id
        FROM wb_chat_messages
        WHERE text ILIKE :pat
        ORDER BY CASE WHEN lower(COALESCE(sender, '')) = 'seller' THEN 0 ELSE 1 END,
                 add_ts DESC NULLS LAST
        LIMIT 1
    """), {"pat": pat}).mappings().first()
    if row and row.get("chat_id"):
        return str(row["chat_id"])
    return None


def chat_has_approved_return(db, chat_id: str | None) -> bool:
    """По чату уже одобрен возврат WB/ЯМ — форму доотправки закрываем."""
    cid = str(chat_id or "").strip()
    if not cid:
        return False
    row = db.execute(text("""
        SELECT 1
        FROM wb_claims cl
        WHERE cl.status = 'Одобрено'
          AND lower(cl.srid) IN (
            SELECT lower(order_rid) FROM wb_chats
             WHERE chat_id = :cid AND coalesce(order_rid, '') <> ''
            UNION
            SELECT lower(rid) FROM wb_chat_pending
             WHERE chat_id = :cid AND coalesce(rid, '') <> ''
            UNION
            SELECT lower(matched_srid) FROM reshipment_requests
             WHERE wb_chat_id = :cid AND coalesce(matched_srid, '') <> ''
          )
        LIMIT 1
    """), {"cid": cid}).first()
    if row:
        return True
    row = db.execute(text("""
        SELECT 1
        FROM ym_returns r
        JOIN ym_chats y ON (
            r.return_id::text = y.return_id::text
            OR r.order_id::text = y.order_id::text
        )
        WHERE y.chat_id = :cid
          AND (
            r.status_ru = 'Одобрено'
            OR r.status IN ('REFUNDED', 'REFUND_IN_PROGRESS')
          )
        LIMIT 1
    """), {"cid": cid}).first()
    return bool(row)


def form_token_block_reason(db, token: str | None, chat_id: str | None = None) -> str | None:
    """Почему ссылка формы уже не действует. None — можно заполнять."""
    cid = str(chat_id or "").strip() or None
    tok = str(token or "").strip()
    if not cid and tok:
        row = db.execute(text("""
            SELECT chat_id FROM reshipment_chat_tokens WHERE token = :t
        """), {"t": tok}).mappings().first()
        cid = str((row or {}).get("chat_id") or "").strip() or None
    if not cid and tok:
        cid = chat_id_for_form_token(db, tok)
    if chat_has_approved_return(db, cid):
        return "return_approved"
    return None


def expire_form_tokens_for_approved_return(
    db,
    *,
    chat_id: str | None = None,
    srid: str | None = None,
    ym_return_id: str | None = None,
    ym_order_id: str | None = None,
) -> int:
    """Погасить неиспользованные ссылки формы, когда возврат уже одобрен."""
    chats = set()
    cid = str(chat_id or "").strip()
    if cid:
        chats.add(cid)
    rid = str(srid or "").strip()
    if rid:
        for row in db.execute(text("""
            SELECT chat_id FROM wb_chats
             WHERE order_rid = :s AND coalesce(chat_id, '') <> ''
            UNION
            SELECT chat_id FROM wb_chat_pending
             WHERE rid = :s AND coalesce(chat_id, '') <> ''
            UNION
            SELECT wb_chat_id FROM reshipment_requests
             WHERE lower(matched_srid) = lower(:s)
               AND coalesce(wb_chat_id, '') <> ''
        """), {"s": rid}).fetchall():
            if row and row[0]:
                chats.add(str(row[0]))
    ym_rid = str(ym_return_id or "").strip()
    ym_oid = str(ym_order_id or "").strip()
    if ym_rid or ym_oid:
        for row in db.execute(text("""
            SELECT chat_id FROM ym_chats
            WHERE coalesce(chat_id, '') <> ''
              AND (
                (:rid <> '' AND return_id::text = :rid)
                OR (:oid <> '' AND order_id::text = :oid)
              )
        """), {"rid": ym_rid, "oid": ym_oid}).fetchall():
            if row and row[0]:
                chats.add(str(row[0]))
    if not chats:
        return 0
    result = db.execute(text("""
        UPDATE reshipment_chat_tokens
        SET expired = TRUE
        WHERE used_at IS NULL
          AND COALESCE(expired, FALSE) IS NOT TRUE
          AND chat_id IN :chats
    """).bindparams(bindparam("chats", expanding=True)), {"chats": list(chats)})
    return int(result.rowcount or 0)


def expire_unused_form_tokens_in_text(db: Session, text_msg: str) -> int:
    """Черновик / сбой отправки: токен не должен считаться «ссылка ушла»."""
    tokens = extract_form_tokens(text_msg or "")
    if not tokens:
        return 0
    n = 0
    for tok in tokens:
        result = db.execute(text("""
            UPDATE reshipment_chat_tokens
            SET expired = TRUE
            WHERE token = :token
              AND used_at IS NULL
              AND COALESCE(expired, FALSE) IS NOT TRUE
        """), {"token": tok})
        n += int(result.rowcount or 0)
    if n:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return n


def _make_form_url(db: Session, chat_id: str, mp: str = "", *, persist: bool = True) -> str:
    # Тот же формат, что кнопка «Сформировать ссылку» в разделе доотправки.
    # persist=False — черновик: клиенту ссылку ещё не даём, токен не пишем.
    if chat_has_approved_return(db, chat_id):
        return ""
    if not persist:
        return ""
    token = secrets.token_urlsafe(12)
    mp_val = (mp or "").strip().lower() or None
    try:
        db.execute(text("""
            INSERT INTO reshipment_chat_tokens (token, chat_id, created_by, expires_at, mp)
            VALUES (:token, :cid, 'chat_bot', NOW() + INTERVAL '7 days', :mp)
        """), {"token": token, "cid": chat_id, "mp": mp_val})
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
        if _WHEEL_MISS_RE.search(s):
            return "wheels"
        if _BOLT_MISS_RE.search(s):
            return "bolts"
        if _LEG_MISS_RE.search(s):
            return "legs"
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
        if mp == "ym":
            late = bool(re.search(
                r"(14\s*дн|более\s+четырнадцати|больше\s+(двух\s+)?недель|месяц\s+назад)",
                msg or "", re.I,
            ))
            return "ask_reason" if late else ""
        return "ask_reason"
    if mp == "ozon":
        branch = _resolve_branch_ozon(theme, msg, stage=stage, last_theme=last_theme)
        if theme == "nekomplekt" and branch != "check_tubes":
            if reshipment_skus.is_allowed(db, article):
                return "sku_allowed"
            return branch or "sku_denied"
        if theme == "brak" and branch != "wrong_item" and reshipment_skus.is_allowed(db, article):
            return "fixable_by_part"
        return branch
    if theme == "nekomplekt":
        if mp == "ym":
            from . import ym_parts
            if ym_parts.is_2sb_hanger(article):
                if stage == "awaiting_client":
                    return "to_return"
                if stage == "awaiting_buy":
                    return "to_return" if _wants_return(msg) else "offer_free_slow"
                return "offer_buy"
            if not reshipment_skus.is_allowed(db, article):
                return "sku_denied"
            return "sku_allowed"
        if not reshipment_skus.is_allowed(db, article):
            return "sku_denied"
        return "sku_allowed"
    if theme == "zapchasti":
        in_stock = chat_playbook.get_setting(db, "nasadki_in_stock", "true").lower() == "true"
        return "in_stock" if in_stock else "out_of_stock"
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


def nekomplekt_chip_branch(db: Session, mp: str, article: str) -> str:
    """Ветка «отправить деталь» для быстрой кнопки: по списку SKU, без ручного выбора."""
    art = (article or "").strip()
    mp = (mp or "wb").lower()
    if mp == "ozon":
        if not art:
            return ""
        return "sku_allowed" if reshipment_skus.is_allowed(db, art) else "sku_denied"
    if not art:
        return ""
    if mp == "ym":
        from . import ym_parts
        if ym_parts.is_2sb_hanger(art):
            return "offer_buy"
        return "sku_allowed" if reshipment_skus.is_allowed(db, art) else "sku_denied"
    return "sku_allowed" if reshipment_skus.is_allowed(db, art) else "sku_denied"


def _fill_placeholders(body: str, *, form_url: str = "", reship: Optional[dict] = None) -> str:
    carrier = "СДЭК"
    track = ""
    if reship:
        if (reship.get("delivery_method") or "") == "yandex":
            carrier = "Яндекс Доставка"
        track = reship.get("cdek_number") or reship.get("track_number") or ""
    if form_url:
        body = body.replace("{form_url}", form_url)
    return (
        body.replace("{carrier}", carrier)
            .replace("{track}", track or "—")
            .replace("{storage_until}", "указанного срока")
    )


GREETING_GAP = timedelta(hours=12)


def _aware_utc(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        noon = datetime(value.year, value.month, value.day, 12, 0, tzinfo=MSK)
        return noon.astimezone(timezone.utc)
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    noon = datetime(d.year, d.month, d.day, 12, 0, tzinfo=MSK)
    return noon.astimezone(timezone.utc)


def _last_seller_greeting_ts(db: Session, chat_id: str, mp: str = "wb") -> Optional[int]:
    if not chat_id:
        return None
    try:
        for h in reversed(_history(db, chat_id, limit=40, mp=mp)):
            sender = (h.get("sender") or "").lower()
            if sender not in ("seller", "partner", "merchant", "courier"):
                continue
            if not _GREETING_RE.match((h.get("text") or "").strip()):
                continue
            ts = h.get("add_ts")
            try:
                return int(ts) if ts else None
            except (TypeError, ValueError):
                return None
    except Exception:
        return None
    return None


def _needs_greeting(state: dict, last_seller_ts: Optional[int] = None,
                    client_msg: str = "",
                    last_seller_greeting_ts: Optional[int] = None) -> bool:
    """Здороваемся, если с нашего последнего приветствия прошло ≥ 12 часов.

    «Здравствуйте» клиента не отменяет наше. last_seller_ts / client_msg
    оставлены, чтобы старые вызовы не ломались.
    """
    del last_seller_ts, client_msg
    candidates = []
    state = state or {}
    at = _aware_utc(state.get("last_greeted_at"))
    if at:
        candidates.append(at)
    elif state.get("last_greeted_date") is not None:
        fallback = _aware_utc(state.get("last_greeted_date"))
        if fallback:
            candidates.append(fallback)
    if last_seller_greeting_ts:
        try:
            candidates.append(
                datetime.fromtimestamp(int(last_seller_greeting_ts) / 1000, tz=timezone.utc)
            )
        except (TypeError, ValueError, OSError):
            pass
    if not candidates:
        return True
    last = max(candidates)
    return datetime.now(timezone.utc) - last >= GREETING_GAP


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
                   reason: str = "",
                   article: str = "",
                   commit_form: bool = False) -> Tuple[str, str]:
    """Возвращает (текст, action). Пустой текст — шаблона нет (тогда возможен ИИ-черновик).

    commit_form=True только когда сообщение реально уходит клиенту.
    Иначе токен формы не создаём — иначе возврат думает, что ссылка уже ушла.
    """
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
        return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "ask_reason"

    # Ozon: некомплект. Разрешённый товар — предложить деталь, остальные ветки — проверка или возврат.
    if mp == "ozon" and theme == "nekomplekt":
        if branch == "sku_allowed":
            body = chat_playbook.pick_variant(db, theme, "sku_allowed", mp=mp)
            if body:
                return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "offer_reshipment"
        body = (
            chat_playbook.pick_variant(db, theme, branch, mp=mp)
            if branch else None
        )
        if not body and branch == "sku_denied":
            body = chat_playbook.pick_variant(db, theme, "no_reship_return", mp=mp)
        if not body:
            body = chat_playbook.pick_variant(db, theme, "no_reship_return", mp=mp)
        if body:
            action = "check_tubes" if branch == "check_tubes" else "explain_return"
            return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), action
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"

    # Ozon/ЯМ: не тот товар
    if mp in ("ozon", "ym") and theme == "wrong_item":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"
        return "", action

    # Ozon/ЯМ: срок возврата истёк
    if mp in ("ozon", "ym") and theme == "return_expired":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_support"
        return "", action

    # Некомплект, который классификатор назвал возвратом: разрешённый товар — предложить деталь.
    if mp == "ozon" and theme == "vozvrat":
        from_parts = (
            source_theme == "nekomplekt"
            or _looks_like_missing_parts(client_msg, reason)
        )
        if from_parts and reshipment_skus.is_allowed(db, article):
            body = chat_playbook.pick_variant(db, "nekomplekt", "sku_allowed", mp=mp)
            if body:
                return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "offer_reshipment"
        if from_parts:
            body = chat_playbook.pick_variant(db, "nekomplekt", "no_reship_return", mp=mp)
            if body:
                return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"
            return (
                _polish_reply(db, _return_only_text(mp), state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp),
                "explain_return",
            )
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if body:
            return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"
    if mp in ("ozon", "ym") and theme == "utochnenie":
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
        if not body:
            body = _clarify_menu_text(mp)
        return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "ask_menu"
    if mp == "ozon" and theme == "brak":
        if branch == "fixable_by_part":
            body = chat_playbook.pick_variant(db, theme, "fixable_by_part", mp=mp)
            if body:
                return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "offer_reshipment"
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
            return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"
        return _polish_reply(db, _return_only_text(mp), state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), "explain_return"

    body = chat_playbook.pick_variant(db, theme, branch, mp=mp)
    if not body and branch:
        body = chat_playbook.pick_variant(db, theme, "", mp=mp)
    if not body:
        body = chat_faq.faq_fallback(theme, mp, branch=branch)
    if not body:
        return "", action

    if mp == "ym" and theme == "nekomplekt":
        if branch == "offer_buy":
            action = "offer_buy"
        elif branch == "offer_free_slow":
            action = "offer_free_slow"
        elif branch == "to_return":
            action = "explain_return"
    elif mp == "ym" and theme == "kupit_detal":
        action = "send_buy_link"

    ym_no_form = mp == "ym" and (
        theme == "kupit_detal"
        or branch in ("offer_buy", "offer_free_slow", "to_return", "sku_denied")
    )
    if mp == "ym" and (
        theme == "kupit_detal"
        or "{promo_code}" in body
        or "{promo_pct}" in body
        or "{part_article}" in body
        or "{part_name}" in body
        or "{part_list}" in body
    ):
        from . import ym_parts
        if theme == "kupit_detal":
            body = ym_parts.prepare_buy_reply(
                db, body, article or "", client_msg=client_msg or "",
            )
        else:
            body = ym_parts.fill_part_placeholders(
                body, product_article=article or "",
                client_msg=client_msg or "", db=db,
            )

    form_url = ""
    # Форма доотправки — WB, ЯМ и Ozon.
    # На ЯМ форма только после согласия на бесплатную доотправку, не на шаге покупки.
    if (not ym_no_form) and mp in ("wb", "ym", "ozon") and (
        action in ("offer_reshipment", "send_form_link", "resend_form_link", "ask_menu")
        or "{form_url}" in body
    ):
        if "{form_url}" in body or action in ("offer_reshipment", "send_form_link", "resend_form_link"):
            form_url = _make_form_url(db, chat_id, mp=mp, persist=commit_form)

    body = _fill_placeholders(
        _strip_greeting(body), form_url=form_url,
        reship=reship if mp in ("wb", "ym", "ozon") else None,
    )
    return _polish_reply(db, body, state, last_seller_ts, client_msg=client_msg, chat_id=chat_id, mp=mp), action


def _next_stage(theme: str, action: str, current: str, branch: str = "") -> str:
    if action == "handoff_to_manager":
        return "handoff"
    if theme == "utochnenie" or action == "ask_menu":
        return "awaiting_menu"
    if theme == "vozvrat" and action == "ask_reason":
        return "awaiting_return_reason"
    if theme == "soglasie" or action == "send_form_link":
        return "form_sent"
    if theme == "kupit_detal" or action == "send_buy_link":
        return "buy_sent"
    if theme == "nekomplekt" and (branch == "offer_buy" or action == "offer_buy"):
        return "awaiting_buy"
    if theme == "nekomplekt" and (branch == "offer_free_slow" or action == "offer_free_slow"):
        return "awaiting_client"
    if theme == "nekomplekt" and branch == "to_return":
        return "closing"
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
            if branch == "offer_buy" or action == "offer_buy":
                return "awaiting_buy"
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
    bind_form_tokens_to_chat(db, txt, chat_id, mp)
    db.commit()
    return True


def _notify_manager(db: Session, reason: str, client: str, sku: str,
                    message: str, chat_id: str, mp: str = "wb"):
    if chat_playbook.get_setting(db, "red_zone_notify_tg", "true").lower() != "true":
        return
    tmpl = chat_playbook.get_setting(db, "red_zone_manager_text", "")
    mp_key = (mp or "wb").lower()
    mp_label = {"wb": "WB", "ozon": "Ozon", "ym": "Яндекс Маркет"}.get(mp_key, mp_key.upper())
    dest = (os.getenv("TG_ALERT_CHAT_ID") or "").strip()
    link = telegram_webapp.mini_app_link(mp_key, chat_id)
    default = (
        "🔴 [{mp}] Чат требует менеджера\n"
        "Причина: {reason}\nКлиент: {client}\nТовар: {sku}\n"
        "Сообщение: {message}"
    )
    values = {
        "reason": reason or "—",
        "client": client or "—",
        "sku": sku or "—",
        "message": (message or "")[:300],
        "link": link,
        "mp": mp_label,
    }
    try:
        text_msg = (tmpl or default).format(**values)
    except (KeyError, ValueError, IndexError):
        text_msg = default.format(**values)
    # Старый шаблон без {mp} — всё равно помечаем площадку в начале
    if "{mp}" not in (tmpl or "") and f"[{mp_label}]" not in text_msg:
        text_msg = f"[{mp_label}] {text_msg}"
    markup = telegram_webapp.chat_open_markup(mp_key, chat_id, dest)
    try:
        sent = False
        if dest:
            sent = telegram_proxy.send_text(dest, text_msg, reply_markup=markup)
        if not sent:
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
    candidates = []
    for h in tail:
        t = _unwrap_client_question(h.get("text") or "")
        if not t or t.startswith("![") or t.startswith("📷"):
            continue
        if _is_marketplace_nudge(t):
            continue
        candidates.append(t)
    if candidates:
        # Цифра меню / короткое решение / отмена заказа важнее пинга «жду ответа»
        for t in reversed(candidates):
            if _MENU_CHOICE_RE.match(t) or _OTHER_SHORT_RE.match(t) or _CANCEL_BOT_RE.match(t):
                return t
            if _CANCEL_RE.search(t):
                return t
            if _RESOLVED_RE.search(t) and len(t) < 160:
                return t
        return candidates[-1]
    fb = _unwrap_client_question(fallback or "")
    if fb.startswith("📷") or fb.startswith("![") or _is_marketplace_nudge(fb):
        return ""
    if fb:
        return fb
    if _is_product_question_wrapper_only(fallback or ""):
        return (fallback or "").strip()
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
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
              AND COALESCE(s.bot_disabled, false) = false
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
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
              AND COALESCE(s.bot_disabled, false) = false
        """)
    else:
        q = text("""
            SELECT c.chat_id, c.client_name, c.nm_id, c.good_name, c.last_message,
                   c.last_sender, c.last_ts, c.reply_sign, c.needs_answer, c.is_closing
            FROM wb_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.chat_id = ANY(:ids)
              AND c.needs_answer = true
              AND d.item_id IS NULL
              AND COALESCE(s.bot_disabled, false) = false
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

    state_early = db.execute(text(
        "SELECT bot_disabled FROM cx_chat_bot_state WHERE chat_id = :cid"
    ), {"cid": chat_id}).fetchone()
    if state_early and state_early[0]:
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
        msg = _unwrap_client_question(chat.get("last_message") or "")
    if not msg:
        return None
    # Превью-фото без текста — не шлём меню, ждём текст (debounce уже выше)
    if mp in ("ozon", "ym") and (msg.startswith("📷") or msg.startswith("![")):
        hist = _history(db, chat_id, limit=15, mp=mp)
        for h in reversed(hist):
            t = _unwrap_client_question(h.get("text") or "")
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

    article = _article_for_chat(db, chat, mp)
    reship = _get_reshipment(db, chat_id) if mp == "wb" else None
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))

    stage = state.get("stage") or "new"
    replied_after_auto = (
        _client_replied_after_sent_auto(db, chat_id, last_ts, mp)
        and not _ym_parts_dialog_open(mp, stage)
    )
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
            chat_id=chat_id,
        )
        source_theme = theme
        theme, reason = _remap_theme_for_mp(theme, mp, reason, client_msg=msg)

        # Не долбим меню, пока клиент шлёт фото/приветствие без выбора
        if "ждём выбор" in (reason or ""):
            return None

        zone = chat_playbook.get_zone(db, theme, mp=mp)
        if need_human:
            zone = "human"

        branch = _branch_or_correction(
            db, theme, article, reship, msg,
            mp=mp, stage=stage, last_theme=state.get("last_theme") or "",
        )
        branch = _dostavka_branch_from_fulfillment(db, theme, branch, mp, chat_id)
    last_seller_ts = _last_seller_ts(db, chat_id, mp=mp)
    greet_ts = _last_seller_greeting_ts(db, chat_id, mp=mp)
    zone, reason, auto_held = gate_auto_by_confidence(zone, theme, conf, reason, db, mp)
    need_greet = _needs_greeting(
        state, last_seller_ts, client_msg=msg, last_seller_greeting_ts=greet_ts,
    )

    try:
        min_fill = float(
            chat_playbook.get_setting_mp(db, "bot_confidence_min", mp, str(MIN_CONFIDENCE))
            or MIN_CONFIDENCE
        )
    except (TypeError, ValueError):
        min_fill = MIN_CONFIDENCE
    try:
        conf_score = float(conf or 0)
    except (TypeError, ValueError):
        conf_score = 0.0
    low_confidence = (not replied_after_auto) and conf_score < min_fill

    if low_confidence:
        # Не тратим шаблон/ИИ при низкой уверенности — чат ждёт менеджера.
        reply = ""
        action = "low_confidence"
        zone = "human"
        reason = (
            f"{reason}; уверенность {conf_score:.0%} < {min_fill:.0%} — без автозаполнения"
        ).strip("; ")
        need_human = True
    elif zone == "human":
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
            article=article, commit_form=(zone == "auto" and not shadow),
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
                reply = _polish_reply(db, draft, state, last_seller_ts, client_msg=msg, chat_id=chat_id, mp=mp)
                action = "ai_draft"
                zone = "draft"
                reason = ((reason or "") + "; ИИ-черновик без шаблона").strip("; ")

    sent = False
    if zone == "auto" and reply and "{form_url}" in reply:
        zone = "draft"
        reason = ((reason or "") + "; ссылка формы не создана — черновик, без автоотправки").strip("; ")
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
        if not sent:
            expire_unused_form_tokens_in_text(db, reply)

    if zone == "human" or need_human:
        _notify_manager(
            db, reason=reason or theme,
            client=chat.get("client_name") or "",
            sku=article,
            message=msg,
            chat_id=chat_id,
            mp=mp,
        )

    new_stage = _next_stage(theme, action, stage, branch=branch)
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
        # Только если реально ушло клиенту — иначе shadow/suggest «съедает» слот приветствия
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        state_update["last_greeted_date"] = _msk_date()
        state_update["last_greeted_at"] = now_utc

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
            "auto_held": auto_held,
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

    msg = _best_client_text(db, chat_id, mp, chat.get("last_message") or "")
    last_ts = int(chat.get("last_ts") or 0)

    if force_ai and (not msg or chat.get("last_sender") == "seller"):
        for h in reversed(_history(db, chat_id, limit=20, mp=mp)):
            if h.get("sender") != "client":
                continue
            t = _unwrap_client_question(h.get("text") or "")
            if t and not t.startswith("![") and not t.startswith("📷"):
                msg = t
                break

    if not force_ai:
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
    elif not msg:
        return {"ok": False, "reason": "нет текста клиента для генерации", "can_generate": False}

    state = _bot_state(db, chat_id)
    if (
        not force_ai
        and _client_replied_after_sent_auto(db, chat_id, last_ts, mp)
        and not _ym_parts_dialog_open(mp, state.get("stage") or "")
    ):
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
            zone, reason, held = gate_auto_by_confidence(
                payload.get("zone") or "",
                payload.get("theme") or "",
                payload.get("confidence") or 0,
                payload.get("reason") or "",
                db,
                mp,
            )
            payload["zone"] = zone
            payload["reason"] = reason
            payload["auto_held"] = held
            try:
                min_fill = float(
                    chat_playbook.get_setting_mp(db, "bot_confidence_min", mp, str(MIN_CONFIDENCE))
                    or MIN_CONFIDENCE
                )
            except (TypeError, ValueError):
                min_fill = MIN_CONFIDENCE
            try:
                conf_score = float(payload.get("confidence") or 0)
            except (TypeError, ValueError):
                conf_score = 0.0
            if conf_score < min_fill:
                payload["reply_text"] = ""
                payload["can_generate"] = False
                payload["low_confidence"] = True
                payload["need_human"] = True
                payload["action"] = "low_confidence"
                payload["reason"] = (
                    f"{reason}; уверенность {conf_score:.0%} < {min_fill:.0%} — шаблон не подставляем"
                ).strip("; ")
            return payload

    article = _article_for_chat(db, chat, mp)
    reship = _get_reshipment(db, chat_id) if mp == "wb" else None
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))
    history = _history(db, chat_id, mp=mp)
    stage = state.get("stage") or "new"

    theme, conf, need_human, reason = classify(
        db, msg, stage, history, article,
        nm_id=chat.get("nm_id") if mp == "wb" else None,
        mp=mp,
        chat_id=chat_id,
    )
    source_theme = theme
    theme, reason = _remap_theme_for_mp(theme, mp, reason, client_msg=msg)

    zone = chat_playbook.get_zone(db, theme, mp=mp)
    if need_human:
        zone = "human"

    branch = _branch_or_correction(
        db, theme, article, reship, msg,
        mp=mp, stage=stage, last_theme=state.get("last_theme") or "",
    )
    branch = _dostavka_branch_from_fulfillment(db, theme, branch, mp, chat_id)
    last_seller_ts = _last_seller_ts(db, chat_id, mp=mp)
    greet_ts = _last_seller_greeting_ts(db, chat_id, mp=mp)
    zone, reason, auto_held = gate_auto_by_confidence(zone, theme, conf, reason, db, mp)

    try:
        min_fill = float(
            chat_playbook.get_setting_mp(db, "bot_confidence_min", mp, str(MIN_CONFIDENCE))
            or MIN_CONFIDENCE
        )
    except (TypeError, ValueError):
        min_fill = MIN_CONFIDENCE
    try:
        conf_score = float(conf or 0)
    except (TypeError, ValueError):
        conf_score = 0.0
    # Ниже порога не собираем шаблон и не тратим ИИ на заполнение окна.
    low_confidence = (not force_ai) and conf_score < min_fill

    reply = ""
    action = ""
    if low_confidence:
        reason = (
            f"{reason}; уверенность {conf_score:.0%} < {min_fill:.0%} — шаблон не подставляем"
        ).strip("; ")
        action = "low_confidence"
    elif zone == "human" and not force_ai:
        reply = (chat_playbook.get_setting(db, "red_zone_client_text", "") or "").strip()
        action = "handoff_to_manager"
        if reply and _needs_greeting(
            state, last_seller_ts, client_msg=msg, last_seller_greeting_ts=greet_ts,
        ):
            greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
            if greet and not _GREETING_RE.match(reply):
                reply = f"{greet}\n\n{reply}"
    elif not force_ai:
        reply, action = assemble_reply(
            db, theme, branch, chat_id, state, reship, last_seller_ts,
            mp=mp, source_theme=source_theme, client_msg=msg, reason=reason,
            article=article,
        )

    # ИИ только по кнопке «Сгенерировать» — не при каждом открытии
    if force_ai:
        draft = generate_ai_draft(
            db, client_msg=msg, theme=theme, article=article or "",
            history=history, nm_id=chat.get("nm_id") if mp == "wb" else None,
            reship=reship, mp=mp, for_manager=True,
        )
        if draft:
            reply = _polish_reply(db, draft, state, last_seller_ts, client_msg=msg, chat_id=chat_id, mp=mp)
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
    # Для менеджера понятнее: ветки Playbook 2.0 (Ozon без досылки)
    if mp == "ozon":
        if theme == "nekomplekt" and branch == "check_tubes":
            theme_name = "Некомплект → проверить трубки"
        elif theme == "nekomplekt" and branch == "sku_allowed":
            theme_name = "Некомплект → выслать деталь"
        elif theme == "nekomplekt" and branch in ("sku_denied", "no_reship_return"):
            theme_name = "Некомплект → возврат"
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
    elif mp == "ym":
        if theme == "kupit_detal":
            theme_name = "Купить деталь за 1 ₽"
        elif theme == "nekomplekt" and branch == "offer_buy":
            theme_name = "Некомплект → купить деталь за 1 ₽"
        elif theme == "nekomplekt" and branch == "offer_free_slow":
            theme_name = "Некомплект → бесплатная доотправка (дольше)"
        elif theme == "nekomplekt" and branch == "to_return":
            theme_name = "Некомплект → возврат"
        elif theme == "wrong_item":
            theme_name = theme_name or "Прислали не тот товар"
        elif theme == "return_expired":
            theme_name = theme_name or "Срок возврата истёк"

    can_generate = (not reply) and bool(msg) and zone != "human" and not low_confidence
    # human тоже можно сгенерировать черновик менеджеру, если красный текст пустой
    if not reply and zone == "human" and not low_confidence:
        can_generate = True
    # Ниже порога — без ИИ-кнопки: менеджер сам выбирает шаблон или пишет
    if low_confidence:
        can_generate = False

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
        "need_human": need_human or zone == "human" or low_confidence,
        "reason": reason or "",
        "skip": False,
        "can_generate": can_generate,
        "from_cache": False,
        "auto_held": auto_held,
        "low_confidence": low_confidence,
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
    # Меню 1–5 только для чатов; в вопросах шаблона utochnenie нет.
    if theme == "utochnenie" and not force_ai:
        return {
            "ok": True, "mp": mp, "theme": "", "theme_name": "",
            "confidence": conf, "zone": "draft", "branch_key": "",
            "action": "", "reply_text": "", "need_human": False,
            "reason": "Нет подходящего шаблона",
            "skip": False, "can_generate": True, "from_cache": False,
            "source_theme": source_theme or "",
        }
    zone = question_playbook.get_zone(db, theme) if theme else "human"
    if need_human:
        zone = "human"

    branch = _branch_or_correction(
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
            body = chat_faq.faq_fallback(theme, mp)
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
            reship=None, mp=mp, for_manager=True,
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
              AND COALESCE(s.bot_disabled, false) = false
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
              AND COALESCE(s.bot_disabled, false) = false
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
              AND COALESCE(s.bot_disabled, false) = false
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


def process_batch_all_isolated(limit: int = BOT_BATCH_LIMIT, *, force: bool = False) -> dict:
    """Сессия БД живёт только в этом потоке — не держим пул, пока думает ИИ."""
    with db_session() as db:
        return process_batch_all(db, limit=limit, force=force)


# ── Воркер ───────────────────────────────────────────────────────────────────

async def start_chat_bot_worker(get_db_func):
    logger.info("[ChatBot] worker started, interval=%ss (wb/ozon/ym)", BOT_INTERVAL_SEC)
    await asyncio.sleep(20)
    while True:
        wait = _quota_wait_sec()
        if wait > 0:
            nap = min(300.0, max(15.0, wait))
            logger.info("[ChatBot] все модели Qwen на паузе, жду %.0f сек — потом следующая", nap)
            await asyncio.sleep(nap)
            continue
        try:
            stats = await asyncio.to_thread(process_batch_all_isolated, BOT_BATCH_LIMIT)
            if stats.get("processed"):
                logger.info("[ChatBot] batch: %s", stats)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[ChatBot] worker error: %s", e)
        await asyncio.sleep(BOT_INTERVAL_SEC)
