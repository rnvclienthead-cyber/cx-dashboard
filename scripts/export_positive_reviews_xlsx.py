#!/usr/bin/env python3
"""Выгрузка положительных отзывов 4 и 5 по артикулам.

На каждый артикул — отдельный лист, до 30 отзывов.
Если хватает обоих оценок: 23 пятёрки и 7 четвёрок (ближе всего к 75/25 при 30 штуках).
Если одной оценки мало — вторую не раздуваем сверх этой пропорции.

Запуск на сервере:
  cd /root/cx-dashboard && PYTHONPATH=/root/cx-dashboard \\
    ./venv/bin/python3 scripts/export_positive_reviews_xlsx.py /tmp/otzyvy.xlsx
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

import requests
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parents[1]

WANT_5 = 23
WANT_4 = 7
CAP = WANT_5 + WANT_4  # 30
PHOTO_LIMIT = 4
PHOTO_PX = 92
PHOTO_CACHE = Path("/tmp/otzyvy_photo_cache")

MP_LABEL = {"wb": "Wildberries", "ym": "Яндекс Маркет", "ozon": "Ozon"}

_NO_CONS = re.compile(
    r"^(нет|нету|не|отсутствуют|отсутствует|не\s+выявлен\w*|не\s+обнаружен\w*|"
    r"без\s+минусов|минусов\s+нет|недостатков\s+нет|нареканий\s+нет|"
    r"претензий\s+нет|вс[её]\s+хорошо|вс[её]\s+отлично|вс[её]\s+устраивает|"
    r"все\s+отлично|все\s+хорошо|-+|—+|–+|нет\s+минусов|нет\s+недостатков)\.?$",
    re.I,
)

# Сначала вырезаем обороты, где отрицание снимает жалобу.
_PRAISE_VIA_NEG = [
    (re.compile(r"ужас\s+как", re.I), " очень "),
    (re.compile(r"не\s+пожалел\w*", re.I), " доволен "),
    (re.compile(r"ожидал\w*\s+хуже", re.I), " приятно удивлен "),
    (re.compile(r"думал\w*\s+будет\s+хуже", re.I), " приятно удивлен "),
    (re.compile(
        r"(без\s+брака|без\s+царапин|без\s+сколов|без\s+дефектов|без\s+повреждений|"
        r"без\s+минусов|минусов\s+нет|недостатков\s+нет|нареканий\s+нет|"
        r"претензий\s+нет|проблем\s+нет|проблем\s+не\s+было|без\s+проблем|"
        r"без\s+повреждений|повреждений\s+нет|"
        r"царапин\s+(нет|не\s+\w+)|сколов\s+нет|брака\s+нет|дефектов\s+нет|"
        r"без\s+жалоб|жалоб\s+нет|"
        r"не\s+слома\w*|не\s+тресну\w*|не\s+скрип\w*|не\s+шата\w*|"
        r"не\s*плохо)",
        re.I,
    ), " "),
]

_NEGATIVE = re.compile(
    r"(брак\w*|слома\w*|тресну\w*|трещин\w*|царап\w*|поцарап\w*|скол\w*|"
    r"вмятин\w*|помят\w*|погнут\w*|крив(о|ой|ая|ые)|шата\w*|люфт\w*|хлипк\w*|"
    r"развал\w*|отвал\w*|откле\w*|недокомплект\w*|(?<![\w])не\s+доложил\w*|"
    r"(?<![\w])не\s+хват\w*|(?<![\w])не\s+комплект\w*|некомплект\w*|"
    r"(?<!не)плохо\w*|ужас\w*|кошмар\w*|отвратительн\w*|разочар\w*|"
    r"(?<![\w])не\s+советую|(?<![\w])не\s+рекоменд\w*|возврат\w*|"
    r"(?<![\w])вернул[аи]?(?![\w])|обман\w*|(?<![\w])развод(?![\w])|"
    r"грязн\w*|воня\w*|ржав\w*|плесен\w*|гниль\w*|скрип\w*|дефект\w*|"
    r"порва\w*|дырк\w*|зазор\w*|(?<![\w])не\s+подош\w*|(?<![\w])не\s+влез\w*|малова\w*|великова\w*|"
    r"пришло\s+не\s+то|перепута\w*|(?<![\w])не\s+тот\s+товар|(?<![\w])не\s+полн\w*\s+комплект|"
    r"(?<![\w])не\s+нрав\w*|(?<![\w])не\s+понрав\w*|(?<![\w])не\s+устойчив\w*|"
    r"к\s+сожалению|(?<![\w])жаль(?![\w])|"
    r"неудобн\w*|некрасив\w*|ненад[её]жн\w*|нехорош\w*|неприятн\w*|некачествен\w*|"
    r"минус(?!ов\s+нет)|недостат(?!ков\s+нет)|"
    r"биты[йяе]|разбит\w*|погнут\w*|качает\w*|неустойчи\w*|шатк\w*|"
    r"слабоват\w*|хрупк\w*|прогиба\w*|гн[её]т\w*|мн[её]т\w*|мнут\w*|"
    r"непрокрас\w*|не\s+прокрас\w*|недолож\w*|проблем\w*|(?<![\w])беда(?![\w])|"
    r"вогнут\w*|не\s+вставл\w*|оторва\w*|надорва\w*|наклонил\w*|прогалин\w*|"
    r"(?<![\w])не\s+очень|(?<![\w])не\s+особо|(?<![\w])не\s+совсем|"
    r"(?<![\w])не\s+удобн\w*|(?<![\w])не\s+лучш\w*|(?<![\w])не\s+плох\w*|неплох\w*|"
    r"в\s+целом|в\s+принципе|(?<![\w])нормальн\w*|(?<![\w])норм(?![\w])|"
    r"если\s+бы|надо\s+было|сделайте|снял\w*\s+звезд\w*|четверк\w*|"
    r"временн\w+\s+(?:вариант|решен\w*)|тяжело|(?<![\w])вроде(?![\w])|(?<![\w])увы(?![\w])|"
    r"тонк(?:ий|ая|ое|ие|ого|ой|ую|им)|очень\s+долго|шла\s+долго|"
    r"досверл\w*|не\s+совпада\w*|просьба\s+к\s+продавцу|а\s+не\s+\d|"
    r"не\s+смогли|расход\w*|обратн\w+\s+связ|раскручива\w*|жалов\w*|"
    r"догадайтесь|убра\w+\s+звезд\w*|неровн\w*|пятн(?!иц)|"
    r"по\s+факту|пока\s+ничего\s+не\s+слома|если\s+не\s+трогать|"
    r"(?<![\w])пойд[её]т(?![\w])|"
    r"потолще|подлиннее|потоньше|покороче|пошире|вместо\s+\d|не\s+везде|"
    r"злипк\w*|желать\s+лучшего|поврежд\w*|выскакива\w*|вылета\w*|не\s+промо\w*|"
    r"четвероч\w*|не\s+крут\w*|разорван\w*|рван\w*|сомнен\w*|нарушен\w*|"
    r"не\s+собрать|перекруч\w*|не\s+внуша\w*|слишком\s+легк\w*|"
    r"не\s+того\s+цвета|не\s+тот\s+цвет|облез\w*|не\s+просверл\w*|выбит\w*|"
    r"болтает\w*|и\s+так\s+пойд\w*|так\s+пойд\w*|вполне(?!\s+довол)|даже\s+ничего|"
    r"снял\w*.{0,20}звезд\w*|не\s+на\s+5|не\s+на\s+пять|баловств\w*|ищите\s+более|"
    r"проклеива\w*|был[ио]\s+бы|дума\w+.{0,40}(?:больше|меньше)|"
    r"(?<![\w])отказ(?![\w])|(?<![\w])дыру?(?![\w])|"
    r"хорошо\s+бы|хотелось\s+бы|можно\s+было\s+лучше|ммнус|"
    r"гнут\w*|лома\w*|промина\w*|расшат\w*|выпада\w*|разлома\w*|"
    r"сыпет\w*|сыпется|отщелк\w*|деформир\w*|рассып\w*|швыря\w*|зашива\w*|"
    r"(?<![\w])не\s+крепк\w*|(?<![\w])не\s+качествен\w*|(?<![\w])не\s+супер|"
    r"(?<![\w])не\s+(?:\S+\s+){0,2}(?:надежн\w*|крепк\w*|устойчив\w*|качествен\w*)|"
    r"не\s+логичн\w*|не\s+полн\w+\s+сварк\w*|"
    r"хиленьк\w*|недосчита\w*|звезд\w*.{0,20}снял\w*|забрал\w*\s+звезд\w*|"
    r"(?<![\w])сойд[её]т(?![\w])|(?<![\w])неплохо(?![\w])|не\s+продуман\w*|приходится|"
    r"остр\w+\s+(?:конц|кра|труб)|отсутству\w+\s+отверст\w*|деыфект\w*|"
    r"расправил\w*|не\s+для\s+каждой|неправильн\w+\s+цвет\w*|вроди|"
    r"побит\w*|надеял\w+.{0,50}более|качество\s+говорит\s+о\s+цене|"
    r"(?<![\w])средн(?:ее|яя|ий|его)|(?<![\w])не\s+стоит|красн\w+\s+цен\w*|"
    r"можно\s+было\s+сделать|возвраща\w*|смят\w*|(?<![\w])не\s+подход\w*|"
    r"отсутствует\s+\w+|повезло[,:]?\s+приш|"
    r"короче\s+на|снима\w*.{0,20}звезд\w*|вскрыт\w*|огорч\w*|несоответств\w*|"
    r"не\s+свезло|не\s+доста[её]т|подмят\w*|замя\w*|"
    r"не\s+буду\s+покупать|учтите|ошибк\w*|буд[ьт]е\s+аккуратнее|свален\w*|смутил\w*|не\s+грузить|"
    r"(?<![\w])не\s*очень|(?<![\w])не\s*обнаруж\w*|просверлил\w*\s+сам\w*|(?<![\w])не\s+ждите|впринципе)",
    re.I,
)

_POSITIVE = re.compile(
    r"(отличн\w*|супер|прекрасн\w*|идеальн\w*|классн\w*|(?<![\w])класс(?![\w])|"
    r"качествен\w*|(?<!не)удобн\w*|(?<!не)красив\w*|(?<![\w])нрав\w*|понрав\w*|довол\w*|"
    r"рекоменд\w*|советую|(?<![\w])(?<!не\s)(?<!у)лучш\w*|шикар\w*|восторг\w*|обожа\w*|кайф\w*|"
    r"(?<![\w])крут(ой|ая|ые|ое|ым)?(?![\w])|(?<!не)(?<!не\s)хорош\w*|спасибо|благодар\w*|"
    r"л[её]гк(?!их\s+вещ)\w*|прочн\w*|(?<!не)(?<!не\s)над[её]жн\w*|"
    r"аккуратн\w*|стильн\w*|практичн\w*|вместит\w*|(?<!не\s)(?<!не)устойчив\w*|"
    r"быстр\w*\s+достав\w*|доставил\w*\s+быстр\w*|"
    r"быстр\w*\s+собра\w*|просто\s+собра\w*|легко\s+собра\w*|л[её]гко\s+собра\w*|"
    r"вс[её]\s+подош\w*|радует\w*|выше\s+ожидан\w*|"
    r"(?<![\w])огонь(?![\w])|(?<![\w])топ(ов\w*)?(?![\w])|(?<![\w])бомба(?![\w])|"
    r"зач[её]т\w*|прелест\w*|симпатичн\w*|компактн\w*|функциональн\w*|"
    r"современ\w*|элегант\w*|изящн\w*|солидн\w*|добротн\w*|"
    r"стоит\s+своих|за\s+свои\s+деньги|порадовал\w*|в\s+восторге|"
    r"(?<!не\s)(?<!не)крепк\w*|"
    r"замечательн\w*|чудесн\w*|великолепн\w*|бесподобн\w*|восхит\w*|(?<!не)приятн\w*|"
    r"уютн\w*|полный\s+комплект)",
    re.I,
)

_NEUTRAL_ONLY = re.compile(
    r"^(норм(ально)?|ок|окей|ok|вс[её]\s+ок|сойд[её]т|пойд[её]т|неплохо|ничего\s+особенного|"
    r"средне|обычно|так\s+себе|приемлемо|удовлетворительно|ожидаемо|"
    r"соответствует(\s+описанию)?|как\s+на\s+фото|как\s+в\s+описании|"
    r"в\s+целом\s+норм\w*|нормас|сойдет|пойдет)[\s\.\!\?👍🔥❤️❤]*$",
    re.I,
)


def _norm(text: str) -> str:
    s = (text or "").replace("ё", "е").replace("Ё", "Е")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def cons_is_complaint(cons: str) -> bool:
    raw = _norm(cons)
    if not raw:
        return False
    if _NO_CONS.match(raw):
        return False
    letters = re.sub(r"[^a-zа-я]", "", raw.lower())
    if len(letters) <= 2:
        return False
    return True


def _scrub(text: str) -> str:
    s = _norm(text)
    for rx, repl in _PRAISE_VIA_NEG:
        s = rx.sub(repl, s)
    return s


def _contrast_ok(text: str) -> bool:
    """«Но / правда / единственное» оставляем только если обе части — хвала."""
    if re.search(r"не\s+только\b.{0,80}\bно\s+и\b", text, re.I):
        return True
    parts = re.split(
        r"\b(?:но|однако|хотя|правда|единственн\w*|а\s+так|в\s+остальном|кроме\s+того)\b",
        text,
        flags=re.I,
    )
    if len(parts) == 1:
        return True
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return False
    if not all(_POSITIVE.search(part) for part in parts):
        return False
    tail = parts[-1]
    if re.search(r"\bл[её]гк\w*", tail, re.I) and not re.search(
        r"(отличн|качествен|удобн|нрав|рекоменд|супер|идеальн|крепк|прочн|устойчив|хорош)",
        tail,
        re.I,
    ):
        return False
    return True


def review_body(body: str, pros: str) -> str:
    chunks = []
    b = _norm(body)
    p = _norm(pros)
    if b:
        chunks.append(b)
    if p and p.lower() not in b.lower() and not _NO_CONS.match(p):
        chunks.append(p)
    return "\n".join(chunks).strip()


def positivity(body: str, pros: str, cons: str) -> tuple[bool, int]:
    """(годится, оценка яркости). Негатив и чистая нейтральность — мимо."""
    if cons_is_complaint(cons):
        return False, 0
    text = review_body(body, pros)
    if not text or not re.search(r"[A-Za-zА-Яа-я]", text):
        return False, 0
    if _NEUTRAL_ONLY.match(_norm(text)):
        return False, 0
    scrubbed = _scrub(text)
    if _NEGATIVE.search(scrubbed):
        return False, 0
    if not _contrast_ok(text):
        return False, 0
    hits = _POSITIVE.findall(text)
    if not hits:
        return False, 0
    score = min(len(hits), 6) * 2
    n = len(text)
    if 50 <= n <= 320:
        score += 2
    elif n < 12:
        score -= 1
    if re.search(r"\bно\b", text, re.I):
        score -= 2
    return True, score


def parse_photos(raw) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    urls = []
    for part in re.split(r"[\s,;]+", text):
        part = part.strip("[]\"'")
        if part.startswith("http://") or part.startswith("https://"):
            if part not in urls:
                urls.append(part)
    return urls


def _cache_path(url: str) -> Path:
    return PHOTO_CACHE / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")


def _download_thumb(url: str) -> bytes | None:
    cached = _cache_path(url)
    if cached.exists() and cached.stat().st_size > 80:
        return cached.read_bytes()
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 CX-dashboard review-export"},
            timeout=12,
        )
        resp.raise_for_status()
        img = PILImage.open(io.BytesIO(resp.content))
        img = img.convert("RGB")
        img.thumbnail((220, 220))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        data = buf.getvalue()
        PHOTO_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data
    except Exception:
        return None


def fetch_thumbs(urls: list[str]) -> dict[str, bytes]:
    unique = list(dict.fromkeys(urls))
    out = {}
    if not unique:
        return out
    workers = min(8, len(unique))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_download_thumb, url): url for url in unique}
        for fut in as_completed(futs):
            url = futs[fut]
            data = fut.result()
            if data:
                out[url] = data
    return out


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


def allocate(n5: int, n4: int) -> tuple[int, int, str]:
    """Сколько пятёрок и четвёрок взять, и короткая пометка.

    Полный набор — 23 и 7. Если четвёрок меньше, пятёрок берём ровно втрое больше.
    Если пятёрок меньше, четвёрок — около трети от пятёрок.
    """
    if n5 <= 0 and n4 <= 0:
        return 0, 0, "нет положительного текста"
    if n4 <= 0:
        return min(n5, CAP), 0, "четвёрок с положительным текстом нет — только пятёрки"
    if n5 <= 0:
        return 0, min(n4, WANT_4), "пятёрок с положительным текстом нет — только четвёрки"
    if n5 >= WANT_5 and n4 >= WANT_4:
        return WANT_5, WANT_4, ""
    take4_cap = min(n4, WANT_4)
    take5_cap = min(n5, WANT_5)
    if take5_cap >= take4_cap * 3:
        take4 = take4_cap
        take5 = take4 * 3 if take4 < WANT_4 else take5_cap
    else:
        take5 = take5_cap
        take4 = min(take4_cap, max(1, round(take5 / 3)))
    note = f"в базе меньше, чем нужно: взято {take5} пятёрок и {take4} четвёрок"
    return take5, take4, note


def pick_rows(rows: list[dict], k: int) -> list[dict]:
    rows = sorted(rows, key=lambda r: (r["score"], r["created"] or date.min), reverse=True)
    out = []
    seen = set()
    for row in rows:
        key = re.sub(r"\W+", "", row["text"].lower())[:160]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= k:
            break
    out.sort(key=lambda r: (r["created"] or date.min), reverse=True)
    return out


def safe_sheet_name(sku: str, used: set[str]) -> str:
    name = re.sub(r"[\[\]\:\*\?\/\\]", " ", sku).strip() or "SKU"
    name = name[:31]
    base = name
    n = 2
    while name in used:
        suffix = f" {n}"
        name = (base[: 31 - len(suffix)] + suffix)
        n += 1
    used.add(name)
    return name


def load_reviews():
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import text
    from backend.database import engine

    sql = """
    SELECT src, sku, created, rating, body, pros, cons, photos FROM (
      SELECT 'wb' AS src,
             TRIM(supplier_article) AS sku,
             created_date AS created,
             valuation AS rating,
             COALESCE(text, '') AS body,
             COALESCE(pros, '') AS pros,
             COALESCE(cons, '') AS cons,
             COALESCE(photos, '') AS photos
      FROM wb_feedbacks
      WHERE valuation IN (4, 5)
        AND supplier_article IS NOT NULL AND TRIM(supplier_article) <> ''
      UNION ALL
      SELECT 'ym',
             TRIM(supplier_article),
             created_date,
             valuation,
             COALESCE(comment, ''),
             COALESCE(pro_text, ''),
             COALESCE(contra_text, ''),
             ''
      FROM ym_feedbacks
      WHERE valuation IN (4, 5)
        AND supplier_article IS NOT NULL AND TRIM(supplier_article) <> ''
      UNION ALL
      SELECT 'ozon',
             TRIM(supplier_article),
             created_at,
             rating,
             COALESCE(review_text, ''),
             COALESCE(pros, ''),
             COALESCE(cons, ''),
             COALESCE(photos, '')
      FROM ozon_reviews
      WHERE rating IN (4, 5)
        AND supplier_article IS NOT NULL AND TRIM(supplier_article) <> ''
    ) s
    """
    names_sql = """
    SELECT sku, name FROM (
      SELECT TRIM(supplier_article) AS sku, NULLIF(TRIM(name_ru), '') AS name, 1 AS pri
      FROM wb_assortment
      WHERE supplier_article IS NOT NULL
      UNION ALL
      SELECT TRIM(supplier_article), NULLIF(TRIM(name), ''), 2
      FROM ym_assortment
      WHERE supplier_article IS NOT NULL
      UNION ALL
      SELECT TRIM(supplier_article), NULLIF(TRIM(product_name), ''), 3
      FROM ozon_reviews
      WHERE supplier_article IS NOT NULL AND product_name IS NOT NULL
    ) n
    WHERE name IS NOT NULL
    ORDER BY pri
    """
    by_sku = defaultdict(lambda: {5: [], 4: []})
    all_skus = set()
    names = {}
    with engine.connect() as conn:
        for row in conn.execute(text(names_sql)).mappings():
            names.setdefault(row["sku"], row["name"])
        for row in conn.execute(text(sql)).mappings():
            sku = row["sku"]
            all_skus.add(sku)
            ok, score = positivity(row["body"], row["pros"], row["cons"])
            if not ok:
                continue
            by_sku[sku][int(row["rating"])].append({
                "src": row["src"],
                "created": _as_date(row["created"]),
                "rating": int(row["rating"]),
                "text": review_body(row["body"], row["pros"]),
                "score": score,
                "photos": parse_photos(row["photos"]),
            })
    return by_sku, names, all_skus


def build_book(by_sku, names, all_skus) -> tuple[Workbook, dict]:
    wb = Workbook()
    summary = wb.active
    summary.title = "Сводка"

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True, name="Calibri", size=11)
    title_font = Font(bold=True, name="Calibri", size=14, color="1F4E79")
    note_font = Font(name="Calibri", size=10, italic=True, color="595959")
    cell_font = Font(name="Calibri", size=11)
    five_fill = PatternFill("solid", fgColor="E2EFDA")
    four_fill = PatternFill("solid", fgColor="FFF2CC")
    thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    wrap = Alignment(wrap_text=True, vertical="top")
    center = Alignment(wrap_text=True, vertical="center", horizontal="center")

    summary["A1"] = "Положительные отзывы с оценкой 4 и 5"
    summary["A1"].font = title_font
    summary.merge_cells("A1:H1")
    summary["A2"] = (
        "Только живой положительный текст: без жалоб, оговорок и нейтральных «нормально / ок / сойдёт». "
        "На артикул до 30 отзывов — 23 с оценкой 5 и 7 с оценкой 4. "
        "Если четвёрок или пятёрок меньше, вторая оценка не раздувается сверх пропорции 3 к 1. "
        "Фото из отзыва вставлены в лист артикула (до 4 кадров)."
    )
    summary["A2"].font = note_font
    summary["A2"].alignment = wrap
    summary.merge_cells("A2:H2")
    summary.row_dimensions[2].height = 48

    headers = ["Артикул", "Название", "Пятёрок", "Четвёрок", "Всего", "Доля пятёрок", "Комментарий"]
    for col, h in enumerate(headers, 1):
        cell = summary.cell(4, col, h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = thin

    used_names = {"Сводка"}
    stats = {"skus": 0, "full": 0, "reviews": 0, "only5": 0, "only4": 0, "short": 0}
    sheet_rows = []

    for sku in sorted(all_skus, key=lambda s: s.casefold()):
        pool = by_sku.get(sku) or {5: [], 4: []}
        take5, take4, note = allocate(len(pool[5]), len(pool[4]))
        chosen5 = pick_rows(pool[5], take5)
        chosen4 = pick_rows(pool[4], take4)
        chosen = chosen5 + chosen4
        if not chosen:
            sheet_rows.append((sku, names.get(sku) or "", 0, 0, 0, note or "нет положительного текста", []))
            continue
        stats["skus"] += 1
        stats["reviews"] += len(chosen)
        if len(chosen5) == WANT_5 and len(chosen4) == WANT_4:
            stats["full"] += 1
        elif not chosen4:
            stats["only5"] += 1
        elif not chosen5:
            stats["only4"] += 1
        else:
            stats["short"] += 1

        title = names.get(sku) or ""
        sheet_rows.append((sku, title, len(chosen5), len(chosen4), len(chosen), note, chosen))

    photo_urls = []
    for _sku, _title, _n5, _n4, _total, _note, chosen in sheet_rows:
        for row in chosen:
            photo_urls.extend((row.get("photos") or [])[:PHOTO_LIMIT])
    thumbs = fetch_thumbs(photo_urls)
    stats["photos"] = sum(1 for u in photo_urls if u in thumbs)
    stats["photo_urls"] = len(photo_urls)
    print(f"photos downloaded={stats['photos']}/{stats['photo_urls']}")

    for i, (sku, title, n5, n4, total, note, chosen) in enumerate(sheet_rows, 5):
        share = f"{round(100 * n5 / total)}%" if total else "—"
        values = [sku, title, n5, n4, total, share, note]
        for col, val in enumerate(values, 1):
            cell = summary.cell(i, col, val)
            cell.font = cell_font
            cell.border = thin
            cell.alignment = wrap if col in (2, 7) else center
        if not chosen:
            continue
        ws = wb.create_sheet(safe_sheet_name(sku, used_names))
        label = f"{sku} — {title}" if title else sku
        mix = f"{n5} с оценкой 5, {n4} с оценкой 4"
        ws["A1"] = label
        ws["A1"].font = title_font
        last_col = 4 + PHOTO_LIMIT
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
        ws["A2"] = mix + (f". {note}" if note else "")
        ws["A2"].font = note_font
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
        ws.row_dimensions[1].height = 22
        headers_sku = ["Площадка", "Дата", "Оценка", "Текст отзыва"] + [
            "Фото" if i == 0 else f"Фото {i + 1}" for i in range(PHOTO_LIMIT)
        ]
        for col, h in enumerate(headers_sku, 1):
            cell = ws.cell(4, col, h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center
            cell.border = thin
        for r_i, row in enumerate(chosen, 5):
            vals = [
                MP_LABEL.get(row["src"], row["src"]),
                row["created"].strftime("%d.%m.%Y") if row["created"] else "",
                row["rating"],
                row["text"],
            ]
            fill = five_fill if row["rating"] == 5 else four_fill
            for col, val in enumerate(vals, 1):
                cell = ws.cell(r_i, col, val)
                cell.font = cell_font
                cell.border = thin
                cell.fill = fill
                cell.alignment = center if col < 4 else wrap
            photos = (row.get("photos") or [])[:PHOTO_LIMIT]
            has_img = False
            for p_i in range(PHOTO_LIMIT):
                cell = ws.cell(r_i, 5 + p_i, "")
                cell.border = thin
                cell.fill = fill
                url = photos[p_i] if p_i < len(photos) else ""
                blob = thumbs.get(url) if url else None
                if blob:
                    xl_img = XLImage(io.BytesIO(blob))
                    xl_img.width = PHOTO_PX
                    xl_img.height = PHOTO_PX
                    xl_img.anchor = f"{get_column_letter(5 + p_i)}{r_i}"
                    ws.add_image(xl_img)
                    has_img = True
                elif url:
                    cell.value = url
                    cell.hyperlink = url
                    cell.alignment = wrap
            ws.row_dimensions[r_i].height = 78 if has_img or photos else 48
        ws.auto_filter.ref = f"A4:{get_column_letter(last_col)}{4 + len(chosen)}"
        ws.freeze_panes = "A5"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        widths = [18, 14, 12, 72] + [16] * PHOTO_LIMIT
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w
        ws.auto_filter.ref = f"A4:{get_column_letter(last_col)}{max(4, 4 + len(chosen))}"
        ws.oddHeader.left.text = sku
        ws.print_title_rows = "1:4"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_view.showGridLines = False
        ws.page_margins.left = 0.4
        ws.page_margins.right = 0.4
        ws.page_margins.top = 0.6
        ws.page_margins.bottom = 0.6

    summary.auto_filter.ref = f"A4:G{4 + len(sheet_rows)}"
    summary.freeze_panes = "A5"
    summary.auto_filter.ref = f"A4:G{max(4, 4 + len(sheet_rows))}"
    for col, w in enumerate([22, 42, 12, 12, 12, 16, 62], 1):
        summary.column_dimensions[get_column_letter(col)].width = w
    summary.row_dimensions[1].height = 24
    summary.sheet_view.showGridLines = False
    summary.page_setup.orientation = "landscape"
    summary.page_setup.fitToPage = True
    summary.page_setup.fitToWidth = 1
    summary.page_setup.fitToHeight = 1
    summary.sheet_properties.pageSetUpPr.fitToPage = True
    summary.page_setup.paperSize = summary.PAPERSIZE_A4
    summary.oddHeader.left.text = "Положительные отзывы"
    return wb, stats


def self_check() -> None:
    good = [
        ("Отличная практичная обувница, спасибо", "", ""),
        ("Легко собралась. Практичная. Советую", "", ""),
        ("Просто супер, очень удобная", "", ""),
        ("Быстрая доставка, легкая сборка, внешний вид супер", "", "Нет"),
        ("Все идеально, даже лишняя запчасть есть", "", ""),
        ("Хорошо", "", ""),
        ("Лёгкая, как в сборке, так и по весу, полный комплект", "", ""),
        ("Минусов нет, всё отлично", "", ""),
        ("Не сломалась, очень крепкая", "", ""),
        ("Без царапин, качество отличное", "", ""),
        ("Сначала заказала себе, так понравилась, что заказала и детям", "", ""),
        ("Не только красивая, но и очень удобная", "", ""),
        ("Мне нравится, спасибо", "", ""),
        ("Обувница сама по себе легкая, но качественная", "", ""),
        ("Собралась легко, а так вообще отличная", "", ""),
        ("Упакован был очень хорошо. Собирать легко, стоит устойчиво, не шатается.", "", ""),
    ]
    bad = [
        ("Хороший стеллаж, не доложили 2 заглушки", "", ""),
        ("Вещь удобная, но она сломалась", "", ""),
        ("Хлипкая конечно, но вроде стоит. За эту цену норм", "", ""),
        ("Вешалка легка в сборке но пришла поцарапанная", "", ""),
        ("Нормально", "", ""),
        ("Ок", "", ""),
        ("Неплохо", "", ""),
        ("По конструкции всё металлическое", "", ""),
        ("Собрала за час", "", ""),
        ("Красивая вешалка", "", "шатается полка"),
        ("", "", ""),
        ("👍🔥", "", ""),
        ("Отличная вещь, но долго везли", "", ""),
        ("Все ок", "", ""),
        ("Неудобная, хотя цвет красивый", "", ""),
        ("Вешалка хорошая, быстро собирается. недоложили 4 болта", "", ""),
        ("В целом хороший товар, если бы довезли аккуратно.", "", ""),
        ("Нормальная этажерка, но легко мнется", "", ""),
        ("Не очень устойчивая", "", ""),
        ("Товар хороший. При сборке были проблемы.", "", ""),
        ("Вешалка не лучшего качества, собрала быстро", "", ""),
        ("Норм. Вместительная", "", ""),
        ("Ручка слабовата, а так довольно удобно", "", ""),
        ("Прочная. Не удобно раздвигать", "", ""),
        ("К производителю момент: сделайте ключ потолще", "", ""),
        ("Коробка пришла повреждена, хорошо что вскрыла", "", ""),
        ("Нам не хватило болтов", "", ""),
        ("на четверочку, колеса не крутятся", "", ""),
        ("Баловство! Ищите более прочную!", "", ""),
        ("Удобная, но очень легкая", "", ""),
        ("Очень удобная, правда думала она больше будет", "", ""),
        ("За свои деньги, вполне даже ничего", "", ""),
        ("Пошире б отлично было бы", "", ""),
        ("Кроме не того цвета, все в порядке и крепкая", "", ""),
        ("Сняла одну звезду за ручку", "", ""),
        ("С виду красивая, но не устойчивая", "", ""),
        ("Немного не понравилось, как окрашены детали", "", ""),
        ("Пришла с оранжевыми пятнами, а так отличная", "", ""),
        ("металл тонкий, крючки пластмассовые", "", ""),
        ("Жаловаться не вижу смысла, вместительная", "", ""),
        ("За свои деньги и если не трогать его, пойдет", "", ""),
    ]
    for text, pros, cons in good:
        ok, _score = positivity(text, pros, cons)
        if not ok:
            raise SystemExit(f"self_check: отсеян положительный: {text}")
    for text, pros, cons in bad:
        ok, _score = positivity(text, pros, cons)
        if ok:
            raise SystemExit(f"self_check: пропущен неположительный: {text} / {cons}")
    if parse_photos("https://a.jpg https://b.webp") != ["https://a.jpg", "https://b.webp"]:
        raise SystemExit("parse_photos space")
    if parse_photos(None) or parse_photos(""):
        raise SystemExit("parse_photos empty")
    a5, a4, _note = allocate(100, 100)
    if (a5, a4) != (23, 7):
        raise SystemExit(f"allocate full: {(a5, a4)}")
    b5, b4, _note = allocate(100, 2)
    if (b5, b4) != (6, 2):
        raise SystemExit(f"allocate few fours: {(b5, b4)}")
    c5, c4, _note = allocate(10, 100)
    if c4 > 4 or c5 != 10:
        raise SystemExit(f"allocate few fives: {(c5, c4)}")
    d5, d4, _note = allocate(40, 0)
    if (d5, d4) != (30, 0):
        raise SystemExit(f"allocate only fives: {(d5, d4)}")


def main() -> None:
    self_check()
    if len(sys.argv) < 2:
        raise SystemExit("Укажите путь к xlsx")
    out = Path(sys.argv[1])
    import random

    by_sku, names, all_skus = load_reviews()
    kept = [
        (sku, row)
        for sku, pool in by_sku.items()
        for row in pool[5] + pool[4]
    ]
    random.seed(14)
    print(f"kept={len(kept)} skus_with_text={len(by_sku)} all_skus={len(all_skus)}")
    print("--- sample kept ---")
    for sku, row in random.sample(kept, min(24, len(kept))):
        text = row["text"].replace("\n", " ")
        if len(text) > 180:
            text = text[:180] + "…"
        print(f"{row['rating']}|{sku}|{text}")
    wb, stats = build_book(by_sku, names, all_skus)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(
        f"sku={stats['skus']} full={stats['full']} short={stats['short']} "
        f"only5={stats['only5']} only4={stats['only4']} reviews={stats['reviews']} "
        f"photos={stats.get('photos', 0)}/{stats.get('photo_urls', 0)} "
        f"file={out}"
    )


if __name__ == "__main__":
    main()
