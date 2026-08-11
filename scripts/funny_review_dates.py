#!/usr/bin/env python3
from datetime import timezone, timedelta
from sqlalchemy import text
from backend.database import engine

MSK = timezone(timedelta(hours=3))

ITEMS = [
    ("9AFu8GxWzdj8jURPUL20", "Зачем вы меня нашли"),
    ("OMAqJC8nuYJJEncy4zKr", "Котик на инструкции"),
    ("KAqLnx4YNDIIAk4ooTsS", "Экскурсия по городам"),
    ("Y2ttLxkLWTFJgAD2o4eN", "Творческий человек"),
    ("07oaXrAlhguNNUefnmgk", "Сборка с 2-летним сыном"),
    ("Bta3oqF9E0oI2T67giRE", "Головоломка"),
    ("0C8VAYot4tFy2B7U6eis", "Умная швабра"),
    ("PwMNJlYabVWIQ1BnKJC3", "Бабулек"),
    ("3c2bRdqdjLRLtvv5QZK3", "Шикарная ведущая"),
    ("Jiv60DyXIrzipa9HZTZa", "Подруга посоветовала"),
]

ids = [i[0] for i in ITEMS]


def fmt(dt):
    if dt is None:
        return "—"
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S МСК")


with engine.connect() as c:
    rows = c.execute(
        text(
            """
        SELECT id, supplier_article, nm_id, valuation, created_date, text
        FROM wb_feedbacks WHERE id = ANY(:ids)
        """
        ),
        {"ids": ids},
    ).mappings().all()

by = {r["id"]: r for r in rows}
for fid, title in ITEMS:
    r = by[fid]
    snippet = (r["text"] or "").replace("\n", " ")[:90]
    print(f"### {title}")
    print(f"дата: {fmt(r['created_date'])}")
    print(f"raw: {r['created_date']!r}")
    print(f"art: {r['supplier_article']}  nm: {r['nm_id']}  ★{r['valuation']}")
    print(f"url: https://www.wildberries.ru/catalog/{r['nm_id']}/feedbacks")
    print(f"text: {snippet}")
    print()
