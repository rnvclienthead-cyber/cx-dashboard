#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine

v1 = (
    "Здравствуйте! Статус доставки до пункта выдачи смотрите в карточке заказа в приложении. "
    "Если срок сдвинулся или статус долго не меняется — актуальные детали быстрее подскажут в поддержке: "
    "профиль → «Задать вопрос» → раздел про доставку. По самому товару (комплектация, сборка, качество) "
    "мы на связи здесь — напишите, поможем."
)
v2 = (
    "Здравствуйте! По пути до пункта выдачи информация обновляется в заказе на маркетплейсе. "
    "По срокам и задержке удобнее уточнить в поддержке: профиль → «Задать вопрос» → доставка. "
    "Если вопрос по товару — пишите нам, разберёмся."
)

with engine.begin() as c:
    rows = c.execute(text("""
        SELECT id, sort_order, left(body, 80)
        FROM cx_chat_variants
        WHERE theme_key = :k AND active
        ORDER BY sort_order, id
    """), {"k": "dostavka_wb"}).fetchall()
    print("before:", rows)
    ids = [r[0] for r in rows]
    if len(ids) >= 1:
        c.execute(text("""
            UPDATE cx_chat_variants
            SET body = :b, updated_at = now(), updated_by = :u
            WHERE id = :id
        """), {"b": v1, "u": "soften_dostavka_wb", "id": ids[0]})
    if len(ids) >= 2:
        c.execute(text("""
            UPDATE cx_chat_variants
            SET body = :b, updated_at = now(), updated_by = :u
            WHERE id = :id
        """), {"b": v2, "u": "soften_dostavka_wb", "id": ids[1]})
    after = c.execute(text("""
        SELECT id, sort_order, body
        FROM cx_chat_variants
        WHERE theme_key = :k AND active
        ORDER BY sort_order, id
    """), {"k": "dostavka_wb"}).fetchall()
    print("after:")
    for r in after:
        print(r[0], r[1], r[2][:120], "...")
