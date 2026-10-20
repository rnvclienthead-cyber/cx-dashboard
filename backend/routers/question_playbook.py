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

# Служебная тема для шаблонов без выбранной группы (не в чипах).
UNASSIGNED_THEME_KEY = "__unassigned__"

# Темы плейбука вопросов: только те, что реально использовались в ответах
# за последний месяц (остальные убраны 2026-08-21).
QUESTION_THEME_KEYS = (
    "nekomplekt", "brak", "sborka", "krepezh",
    "dostavka_wb", "vozvrat", "wrong_item", "nevozvratny",
    "zapchasti", "tech_otverstiya",
)

_PRUNE_UNUSED_THEMES = "question_playbook_prune_unused_30d_v1"

# Темы, которые по разбору ответов за 30 дней ни разу не брались из шаблонов.
_UNUSED_QUESTION_THEMES = (
    "utochnenie",
    "blagodarnost",
    "cheque",
    "dengi",
    "ekspluataciya",
    "parametry",
    "return_expired",
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
        for col_sql in (
            "ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'approved'",
            "ADD COLUMN IF NOT EXISTS created_by TEXT NOT NULL DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",
            "ADD COLUMN IF NOT EXISTS source_question_id TEXT NOT NULL DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS source_mp TEXT NOT NULL DEFAULT ''",
        ):
            conn.execute(text(f"ALTER TABLE cx_question_variants {col_sql}"))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cx_q_variants_review
            ON cx_question_variants (review_status)
            WHERE review_status = 'pending'
        """))
        conn.commit()

    _seed_from_chat_if_empty(engine)
    _hide_chat_menu_from_questions(engine)
    prune_unused_question_themes(engine)
    ensure_missing_from_chat(engine)
    ensure_unassigned_theme(engine)


def ensure_unassigned_theme(engine) -> None:
    with engine.connect() as conn:
        _ensure_unassigned_theme_sql(conn)
        conn.commit()


def _ensure_unassigned_theme_sql(conn) -> None:
    conn.execute(text("""
        INSERT INTO cx_question_themes
            (theme_key, name, when_text, zone, action, note, sort_order, active, updated_by)
        VALUES (:k, 'Без темы', '', 'human', '',
                'Служебная: шаблоны из вопроса без выбранной группы. Не показывать в чипах.',
                9990, false, 'system')
        ON CONFLICT (theme_key) DO UPDATE SET
            name = EXCLUDED.name,
            active = false,
            note = EXCLUDED.note
    """), {"k": UNASSIGNED_THEME_KEY})


def prune_unused_question_themes(engine) -> dict:
    """Удаляет темы/варианты вопросов, не использовавшиеся в ответах за месяц.

    Идемпотентно (маркер в cx_schema_migrations). Чатовый плейбук не трогает.
    """
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_schema_migrations (
                key        TEXT PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL DEFAULT now()
            )
        """))
        done = conn.execute(text(
            "SELECT 1 FROM cx_schema_migrations WHERE key = :k"
        ), {"k": _PRUNE_UNUSED_THEMES}).scalar()
        if done:
            conn.commit()
            return {"skipped": True}

        keys = list(_UNUSED_QUESTION_THEMES)
        deleted_variants = conn.execute(text("""
            DELETE FROM cx_question_variants
            WHERE theme_key IN :keys
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys}).rowcount or 0
        deleted_themes = conn.execute(text("""
            DELETE FROM cx_question_themes
            WHERE theme_key IN :keys
        """).bindparams(bindparam("keys", expanding=True)), {"keys": keys}).rowcount or 0

        keep = list(QUESTION_THEME_KEYS) + [UNASSIGNED_THEME_KEY]
        extra_v = conn.execute(text("""
            DELETE FROM cx_question_variants
            WHERE theme_key NOT IN :keep
        """).bindparams(bindparam("keep", expanding=True)), {"keep": keep}).rowcount or 0
        extra_t = conn.execute(text("""
            DELETE FROM cx_question_themes
            WHERE theme_key NOT IN :keep
        """).bindparams(bindparam("keep", expanding=True)), {"keep": keep}).rowcount or 0

        conn.execute(text("""
            INSERT INTO cx_schema_migrations (key, applied_at)
            VALUES (:k, now())
            ON CONFLICT (key) DO NOTHING
        """), {"k": _PRUNE_UNUSED_THEMES})
        conn.commit()
        result = {
            "deleted_variants": int(deleted_variants) + int(extra_v),
            "deleted_themes": int(deleted_themes) + int(extra_t),
        }
        logger.info("[QPlaybook] pruned unused themes: %s", result)
        return result


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


def _hide_chat_menu_from_questions(engine) -> None:
    """Меню 1–5 в вопросах на карточке не нужно — оставляем его только в чатах."""
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE cx_question_themes
            SET active = false, updated_at = now(), updated_by = 'hide-menu'
            WHERE theme_key = 'utochnenie' AND active
        """))
        conn.execute(text("""
            UPDATE cx_question_variants
            SET active = false, updated_at = now(), updated_by = 'hide-menu'
            WHERE theme_key = 'utochnenie' AND active
        """))
        conn.commit()


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
          AND v.theme_key <> 'utochnenie'
          AND v.theme_key <> :unassigned
          AND (
            :mp = 'all'
            OR coalesce(v.mp, '') = ''
            OR coalesce(v.mp, '') = :mp
          )
        ORDER BY t.sort_order, v.theme_key, v.sort_order, v.id
    """), {"mp": mp, "unassigned": UNASSIGNED_THEME_KEY}).mappings().all()

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

_TEMPLATE_EDITOR_ROLES = {"admin", "cs_manager", "moderator"}


def _username(user) -> str:
    if isinstance(user, dict):
        return (user.get("username") or "").strip()
    return (getattr(user, "username", None) or "").strip()


def _role(user) -> str:
    if isinstance(user, dict):
        return (user.get("role") or "").strip()
    return (getattr(user, "role", None) or "").strip()


def _require_template_editor(user) -> None:
    if _role(user) not in _TEMPLATE_EDITOR_ROLES:
        raise HTTPException(403, "Нет доступа к правке шаблонов вопросов")


def _strip_greeting_and_signature(body: str) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?im)^\s*здравствуйте[!.,\s]*\n*", "", text).strip()
    text = re.sub(r"(?im)^\s*добрый\s+(день|вечер)[!.,\s]*\n*", "", text).strip()
    text = re.split(r"(?im)\n*\s*александр,?\s+команда\s+видовито\s*$", text)[0].strip()
    return text


def _normalize_from_manager_theme_key(theme_key: Optional[str]) -> str:
    key = (theme_key or "").strip().lower()
    if not key or key in (UNASSIGNED_THEME_KEY, "none", "bez_temy", "без_темы"):
        return UNASSIGNED_THEME_KEY
    return key


def _create_theme_row(
    db: Session,
    *,
    name: str,
    when_text: str = "",
    note: str = "",
    theme_key: Optional[str] = None,
    username: str = "",
) -> str:
    from . import chat_playbook
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название группы")
    key = (theme_key or "").strip().lower() or chat_playbook._theme_key_from_name(name)
    key = re.sub(r"[^a-z0-9_]+", "_", key).strip("_")
    if not key or key == UNASSIGNED_THEME_KEY:
        raise HTTPException(400, "Не удалось сделать ключ темы")
    exists = db.execute(text(
        "SELECT 1 FROM cx_question_themes WHERE theme_key = :k"
    ), {"k": key}).scalar()
    if exists:
        base = key
        for i in range(2, 50):
            cand = f"{base}_{i}"
            if not db.execute(text(
                "SELECT 1 FROM cx_question_themes WHERE theme_key = :k"
            ), {"k": cand}).scalar():
                key = cand
                break
        else:
            raise HTTPException(400, "Тема с таким ключом уже есть")
    nxt = db.execute(text(
        "SELECT COALESCE(max(sort_order), 0) + 10 FROM cx_question_themes"
    )).scalar() or 10
    db.execute(text("""
        INSERT INTO cx_question_themes
            (theme_key, name, when_text, zone, action, note, sort_order, active, updated_by)
        VALUES (:k, :n, :w, 'draft', '', :note, :s, true, :u)
    """), {
        "k": key, "n": name, "w": (when_text or "").strip(),
        "note": (note or "").strip(), "s": nxt, "u": username,
    })
    return key


def _resolve_review_theme_key(
    db: Session,
    *,
    current_theme_key: str,
    payload: "VariantReview",
    username: str,
) -> str:
    new_name = (payload.new_theme_name or "").strip()
    if new_name:
        return _create_theme_row(
            db,
            name=new_name,
            when_text=payload.new_theme_when or "",
            theme_key=payload.new_theme_key,
            username=username,
        )
    chosen = (payload.theme_key or "").strip().lower()
    if chosen and chosen != UNASSIGNED_THEME_KEY:
        ok = db.execute(text(
            "SELECT 1 FROM cx_question_themes WHERE theme_key = :k AND theme_key <> :u"
        ), {"k": chosen, "u": UNASSIGNED_THEME_KEY}).scalar()
        if not ok:
            raise HTTPException(404, "Тема не найдена")
        return chosen
    if current_theme_key and current_theme_key != UNASSIGNED_THEME_KEY:
        return current_theme_key
    raise HTTPException(400, "Укажите тему или создайте новую")


class ThemeUpdate(BaseModel):
    name: Optional[str] = None
    when_text: Optional[str] = None
    zone: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None


class ThemeCreate(BaseModel):
    name: str
    when_text: str = ""
    note: str = ""
    theme_key: Optional[str] = None


class VariantIn(BaseModel):
    theme_key: str
    branch_key: str = ""
    branch_label: str = ""
    body: str
    active: bool = True
    mp: str = ""


class VariantFromQuestion(BaseModel):
    theme_key: str = ""
    body: str
    mp: str = "wb"
    question_id: str = ""
    branch_key: str = ""
    branch_label: str = ""


class VariantUpdate(BaseModel):
    body: Optional[str] = None
    active: Optional[bool] = None
    branch_label: Optional[str] = None
    mp: Optional[str] = None
    theme_key: Optional[str] = None


class VariantReview(BaseModel):
    status: str = "approved"
    body: Optional[str] = None
    theme_key: Optional[str] = None
    new_theme_name: Optional[str] = None
    new_theme_when: Optional[str] = None
    new_theme_key: Optional[str] = None


@router.get("")
def get_playbook(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    themes = db.execute(text("""
        SELECT theme_key, name, when_text, zone, action, note, sort_order, active
        FROM cx_question_themes ORDER BY sort_order, name
    """)).mappings().all()
    variants = db.execute(text("""
        SELECT id, theme_key, branch_key, branch_label, body, sort_order, active,
               coalesce(mp,'') AS mp,
               coalesce(review_status, 'approved') AS review_status,
               coalesce(created_by, '') AS created_by,
               created_at,
               coalesce(source_question_id, '') AS source_question_id,
               coalesce(source_mp, '') AS source_mp
        FROM cx_question_variants ORDER BY theme_key, mp, branch_key, sort_order, id
    """)).mappings().all()

    by_theme: dict = {}
    pending_count = 0
    for v in variants:
        row = dict(v)
        if row.get("created_at") is not None:
            row["created_at"] = row["created_at"].isoformat()
        if row.get("review_status") == "pending":
            pending_count += 1
        by_theme.setdefault(row["theme_key"], []).append(row)

    out_themes = []
    for t in themes:
        if t["theme_key"] == UNASSIGNED_THEME_KEY:
            continue
        out_themes.append({**dict(t), "variants": by_theme.get(t["theme_key"], [])})

    return {
        "themes": out_themes,
        "pending_count": pending_count,
        "source": "question-playbook",
        "note": "Копия плейбука чатов; правки здесь не влияют на чаты.",
    }


@router.post("/sync-from-chats")
def sync_from_chats(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Добавить недостающие темы/тексты из плейбука чатов (существующие не затирает)."""
    _require_template_editor(current_user)
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
    _require_template_editor(current_user)
    if theme_key == UNASSIGNED_THEME_KEY:
        raise HTTPException(400, "Служебную тему нельзя менять")
    fields = []
    params = {"k": theme_key, "u": _username(current_user)}
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


@router.post("/themes")
def create_theme(
    payload: ThemeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_template_editor(current_user)
    key = _create_theme_row(
        db,
        name=payload.name,
        when_text=payload.when_text or "",
        note=payload.note or "",
        theme_key=payload.theme_key,
        username=_username(current_user),
    )
    db.commit()
    return {"ok": True, "theme_key": key}


@router.post("/variants")
def create_variant(
    payload: VariantIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_template_editor(current_user)
    body = _strip_greeting_and_signature(payload.body)
    if not body:
        raise HTTPException(400, "Текст не может быть пустым")
    mp = (payload.mp or "").strip().lower()
    if mp not in ("", "wb", "ozon", "ym"):
        raise HTTPException(400, "mp: wb|ozon|ym|пусто")
    theme_ok = db.execute(text(
        "SELECT 1 FROM cx_question_themes WHERE theme_key = :k"
    ), {"k": payload.theme_key}).scalar()
    if not theme_ok:
        raise HTTPException(404, "Тема не найдена")
    sort = db.execute(text("""
        SELECT COALESCE(max(sort_order), 0) + 1 FROM cx_question_variants
        WHERE theme_key = :k
    """), {"k": payload.theme_key}).scalar()
    username = _username(current_user)
    review = "pending" if _role(current_user) == "cs_manager" else "approved"
    row = db.execute(text("""
        INSERT INTO cx_question_variants
            (theme_key, branch_key, branch_label, body, sort_order, active, mp,
             updated_by, review_status, created_by, created_at, source_mp)
        VALUES (:tk, :bk, :bl, :body, :s, :a, :mp, :u, :rs, :u, now(), :mp)
        RETURNING id
    """), {
        "tk": payload.theme_key, "bk": payload.branch_key or "",
        "bl": payload.branch_label or "", "body": body,
        "s": sort, "a": payload.active, "mp": mp,
        "u": username, "rs": review,
    }).fetchone()
    db.commit()
    return {"ok": True, "id": row[0], "review_status": review}


@router.post("/variants/from-question")
def create_variant_from_question(
    payload: VariantFromQuestion,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Сохранить ответ менеджера из вопроса как новый шаблон (сразу active, на проверку)."""
    _require_template_editor(current_user)
    body = _strip_greeting_and_signature(payload.body)
    if not body:
        raise HTTPException(400, "Текст не может быть пустым")
    mp = (payload.mp or "wb").strip().lower()
    if mp not in ("wb", "ozon", "ym"):
        raise HTTPException(400, "mp: wb|ozon|ym")
    _ensure_unassigned_theme_sql(db)
    theme_key = _normalize_from_manager_theme_key(payload.theme_key)
    if theme_key != UNASSIGNED_THEME_KEY:
        theme_ok = db.execute(text(
            "SELECT 1 FROM cx_question_themes WHERE theme_key = :k"
        ), {"k": theme_key}).scalar()
        if not theme_ok:
            raise HTTPException(404, "Тема не найдена")
    sort = db.execute(text("""
        SELECT COALESCE(max(sort_order), 0) + 1 FROM cx_question_variants
        WHERE theme_key = :k AND coalesce(mp,'') = :mp
    """), {"k": theme_key, "mp": mp}).scalar()
    username = _username(current_user)
    row = db.execute(text("""
        INSERT INTO cx_question_variants
            (theme_key, branch_key, branch_label, body, sort_order, active, mp,
             updated_by, review_status, created_by, created_at,
             source_question_id, source_mp)
        VALUES (:tk, :bk, :bl, :body, :s, true, :mp, :u, 'pending', :u, now(), :qid, :mp)
        RETURNING id
    """), {
        "tk": theme_key,
        "bk": payload.branch_key or "",
        "bl": payload.branch_label or "",
        "body": body,
        "s": sort,
        "mp": mp,
        "u": username,
        "qid": (payload.question_id or "").strip(),
    }).fetchone()
    db.commit()
    return {
        "ok": True,
        "id": row[0],
        "review_status": "pending",
        "theme_key": theme_key,
        "needs_theme": theme_key == UNASSIGNED_THEME_KEY,
    }


def _require_admin(user) -> None:
    if _role(user) not in {"admin"}:
        raise HTTPException(403, "Только для администратора")


@router.get("/variants/pending")
def list_pending_variants(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    rows = db.execute(text("""
        SELECT v.id, v.theme_key, t.name AS theme_name,
               v.branch_key, v.branch_label, v.body, v.active,
               coalesce(v.mp, '') AS mp,
               coalesce(v.review_status, 'approved') AS review_status,
               coalesce(v.created_by, '') AS created_by,
               v.created_at,
               coalesce(v.source_question_id, '') AS source_question_id,
               coalesce(v.source_mp, '') AS source_mp
        FROM cx_question_variants v
        LEFT JOIN cx_question_themes t ON t.theme_key = v.theme_key
        WHERE coalesce(v.review_status, 'approved') = 'pending'
        ORDER BY v.created_at DESC NULLS LAST, v.id DESC
    """)).mappings().all()
    items = []
    for r in rows:
        d = dict(r)
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
        d["needs_theme"] = (d.get("theme_key") or "") == UNASSIGNED_THEME_KEY
        if d["needs_theme"]:
            d["theme_name"] = "Без темы"
        items.append(d)
    return {"items": items, "count": len(items)}


@router.put("/variants/{variant_id}/review")
def review_variant(
    variant_id: int,
    payload: VariantReview,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    status = (payload.status or "approved").strip().lower()
    if status != "approved":
        raise HTTPException(400, "status: approved")
    row = db.execute(text("""
        SELECT theme_key FROM cx_question_variants WHERE id = :id
    """), {"id": variant_id}).fetchone()
    if not row:
        raise HTTPException(404, "Вариант не найден")
    username = _username(current_user)
    theme_key = _resolve_review_theme_key(
        db,
        current_theme_key=row[0] or "",
        payload=payload,
        username=username,
    )
    fields = [
        "review_status = :rs",
        "theme_key = :tk",
        "updated_at = now()",
        "updated_by = :u",
    ]
    params = {
        "id": variant_id,
        "rs": "approved",
        "tk": theme_key,
        "u": username,
    }
    if payload.body is not None:
        body = _strip_greeting_and_signature(payload.body)
        if not body:
            raise HTTPException(400, "Текст не может быть пустым")
        fields.append("body = :body")
        params["body"] = body
    res = db.execute(text(f"""
        UPDATE cx_question_variants SET {', '.join(fields)}
        WHERE id = :id
    """), params)
    if not res.rowcount:
        raise HTTPException(404, "Вариант не найден")
    db.commit()
    return {"ok": True, "theme_key": theme_key}


@router.patch("/variants/{variant_id}")
def update_variant(
    variant_id: int,
    payload: VariantUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_template_editor(current_user)
    fields = []
    params = {"id": variant_id, "u": _username(current_user)}
    for col in ("body", "active", "branch_label", "mp", "theme_key"):
        val = getattr(payload, col)
        if val is not None:
            if col == "body":
                val = _strip_greeting_and_signature(val)
                if not val:
                    raise HTTPException(400, "Текст не может быть пустым")
            fields.append(f"{col} = :{col}")
            params[col] = val
    if not fields:
        raise HTTPException(400, "Нечего обновлять")
    if _role(current_user) == "cs_manager" and payload.body is not None:
        fields.append("review_status = 'pending'")
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
    _require_template_editor(current_user)
    res = db.execute(text("DELETE FROM cx_question_variants WHERE id = :id"), {"id": variant_id})
    if not res.rowcount:
        raise HTTPException(404, "Вариант не найден")
    db.commit()
    return {"ok": True}
