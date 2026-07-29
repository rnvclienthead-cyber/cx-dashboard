"""
База знаний ответов на отзывы («Конструктор Vidovito»).

Детерминированная логика подстановки ответов на основе таблицы Рустама:
  ответ = Приветствие + Эмпатия + Решение + Энергия + Прощание

- группа определяется по supplier_article,
- категория проблемы — по совпадению триггеров и оценки,
- приветствие/прощание — по тону (позитив 4-5 / проблема 1-3).

Эти же данные используются как «знания» для ИИ-бота при генерации (grounding).
"""
import re
import random
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])


# ── Инициализация таблиц ─────────────────────────────────────────────────────

def init_kb_tables(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_kb_groups (
                id       SERIAL PRIMARY KEY,
                group_no INTEGER UNIQUE,
                name     TEXT NOT NULL
            )"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_kb_articles (
                article     TEXT PRIMARY KEY,
                article_raw TEXT,
                group_id    INTEGER REFERENCES cx_kb_groups(id) ON DELETE CASCADE
            )"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_kb_rules (
                id           SERIAL PRIMARY KEY,
                group_id     INTEGER REFERENCES cx_kb_groups(id) ON DELETE CASCADE,
                problem      TEXT,
                triggers     TEXT DEFAULT '',
                ratings      TEXT DEFAULT '',
                empathy      TEXT DEFAULT '',
                solution     TEXT DEFAULT '',
                energy       TEXT DEFAULT '',
                manual_check BOOLEAN DEFAULT FALSE,
                sort_order   INTEGER DEFAULT 0,
                active       BOOLEAN DEFAULT TRUE
            )"""))
        conn.execute(text("ALTER TABLE cx_kb_rules ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_kb_phrases (
                id         SERIAL PRIMARY KEY,
                kind       TEXT,
                situation  TEXT,
                variant_no INTEGER,
                text       TEXT
            )"""))
        conn.commit()


def _norm(a: str) -> str:
    return re.sub(r"\s+", "", a or "").lower()


# ── Импорт seed ──────────────────────────────────────────────────────────────

class KBSeed(BaseModel):
    groups: list
    articles: list
    rules: list
    phrases: list


@router.post("/import")
def import_kb(seed: KBSeed, db: Session = Depends(get_db),
              current_user: dict = Depends(get_current_user)):
    """Полная замена базы знаний из seed-JSON (группы, артикулы, правила, фразы)."""
    with db.bind.connect() as conn:
        conn.execute(text("DELETE FROM cx_kb_articles"))
        conn.execute(text("DELETE FROM cx_kb_rules"))
        conn.execute(text("DELETE FROM cx_kb_phrases"))
        conn.execute(text("DELETE FROM cx_kb_groups"))

        gid_by_no = {}
        for g in seed.groups:
            row = conn.execute(text(
                "INSERT INTO cx_kb_groups (group_no, name) VALUES (:n, :nm) RETURNING id"
            ), {"n": g["group_no"], "nm": g["name"]}).first()
            gid_by_no[g["group_no"]] = row[0]

        for a in seed.articles:
            gid = gid_by_no.get(a["group_no"])
            if not gid:
                continue
            conn.execute(text(
                "INSERT INTO cx_kb_articles (article, article_raw, group_id) "
                "VALUES (:a, :ar, :g) ON CONFLICT (article) DO UPDATE SET group_id = :g"
            ), {"a": _norm(a["article"]), "ar": a["article"], "g": gid})

        for r in seed.rules:
            gid = gid_by_no.get(r["group_no"])
            if not gid:
                continue
            conn.execute(text("""
                INSERT INTO cx_kb_rules
                    (group_id, problem, triggers, ratings, empathy, solution, energy, manual_check, sort_order)
                VALUES (:g, :p, :tr, :ra, :em, :so, :en, :mc, :ord)
            """), {
                "g": gid, "p": r["problem"],
                "tr": "\n".join(r.get("triggers", [])),
                "ra": ",".join(str(x) for x in r.get("ratings", [])),
                "em": r.get("empathy", ""), "so": r.get("solution", ""),
                "en": r.get("energy", ""), "mc": bool(r.get("manual_check")),
                "ord": r.get("sort_order", 0),
            })

        for p in seed.phrases:
            conn.execute(text(
                "INSERT INTO cx_kb_phrases (kind, situation, variant_no, text) "
                "VALUES (:k, :s, :v, :t)"
            ), {"k": p["kind"], "s": p["situation"], "v": p.get("variant_no", 1), "t": p["text"]})

        conn.commit()

    return {"status": "ok", "groups": len(seed.groups), "articles": len(seed.articles),
            "rules": len(seed.rules), "phrases": len(seed.phrases)}


# ── Чтение (для UI управления) ───────────────────────────────────────────────

@router.get("")
def get_kb(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    with db.bind.connect() as conn:
        groups = conn.execute(text(
            "SELECT id, group_no, name FROM cx_kb_groups ORDER BY group_no")).mappings().all()
        rules = conn.execute(text(
            "SELECT id, group_id, problem, triggers, ratings, empathy, solution, energy, "
            "manual_check, sort_order, COALESCE(active, true) AS active "
            "FROM cx_kb_rules ORDER BY group_id, sort_order")).mappings().all()
        phrases = conn.execute(text(
            "SELECT id, kind, situation, variant_no, text FROM cx_kb_phrases "
            "ORDER BY kind, situation, variant_no")).mappings().all()
        art_cnt = conn.execute(text(
            "SELECT group_id, COUNT(*) AS c FROM cx_kb_articles GROUP BY group_id")).mappings().all()
    art_map = {r["group_id"]: r["c"] for r in art_cnt}
    return {
        "groups":  [dict(g, articles=art_map.get(g["id"], 0)) for g in groups],
        "rules":   [dict(r) for r in rules],
        "phrases": [dict(p) for p in phrases],
    }


# ── Конструктор ответа ───────────────────────────────────────────────────────

def _pick(seq):
    return random.choice(seq) if seq else ""


TEMPLATE_SIGNERS = ["Ольга", "Софья"]

NO_TEXT_GROUP_NO = 0  # спец-группа шаблонов для отзывов без текста


def _build_no_text(db, rating: int, signer=None):
    """Шаблоны для отзывов без текста (только оценка). Возвращает tuple или None."""
    with db.bind.connect() as conn:
        gid = conn.execute(text(
            "SELECT id FROM cx_kb_groups WHERE group_no = :n"), {"n": NO_TEXT_GROUP_NO}).scalar()
        if not gid:
            return None
        rules = conn.execute(text(
            "SELECT problem, ratings, empathy, solution, energy, manual_check "
            "FROM cx_kb_rules WHERE group_id = :g AND COALESCE(active, true)"
        ), {"g": gid}).mappings().all()
        prows = conn.execute(text(
            "SELECT kind, situation, text FROM cx_kb_phrases WHERE kind IN ('greeting','farewell')"
        )).mappings().all()

    def rating_ok(r):
        rs = [x for x in (r["ratings"] or "").split(",") if x]
        return not rs or str(rating) in rs

    cands = [r for r in rules if rating_ok(r)]
    if not cands:
        return None
    matched = random.choice(cands)

    pm = {}
    for pr in prows:
        pm.setdefault((pr["kind"], pr["situation"]), []).append(pr["text"])
    situation = "positive" if rating >= 4 else "problem"
    greeting = random.choice(pm.get(("greeting", situation)) or [""])
    farewell = random.choice(pm.get(("farewell", situation)) or [""])
    parts = [greeting, matched["empathy"], matched["solution"], matched["energy"], farewell]
    answer = " ".join(p.strip() for p in parts if p and p.strip())
    if signer:
        answer += f"\n\n{signer}, команда Видовито"
    return answer, f"Без текста · {matched['problem']}", bool(matched["manual_check"]), {"no_text": True}


def build_answer(db, supplier_article: str, review_text: str, rating, signer=None):
    """Возвращает (answer, scenario_label, manual_check, meta) или (None, ...) если группа не найдена.
    signer — имя менеджера для подписи (напр. «Ольга»); None = без подписи."""
    try:
        rating = int(rating) if rating not in (None, "", "None") else 0
    except (TypeError, ValueError):
        rating = 0
    text_low = (review_text or "").lower()
    na = _norm(supplier_article)

    # Отзыв без текста (только оценка) — глобальные шаблоны из группы «Без текста» (group_no=0)
    if not (review_text or "").strip():
        nt = _build_no_text(db, rating, signer)
        if nt:
            return nt

    with db.bind.connect() as conn:
        # группа: точное совпадение, иначе по префиксу
        grow = conn.execute(text(
            "SELECT g.id, g.name FROM cx_kb_articles a "
            "JOIN cx_kb_groups g ON g.id = a.group_id WHERE a.article = :a"
        ), {"a": na}).mappings().first()
        if not grow and na:
            grow = conn.execute(text(
                "SELECT g.id, g.name FROM cx_kb_articles a "
                "JOIN cx_kb_groups g ON g.id = a.group_id "
                "WHERE :a LIKE a.article || '%' OR a.article LIKE :a || '%' LIMIT 1"
            ), {"a": na}).mappings().first()
        if not grow:
            return None, "Группа не определена", True, {}

        gid, gname = grow["id"], grow["name"]
        rules = conn.execute(text(
            "SELECT problem, triggers, ratings, empathy, solution, energy, manual_check, sort_order, "
            "COALESCE(active, true) AS active "
            "FROM cx_kb_rules WHERE group_id = :g ORDER BY sort_order"
        ), {"g": gid}).mappings().all()
        rules = [dict(r) for r in rules]

        prows = conn.execute(text(
            "SELECT kind, situation, text FROM cx_kb_phrases")).mappings().all()

    phrase_map = {}
    for pr in prows:
        phrase_map.setdefault((pr["kind"], pr["situation"]), []).append(pr["text"])

    def phrases(kind, situation):
        return phrase_map.get((kind, situation), [])

    def rating_ok(r):
        rs = [x for x in (r["ratings"] or "").split(",") if x]
        return not rs or str(rating) in rs

    applicable = [r for r in rules if rating_ok(r) and r.get("active", True)]

    # 1) определяем КАТЕГОРИЮ (проблему) по совпадению триггеров
    category = None
    for r in applicable:
        trigs = [t.strip().lower() for t in (r["triggers"] or "").split("\n") if t.strip()]
        if trigs and any(t in text_low for t in trigs):
            category = r["problem"]
            break

    # 2) фоллбэки без триггеров
    if not category:
        if rating >= 5:
            cands = [r for r in applicable if "5" in (r["problem"] or "") or "Отличн" in (r["problem"] or "")]
        elif rating == 4:
            cands = [r for r in applicable if "4" in (r["problem"] or "") or "Хорош" in (r["problem"] or "")]
        else:
            cands = [r for r in applicable if "Качество" in (r["problem"] or "")]
        if not cands:
            cands = applicable
        category = cands[0]["problem"] if cands else None

    if not category:
        return None, f"{gname}: правило не найдено", True, {"group": gname}

    # 3) собираем все активные варианты этой категории и выбираем случайный
    variants = [r for r in applicable if r["problem"] == category]
    matched = _pick(variants)
    if not matched:
        return None, f"{gname}: нет активных вариантов", True, {"group": gname}

    situation = "positive" if rating >= 4 else "problem"
    greeting = _pick(phrases("greeting", situation))
    farewell = _pick(phrases("farewell", situation))
    parts = [greeting, matched["empathy"], matched["solution"], matched["energy"], farewell]
    answer = " ".join(p.strip() for p in parts if p and p.strip())
    if signer:
        answer += f"\n\n{signer}, команда Видовито"

    manual = bool(matched["manual_check"]) or (rating <= 3 and not any(
        t.strip() and t.strip().lower() in text_low
        for t in (matched["triggers"] or "").split("\n")))
    scenario = f"{gname} · {matched['problem']}"
    return answer, scenario, manual, {"group": gname, "problem": matched["problem"]}


class BuildReq(BaseModel):
    supplier_article: str = ""
    text: str = ""
    rating: int = 0


@router.post("/build")
def build_endpoint(req: BuildReq, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    signer = random.choice(TEMPLATE_SIGNERS)
    answer, scenario, manual, meta = build_answer(db, req.supplier_article, req.text, req.rating, signer=signer)
    if answer is None:
        return {"ok": False, "scenario": scenario, "manual_check": manual, "meta": meta}
    return {"ok": True, "answer": answer, "scenario": scenario, "manual_check": manual, "meta": meta}


# ── CRUD правил ──────────────────────────────────────────────────────────────

class RulePayload(BaseModel):
    group_id: int
    problem: str = ""
    triggers: str = ""
    ratings: str = ""
    empathy: str = ""
    solution: str = ""
    energy: str = ""
    manual_check: bool = False
    sort_order: int = 0
    active: bool = True


@router.post("/rules")
def create_rule(p: RulePayload, db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    with db.bind.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO cx_kb_rules (group_id, problem, triggers, ratings, empathy, solution, energy, manual_check, sort_order, active)
            VALUES (:g, :p, :tr, :ra, :em, :so, :en, :mc, :ord, :ac) RETURNING id
        """), {"g": p.group_id, "p": p.problem, "tr": p.triggers, "ra": p.ratings,
               "em": p.empathy, "so": p.solution, "en": p.energy,
               "mc": p.manual_check, "ord": p.sort_order, "ac": p.active}).first()
        conn.commit()
    return {"id": row[0]}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, p: RulePayload, db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    with db.bind.connect() as conn:
        res = conn.execute(text("""
            UPDATE cx_kb_rules SET problem=:p, triggers=:tr, ratings=:ra, empathy=:em,
                solution=:so, energy=:en, manual_check=:mc, sort_order=:ord, active=:ac WHERE id=:id
        """), {"id": rule_id, "p": p.problem, "tr": p.triggers, "ra": p.ratings,
               "em": p.empathy, "so": p.solution, "en": p.energy,
               "mc": p.manual_check, "ord": p.sort_order, "ac": p.active})
        conn.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return {"status": "ok"}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    with db.bind.connect() as conn:
        conn.execute(text("DELETE FROM cx_kb_rules WHERE id=:id"), {"id": rule_id})
        conn.commit()
    return {"status": "ok"}


class PhrasePayload(BaseModel):
    text: str


@router.put("/phrases/{phrase_id}")
def update_phrase(phrase_id: int, p: PhrasePayload, db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    with db.bind.connect() as conn:
        conn.execute(text("UPDATE cx_kb_phrases SET text=:t WHERE id=:id"),
                     {"id": phrase_id, "t": p.text})
        conn.commit()
    return {"status": "ok"}
