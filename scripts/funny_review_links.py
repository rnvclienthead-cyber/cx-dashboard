#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine

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
labels = dict(ITEMS)

with engine.connect() as c:
    rows = c.execute(
        text(
            "SELECT id, supplier_article, nm_id, imt_id, valuation "
            "FROM wb_feedbacks WHERE id = ANY(:ids)"
        ),
        {"ids": ids},
    ).mappings().all()
by = {r["id"]: r for r in rows}

for fid, title in ITEMS:
    r = by.get(fid)
    if not r:
        print(title, "MISSING", fid)
        continue
    nm = r["nm_id"]
    art = r["supplier_article"]
    print(f"### {title}")
    print(f"- артикул: `{art}` · ★{r['valuation']} · id `{fid}`")
    print(f"- карточка: https://www.wildberries.ru/catalog/{nm}/detail.aspx")
    print(f"- отзывы: https://www.wildberries.ru/catalog/{nm}/feedbacks")
    print()
