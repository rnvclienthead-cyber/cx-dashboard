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
from ..review_policy import (
    CANNED_OVERLAY_TOPICS,
    OVERLAY_TOPICS,
    REVIEW_TOPIC_CHOICES,
    apply_article_hints,
    detect_review_topic,
    needs_lived_reply,
    no_text_answer,
    no_text_scenario,
    topic_answer,
    topic_label,
    _furniture_wording,
)
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])


def _require_admin(user: dict):
    if (user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может править шаблоны")


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


_SHELF_WHEEL_REPLACEMENTS = (
    (
        "Досадно, когда полка кривая или колесо не крутится. Понимаем ваше расстройство.",
        "Досадно, когда полка кривая или деталь с дефектом. Понимаем ваше расстройство.",
    ),
    (
        "Понимаем, что установка колёс и сборка ярусов могут вызвать вопросы — мы на связи.",
        "Понимаем, что сборка может вызвать вопросы — мы на связи.",
    ),
    (
        "Убедитесь, что колёса вставлены до щелчка; если возникли сложности — напишите в «Чат с продавцом», поможем.",
        "Убедитесь, что полки стоят ровно и болты затянуты; если возникли сложности — напишите в «Чат с продавцом», поможем.",
    ),
    (
        "Для удобства перемещения ставьте более тяжёлые предметы на нижние полки; за идеями хранения ждём в «Чате с продавцом».",
        "Спасибо, что поделились впечатлением.",
    ),
    (
        "Благодарим за пятёрку! Рады, что мобильность стеллажа пришлась по душе.",
        "Благодарим за пятёрку! Рады, что стеллаж пришёлся по душе.",
    ),
    (
        "Чтобы колёса вращались плавно, периодически очищайте их от пыли.",
        "",
    ),
    (
        "На ковре стеллаж на колёсах лучше приподнимать при перемещении — так ролики служат дольше.",
        "",
    ),
    (
        "Здорово, что стеллаж влез куда нужно. Захотите дополнить систему — подскажем модели в чате.",
        "Здорово, что стеллаж влез куда нужно.",
    ),
)
_SHELF_WHEEL_WORD = re.compile(r"колёс|колес|ролик|мобильност", re.I)


def _scrub_shelf_wheel_text(value: str) -> str:
    text = value or ""
    for old, new in _SHELF_WHEEL_REPLACEMENTS:
        text = text.replace(old, new)
    parts = re.split(r"(?<=[.!?])\s+", text.strip()) if text.strip() else []
    kept = [p for p in parts if p and not _SHELF_WHEEL_WORD.search(p)]
    return " ".join(kept).strip()


def ensure_shelf_rules_without_wheels(engine) -> None:
    """Стеллажи Shelf без колёс: убрать советы про колёса, скопированные с этажерок."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.id, r.empathy, r.solution, r.energy
            FROM cx_kb_rules r
            JOIN cx_kb_groups g ON g.id = r.group_id
            WHERE g.name = 'Стеллажи'
        """)).mappings().all()
        for row in rows:
            empathy = _scrub_shelf_wheel_text(row["empathy"] or "")
            solution = _scrub_shelf_wheel_text(row["solution"] or "")
            energy = _scrub_shelf_wheel_text(row["energy"] or "")
            if (empathy, solution, energy) == (
                row["empathy"] or "", row["solution"] or "", row["energy"] or "",
            ):
                continue
            conn.execute(text("""
                UPDATE cx_kb_rules
                SET empathy = :em, solution = :so, energy = :en
                WHERE id = :id
            """), {"em": empathy, "so": solution, "en": energy, "id": row["id"]})
        conn.commit()


def ensure_furniture_wording(engine) -> None:
    """Вешалки — мебель, не одежда: убрать «носите с удовольствием»."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, empathy, solution, energy
            FROM cx_kb_rules
            WHERE coalesce(empathy, '') ILIKE '%носит%'
               OR coalesce(solution, '') ILIKE '%носит%'
               OR coalesce(energy, '') ILIKE '%носит%'
        """)).mappings().all()
        for row in rows:
            empathy = _furniture_wording(row["empathy"] or "")
            solution = _furniture_wording(row["solution"] or "")
            energy = _furniture_wording(row["energy"] or "")
            if (empathy, solution, energy) == (
                row["empathy"] or "", row["solution"] or "", row["energy"] or "",
            ):
                continue
            conn.execute(text("""
                UPDATE cx_kb_rules
                SET empathy = :em, solution = :so, energy = :en
                WHERE id = :id
            """), {"em": empathy, "so": solution, "en": energy, "id": row["id"]})
        phrases = conn.execute(text("""
            SELECT id, text FROM cx_kb_phrases WHERE coalesce(text, '') ILIKE '%носит%'
        """)).mappings().all()
        for row in phrases:
            new = _furniture_wording(row["text"] or "")
            if new != (row["text"] or ""):
                conn.execute(
                    text("UPDATE cx_kb_phrases SET text = :t WHERE id = :id"),
                    {"t": new, "id": row["id"]},
                )
        conn.commit()


_WARM_CLICHE_REPLACEMENTS = (
    ("Спасибо за тёплые слова!", "Спасибо за отзыв!"),
    ("Спасибо за теплые слова!", "Спасибо за отзыв!"),
    ("спасибо за тёплые слова", "спасибо за отзыв"),
    ("спасибо за теплые слова", "спасибо за отзыв"),
    ("Спасибо за тёплую оценку!", "Спасибо за оценку!"),
    ("Спасибо за теплую оценку!", "Спасибо за оценку!"),
    ("Спасибо за пятёрку и тёплые слова!", "Спасибо за пятёрку!"),
    ("Спасибо за пятёрку и теплые слова!", "Спасибо за пятёрку!"),
)


def ensure_no_warm_cliches(engine) -> None:
    """Убрать клише «спасибо за тёплые слова» из конструктора."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, empathy, solution, energy FROM cx_kb_rules
            WHERE coalesce(empathy, '') ILIKE '%тёпл%'
               OR coalesce(empathy, '') ILIKE '%тепл%'
               OR coalesce(solution, '') ILIKE '%тёпл%'
               OR coalesce(solution, '') ILIKE '%тепл%'
               OR coalesce(energy, '') ILIKE '%тёпл%'
               OR coalesce(energy, '') ILIKE '%тепл%'
        """)).mappings().all()
        for row in rows:
            empathy, solution, energy = row["empathy"] or "", row["solution"] or "", row["energy"] or ""
            for old, new in _WARM_CLICHE_REPLACEMENTS:
                empathy = empathy.replace(old, new)
                solution = solution.replace(old, new)
                energy = energy.replace(old, new)
            if (empathy, solution, energy) == (
                row["empathy"] or "", row["solution"] or "", row["energy"] or "",
            ):
                continue
            conn.execute(text("""
                UPDATE cx_kb_rules
                SET empathy = :em, solution = :so, energy = :en
                WHERE id = :id
            """), {"em": empathy, "so": solution, "en": energy, "id": row["id"]})
        phrases = conn.execute(text("""
            SELECT id, text FROM cx_kb_phrases
            WHERE coalesce(text, '') ILIKE '%тёпл%' OR coalesce(text, '') ILIKE '%тепл%'
        """)).mappings().all()
        for row in phrases:
            new = row["text"] or ""
            for old, repl in _WARM_CLICHE_REPLACEMENTS:
                new = new.replace(old, repl)
            if new != (row["text"] or ""):
                conn.execute(
                    text("UPDATE cx_kb_phrases SET text = :t WHERE id = :id"),
                    {"t": new, "id": row["id"]},
                )
        conn.commit()


_RECOMMEND_THANKS_REPLACEMENTS = (
    ("Спасибо, что порекомендовали нас — это лучшая благодарность для команды.", "Спасибо за отзыв!"),
    ("Спасибо, что порекомендовали нас — это лучшая благодарность.", "Спасибо за отзыв!"),
    ("Спасибо, что порекомендовали нас.", "Спасибо за отзыв!"),
    ("Спасибо, что порекомендовали.", "Спасибо за отзыв!"),
    ("Спасибо что порекомендовали нас — это лучшая благодарность для команды.", "Спасибо за отзыв!"),
    ("Спасибо что порекомендовали нас.", "Спасибо за отзыв!"),
)
_RECOMMEND_THANKS_RE = re.compile(
    r"спасибо,?\s*что\s+порекомендовали[^.!?\n]*[.!?]?\s*",
    re.IGNORECASE,
)
_RECOMMEND_IN_REVIEW_RE = re.compile(r"рекоменд", re.IGNORECASE)
_RECOMMEND_TRIGGER_RE = re.compile(r"рекоменд", re.IGNORECASE)


def _triggers_are_recommend_only(triggers: str) -> bool:
    lines = [t.strip() for t in (triggers or "").split("\n") if t.strip()]
    if not lines:
        return False
    return all(_RECOMMEND_TRIGGER_RE.search(t) for t in lines)


def _rewrite_recommend_thanks(text: str) -> str:
    out = text or ""
    for old, new in _RECOMMEND_THANKS_REPLACEMENTS:
        out = out.replace(old, new)
    out = _RECOMMEND_THANKS_RE.sub("Спасибо за отзыв! ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def scrub_recommend_thanks(answer: str, review_text: str) -> str:
    """«Порекомендовали» только если клиент реально написал про рекомендацию."""
    if not answer:
        return answer
    if _RECOMMEND_IN_REVIEW_RE.search(review_text or ""):
        return answer
    return _rewrite_recommend_thanks(answer)


def ensure_no_stray_recommend_thanks(engine) -> None:
    """Убрать «спасибо, что порекомендовали» из шаблонов, где это не про рекомендацию."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, empathy, solution, energy, triggers FROM cx_kb_rules
            WHERE coalesce(empathy, '') ILIKE '%порекомендовал%'
               OR coalesce(solution, '') ILIKE '%порекомендовал%'
               OR coalesce(energy, '') ILIKE '%порекомендовал%'
        """)).mappings().all()
        for row in rows:
            if _triggers_are_recommend_only(row["triggers"] or ""):
                continue
            empathy = _rewrite_recommend_thanks(row["empathy"] or "")
            solution = _rewrite_recommend_thanks(row["solution"] or "")
            energy = _rewrite_recommend_thanks(row["energy"] or "")
            if (empathy, solution, energy) == (
                row["empathy"] or "", row["solution"] or "", row["energy"] or "",
            ):
                continue
            conn.execute(text("""
                UPDATE cx_kb_rules
                SET empathy = :em, solution = :so, energy = :en
                WHERE id = :id
            """), {"em": empathy, "so": solution, "en": energy, "id": row["id"]})
        phrases = conn.execute(text("""
            SELECT id, text FROM cx_kb_phrases
            WHERE coalesce(text, '') ILIKE '%порекомендовал%'
        """)).mappings().all()
        for row in phrases:
            new = _rewrite_recommend_thanks(row["text"] or "")
            if new != (row["text"] or ""):
                conn.execute(
                    text("UPDATE cx_kb_phrases SET text = :t WHERE id = :id"),
                    {"t": new, "id": row["id"]},
                )
        conn.commit()


def _norm(a: str) -> str:
    return re.sub(r"\s+", "", a or "").lower()


def _pick_article_group(rows, sku_norm: str):
    """Найти группу по артикулу: точное → префикс → артикул внутри названия (Collectable PB-401B)."""
    if not sku_norm or not rows:
        return None
    for row in rows:
        if (row.get("article") or "") == sku_norm:
            return row
    for row in rows:
        art = row.get("article") or ""
        if art and (sku_norm.startswith(art) or art.startswith(sku_norm)):
            return row
    contained = [
        row for row in rows
        if (row.get("article") or "") and len(row["article"]) >= 6 and row["article"] in sku_norm
    ]
    if contained:
        return max(contained, key=lambda r: len(r.get("article") or ""))
    return None


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
    _require_admin(current_user)
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


TEMPLATE_SIGNERS = ["Ольга", "Софья", "Александр"]

NO_TEXT_GROUP_NO = 0  # спец-группа шаблонов для отзывов без текста

_NAMED_SIGN_RE = re.compile(
    r"(?:\n\n|\s+)(?:С уважением,\s*)?(?:Ольга|Софья|Александр),?\s*команда Видовито\.?\s*$",
    re.IGNORECASE,
)
_NAKED_SIGN_RE = re.compile(
    r"(?:\n\n|\s+)(?:С уважением,\s*)?Команда Видовито\.?\s*$",
    re.IGNORECASE,
)


def pick_review_signer(prefer=None) -> str:
    if prefer in TEMPLATE_SIGNERS:
        return prefer
    return random.choice(TEMPLATE_SIGNERS)


def _body_without_signer(text: str) -> str:
    cleaned = _NAMED_SIGN_RE.sub("", (text or "").strip())
    return _NAKED_SIGN_RE.sub("", cleaned).strip()


def ensure_review_signature(answer: str, signer=None) -> str:
    """Подпись отзыва всегда с именем: Ольга, Софья или Александр."""
    if not (answer or "").strip():
        return answer
    name = pick_review_signer(signer)
    text = answer.rstrip()
    if _NAMED_SIGN_RE.search(text):
        return text
    text = _NAKED_SIGN_RE.sub("", text).rstrip()
    return f"{text}\n\n{name}, команда Видовито"


def _sign_answer(answer: str, signer=None) -> str:
    return ensure_review_signature(answer, signer)


def _compose_rule(greeting, rule, farewell, rating=0) -> str:
    """5★ — только спасибо, без советов «протрите / уберите полки» (часто не тот артикул)."""
    if int(rating or 0) >= 5:
        parts = [greeting, rule.get("empathy"), farewell]
    else:
        parts = [greeting, rule.get("empathy"), rule.get("solution"), rule.get("energy"), farewell]
    return " ".join(p.strip() for p in parts if p and p.strip())


def _build_no_text(db, rating: int, signer=None):
    """Отзыв без текста: фиксированные формулировки (без возврата/замены сразу)."""
    answer = _sign_answer(no_text_answer(rating), signer)
    return answer, no_text_scenario(rating), False, {"no_text": True}


def build_answer(db, supplier_article: str, review_text: str, rating, signer=None,
                 use_overlay=True, avoid_answer="", topic=None):
    """Возвращает (answer, scenario_label, manual_check, meta) или (None, ...) если группа не найдена.
    signer — имя менеджера для подписи (Ольга / Софья / Александр); если нет — выбирается случайно.
    use_overlay — подставить короткий ответ по теме (для префилла очереди).
    topic — ручной тег проблемы: тогда берём готовый ответ по теме, а не автоподбор.
    Кнопка «из шаблона» вызывает с use_overlay=False, чтобы текст реально менялся.
    """
    try:
        rating = int(rating) if rating not in (None, "", "None") else 0
    except (TypeError, ValueError):
        rating = 0
    text_low = (review_text or "").lower()
    na = _norm(supplier_article)
    forced_topic = str(topic or "").strip()
    if forced_topic in OVERLAY_TOPICS:
        overlay = topic_answer(forced_topic, na, rating)
        if overlay:
            return (
                _sign_answer(overlay, signer),
                topic_label(forced_topic),
                True,
                {"topic": forced_topic, "problem": topic_label(forced_topic)},
            )

    # Отзыв без текста (только оценка) — глобальные шаблоны из группы «Без текста» (group_no=0)
    if not (review_text or "").strip():
        nt = _build_no_text(db, rating, signer)
        if nt:
            return nt

    with db.bind.connect() as conn:
        catalog = conn.execute(text(
            "SELECT a.article, g.id, g.name FROM cx_kb_articles a "
            "JOIN cx_kb_groups g ON g.id = a.group_id"
        )).mappings().all()
        grow = _pick_article_group([dict(r) for r in catalog], na)
        generic = False
        if not grow:
            topic = detect_review_topic(text_low, rating)
            overlay = topic_answer(topic, na, rating) if (use_overlay and topic) else None
            if overlay:
                return (
                    _sign_answer(overlay, signer),
                    f"Общий шаблон · {topic_label(topic)}",
                    True,
                    {"topic": topic},
                )
            gen = conn.execute(text(
                "SELECT id, name FROM cx_kb_groups WHERE group_no = 0 LIMIT 1"
            )).mappings().first()
            if not gen:
                return None, "Группа не определена", True, {}
            grow = {"id": gen["id"], "name": "Общий шаблон"}
            generic = True

        gid, gname = grow["id"], ("Общий шаблон" if generic else grow["name"])
        topic = detect_review_topic(text_low, rating)
        skip_canned = needs_lived_reply(review_text, rating) and topic in CANNED_OVERLAY_TOPICS
        if use_overlay and topic in OVERLAY_TOPICS and not skip_canned:
            overlay = topic_answer(topic, na, rating)
            if overlay:
                return (
                    _sign_answer(overlay, signer),
                    f"{gname} · {topic_label(topic)}",
                    True,
                    {"group": gname, "topic": topic, "problem": topic_label(topic)},
                )
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
            cands = [r for r in applicable if "Качество" in (r["problem"] or "")
                     or "низк" in (r["problem"] or "").lower()]
        if not cands:
            cands = applicable
        category = cands[0]["problem"] if cands else None

    if not category:
        return None, f"{gname}: правило не найдено", True, {"group": gname}

    situation = "positive" if rating >= 4 else "problem"

    # 3) префилл — правило выбранной категории; кнопка — любой подходящий вариант
    variants = list(applicable) if not use_overlay else [r for r in applicable if r["problem"] == category]
    if not variants:
        variants = [r for r in applicable if r["problem"] == category]
    if not variants:
        return None, f"{gname}: нет активных вариантов", True, {"group": gname}

    avoid = _body_without_signer(avoid_answer)
    matched = None
    answer = ""
    for _ in range(12):
        greeting = _pick(phrases("greeting", situation))
        farewell = _pick(phrases("farewell", situation))
        matched = _pick(variants)
        cand = apply_article_hints(na, _compose_rule(greeting, matched, farewell, rating), rating)
        answer = cand
        if not avoid or _body_without_signer(cand) != avoid:
            break
    answer = _sign_answer(scrub_recommend_thanks(answer, review_text), signer)

    manual = bool(matched["manual_check"]) or (rating <= 3 and not any(
        t.strip() and t.strip().lower() in text_low
        for t in (matched["triggers"] or "").split("\n")))
    scenario = f"{gname} · {matched['problem']}"
    return answer, scenario, manual, {"group": gname, "problem": matched["problem"]}


class BuildReq(BaseModel):
    supplier_article: str = ""
    text: str = ""
    rating: int = 0
    use_overlay: bool = False
    avoid: str = ""
    topic: str = ""


@router.get("/topics")
def list_review_topics(current_user: dict = Depends(get_current_user)):
    return [{"id": key, "label": label} for key, label in REVIEW_TOPIC_CHOICES]


@router.post("/build")
def build_endpoint(req: BuildReq, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    signer = random.choice(TEMPLATE_SIGNERS)
    answer, scenario, manual, meta = build_answer(
        db, req.supplier_article, req.text, req.rating, signer=signer,
        use_overlay=req.use_overlay, avoid_answer=req.avoid, topic=req.topic,
    )
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
    _require_admin(current_user)
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
    _require_admin(current_user)
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
    _require_admin(current_user)
    with db.bind.connect() as conn:
        conn.execute(text("DELETE FROM cx_kb_rules WHERE id=:id"), {"id": rule_id})
        conn.commit()
    return {"status": "ok"}


class PhrasePayload(BaseModel):
    text: str


@router.put("/phrases/{phrase_id}")
def update_phrase(phrase_id: int, p: PhrasePayload, db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    with db.bind.connect() as conn:
        conn.execute(text("UPDATE cx_kb_phrases SET text=:t WHERE id=:id"),
                     {"id": phrase_id, "t": p.text})
        conn.commit()
    return {"status": "ok"}
