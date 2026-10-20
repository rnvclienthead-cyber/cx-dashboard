"""Правила ответов на отзывы: очередь, авто (пока выкл.) и подсказки для ИИ.

Автоотправка включится только тумблерами площадок и сценариев. Сама логика уже такая:

- 5 без текста и фото: короткое спасибо
- 4 без текста и фото: спасибо и вопрос, чего не хватило до пятёрки
- 1–3 без текста и фото: извинение и приглашение в чат
- 4–5 с текстом или фото: черновик из конструктора, отправка только вручную
- 1–3 с текстом или фото: пустое поле, ИИ только по кнопке менеджера
"""
from __future__ import annotations

import re

MODE_AUTO_THANKS = "auto_thanks"
MODE_AUTO_SORRY = "auto_sorry"
MODE_APPROVE_TEMPLATE = "approve_template"
MODE_AI_ONLY = "ai_only"

SIGN = "Команда Видовито"

TOPIC_INCOMPLETE = "incomplete"
TOPIC_DEFECT = "defect"
TOPIC_DELIVERY = "delivery"
TOPIC_RETURNED = "already_returned"
TOPIC_ASSEMBLY = "assembly"
TOPIC_LOAD = "load"
TOPIC_UNSUITABLE = "unsuitable"
TOPIC_SIZE = "size"
TOPIC_FOUR_ASK = "four_ask"
TOPIC_PAINT_HOLES = "paint_holes"

REVIEW_TOPIC_CHOICES = (
    (TOPIC_INCOMPLETE, "Некомплект"),
    (TOPIC_DEFECT, "Брак"),
    (TOPIC_DELIVERY, "Упаковка / доставка"),
    (TOPIC_ASSEMBLY, "Сборка"),
    (TOPIC_LOAD, "Нагрузка"),
    (TOPIC_UNSUITABLE, "Не подошло"),
    (TOPIC_SIZE, "Размеры"),
    (TOPIC_RETURNED, "Уже возврат"),
    (TOPIC_FOUR_ASK, "4★ уточнить"),
    (TOPIC_PAINT_HOLES, "Технологические отверстия"),
)

PAINT_HOLES_REPLY = (
    "Понимаем, выглядит необычно. Это технологические отверстия: "
    "за них деталь вешают на линии покраски. "
    "Собирать через них ничего не нужно, на устойчивость не влияют."
)

# Не брак и не сборка: дырки на полке/подносе — крючки линии покраски.
_PAINT_HOLE_NOT_RE = re.compile(
    r"(нет\s+отверст|не\s+хвата\w*\s+отверст|отверст\w*\s+не\s+совпад|"
    r"не\s+просверл|под\s+болт|болт\w*\s+не\s+(вход|прох)|"
    r"с\s+(другой|не\s+той)\s+сторон|отверст\w*\s+не\s+там|"
    r"сделано\s+не\s+там|дыряв\w*\s+(короб|упаков)|сливн|"
    r"заглушк|защ[её]лк)",
    re.I,
)
_PAINT_HOLE_ASK_RE = re.compile(
    r"(?:"
    r"(?:для\s+чего|зачем|почему|что\s+(?:это\s+)?за|что\s+знач)\w*.{0,56}"
    r"(?:отверст|дырк|дырочк)"
    r"|(?:отверст|дырк|дырочк)\w*.{0,56}"
    r"(?:для\s+чего|зачем|почему|технолог|покраск|"
    r"не\s+на\s+(?:фото|карточ)|нет\s+на\s+(?:фото|карточ)|"
    r"на\s+фото.{0,16}нет|на\s+карточк\w*.{0,16}нет|не\s+указан|не\s+показан)"
    r"|технологическ\w*\s+отверст"
    r"|отверст\w*.{0,32}покраск"
    r"|дырочк\w*.{0,32}(?:поднос|полк|боку|сбоку)"
    r"|(?:четыре|4)\s+отверст\w*.{0,32}полк"
    r"|дыр[ыа]\s+сбоку"
    r"|(?:отверст|дырк)\w*.{0,24}(?:на\s+полк|в\s+поднос|на\s+поднос|этажер)"
    r")",
    re.I | re.DOTALL,
)


def is_paint_hole_question(text: str) -> bool:
    """Дырки на полке/подносе для покраски, не отверстие под болт."""
    t = text or ""
    if _PAINT_HOLE_NOT_RE.search(t):
        return False
    return bool(_PAINT_HOLE_ASK_RE.search(t))


OVERLAY_TOPICS = (
    TOPIC_INCOMPLETE, TOPIC_DEFECT, TOPIC_DELIVERY,
    TOPIC_RETURNED, TOPIC_ASSEMBLY, TOPIC_LOAD,
    TOPIC_UNSUITABLE, TOPIC_SIZE, TOPIC_FOUR_ASK,
    TOPIC_PAINT_HOLES,
)
# Сухие заготовки — не накрывать ими живой длинный 4–5★ отзыв.
CANNED_OVERLAY_TOPICS = (
    TOPIC_SIZE, TOPIC_FOUR_ASK, TOPIC_UNSUITABLE,
)

_DASH = re.compile(r"^[\s—\-–−.]*$")
_SHO_LOAD_HINT = (
    " Нагрузка на всю вешалку до 35 кг, включая полки: на один рейл до 5 кг, на полку до 3–4 кг."
)
_BANNED_ADVICE = re.compile(
    r"[^.!?]*("
    r"расширить систем|расширяйте систем|расширить конструкц|"
    r"верхн\w*\s+ярус|"
    r"в прихож|для прихож|"
    r"номер заказ|назовите номер|пришлите номер|"
    r"мы не можем повлиять|не можем повлиять|"
    r"поставьте на ковёр|поставьте на ковер|на мягк\w+ ков"
    r")[^.!?]*[.!?]?",
    re.I,
)
_KG35_SENTENCE = re.compile(
    r"[^.!?]*("
    r"35\s*кг|рейл до 5\s*кг|полк\w+ до 3"
    r")[^.!?]*[.!?]?",
    re.I,
)


def _clean(value) -> str:
    return (value or "").strip()


def _stars(rating) -> int:
    try:
        return int(rating or 0)
    except (TypeError, ValueError):
        return 0


def has_review_text(body="", pros="", cons="") -> bool:
    parts = []
    for raw in (body, pros, cons):
        t = _clean(raw)
        if t and t != "—" and not _DASH.match(t):
            parts.append(t)
    return bool(parts)


def has_review_media(photos) -> bool:
    if not photos:
        return False
    if isinstance(photos, (list, tuple, set)):
        return any(str(p).strip() for p in photos)
    text = str(photos).strip()
    if not text:
        return False
    return any(p.startswith("http") or p.strip() for p in text.replace(",", " ").split())


def classify_review_reply(rating, body="", photos=None, pros="", cons="") -> str:
    stars = _stars(rating)
    texted = has_review_text(body, pros, cons)
    media = has_review_media(photos)
    if stars >= 4:
        if not texted and not media:
            return MODE_AUTO_THANKS
        return MODE_APPROVE_TEMPLATE
    if not texted and not media:
        return MODE_AUTO_SORRY
    return MODE_AI_ONLY


REVIEW_AUTO_SCENARIOS = ("thanks_5", "thanks_4", "sorry")


def auto_send_scenario(mode, rating) -> str | None:
    """Какой тумблер автоответа отвечает за этот отзыв. None — сами не отправляем."""
    if mode == MODE_AUTO_THANKS:
        return "thanks_5" if _stars(rating) >= 5 else "thanks_4"
    if mode == MODE_AUTO_SORRY:
        return "sorry"
    return None


def should_auto_send(mode, rating, scenarios=None) -> bool:
    key = auto_send_scenario(mode, rating)
    if not key:
        return False
    flags = scenarios or {}
    if key not in flags:
        return True
    return bool(flags[key])


def no_text_scenario(rating) -> str:
    stars = _stars(rating)
    if stars >= 5:
        return "Без текста · 5 звёзд"
    if stars == 4:
        return "Без текста · 4 звезды"
    return "Без текста · низкая оценка"


def no_text_answer(rating) -> str:
    stars = _stars(rating)
    if stars >= 5:
        text = "Здравствуйте! Спасибо за высокую оценку — нам очень приятно."
    elif stars == 4:
        text = (
            "Приветствуем! Приятно, что вы выбрали нас. Спасибо за оценку! "
            "Рады, что товар в целом пришёлся вам по душе. "
            "Подскажите, чего не хватило до «пятёрки»? "
            "ваш комментарий в «Чате с продавцом» поможет нам стать лучше. "
            "Приятного пользования!"
        )
    else:
        text = (
            "Здравствуйте. Нам жаль, что покупка не оправдала ожидания. "
            "Напишите, пожалуйста, в «Чат с продавцом» — разберёмся."
        )
    return f"{text}\n\n{SIGN}"


def _norm_article(sku: str) -> str:
    return re.sub(r"\s+", "", sku or "").lower()


def is_sho_article(sku: str) -> bool:
    n = _norm_article(sku)
    return n.startswith("шо") or n.startswith("sho")


def is_shoe_rack_article(sku: str) -> bool:
    n = _norm_article(sku)
    return n.startswith("по-ws") or n.startswith("c0-bb") or n.startswith("sh-")


def is_floor_hanger_article(sku: str) -> bool:
    n = _norm_article(sku)
    return n.startswith("bo-") or n.startswith("bs-") or n.startswith("polko")


_WHEEL_SENTENCE = re.compile(
    r"[^.!?]*("
    r"колёс|колес|ролик|мобильност"
    r")[^.!?]*[.!?]?",
    re.I,
)


def is_shelf_article(sku: str) -> bool:
    """Напольные стеллажи Shelf — без колёс, в отличие от этажерок."""
    return _norm_article(sku).startswith("shelf")


def _is_size_complaint(t: str) -> bool:
    if any(w in t for w in ("не влез", "стелят", "короткие плать")):
        return True
    if not any(w in t for w in ("размер", "габарит")):
        return False
    if any(w in t for w in (
        "идентичн", "совпада", "как в карточк", "как указан", "соответств",
    )):
        return False
    return any(w in t for w in (
        "маленьк", "велик", "не тот", "не подх", "не соотв", "ошиб", "неправильн",
        "не совпал", "не совпали",
    ))


def needs_lived_reply(text: str, rating=0) -> bool:
    """Длинный 4–5★ с живым текстом: не сухой шаблон, смотрит менеджер / умный ИИ."""
    if _stars(rating) < 4:
        return False
    t = (text or "").strip()
    return len(t) >= 160


def detect_review_topic(text: str, rating=0) -> str | None:
    t = (text or "").lower()
    stars = _stars(rating)
    if not t.strip():
        return None
    if any(w in t for w in ("оформила возврат", "отправила возврат", "уже верн", "сделала возврат")):
        return TOPIC_RETURNED
    if any(w in t for w in (
        "некомплект", "недокомплект", "недоукомплект", "не долож",
        "неполный комплект", "не полной комплект", "деталей не хвата",
        "нет болт", "нет труб", "не доложили",
        "нет колёс", "нет колес", "без колёс", "без колес",
        "нет крючк",
    )) or (
        "не хвата" in t
        and "пятёр" not in t and "пятер" not in t and "звезд" not in t
        and any(w in t for w in (
            "болт", "винт", "труб", "полк", "детал", "крюч", "колёс", "колес",
            "ножек", "перекладин", "запчаст",
        ))
    ):
        return TOPIC_INCOMPLETE
    if any(w in t for w in (
        "бракован", "ржавчин", "пришла слом", "пришло слом",
        "пришла полом", "пришло полом", "поломанн",
    )) or ("ножк" in t and "короч" in t) or (
        "дефект" in t and "без дефект" not in t
    ):
        return TOPIC_DEFECT
    if is_paint_hole_question(t):
        return TOPIC_PAINT_HOLES
    if any(w in t for w in (
        "порван", "разорв", "помя", "смят", "мятая короб",
        "курьер", "без короб", "пришло в пакет",
    )) or (
        any(w in t for w in ("коробк", "упаковк", "посылк"))
        and any(w in t for w in ("порв", "помя", "плох", "ужас", "разбит", "смят", "дыряв"))
    ):
        return TOPIC_DELIVERY
    if any(w in t for w in (
        "сложно собр", "трудн собр", "качает", "шата", "нет отверст",
        "криво собр", "не совпад",
    )):
        return TOPIC_ASSEMBLY
    wear = bool(re.search(r"сломал\w*.{0,24}(спустя|через|месяц|недел)", t)) or bool(
        re.search(r"(спустя|через \w+).{0,24}сломал", t)
    )
    broke_in_use = bool(re.search(r"(?<!не )сломал", t)) and "пришл" not in t
    if wear or broke_in_use or any(w in t for w in (
        "хлипк", "хлюп", "провиса", "погнул", "развалил", "не выдерж",
    )) or (
        "нагруз" in t and any(w in t for w in ("превыс", "не выдерж", "не рассчитан", "не расчитан"))
    ):
        return TOPIC_LOAD
    if _is_size_complaint(t):
        return TOPIC_SIZE
    if stars == 4 and any(w in t for w in (
        "не довольн", "не подошл", "не устроил", "разочар",
        "не оправдал", "долго не прослуж",
    )):
        return TOPIC_UNSUITABLE
    if stars == 4 and "временн" in t and "вариант" not in t:
        return TOPIC_UNSUITABLE
    if stars == 4:
        praise = sum(1 for w in (
            "удобн", "нрав", "хорош", "отличн", "супер", "спасибо", "качеств", "довольн",
        ) if w in t)
        if praise == 0 or (praise <= 1 and len(t) < 120):
            return TOPIC_FOUR_ASK
    return None


def topic_label(topic: str | None) -> str:
    return dict(REVIEW_TOPIC_CHOICES).get(topic or "", "По отзыву")


def topic_answer(topic: str, sku: str = "", rating=0) -> str | None:
    """Готовый текст, если конструктор не должен говорить про «качество вообще»."""
    stars = _stars(rating)
    greeting = "Здравствуйте!" if stars >= 4 else "Здравствуйте."

    if topic == TOPIC_RETURNED:
        body = "Очень жаль, что так вышло."
    elif topic == TOPIC_INCOMPLETE:
        if is_sho_article(sku):
            body = (
                "Очень жаль, что комплект оказался неполным. "
                "По этому товару детали отдельно не досылаем — оформите, пожалуйста, возврат."
            )
        else:
            body = (
                "Очень жаль, что в комплекте не хватило деталей. "
                "Напишите, пожалуйста, в «Чат с продавцом» — разберёмся."
            )
    elif topic == TOPIC_DEFECT:
        body = (
            "Очень жаль, что товар пришёл с браком. "
            "Оформите, пожалуйста, возврат по браку в личном кабинете. "
            "Если нужна помощь — напишите в «Чат с продавцом»."
        )
    elif topic == TOPIC_DELIVERY:
        body = (
            "Очень жаль, что товар приехал в помятой или порванной упаковке. "
            "Напишите, пожалуйста, в «Чат с продавцом» — разберёмся и подскажем, как лучше поступить."
        )
    elif topic == TOPIC_ASSEMBLY:
        extra = ""
        if is_floor_hanger_article(sku):
            extra = (
                " Попробуйте ослабить болты, выровнять конструкцию на полу (не на весу) и затянуть снова."
            )
        body = (
            "Очень жаль, что со сборкой вышло неудобно."
            f"{extra} "
            "Если не получится — напишите в «Чат с продавцом», подскажем."
        )
    elif topic == TOPIC_LOAD:
        extra = _SHO_LOAD_HINT if is_sho_article(sku) else ""
        body = (
            "Жаль, что товар не оправдал ожидания. "
            "По отзывам покупателей он обычно служит хорошо — важно собрать по инструкции "
            "и не превышать допустимую нагрузку. "
            "Конструкция не рассчитана на сверхнагрузку и «вечную» службу."
            f"{extra}"
        )
    elif topic == TOPIC_UNSUITABLE:
        body = (
            "Жаль, что товар не до конца подошёл. "
            "Спасибо, что выбрали нас — будем работать над ассортиментом."
        )
    elif topic == TOPIC_SIZE:
        body = "Спасибо за отзыв. Размеры указаны в карточке товара."
    elif topic == TOPIC_FOUR_ASK:
        body = (
            "Спасибо за оценку! Подскажите, чего не хватило до «пятёрки»? "
            "Напишите в «Чат с продавцом» — это поможет нам стать лучше."
        )
    elif topic == TOPIC_PAINT_HOLES:
        body = PAINT_HOLES_REPLY
    else:
        return None
    return f"{greeting} {body}\n\n{SIGN}"


_SHELF_TIP = re.compile(
    r"[^.!?]*("
    r"полки можно просто убрать|"
    r"регулируем\w*\s+полк"
    r")[^.!?]*[.!?]?",
    re.I,
)
_CARE_ADVICE = re.compile(
    r"[^.!?]*("
    r"полки можно просто убрать|"
    r"регулируем\w*\s+полк|"
    r"очищайте их от пыли|"
    r"периодически очища|"
    r"протирайте|"
    r"протрите|"
    r"не сушите микрофибр|"
    r"стирайте её при температуре|"
    r"не перегружайте верхн"
    r")[^.!?]*[.!?]?",
    re.I,
)


_CLOTHES_WEAR = re.compile(
    r"носите любимые образы с удовольствием",
    re.I,
)


def _furniture_wording(text: str) -> str:
    """Мебель, не одежда: «носите» → «пользуйтесь»."""
    out = _CLOTHES_WEAR.sub("пользуйтесь с удовольствием", text or "")
    out = re.sub(r"\b[Нн]осите с удовольствием", "Пользуйтесь с удовольствием", out)
    out = re.sub(r"\bносите с удовольствием", "пользуйтесь с удовольствием", out)
    return out


def apply_article_hints(sku: str, answer: str, rating=0) -> str:
    """Убрать неверные факты из конструктора/ИИ и добавить нагрузку только для ШО."""
    text = answer or ""
    if is_shoe_rack_article(sku):
        text = _SHELF_TIP.sub("", text)
        if "тяжёл" in text.lower() and "обув" in text.lower():
            text = re.sub(r"[^.]*тяжёл[^.]*обув[^.]*\.", "", text, flags=re.I)
    text = _BANNED_ADVICE.sub("", text)
    text = _furniture_wording(text)
    if is_shelf_article(sku):
        text = _WHEEL_SENTENCE.sub("", text)
    if _stars(rating) >= 5:
        text = _CARE_ADVICE.sub("", text)
        text = _WHEEL_SENTENCE.sub("", text)
    if is_sho_article(sku):
        if "35 кг" not in text and any(w in text.lower() for w in ("хлип", "нагруз", "провис", "прочн", "сверхнагруз")):
            text = text.rstrip() + _SHO_LOAD_HINT
    else:
        text = _KG35_SENTENCE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def article_facts_for_ai(sku: str) -> str:
    """Жёсткие факты в сообщение ИИ, чтобы не путал ШО с BO/BB."""
    if is_sho_article(sku):
        return (
            "Факты по артикулу ШО: нагрузка на всю вешалку до 35 кг, рейл до 5 кг, полка 3–4 кг. "
            "Детали не досылаем — при некомплекте предлагай возврат."
        )
    bits = ["Это НЕ артикул ШО: не пиши про 35 кг, рейл 5 кг и полки 3–4 кг."]
    if is_floor_hanger_article(sku):
        bits.append(
            "Напольная вешалка BO/BS/POLKO: если качается — ослабить болты, "
            "выровнять на полу (не на весу), затянуть."
        )
    elif is_shoe_rack_article(sku):
        bits.append(
            "Обувница: не пиши про съёмные полки, колёса и уход "
            "(убрать, протереть, почистить), если клиент сам этого не написал."
        )
    elif is_shelf_article(sku):
        bits.append(
            "Стеллаж Shelf: колёс нет. Не пиши про колёса, ролики, катать и мобильность."
        )
    return " ".join(bits)


REVIEW_AI_SYSTEM = (
    "Ты менеджер клиентского сервиса бренда Vidovito (товары для дома) на маркетплейсе. "
    "Пиши спокойно и по делу, на русском, без канцелярита, без эмодзи, без лишних эмоций "
    "(никаких «ох», «душевно», «вкладываем душу», выдуманных названий вроде «швабровый комплект»). "
    "Согласуй род: «такое впечатление», не «такой впечатление». "
    "К клиенту обращайся на «вы/вас/ваш» со строчной буквы, никогда «Вы/Вас/Ваш». "
    "Не подписывайся — подпись добавится сама. Ответ сразу к отправке, без заголовков.\n"
    "Правила:\n"
    "1) Не предлагай возврат, замену или «исправим/заменим», пока клиент сам об этом не просит "
    "и пока это не явный брак при получении (пришло сломано, ржавчина, ножка короче, отверстие не там). "
    "Обычный негатив: коротко жаль и «напишите в Чат с продавцом». Не обещай, что точно исправим.\n"
    "2) Некомплект: в отзыве не обещай досылку. Для вешалок ШО детали не досылаем — предложи возврат, не чат. "
    "Для остальных артикулов (в том числе BO и сушилки) — только «напишите в Чат с продавцом».\n"
    "3) Явный брак при получении или при сборке (ножка короче, ржавчина, пришло сломано): "
    "жаль, оформите возврат по браку. Не называй браком шаткость и ошибки сборки: "
    "сначала совет по сборке, затем чат.\n"
    "4) Хлипкая / сломалась в использовании (спустя недели, не выдержала вес): это не возврат и не замена. "
    "Формула: жаль, что не оправдало ожидания; по отзывам покупателей товар обычно хороший; "
    "важно соблюдать инструкцию и нормы веса; конструкция не рассчитана на сверхнагрузку и «вечную» службу. "
    "Цифры 35 кг / рейл 5 кг / полка 3–4 кг — ТОЛЬКО если в фактах по артикулу сказано, что это ШО. "
    "Для BO, BB, BS, POLKO и остальных эти цифры запрещены.\n"
    "5) Если клиент уже оформил возврат — не зови в чат.\n"
    "6) Порванная или помятая упаковка: жаль, пригласи в «Чат с продавцом», чтобы разобраться. "
    "Не отправляй в поддержку площадки и не проси скорректировать оценку. "
    "Не называй площадку (Ozon/WB) — модерация может не пропустить.\n"
    "7) Короткий отзыв без сути («не рекомендую») — жаль, напишите в чат, не угадывай причину.\n"
    "8) 4 звезды: не благодари как за пятёрку, если есть претензия. "
    "Брак при сборке — как к браку. «Не подошло» / временная мебель — жаль, спасибо что выбрали, "
    "будем работать над товарами; без возврата. Если непонятно, чего не хватило — спроси про «пятёрку» и позови в чат.\n"
    "9) Не выдумывай свойства товара. Размеры — «указаны в карточке», без просьбы написать в чат. "
    "Не предлагай «расширить систему» и допкомплектующие. Не пиши про верхние ярусы, ковры и комнаты "
    "(прихожая), если клиент сам об этом не сказал. Не проси номер заказа.\n"
    "10) Для BO/BS/POLKO при шатании: ослабить болты, выровнять на полу (не на весу), затянуть. "
    "Этот совет не давать для BB и ШО.\n"
    "11) 5 звёзд и позитив (даже если упомянуты сборка или доставка) — короткое спасибо, без советов "
    "про лёгкое/тяжёлое, без ухода за товаром (протереть, почистить, убрать полки), "
    "без приглашения в чат и без сценария «проблема со сборкой». "
    "Не выдумывай детали товара: если в отзыве нет полок или колёс — не пиши про них.\n"
    "12) Не спорь с оценкой и не хвали товар, если клиент недоволен. "
    "Если оценка низкая, а текст хвалебный — вежливо предложи поправить оценку.\n"
    "13) Держи ответ коротким: 2–4 предложения.\n"
    "14) Не пиши про колёса, ролики и мобильность, если в фактах сказано, что колёс нет. "
    "Стеллажи Shelf — без колёс; не путай их с этажерками.\n"
    "15) Это мебель и товары для дома, не одежда. Не пиши «носите с удовольствием» и про «образы».\n"
    "16) Длинный живой отзыв (сравнение с другим брендом, инструкция, соединители, «рекомендую») — "
    "поблагодари за подробности и отреагируй на 1–2 факта из текста. "
    "Не своди к «размеры указаны в карточке», если про размеры не спрашивали.\n"
    "17) Дырки / отверстия на полке, подносе, сбоку (не под болт): это технологические отверстия "
    "линии покраски, не брак. Собирать через них ничего не нужно, на устойчивость не влияют. "
    "Не путать с «нет отверстия под болт» / «отверстие не там» — это брак или сборка.\n"
    "18) Не пиши «спасибо за тёплые слова», «тёплая оценка» и похожие клише. "
    "Для позитива достаточно короткого «спасибо за отзыв» или «спасибо за оценку».\n"
    "19) «Спасибо, что порекомендовали» — только если в отзыве есть «рекомендую» / «рекомендуем». "
    "Не благодари за рекомендацию, если клиент просто написал «супер» или «удобно»."
)


def should_ground_constructor(mode: str, text: str = "", rating=0) -> bool:
    """ИИ для 1–3 с текстом/фото и для длинных 4–5★ не опирается на сухой конструктор."""
    if mode == MODE_AI_ONLY:
        return False
    if needs_lived_reply(text, rating):
        return False
    return True
