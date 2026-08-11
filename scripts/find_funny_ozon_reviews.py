#!/usr/bin/env python3
"""Поиск забавных отзывов Ozon + ссылки и даты."""
from datetime import timezone, timedelta
from sqlalchemy import text
from backend.database import engine

MSK = timezone(timedelta(hours=3))

FUN_HINTS = [
    "кот", "кошк", "пёс", "собак", "муж", "жена", "тёща", "теща", "свекр",
    "ребён", "ребен", "малыш", "внук", "внуч", "бабуш", "дедуш",
    "ору", "оруу", "смех", "ржа", "угар", "ахаха", "лол", "😂", "🤣", "😅",
    "теперь живёт", "теперь живет", "поселился", "как будто", "будто",
    "лучш", "обожаю", "кайф", "кайфую", "влюбил",
    "соседи", "гости", "все спрашивают", "все думают",
    "сборка", "собрал", "собрала", "инструкц", "винт", "болт",
    "упаковк", "коробк", "доставк", "курьер",
    "головолом", "пазл", "лего", "конструктор", "экскурси",
    "зачем", "наконец", "мечт", "счасть", "восторг", "чудо",
    "подруг", "соседк", "мама", "папа", "сын", "доч",
]

EXCLUDE = [
    "брак", "сломал", "сломан", "вернул", "возврат", "обман", "развод",
    "ужас", "кошмар", "деньги верн", "не советую", "не покупайте",
    "плохое качество", "отвратитель", "мошен", "плохое", "ужасн",
]


def score(txt: str) -> int:
    t = (txt or "").lower()
    if len(t) < 40:
        return -1
    if any(x in t for x in EXCLUDE):
        return -1
    s = 0
    for h in FUN_HINTS:
        if h in t:
            s += 2
    if "!" in (txt or ""):
        s += 1
    if any(c in (txt or "") for c in "😂🤣😅😆🙈🔥💞"):
        s += 3
    if len(t) > 120:
        s += 1
    if len(t) > 250:
        s += 1
    # разговорный стиль
    for w in ("ахах", "ору", "ржу", "прикол", "смешно", "забавн", "мило"):
        if w in t:
            s += 3
    return s


def fmt(dt):
    if dt is None:
        return "—"
    if getattr(dt, "tzinfo", None) is None:
        # Ozon created_at часто без tz — считаем UTC как у WB API
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")


with engine.connect() as c:
    cols = [
        r[0]
        for r in c.execute(
            text(
                """
            SELECT column_name FROM information_schema.columns
            WHERE table_name='ozon_reviews' ORDER BY ordinal_position
            """
            )
        )
    ]
    print("cols:", ", ".join(cols))
    total = c.execute(text("SELECT count(*) FROM ozon_reviews")).scalar()
    pos = c.execute(
        text(
            "SELECT count(*) FROM ozon_reviews WHERE rating>=4 AND length(coalesce(review_text,''))>=50"
        )
    ).scalar()
    print(f"total={total} positive_long={pos}\n")

    rows = c.execute(
        text(
            """
        SELECT review_id, sku, supplier_article, product_name, rating,
               trim(both E'\\n' from
                 coalesce(nullif(trim(review_text),''), '') ||
                 CASE WHEN coalesce(trim(pros),'')<>'' THEN E'\\n+ '||pros ELSE '' END ||
                 CASE WHEN coalesce(trim(cons),'')<>'' THEN E'\\n- '||cons ELSE '' END
               ) AS body,
               created_at
        FROM ozon_reviews
        WHERE rating >= 4
          AND length(coalesce(review_text,'')) >= 50
          AND created_at >= CURRENT_DATE - INTERVAL '24 months'
        ORDER BY created_at DESC
        LIMIT 8000
        """
        )
    ).mappings().all()

scored = []
for r in rows:
    sc = score(r["body"])
    if sc >= 3:
        scored.append((sc, dict(r)))
scored.sort(key=lambda x: (-x[0], -len(x[1]["body"] or "")))

seen = set()
out = []
for sc, r in scored:
    key = (r["body"] or "")[:80].lower()
    if key in seen:
        continue
    seen.add(key)
    out.append((sc, r))
    if len(out) >= 40:
        break

print(f"scanned {len(rows)} → candidates {len(out)}\n")
for i, (sc, r) in enumerate(out, 1):
    body = (r["body"] or "").strip()
    if len(body) > 500:
        body = body[:500] + "…"
    sku = r["sku"]
    print(f"——— {i}. ★{r['rating']}  {r.get('supplier_article') or '—'}  score={sc}")
    print(f"date: {fmt(r['created_at'])}  raw={r['created_at']!r}")
    print(f"sku: {sku}  id: {r['review_id']}")
    print(f"product: {r.get('product_name') or '—'}")
    if sku:
        print(f"url: https://www.ozon.ru/product/{sku}/reviews/")
    print(body)
    print()
