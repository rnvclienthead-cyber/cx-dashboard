#!/usr/bin/env python3
"""Поиск отзывов с потенциалом для юмористических постов."""
from sqlalchemy import text
from backend.database import engine

# эвристики «весёлого» текста (не жалобы на брак)
FUN_HINTS = [
    "кот", "кошк", "пёс", "собак", "муж", "жена", "тёща", "теща", "свекр",
    "ребён", "ребен", "малыш", "внук", "внуч", "бабуш", "дедуш",
    "ору", "оруу", "смех", "ржа", "угар", "ахаха", "лол", "😂", "🤣", "😅",
    "теперь живёт", "теперь живет", "поселился", "как будто", "будто",
    "лучш", "обожаю", "обожаю", "кайф", "кайфую", "влюбил",
    "соседи", "гости", "все спрашивают", "все думают",
    "раньше", "теперь", "вместо", "вместо шкафа", "вместо стола",
    "сборка", "собрал", "собрала", "инструкц", "винт", "болт",
    "упаковк", "коробк", "доставк", "курьер",
]

EXCLUDE = [
    "брак", "сломал", "сломан", "вернул", "возврат", "обман", "развод",
    "ужас", "кошмар", "деньги верн", "не советую", "не покупайте",
    "плохое качество", "отвратитель", "мошен",
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
    # живые маркеры
    if "!" in txt:
        s += 1
    if any(c in txt for c in "😂🤣😅😆🙈🔥"):
        s += 3
    if len(t) > 120:
        s += 1
    if len(t) > 250:
        s += 1
    return s


def pick(rows, limit=25):
    scored = []
    for r in rows:
        body = r.get("body") or ""
        sc = score(body)
        if sc >= 3:
            scored.append((sc, r))
    scored.sort(key=lambda x: (-x[0], -len(x[1].get("body") or "")))
    # dedupe by first 80 chars
    seen = set()
    out = []
    for sc, r in scored:
        key = (r.get("body") or "")[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((sc, r))
        if len(out) >= limit:
            break
    return out


with engine.connect() as c:
    wb = c.execute(text("""
      SELECT 'wb' AS mp, id::text AS rid, supplier_article, valuation AS rating,
             coalesce(nullif(trim(text),''), '') ||
             CASE WHEN coalesce(trim(pros),'')<>'' THEN E'\n+ '||pros ELSE '' END ||
             CASE WHEN coalesce(trim(cons),'')<>'' THEN E'\n- '||cons ELSE '' END AS body,
             created_date
      FROM wb_feedbacks
      WHERE valuation >= 4
        AND length(coalesce(text,'')) >= 50
        AND created_date >= CURRENT_DATE - INTERVAL '18 months'
      ORDER BY created_date DESC
      LIMIT 8000
    """)).mappings().all()

    ym = c.execute(text("""
      SELECT 'ym' AS mp, feedback_id AS rid, supplier_article, valuation AS rating,
             trim(both E'\n' from
               coalesce(nullif(trim(comment),''), '') ||
               CASE WHEN coalesce(trim(pro_text),'')<>'' THEN E'\n+ '||pro_text ELSE '' END ||
               CASE WHEN coalesce(trim(contra_text),'')<>'' THEN E'\n- '||contra_text ELSE '' END
             ) AS body,
             created_date
      FROM ym_feedbacks
      WHERE valuation >= 4
        AND (
          length(coalesce(comment,'')) >= 40
          OR length(coalesce(pro_text,'')) >= 40
        )
        AND created_date >= CURRENT_DATE - INTERVAL '18 months'
      ORDER BY created_date DESC
      LIMIT 4000
    """)).mappings().all()

    oz = c.execute(text("""
      SELECT 'ozon' AS mp, review_id AS rid, supplier_article, rating,
             trim(both E'\n' from
               coalesce(nullif(trim(review_text),''), '') ||
               CASE WHEN coalesce(trim(pros),'')<>'' THEN E'\n+ '||pros ELSE '' END ||
               CASE WHEN coalesce(trim(cons),'')<>'' THEN E'\n- '||cons ELSE '' END
             ) AS body,
             created_at AS created_date
      FROM ozon_reviews
      WHERE rating >= 4
        AND length(coalesce(review_text,'')) >= 50
        AND created_at >= CURRENT_DATE - INTERVAL '18 months'
      ORDER BY created_at DESC
      LIMIT 4000
    """)).mappings().all()

rows = [dict(r) for r in list(wb) + list(ym) + list(oz)]
print(f"scanned {len(rows)} positive reviews")
top = pick(rows, 30)
print(f"candidates {len(top)}\n")
for i, (sc, r) in enumerate(top, 1):
    body = (r["body"] or "").strip()
    if len(body) > 420:
        body = body[:420] + "…"
    print(f"——— {i}. [{r['mp'].upper()}] ★{r['rating']}  {r.get('supplier_article') or '—'}  score={sc}")
    print(body)
    print()
