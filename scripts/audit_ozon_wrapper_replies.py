"""Сколько автоответов Ozon ушло по шапке карточки, а не по вопросу клиента."""
from collections import Counter

from sqlalchemy import text

from backend.chat_bot import _rule_classify, _unwrap_client_question
from backend.database import engine

SQL = """
    SELECT id, chat_id, theme, branch_key, zone, confidence, sent,
           reason, client_message AS msg, created_at
    FROM cx_chat_bot_log
    WHERE coalesce(mp, 'wb') = 'ozon'
      AND sent = true
      AND client_message ILIKE :pat
    ORDER BY created_at DESC
"""


def main() -> None:
    with engine.connect() as conn:
        rows = conn.execute(text(SQL), {"pat": "%у меня вопрос по вашему товару%"}).mappings().all()
        total_sent = conn.execute(text("""
            SELECT count(*) FROM cx_chat_bot_log
            WHERE coalesce(mp,'wb')='ozon' AND sent=true
        """)).scalar()
    print(f"ozon auto-sent all: {total_sent}")
    print(f"ozon auto-sent with product wrapper: {len(rows)}")

    by_theme = Counter()
    mismatch = []
    empty_q = 0
    for r in rows:
        raw = r["msg"] or ""
        q = _unwrap_client_question(raw)
        by_theme[r["theme"] or "∅"] += 1
        old = r["theme"] or ""
        if not q:
            empty_q += 1
            mismatch.append({
                "kind": "только шапка",
                "sent": old,
                "branch": r["branch_key"],
                "would": "менеджеру/пусто",
                "q": "",
                "chat_id": r["chat_id"],
                "at": r["created_at"],
                "reason": (r["reason"] or "")[:80],
            })
            continue
        ruled = _rule_classify(q, "new", mp="ozon")
        new_theme = ruled[0] if ruled else "utochnenie"
        new_need = ruled[2] if ruled else False
        if new_theme != old or new_need:
            mismatch.append({
                "kind": "другая тема",
                "sent": old,
                "branch": r["branch_key"],
                "would": f"{new_theme}{' → менеджер' if new_need else ''}",
                "q": q[:160],
                "chat_id": r["chat_id"],
                "at": r["created_at"],
                "reason": (r["reason"] or "")[:80],
            })

    print(f"wrapper-only (no real question): {empty_q}")
    print(f"likely wrong: {len(mismatch)}")
    print("themes sent on wrapped msgs:", dict(by_theme))
    print("--- all wrapped autosent ---")
    for r in rows:
        q = _unwrap_client_question(r["msg"] or "")
        print(f"{r['created_at']} sent={r['theme']}/{r['branch_key']} q={q!r} raw_len={len(r['msg'] or '')}")
    for item in mismatch:
        print(
            f"{item['at']}  sent={item['sent']}/{item['branch']}  "
            f"should={item['would']}  q={item['q']!r}  chat={item['chat_id']}"
        )


if __name__ == "__main__":
    main()
