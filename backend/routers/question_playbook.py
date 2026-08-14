"""
Плейбук ответов на вопросы покупателей.

Стартует как копия плейбука чатов (темы + варианты), дальше живёт отдельно —
правки в вопросах не трогают чаты и наоборот.
"""
from __future__ import annotations

import logging
import random
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text, bindparam
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

logger = logging.getLogger("question_playbook")

router = APIRouter(prefix="/api/v1/question-playbook", tags=["question-playbook"])

ZONES = {"auto", "draft", "human"}

# Темы, полезные для Q&A на карточке (без «меню 1–5» и т.п.)
QUESTION_THEME_KEYS = (
    "nekomplekt", "brak", "sborka", "parametry", "krepezh",
    "ekspluataciya", "dostavka_wb", "cheque", "dengi", "vozvrat",
    "wrong_item", "return_expired", "nevozvratny", "blagodarnost",
    "utochnenie", "zapchasti",
)


def init_question_playbook_tables(engine) -> None:
    """Создаёт таблицы и один раз копирует активные темы/варианты из плейбука чатов."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_question_themes (
                theme_key  TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                when_text  TEXT    DEFAULT '',
                zone       TEXT    DEFAULT 'draft',
                action     TEXT    DEFAULT '',
                note       TEXT    DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                active     BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT now(),
                updated_by TEXT    DEFAULT ''
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_question_variants (
                id           SERIAL PRIMARY KEY,
                theme_key    TEXT NOT NULL,
                branch_key   TEXT    DEFAULT '',
                branch_label TEXT    DEFAULT '',
                body         TEXT NOT NULL,
                sort_order   INTEGER DEFAULT 0,
                active       BOOLEAN DEFAULT TRUE,
                mp           TEXT    NOT NULL DEFAULT '',
                updated_at   TIMESTAMP DEFAULT now(),
                updated_by   TEXT    DEFAULT ''
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cx_q_variants_theme
            ON cx_question_variants (theme_key, active)
        """))
        conn.commit()

    _seed_from_chat_if_empty(engine)


def _seed_from_chat_if_empty(engine) -> None:
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM cx_question_variants")).scalar() or 0
        if n:
            return
        chat_n = conn.execute(text("SELECT count(*) FROM cx_chat_variants")).scalar() or 0
        if not chat_n:
            logger.warning("[QPlaybook] cx_chat_variants пуст — сид вопросов пропущен")
            return

        keys = list(QUESTION_THEME_KEYS)
        conn.execute(text("""
            INSERT INTO cx_question_themes
                (theme_key, name, when_text, zone, action, note, sort_order, active, updated_by)
            SELECT theme_key, name, when_text, zone, action, note, sort_order, active, 'seed-from-chat'
            FROM cx_chat_themes
            WHERE theme_key IN :keys
            ON CONFLICT (theme_key) DO NOTHING
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys})

        conn.execute(text("""
            INSERT INTO cx_question_variants
                (theme_key, branch_key, branch_label, body, sort_order, active, mp, updated_by)
            SELECT v.theme_key, coalesce(v.branch_key,''), coalesce(v.branch_label,''),
                   regexp_replace(v.body, '\\{form_url\\}', '', 'g'),
                   v.sort_order, v.active, coalesce(v.mp,''), 'seed-from-chat'
            FROM cx_chat_variants v
            WHERE v.theme_key IN :keys
              AND v.active
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys})
        conn.commit()
        logger.info("[QPlaybook] seeded from chat playbook")


def ensure_missing_from_chat(engine) -> dict:
    """Добавляет темы/варианты, которых ещё нет в плейбуке вопросов (без затирания правок)."""
    added_themes = 0
    added_variants = 0
    keys = list(QUESTION_THEME_KEYS)
    with engine.connect() as conn:
        for row in conn.execute(text("""
            SELECT theme_key, name, when_text, zone, action, note, sort_order, active
            FROM cx_chat_themes
            WHERE theme_key IN :keys
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys}).mappings():
            exists = conn.execute(text(
                "SELECT 1 FROM cx_question_themes WHERE theme_key = :theme_key"
            ), {"theme_key": row["theme_key"]}).scalar()
            if exists:
                continue
            conn.execute(text("""
                INSERT INTO cx_question_themes
                    (theme_key, name, when_text, zone, action, note, sort_order, active, updated_by)
                VALUES (:theme_key, :name, :when_text, :zone, :action, :note, :sort_order, :active, 'sync-from-chat')
            """), dict(row))
            added_themes += 1

        chat_vars = conn.execute(text("""
            SELECT theme_key, coalesce(branch_key,'') AS branch_key,
                   coalesce(branch_label,'') AS branch_label,
                   regexp_replace(body, '\\{form_url\\}', '', 'g') AS body,
                   sort_order, active, coalesce(mp,'') AS mp
            FROM cx_chat_variants
            WHERE theme_key IN :keys AND active
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys}).mappings().all()

        for v in chat_vars:
            exists = conn.execute(text("""
                SELECT 1 FROM cx_question_variants
                WHERE theme_key = :theme_key
                  AND coalesce(branch_key,'') = :branch_key
                  AND coalesce(mp,'') = :mp
                  AND body = :body
                LIMIT 1
            """), dict(v)).scalar()
            if exists:
                continue
            conn.execute(text("""
                INSERT INTO cx_question_variants
                    (theme_key, branch_key, branch_label, body, sort_order, active, mp, updated_by)
                VALUES (:theme_key, :branch_key, :branch_label, :body, :sort_order, :active, :mp, 'sync-from-chat')
            """), dict(v))
            added_variants += 1
        conn.commit()
    return {"added_themes": added_themes, "added_variants": added_variants}


def get_zone(db: Session, theme_key: str) -> str:
    row = db.execute(text(
        "SELECT zone, active FROM cx_question_themes WHERE theme_key = :k"
    ), {"k": theme_key}).fetchone()
    if not row:
        # fallback на чатовый плейбук, если тема ещё не скопирована
        row = db.execute(text(
            "SELECT zone, active FROM cx_chat_themes WHERE theme_key = :k"
        ), {"k": theme_key}).fetchone()
    if not row or not row[1]:
        return "human"
    return row[0] if row[0] in ZONES else "human"


def pick_variant(db: Session, theme_key: str, branch_key: str = "",
                 mp: str = "wb") -> Optional[str]:
    """Активный вариант из плейбука вопросов; если пусто — fallback на чаты."""
    mp = (mp or "wb").lower()
    body = _pick_from(db, "cx_question_variants", theme_key, branch_key, mp)
    if body:
        return body
    # fallback: чатовый плейбук (на случай новой темы до синка)
    from . import chat_playbook
    return chat_playbook.pick_variant(db, theme_key, branch_key, mp=mp)


def _pick_from(db: Session, table: str, theme_key: str, branch_key: str, mp: str) -> Optional[str]:
    if table not in ("cx_question_variants", "cx_chat_variants"):
        return None
    if mp in ("ozon", "ym"):
        rows = db.execute(text(f"""
            SELECT body FROM {table}
            WHERE theme_key = :k AND branch_key = :b AND active
              AND coalesce(mp,'') = :mp
            ORDER BY sort_order
        """), {"k": theme_key, "b": branch_key, "mp": mp}).fetchall()
        if rows:
            return random.choice([r[0] for r in rows])
        rows = db.execute(text(f"""
            SELECT body FROM {table}
            WHERE theme_key = :k AND branch_key = :b AND active
              AND coalesce(mp,'') IN ('', 'wb')
            ORDER BY sort_order
        """), {"k": theme_key, "b": branch_key}).fetchall()
        if rows:
            return random.choice([r[0] for r in rows])
        return None

    rows = db.execute(text(f"""
        SELECT body FROM {table}
        WHERE theme_key = :k AND branch_key = :b AND active
          AND coalesce(mp,'') IN ('', 'wb')
        ORDER BY sort_order
    """), {"k": theme_key, "b": branch_key}).fetchall()
    if not rows:
        # любая ветка темы
        rows = db.execute(text(f"""
            SELECT body FROM {table}
            WHERE theme_key = :k AND active
              AND coalesce(mp,'') IN ('', 'wb', :mp)
            ORDER BY sort_order, id
            LIMIT 5
        """), {"k": theme_key, "mp": mp}).fetchall()
    if not rows:
        return None
    return random.choice([r[0] for r in rows])


def list_chip_templates(db: Session, mp: str = "wb") -> list[dict]:
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym", "all"):
        mp = "wb"
    rows = db.execute(text("""
        SELECT v.id, v.theme_key, t.name AS theme_name, v.branch_key, v.branch_label,
               v.body, coalesce(v.mp, '') AS mp
        FROM cx_question_variants v
        JOIN cx_question_themes t ON t.theme_key = v.theme_key
        WHERE v.active AND t.active
          AND (
            :mp = 'all'
            OR coalesce(v.mp, '') = ''
            OR coalesce(v.mp, '') = :mp
          )
        ORDER BY t.sort_order, v.theme_key, v.sort_order, v.id
    """), {"mp": mp}).mappings().all()

    items = []
    for r in rows:
        body = re.sub(r"\{form_url\}", "", r["body"] or "").strip()
        if not body:
            continue
        key = r["theme_key"]
        title = r["theme_name"] or key
        if r.get("branch_label"):
            title = f"{title}: {r['branch_label']}"
        items.append({
            "id": f"qp-{r['id']}",
            "title": title,
            "body": body,
            "theme_key": key,
            "theme_name": r["theme_name"] or key,
            "branch_key": r.get("branch_key") or "",
            "branch_label": r.get("branch_label") or "",
            "mp": r["mp"] or "all",
            "item_type": "question",
            "source": "question-playbook",
        })
    return items


# ── API ──────────────────────────────────────────────────────────────────────

class ThemeUpdate(BaseModel):
    name: Optional[str] = None
    when_text: Optional[str] = None
    zone: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None


class VariantIn(BaseModel):
    theme_key: str
    branch_key: str = ""
    branch_label: str = ""
    body: str
    active: bool = True
    mp: str = ""


class VariantUpdate(BaseModel):
    body: Optional[str] = None
    active: Optional[bool] = None
    branch_label: Optional[str] = None
    mp: Optional[str] = None
    theme_key: Optional[str] = None


@router.get("")
def get_playbook(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    themes = db.execute(text("""
        SELECT theme_key, name, when_text, zone, action, note, sort_order, active
        FROM cx_question_themes ORDER BY sort_order, name
    """)).mappings().all()
    variants = db.execute(text("""
        SELECT id, theme_key, branch_key, branch_label, body, sort_order, active,
               coalesce(mp,'') AS mp
        FROM cx_question_variants ORDER BY theme_key, mp, branch_key, sort_order, id
    """)).mappings().all()

    by_theme: dict = {}
    for v in variants:
        by_theme.setdefault(v["theme_key"], []).append(dict(v))

    return {
        "themes": [{**dict(t), "variants": by_theme.get(t["theme_key"], [])} for t in themes],
        "source": "question-playbook",
        "note": "Копия плейбука чатов; правки здесь не влияют на чаты.",
    }


@router.post("/sync-from-chats")
def sync_from_chats(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Добавить недостающие темы/тексты из плейбука чатов (существующие не затирает)."""
    from ..database import engine
    result = ensure_missing_from_chat(engine)
    return {"ok": True, **result}


@router.patch("/themes/{theme_key}")
def update_theme(
    theme_key: str,
    payload: ThemeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    fields = []
    params = {"k": theme_key, "u": getattr(current_user, "username", "") or ""}
    for col in ("name", "when_text", "zone", "note", "active"):
        val = getattr(payload, col)
        if val is not None:
            fields.append(f"{col} = :{col}")
            params[col] = val
    if not fields:
        raise HTTPException(400, "Нечего обновлять")
    res = db.execute(text(f"""
        UPDATE cx_question_themes SET {', '.join(fields)},
               updated_at = now(), updated_by = :u
        WHERE theme_key = :k
    """), params)
    if not res.rowcount:
        raise HTTPException(404, "Тема не найдена")
    db.commit()
    return {"ok": True}


@router.post("/variants")
def create_variant(
    payload: VariantIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    sort = db.execute(text("""
        SELECT COALESCE(max(sort_order), 0) + 1 FROM cx_question_variants
        WHERE theme_key = :k
    """), {"k": payload.theme_key}).scalar()
    row = db.execute(text("""
        INSERT INTO cx_question_variants
            (theme_key, branch_key, branch_label, body, sort_order, active, mp, updated_by)
        VALUES (:tk, :bk, :bl, :body, :s, :a, :mp, :u)
        RETURNING id
    """), {
        "tk": payload.theme_key, "bk": payload.branch_key or "",
        "bl": payload.branch_label or "", "body": payload.body,
        "s": sort, "a": payload.active, "mp": payload.mp or "",
        "u": getattr(current_user, "username", "") or "",
    }).fetchone()
    db.commit()
    return {"ok": True, "id": row[0]}


@router.patch("/variants/{variant_id}")
def update_variant(
    variant_id: int,
    payload: VariantUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    fields = []
    params = {"id": variant_id, "u": getattr(current_user, "username", "") or ""}
    for col in ("body", "active", "branch_label", "mp", "theme_key"):
        val = getattr(payload, col)
        if val is not None:
            fields.append(f"{col} = :{col}")
            params[col] = val
    if not fields:
        raise HTTPException(400, "Нечего обновлять")
    res = db.execute(text(f"""
        UPDATE cx_question_variants SET {', '.join(fields)},
               updated_at = now(), updated_by = :u
        WHERE id = :id
    """), params)
    if not res.rowcount:
        raise HTTPException(404, "Вариант не найден")
    db.commit()
    return {"ok": True}


@router.delete("/variants/{variant_id}")
def delete_variant(
    variant_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    res = db.execute(text("DELETE FROM cx_question_variants WHERE id = :id"), {"id": variant_id})
    if not res.rowcount:
        raise HTTPException(404, "Вариант не найден")
    db.commit()
    return {"ok": True}
