#!/usr/bin/env python3
"""Wave 2A / phase 4: enable the two-step return flow (idempotent).

Run manually on the target environment after reviewing the diff. The whole
change is committed atomically; this script does not touch ``nevozvratny``.
"""
from __future__ import annotations

from sqlalchemy import text

from backend.database import engine


THEME_NOTE = (
    "Сначала уточнить причину. После причины — менеджеру, "
    "без инструкции оформления."
)

VARIANTS = {
    "wb": (
        "Подскажите, пожалуйста, почему решили вернуть товар? Например: не подошёл "
        "размер, обнаружили брак, товар оказался лишним или вы передумали. "
        "Товар можно найти во вкладке «Покупки»."
    ),
    "ozon": (
        "Подскажите, пожалуйста, почему решили вернуть товар? Например: не подошёл "
        "размер, обнаружили брак, товар оказался лишним или вы передумали. "
        "Товар можно найти в разделе «Заказы»."
    ),
    "ym": (
        "Подскажите, пожалуйста, почему решили вернуть товар? Например: не подошёл "
        "размер, обнаружили брак, товар оказался лишним или вы передумали. "
        "Товар можно найти в разделе «Заказы»."
    ),
}


def main() -> None:
    with engine.begin() as conn:
        theme = conn.execute(text("""
            UPDATE cx_chat_themes
            SET zone = 'auto', action = 'ask_reason', note = :note,
                updated_at = now(), updated_by = 'wave2a_return_reason'
            WHERE theme_key = 'vozvrat'
        """), {"note": THEME_NOTE})
        if theme.rowcount != 1:
            raise RuntimeError("Expected exactly one cx_chat_themes row for vozvrat")

        for mp, body in VARIANTS.items():
            updated = conn.execute(text("""
                UPDATE cx_chat_variants
                SET branch_label = 'Уточнить причину', body = :body,
                    sort_order = 1, active = true, updated_at = now(),
                    updated_by = 'wave2a_return_reason'
                WHERE theme_key = 'vozvrat'
                  AND coalesce(branch_key, '') = 'ask_reason'
                  AND coalesce(mp, '') = :mp
            """), {"mp": mp, "body": body})
            if not updated.rowcount:
                conn.execute(text("""
                    INSERT INTO cx_chat_variants
                        (theme_key, branch_key, branch_label, body, sort_order,
                         active, mp, updated_by)
                    VALUES
                        ('vozvrat', 'ask_reason', 'Уточнить причину', :body, 1,
                         true, :mp, 'wave2a_return_reason')
                """), {"mp": mp, "body": body})

        counts = conn.execute(text("""
            SELECT mp, count(*) AS n
            FROM cx_chat_variants
            WHERE theme_key = 'vozvrat' AND branch_key = 'ask_reason'
              AND mp IN ('wb', 'ozon', 'ym') AND active
            GROUP BY mp ORDER BY mp
        """)).mappings().all()
        if {row["mp"] for row in counts} != set(VARIANTS):
            raise RuntimeError(f"Return reason variants verification failed: {counts}")

    print("wave2a_return_reason_apply done:", dict((r["mp"], r["n"]) for r in counts))


if __name__ == "__main__":
    main()
