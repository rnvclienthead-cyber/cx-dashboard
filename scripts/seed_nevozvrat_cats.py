#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine, SessionLocal
from backend.routers import nonreturnable_cats

nonreturnable_cats.init_nonreturnable_cats(engine)
db = SessionLocal()
try:
    print("cats:", nonreturnable_cats.enabled_list(db))
    print("dryer:", nonreturnable_cats.is_nonreturnable(db, article="2-СБ-White"))
    row = db.execute(text(
        "SELECT supplier_article FROM wb_assortment WHERE category_1='Вешалки' LIMIT 1"
    )).fetchone()
    if row:
        print("hanger:", row[0], nonreturnable_cats.is_nonreturnable(db, article=row[0]))

    db.execute(text("""
        UPDATE cx_chat_themes SET
          zone = 'draft',
          note = 'Черновик. Привязка к категориям ассортимента.',
          when_text = '«Заказала две, одна лишняя», «не подошёл размер», «почему невозвратный». Также: товар из категории невозвратных (сушилки и др.).'
        WHERE theme_key = 'nevozvratny'
    """))
    db.execute(text("DELETE FROM cx_chat_variants WHERE theme_key = 'nevozvratny'"))
    db.execute(text("""
        INSERT INTO cx_chat_variants (theme_key, branch_key, branch_label, body, sort_order, updated_by)
        VALUES
          ('nevozvratny', '', '',
           'Этот товар невозвратный. Возвратность товара определяет сервис WB. От товара можно отказаться при получении.',
           1, 'system'),
          ('nevozvratny', '', '',
           'По этому товару возврат не предусмотрен — категорию возвратности определяет маркетплейс, мы на неё не влияем. Отказаться можно было при получении в пункте выдачи.',
           2, 'system')
    """))
    db.commit()
    print("zone:", db.execute(text(
        "SELECT zone FROM cx_chat_themes WHERE theme_key='nevozvratny'"
    )).scalar())
    print("ok")
finally:
    db.close()
