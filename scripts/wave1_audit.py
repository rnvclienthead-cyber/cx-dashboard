#!/usr/bin/env python3
"""Audit wave-1 template cleanup targets on VPS."""
from sqlalchemy import text
from backend.database import engine

with engine.connect() as c:
    print("=== dokup / upsell-ish ===")
    rows = c.execute(text("""
      SELECT id, theme_key, coalesce(mp,'') mp, left(body,160) b
      FROM cx_chat_variants
      WHERE body ~* '(докуп|докап|заказать снова|будем рады|рекомендуем.*(купи|заказ)|версию с)'
      ORDER BY id
    """)).mappings().all()
    print("chat variants", len(rows))
    for r in rows:
        print(r["id"], r["theme_key"], r["mp"], "|", (r["b"] or "").replace("\n", " "))

    try:
        rows = c.execute(text("""
          SELECT id, theme_key, coalesce(mp,'') mp, left(body,160) b
          FROM cx_question_variants
          WHERE body ~* '(докуп|докап|заказать снова|будем рады|рекомендуем.*(купи|заказ))'
        """)).mappings().all()
        print("question variants", len(rows))
        for r in rows:
            print(r["id"], r["theme_key"], r["mp"], "|", (r["b"] or "").replace("\n", " "))
    except Exception as e:
        print("question variants err", e)

    print("\n=== greetings leading ===")
    n = c.execute(text("""
      SELECT count(*) FROM cx_chat_variants
      WHERE body ~* '^(Здравствуйте|Добрый день|Добрый вечер|Привет)'
    """)).scalar()
    print("count", n)

    print("\n=== mop/bucket assortment ===")
    cols = [r[0] for r in c.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_name='wb_assortment' ORDER BY 1
    """))]
    print("cols", cols)
    name_col = "name" if "name" in cols else ("sku_name" if "sku_name" in cols else None)
    q = """
      SELECT DISTINCT supplier_article, category_1
      FROM wb_assortment
      WHERE supplier_article ILIKE 'Mop%'
         OR category_1 ILIKE '%швабр%'
         OR category_1 ILIKE '%ведр%'
         OR supplier_article ILIKE '%ведр%'
    """
    if name_col:
        q = q.replace(
            "FROM wb_assortment",
            f", {name_col} FROM wb_assortment",
        )
        q += f" OR {name_col} ILIKE '%швабр%' OR {name_col} ILIKE '%ведр%'"
    q += " LIMIT 50"
    for r in c.execute(text(q)).mappings():
        print(dict(r))

    print("\n=== return_expired wb variants ===")
    for r in c.execute(text("""
      SELECT id, coalesce(mp,'(null)') mp, body FROM cx_chat_variants
      WHERE theme_key='return_expired'
    """)).mappings():
        print(r["id"], r["mp"], "|", (r["body"] or "")[:200].replace("\n", " "))
