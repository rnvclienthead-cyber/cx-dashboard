#!/usr/bin/env python3
"""Read-only production verification helpers for post-Codex audit."""
from sqlalchemy import text
from backend.database import engine

with engine.connect() as c:
    print("deployed:", open("/root/cx-dashboard/.deployed_commit").read().strip())

    rows = c.execute(
        text(
            """
        SELECT created_at, action, status, left(coalesce(details, ''), 140)
        FROM system_logs
        WHERE created_at > now() - interval '2 days'
          AND (
            lower(status) LIKE '%err%' OR lower(status) LIKE '%fail%'
            OR lower(action) LIKE '%sheet%' OR lower(action) LIKE '%photo%'
            OR lower(action) LIKE '%deploy%' OR lower(action) LIKE '%ozon_chat%'
          )
        ORDER BY created_at DESC
        LIMIT 20
        """
        )
    ).fetchall()
    print("recent err/sheet/photo/ozon_chat:", len(rows))
    for r in rows:
        print(r[0], r[1], r[2], (r[3] or "")[:120])

    print(
        "ozon_chats",
        c.execute(text("SELECT count(*) FROM ozon_chats")).scalar(),
        "msgs_3d",
        c.execute(
            text(
                "SELECT count(*) FROM ozon_chat_messages WHERE created_at > now() - interval '3 days'"
            )
        ).scalar(),
    )

    # handoff samples
    print("\n=== handoff log sample ===")
    for r in c.execute(
        text(
            """
        SELECT created_at, mp, theme, action, sent, need_human, shadow, left(coalesce(reason,''),80)
        FROM cx_chat_bot_log
        WHERE created_at > now() - interval '14 days'
          AND (action = 'handoff_to_manager' OR need_human = true OR theme = 'vozvrat')
        ORDER BY created_at DESC
        LIMIT 12
        """
        )
    ):
        print(r)

    # shadow should not dominate recent handoffs that claim sent
    bad = c.execute(
        text(
            """
        SELECT count(*) FROM cx_chat_bot_log
        WHERE created_at > now() - interval '14 days'
          AND action = 'handoff_to_manager'
          AND sent = true
          AND coalesce(shadow, false) = true
        """
        )
    ).scalar()
    print("handoff sent+shadow (should be 0):", bad)

    print("\n=== zapchasti/krepezh mp ===")
    for r in c.execute(
        text(
            """
        SELECT theme_key, coalesce(nullif(mp,''),'(empty)') mp, count(*),
               bool_or(body ILIKE '%ozon.ru%') has_ozon_link
        FROM cx_chat_variants
        WHERE theme_key IN ('zapchasti','krepezh')
        GROUP BY 1,2
        ORDER BY 1,2
        """
        )
    ):
        print(r)

    wb_ozon = c.execute(
        text(
            """
        SELECT id, theme_key, mp, left(body,100)
        FROM cx_chat_variants
        WHERE theme_key IN ('zapchasti','krepezh')
          AND coalesce(mp,'') IN ('','wb')
          AND body ILIKE '%ozon%'
        """
        )
    ).fetchall()
    print("wb/empty with ozon mention:", len(wb_ozon))
    for r in wb_ozon:
        print(r)

    print("\n=== yandex acts ===")
    try:
        n = c.execute(text("SELECT count(*) FROM yandex_dispatch_acts")).scalar()
        print("acts", n)
        for r in c.execute(
            text(
                """
            SELECT id, act_number, created_at, created_by
            FROM yandex_dispatch_acts
            ORDER BY id DESC LIMIT 5
            """
            )
        ):
            print(r)
    except Exception as e:
        print("acts err", e)
