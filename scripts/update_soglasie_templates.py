#!/usr/bin/env python3
"""Обновить шаблоны soglasie + проверить формат токена бота."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from backend.database import SessionLocal

V1 = (
    "Отлично! Заполните, пожалуйста, короткую форму — укажите, какая деталь нужна и куда её отправить. Займёт пару минут.\n\n"
    "Важно: в чате Wildberries ссылка обычно не открывается по нажатию. Скопируйте её целиком и вставьте в адресную строку браузера:\n"
    "{form_url}"
)
V2 = (
    "Хорошо, оформим! Опишите в форме, чего не хватает, и укажите адрес — это быстро.\n\n"
    "Ссылка в приложении WB часто не кликабельна: скопируйте её и откройте в браузере:\n"
    "{form_url}"
)

db = SessionLocal()
try:
    rows = db.execute(text("""
      SELECT id, sort_order, left(body, 80) FROM cx_chat_variants
      WHERE theme_key='soglasie' ORDER BY sort_order, id
    """)).fetchall()
    print("before:", rows)
    # Обновляем по sort_order 1 и 2; если нет — вставляем
    for sort_order, body in ((1, V1), (2, V2)):
        n = db.execute(text("""
          UPDATE cx_chat_variants SET body = :b, updated_at = now()
          WHERE theme_key = 'soglasie' AND coalesce(branch_key,'') = ''
            AND sort_order = :s
        """), {"b": body, "s": sort_order}).rowcount
        if not n:
            db.execute(text("""
              INSERT INTO cx_chat_variants (theme_key, branch_key, branch_label, body, sort_order, updated_by)
              VALUES ('soglasie', '', '', :b, :s, 'system')
            """), {"b": body, "s": sort_order})
            print("inserted sort", sort_order)
        else:
            print("updated sort", sort_order, "rows", n)
    db.commit()
    after = db.execute(text("""
      SELECT sort_order, body FROM cx_chat_variants
      WHERE theme_key='soglasie' ORDER BY sort_order, id
    """)).fetchall()
    for s, b in after:
        print("---", s)
        print(b)
finally:
    db.close()
