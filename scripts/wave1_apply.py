#!/usr/bin/env python3
"""Wave 1: apply template/DB cleanups on VPS (idempotent)."""
from __future__ import annotations

import re
from sqlalchemy import text
from backend.database import engine

GREET_RE = re.compile(
    r"^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*",
    re.I,
)

WB_RETURN_EXPIRED = (
    "Если срок возврата истёк и оформить заявку в кабинете не получается — "
    "напишите в поддержку Wildberries: профиль → «Задать вопрос», укажите номер "
    "заказа, опишите проблему и приложите фото. Решение принимает площадка."
)

NOTE_RETURN_EXPIRED = (
    "WB: «Покупки» / поддержка WB. Ozon: не давить «Заказы» — в поддержку Ozon. "
    "Решение за площадкой."
)

NOTE_NEVOZ = (
    "Правило только WB (сушилки и др. по списку категорий). На Ozon/ЯМ не применять."
)

WHEN_EKSP = (
    "Только швабры/вёдра Видовито (артикулы Mop-*): собрано, но не увлажняется, "
    "не крутится, вода не сливается. На другие товары не применять."
)

NOTE_EKSP = "Видео и текст — только для швабр/вёдер. Иначе → меню уточнения."


def strip_greet(body: str) -> str:
    t = body or ""
    t2 = GREET_RE.sub("", t, count=1).lstrip(" \t\r\n")
    # если после приветствия шла пустая строка
    t2 = re.sub(r"^\n+", "", t2)
    return t2


def main():
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE cx_chat_themes SET note=:n, updated_at=now() "
                "WHERE theme_key='return_expired'"
            ),
            {"n": NOTE_RETURN_EXPIRED},
        )
        c.execute(
            text(
                "UPDATE cx_chat_themes SET note=:n, updated_at=now() "
                "WHERE theme_key='nevozvratny'"
            ),
            {"n": NOTE_NEVOZ},
        )
        c.execute(
            text(
                "UPDATE cx_chat_themes SET when_text=:w, note=:n, updated_at=now() "
                "WHERE theme_key='ekspluataciya'"
            ),
            {"w": WHEN_EKSP, "n": NOTE_EKSP},
        )

        # Пустой / Ozon-текст в WB return_expired → нормальный WB-текст
        c.execute(
            text(
                """
            UPDATE cx_chat_variants
            SET body = :body
            WHERE theme_key = 'return_expired'
              AND (coalesce(mp,'') IN ('', 'wb'))
              AND (
                coalesce(trim(body),'') = ''
                OR body ILIKE '%поддержку Ozon%'
                OR body ILIKE '%раздел «Заказы»%'
              )
            """
            ),
            {"body": WB_RETURN_EXPIRED},
        )
        # Универсальные (mp null) return_expired без Ozon — тоже на WB-формулировку
        c.execute(
            text(
                """
            UPDATE cx_chat_variants
            SET body = :body
            WHERE theme_key = 'return_expired'
              AND coalesce(mp,'') = ''
              AND body ILIKE '%поддержку маркетплейса%'
            """
            ),
            {"body": WB_RETURN_EXPIRED},
        )

        # Убрать upsell «заказать снова»
        c.execute(
            text(
                """
            UPDATE cx_chat_variants
            SET body = regexp_replace(
              body,
              '\\s*Если решите заказать снова,? мы будем рады\\.?\\s*',
              '',
              'gi'
            )
            WHERE body ~* 'заказать снова'
            """
            )
        )
        try:
            c.execute(
                text(
                    """
                UPDATE cx_question_variants
                SET body = regexp_replace(
                  body,
                  '\\s*Если решите заказать снова,? мы будем рады\\.?\\s*',
                  '',
                  'gi'
                )
                WHERE body ~* 'заказать снова'
                """
                )
            )
        except Exception as e:
            print("question variants skip:", e)

        # Удалить Ozon-варианты темы nevozvratny (тема WB-only)
        deleted = c.execute(
            text(
                "DELETE FROM cx_chat_variants WHERE theme_key='nevozvratny' AND mp IN ('ozon','ym')"
            )
        ).rowcount
        print("deleted ozon/ym nevozvratny variants:", deleted)

        # Strip leading greetings from all chat variants
        rows = c.execute(text("SELECT id, body FROM cx_chat_variants")).mappings().all()
        n_strip = 0
        for r in rows:
            body = r["body"] or ""
            new = strip_greet(body)
            if new != body:
                c.execute(
                    text("UPDATE cx_chat_variants SET body=:b WHERE id=:i"),
                    {"b": new, "i": r["id"]},
                )
                n_strip += 1
        print("stripped greetings in chat variants:", n_strip)

        try:
            rows = c.execute(text("SELECT id, body FROM cx_question_variants")).mappings().all()
            n_q = 0
            for r in rows:
                body = r["body"] or ""
                new = strip_greet(body)
                if new != body:
                    c.execute(
                        text("UPDATE cx_question_variants SET body=:b WHERE id=:i"),
                        {"b": new, "i": r["id"]},
                    )
                    n_q += 1
            print("stripped greetings in question variants:", n_q)
        except Exception as e:
            print("question greet strip skip:", e)

    print("wave1_apply done")


if __name__ == "__main__":
    main()
