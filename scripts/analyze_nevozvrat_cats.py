#!/usr/bin/env python3
"""Какие категории ассортимента получали ответ про невозвратный товар."""
from collections import Counter
from sqlalchemy import text
from backend.database import engine

with engine.connect() as c:
    rows = c.execute(text("""
      WITH msgs AS (
        SELECT DISTINCT m.chat_id
        FROM wb_chat_messages m
        WHERE m.sender = 'seller'
          AND m.text ILIKE '%невозвратн%'
      ),
      chats AS (
        SELECT c.chat_id, c.nm_id, c.good_name,
               coalesce(
                 (SELECT cl.supplier_article FROM wb_claims cl
                  WHERE cl.nm_id::text = c.nm_id::text
                    AND nullif(trim(cl.supplier_article),'') IS NOT NULL
                  LIMIT 1),
                 (SELECT f.supplier_article FROM wb_feedbacks f
                  WHERE f.nm_id::text = c.nm_id::text
                    AND nullif(trim(f.supplier_article),'') IS NOT NULL
                  LIMIT 1),
                 (SELECT l.supplier_article FROM wb_logistics l
                  WHERE l.nm_id::text = c.nm_id::text
                    AND nullif(trim(l.supplier_article),'') IS NOT NULL
                  LIMIT 1)
               ) AS article
        FROM msgs
        JOIN wb_chats c ON c.chat_id = msgs.chat_id
      )
      SELECT
        coalesce(nullif(trim(a.category_1),''),
                 nullif(trim(l.category),''),
                 '(нет категории)') AS cat,
        coalesce(nullif(trim(ch.article),''), '?') AS article,
        coalesce(a.name_ru, ch.good_name, '') AS name,
        ch.nm_id,
        count(DISTINCT ch.chat_id) AS chats
      FROM chats ch
      LEFT JOIN wb_assortment a
        ON lower(replace(a.supplier_article,' ','')) = lower(replace(ch.article,' ',''))
      LEFT JOIN LATERAL (
        SELECT category FROM wb_logistics
        WHERE nm_id::text = ch.nm_id::text AND nullif(trim(category),'') IS NOT NULL
        LIMIT 1
      ) l ON true
      GROUP BY 1,2,3,4
      ORDER BY chats DESC, cat, article
    """)).mappings().fetchall()

    by_cat = Counter()
    print("=== by product ===")
    for r in rows:
        by_cat[r["cat"]] += r["chats"]
        print(f"{r['chats']:3d} | {str(r['cat'])[:35]:35s} | nm={r['nm_id']} | {str(r['article'])[:22]:22s} | {str(r['name'])[:45]}")

    print("\n=== by category ===")
    for cat, n in by_cat.most_common():
        print(f"{n:4d}  {cat}")
    print("total chats:", sum(by_cat.values()), "products:", len(rows))

    # all categories in assortment for comparison
    print("\n=== all assortment categories ===")
    for r in c.execute(text("""
      SELECT coalesce(nullif(trim(category_1),''),'(пусто)') cat, count(*) n
      FROM wb_assortment GROUP BY 1 ORDER BY 2 DESC
    """)).mappings():
        print(f"{r['n']:4d}  {r['cat']}")
