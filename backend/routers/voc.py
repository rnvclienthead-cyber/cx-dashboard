from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, timedelta
import json

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/voc", tags=["Voice of Customer"], dependencies=[Depends(get_current_user)])

# ── Классификатор тем вопросов ────────────────────────────────────────────────

QUESTION_TOPICS: list[tuple[str, list[str]]] = [
    ("Доставка",        ["доставк", "привез", "приехал", "опоздал", "курьер", "не пришел", "не приехал", "везут", "везти", "сроки"]),
    ("Размеры",         ["размер", "высота", "ширина", "глубина", "длина", "габарит", "не подошел", "маленький", "большой", "подойдет ли", "помещается"]),
    ("Качество",        ["качество", "материал", "запах", "прочность", "хлипк", "ломк", "сломался", "поломка", "брак", "дефект"]),
    ("Цвет",            ["цвет", "оттен", "покрытие", "окраска"]),
    ("Комплектация",    ["комплект", "включен", "входит", "идет ли", "есть ли", "крепеж", "болты", "гайки", "шурупы", "фурнитур"]),
    ("Сборка",          ["сборка", "собрать", "собирается", "инструкц", "схем", "монтаж", "как собра"]),
    ("Возврат/Обмен",   ["возврат", "вернуть", "обмен", "заменить", "замена"]),
    ("Описание/Фото",   ["описание", "фото", "соответствует", "как на фото", "картинк", "характеристик"]),
]

def classify_question(t: str) -> str:
    t = t.lower()
    for topic, kws in QUESTION_TOPICS:
        if any(kw in t for kw in kws):
            return topic
    return "Другое"

# ── Категоризатор тегов отзывов ───────────────────────────────────────────────

REVIEW_CATS = ["Конструкция", "Эргономика", "Функционал", "Сборка", "Ожидания"]
CAT_PREFIXES = {
    "КОНСТРУКЦИЯ": "Конструкция",
    "ЭРГОНОМИКА":  "Эргономика",
    "ФУНКЦИОНАЛ":  "Функционал",
    "СБОРКА":      "Сборка",
    "ОЖИДАНИЯ":    "Ожидания",
}

def tag_category(tag: str) -> str:
    up = tag.upper()
    for prefix, cat in CAT_PREFIXES.items():
        if up.startswith(prefix):
            return cat
    return "Прочее"

# ── Вспомогательные функции ───────────────────────────────────────────────────

def _build_daily_series(rows: list[tuple], date_from: date, date_to: date) -> dict[str, dict[str, int]]:
    """Возвращает {key: {date_str: count}} за весь диапазон (нули включены)."""
    all_dates = [str(date_from + timedelta(days=i)) for i in range((date_to - date_from).days + 1)]
    series: dict[str, dict[str, int]] = {}
    for row in rows:
        key, day, cnt = str(row[0]), str(row[1]), int(row[2])
        if key not in series:
            series[key] = {d: 0 for d in all_dates}
        if day in series[key]:
            series[key][day] += cnt
    for key in series:
        for d in all_dates:
            series[key].setdefault(d, 0)
    return series


def _share_delta(cur: float, prev: float) -> float:
    return round(cur - prev, 4)


# ── Endpoint: /api/v1/voc/trends ─────────────────────────────────────────────

@router.get("/trends")
def get_voc_trends(
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """
    Возвращает временну́ю динамику проблем отзывов и тем вопросов.
    Используется для аналитики VOC с динамикой день-за-днём и долями.
    """
    try:
        today = date.today()
        date_to   = today - timedelta(days=1)   # вчера (актуальные данные)
        date_from = today - timedelta(days=days)
        date_from_prev = today - timedelta(days=days * 2)  # предыдущий период для сравнения

        with db.bind.connect() as conn:
            return _compute_trends(conn, platform, date_from, date_to, date_from_prev, days)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _compute_trends(conn, platform: str, date_from: date, date_to: date, date_from_prev: date, days: int) -> dict:
    # ── 1. Выбираем источники в зависимости от платформы ─────────────────────

    if platform == "wb":
        fb_tables   = ["wb_feedbacks"]
        q_tables    = ["wb_questions"]
    elif platform == "ym":
        fb_tables   = ["ym_feedbacks"]
        q_tables    = ["ym_questions"]
    else:
        fb_tables   = ["wb_feedbacks", "ym_feedbacks"]
        q_tables    = ["wb_questions", "ym_questions"]

    # ── 2. Текущий период: суточные теги из отзывов ───────────────────────────

    tag_rows_cur  = []
    tag_rows_prev = []
    totals_daily  = {}
    totals_prev   = {}

    for tbl in fb_tables:
        tag_filter = "ai_tags IS NOT NULL AND ai_tags ? 'processed'" if tbl == "wb_feedbacks" else "ai_tags IS NOT NULL"

        # Текущий период: разворачиваем tags-массив
        rows = conn.execute(text(f"""
            SELECT
                tag_text,
                created_date::date AS day,
                COUNT(*) AS cnt
            FROM {tbl},
                 LATERAL jsonb_array_elements_text(
                     CASE WHEN ai_tags ? 'tags' THEN ai_tags->'tags' ELSE '[]'::jsonb END
                 ) AS tag_text
            WHERE {tag_filter}
              AND created_date::date BETWEEN :df AND :dt
            GROUP BY tag_text, day
        """), {"df": date_from, "dt": date_to}).fetchall()
        tag_rows_cur.extend(rows)

        # Предыдущий период (для сравнения долей)
        rows_prev = conn.execute(text(f"""
            SELECT
                tag_text,
                created_date::date AS day,
                COUNT(*) AS cnt
            FROM {tbl},
                 LATERAL jsonb_array_elements_text(
                     CASE WHEN ai_tags ? 'tags' THEN ai_tags->'tags' ELSE '[]'::jsonb END
                 ) AS tag_text
            WHERE {tag_filter}
              AND created_date::date BETWEEN :df AND :dt
            GROUP BY tag_text, day
        """), {"df": date_from_prev, "dt": date_from - timedelta(days=1)}).fetchall()
        tag_rows_prev.extend(rows_prev)

        # Суточный итог отзывов (только с тегами)
        day_totals = conn.execute(text(f"""
            SELECT created_date::date AS day, COUNT(*) AS cnt
            FROM {tbl}
            WHERE {tag_filter}
              AND created_date::date BETWEEN :df AND :dt
            GROUP BY day
        """), {"df": date_from, "dt": date_to}).fetchall()
        for r in day_totals:
            d = str(r[0])
            totals_daily[d] = totals_daily.get(d, 0) + int(r[1])

        day_totals_prev = conn.execute(text(f"""
            SELECT created_date::date AS day, COUNT(*) AS cnt
            FROM {tbl}
            WHERE {tag_filter}
              AND created_date::date BETWEEN :df AND :dt
            GROUP BY day
        """), {"df": date_from_prev, "dt": date_from - timedelta(days=1)}).fetchall()
        for r in day_totals_prev:
            totals_prev[str(r[0])] = totals_prev.get(str(r[0]), 0) + int(r[1])

    # ── 3. Агрегация по категориям ────────────────────────────────────────────

    cat_daily_cur:  dict[str, dict[str, int]] = {c: {} for c in REVIEW_CATS}
    cat_daily_prev: dict[str, dict[str, int]] = {c: {} for c in REVIEW_CATS}

    subtag_cur:  dict[str, int] = {}
    subtag_cat:  dict[str, str] = {}

    for row in tag_rows_cur:
        tag, day, cnt = str(row[0]), str(row[1]), int(row[2])
        cat = tag_category(tag)
        if cat in cat_daily_cur:
            cat_daily_cur[cat][day] = cat_daily_cur[cat].get(day, 0) + cnt
        subtag_cur[tag] = subtag_cur.get(tag, 0) + cnt
        subtag_cat[tag] = cat

    for row in tag_rows_prev:
        tag, day, cnt = str(row[0]), str(row[1]), int(row[2])
        cat = tag_category(tag)
        if cat in cat_daily_prev:
            cat_daily_prev[cat][day] = cat_daily_prev[cat].get(day, 0) + cnt

    # Суммарные числа
    total_tagged_cur  = sum(totals_daily.values())
    total_tagged_prev = sum(totals_prev.values())

    # Дополняем нулями все даты периода
    all_dates = [str(date_from + timedelta(days=i)) for i in range((date_to - date_from).days + 1)]

    review_categories = []
    for cat in REVIEW_CATS:
        daily = cat_daily_cur.get(cat, {})
        daily_full = {d: daily.get(d, 0) for d in all_dates}
        total_cur  = sum(daily.values())
        total_prev_v = sum(cat_daily_prev.get(cat, {}).values())
        share_cur  = round(total_cur / total_tagged_cur, 4) if total_tagged_cur else 0
        share_prev = round(total_prev_v / total_tagged_prev, 4) if total_tagged_prev else 0
        review_categories.append({
            "name":       cat,
            "total":      total_cur,
            "share":      share_cur,
            "share_prev": share_prev,
            "share_delta": _share_delta(share_cur, share_prev),
            "daily":      daily_full,
        })
    review_categories.sort(key=lambda x: x["total"], reverse=True)

    # Топ-20 подтегов
    top_subtags = []
    for tag, cnt in sorted(subtag_cur.items(), key=lambda x: x[1], reverse=True)[:20]:
        top_subtags.append({
            "tag":      tag,
            "category": subtag_cat.get(tag, "Прочее"),
            "total":    cnt,
            "share":    round(cnt / total_tagged_cur, 4) if total_tagged_cur else 0,
        })

    # ── 4. Вопросы: суточная классификация ───────────────────────────────────

    q_rows_cur:  list = []
    q_rows_prev: list = []
    q_totals_daily:  dict[str, int] = {}

    for tbl in q_tables:
        rows = conn.execute(text(f"""
            SELECT text, created_date::date AS day
            FROM {tbl}
            WHERE text IS NOT NULL AND TRIM(text) != ''
              AND created_date::date BETWEEN :df AND :dt
        """), {"df": date_from, "dt": date_to}).fetchall()
        q_rows_cur.extend(rows)

        rows_prev = conn.execute(text(f"""
            SELECT text, created_date::date AS day
            FROM {tbl}
            WHERE text IS NOT NULL AND TRIM(text) != ''
              AND created_date::date BETWEEN :df AND :dt
        """), {"df": date_from_prev, "dt": date_from - timedelta(days=1)}).fetchall()
        q_rows_prev.extend(rows_prev)

    # Классифицируем вопросы
    topic_daily_cur:  dict[str, dict[str, int]] = {}
    topic_daily_prev: dict[str, dict[str, int]] = {}

    for row in q_rows_cur:
        t, day = str(row[0]), str(row[1])
        topic = classify_question(t)
        if topic not in topic_daily_cur:
            topic_daily_cur[topic] = {}
        topic_daily_cur[topic][day] = topic_daily_cur[topic].get(day, 0) + 1
        q_totals_daily[day] = q_totals_daily.get(day, 0) + 1

    for row in q_rows_prev:
        t, day = str(row[0]), str(row[1])
        topic = classify_question(t)
        if topic not in topic_daily_prev:
            topic_daily_prev[topic] = {}
        topic_daily_prev[topic][day] = topic_daily_prev[topic].get(day, 0) + 1

    total_q_cur  = sum(q_totals_daily.values())
    total_q_prev = sum(sum(v.values()) for v in topic_daily_prev.values())

    question_topics = []
    for topic, daily in topic_daily_cur.items():
        total_cur  = sum(daily.values())
        total_prev_v = sum(topic_daily_prev.get(topic, {}).values())
        share_cur  = round(total_cur / total_q_cur, 4) if total_q_cur else 0
        share_prev = round(total_prev_v / total_q_prev, 4) if total_q_prev else 0
        daily_full = {d: daily.get(d, 0) for d in all_dates}
        question_topics.append({
            "topic":       topic,
            "total":       total_cur,
            "share":       share_cur,
            "share_prev":  share_prev,
            "share_delta": _share_delta(share_cur, share_prev),
            "daily":       daily_full,
        })
    question_topics.sort(key=lambda x: x["total"], reverse=True)

    # ── 5. Разбивка по SKU ────────────────────────────────────────────────────

    sku_map: dict[str, dict] = {}
    for tbl in fb_tables:
        tag_filter = "ai_tags IS NOT NULL AND ai_tags ? 'processed'" if tbl == "wb_feedbacks" else "ai_tags IS NOT NULL"
        rows = conn.execute(text(f"""
            SELECT supplier_article, valuation, ai_tags
            FROM {tbl}
            WHERE {tag_filter}
              AND created_date::date BETWEEN :df AND :dt
        """), {"df": date_from, "dt": date_to}).fetchall()

        for row in rows:
            sku, val = str(row[0]), int(row[1] or 0)
            tags_json = row[2]
            if isinstance(tags_json, str):
                try: tags_json = json.loads(tags_json)
                except: continue
            if not isinstance(tags_json, dict): continue

            if sku not in sku_map:
                sku_map[sku] = {"sku": sku, "total": 0, "negative": 0, "sum_rating": 0, "tags": {}}
            sku_map[sku]["total"] += 1
            sku_map[sku]["sum_rating"] += val
            if val <= 3:
                sku_map[sku]["negative"] += 1
            for tag in tags_json.get("tags", []):
                sku_map[sku]["tags"][tag] = sku_map[sku]["tags"].get(tag, 0) + 1

    sku_problems = []
    for sku, s in sku_map.items():
        if not s["total"]:
            continue
        top = sorted(s["tags"].items(), key=lambda x: x[1], reverse=True)[:3]
        sku_problems.append({
            "sku":        sku,
            "total":      s["total"],
            "negative":   s["negative"],
            "avg_rating": round(s["sum_rating"] / s["total"], 2),
            "top_problems": [{"tag": t, "count": c} for t, c in top],
        })
    sku_problems.sort(key=lambda x: x["negative"], reverse=True)

    # ── 6. Суммарный ряд дат для фронтенда ───────────────────────────────────

    totals_full       = {d: totals_daily.get(d, 0)   for d in all_dates}
    q_totals_full     = {d: q_totals_daily.get(d, 0) for d in all_dates}

    return {
        "period_days":        days,
        "date_from":          str(date_from),
        "date_to":            str(date_to),
        "dates":              all_dates,
        "total_tagged":       total_tagged_cur,
        "total_questions":    total_q_cur,
        "review_categories":  review_categories,
        "top_subtags":        top_subtags,
        "question_topics":    question_topics,
        "totals_daily":       totals_full,
        "q_totals_daily":     q_totals_full,
        "sku_problems":       sku_problems[:30],
    }


# ── Endpoint: /api/v1/voc/dashboard (старый — обратная совместимость) ────────

@router.get("/dashboard")
def get_voc_dashboard(
    platform: str = Query("wb", pattern="^(wb|ym|ozon|all)$"),
    db: Session = Depends(get_db),
):
    try:
        with db.bind.connect() as conn:
            if platform == "wb":
                fb_query = text("""
                    SELECT id, supplier_article, created_date, valuation, ai_tags
                    FROM wb_feedbacks
                    WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed'
                """)
                q_query = text(
                    "SELECT id, supplier_article, created_date, text, answer_text, state "
                    "FROM wb_questions ORDER BY created_date DESC LIMIT 100"
                )
            elif platform == "ym":
                fb_query = text("""
                    SELECT id, supplier_article, created_date, valuation, ai_tags
                    FROM ym_feedbacks
                    WHERE ai_tags IS NOT NULL
                """)
                q_query = text(
                    "SELECT id, supplier_article, created_date, text, answer_text, state "
                    "FROM ym_questions ORDER BY created_date DESC LIMIT 100"
                )
            else:
                fb_query = text("""
                    SELECT id, supplier_article, created_date, valuation, ai_tags
                    FROM wb_feedbacks WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed'
                    UNION ALL
                    SELECT id, supplier_article, created_date, valuation, ai_tags
                    FROM ym_feedbacks WHERE ai_tags IS NOT NULL
                """)
                q_query = text("""
                    SELECT id, supplier_article, created_date, text, answer_text, state
                    FROM (
                        SELECT id, supplier_article, created_date, text, answer_text, state FROM wb_questions
                        UNION ALL
                        SELECT id, supplier_article, created_date, text, answer_text, state FROM ym_questions
                    ) combined
                    ORDER BY created_date DESC LIMIT 100
                """)
            feedbacks = conn.execute(fb_query).mappings().all()
            questions = conn.execute(q_query).mappings().all()

        tags_distribution = {}
        suggestions = []
        sku_stats = {}
        funnel = {"Конструкция": 0, "Эргономика": 0, "Функционал": 0, "Сборка": 0, "Ожидания": 0}

        for fb in feedbacks:
            sku = fb['supplier_article']
            val = fb['valuation']
            tags_json = fb['ai_tags']

            if sku not in sku_stats:
                sku_stats[sku] = {"total": 0, "negative": 0, "sum_rating": 0, "tags": {}}

            sku_stats[sku]['total'] += 1
            sku_stats[sku]['sum_rating'] += val
            if val <= 3: sku_stats[sku]['negative'] += 1

            if isinstance(tags_json, str):
                try: tags_json = json.loads(tags_json)
                except: continue

            if not isinstance(tags_json, dict): continue

            for tag in tags_json.get('tags', []):
                if ":" in tag:
                    parent, child = [t.strip() for t in tag.split(':', 1)]
                    if "КОНСТРУКЦИЯ" in parent: funnel["Конструкция"] += 1
                    elif "ЭРГОНОМИКА" in parent: funnel["Эргономика"] += 1
                    elif "ФУНКЦИОНАЛ" in parent: funnel["Функционал"] += 1
                    elif "СБОРКА" in parent: funnel["Сборка"] += 1
                    elif "ОЖИДАНИЯ" in parent: funnel["Ожидания"] += 1
                    tags_distribution[tag] = tags_distribution.get(tag, 0) + 1
                    sku_stats[sku]['tags'][tag] = sku_stats[sku]['tags'].get(tag, 0) + 1

            if tags_json.get('suggestion'):
                suggestions.append({
                    "id": fb['id'], "sku": sku,
                    "date": fb['created_date'].strftime('%Y-%m-%d') if fb['created_date'] else '-',
                    "rating": val, "text": tags_json['suggestion']
                })

        sku_list = []
        for k, v in sku_stats.items():
            if v['total'] > 0:
                top_tag = max(v['tags'], key=v['tags'].get) if v['tags'] else "Нет проблем"
                sku_list.append({
                    "sku": k, "total": v['total'], "negative": v['negative'],
                    "avg_rating": round(v['sum_rating'] / v['total'], 2), "top_problem": top_tag
                })
        sku_list = sorted(sku_list, key=lambda x: x['negative'], reverse=True)[:20]

        return {
            "status": "success",
            "tags_distribution": tags_distribution,
            "funnel": funnel,
            "suggestions": suggestions[::-1][:50],
            "questions": [dict(q) for q in questions],
            "sku_ranking": sku_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
