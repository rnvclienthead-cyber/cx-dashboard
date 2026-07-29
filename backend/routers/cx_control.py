"""
Контроль клиентского сервиса - razdel «Контроль КС».
Dostup: tol'ko rol' admin.

Logika «zhdet otveta»:
  WB otzyvy   - wb_feedbacks.state = 'active'
  WB voprosy  - wb_questions.state = 'active'
  YaM voprosy - ym_questions.state = 'UNANSWERED'
  YaM otzyvy  - status otveta v BD net -> rezhim «novye» (intake), bez SLA

SLA-limity (defolt):
  Chaty 30 min  Voprosy 4 ch  Otzyvy 24 ch (negativ 4 ch)  Vozvrat 24 ch
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, timedelta

from ..database import get_db
from .auth import get_current_user


def _admin_only(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Tolko dlya administratorov")
    return current_user


router = APIRouter(
    prefix="/api/v1/cx-control",
    tags=["CX Control"],
    dependencies=[Depends(_admin_only)],
)

# ── SLA defaults (minutes) — overridden by cx_sla_settings table ─────────────
SLA_DEFAULTS = {
    "wb_chat":    1440,
    "wb_rev_neg": 1440,
    "wb_rev_pos": 1440,
    "wb_q":       1440,
    "ym_q":       1440,
    "mod_wb_claims": 4320,
    "mod_ym_returns": 4320,
    "mod_ozon_returns": 4320,
    "mod_wb_fb": 4320,
    "mod_ym_fb": 4320,
}

def _load_sla(conn) -> dict:
    rows = conn.execute(text("SELECT queue_id, sla_min FROM cx_sla_settings")).fetchall()
    result = dict(SLA_DEFAULTS)
    for r in rows:
        result[r[0]] = int(r[1])
    return result

TEXT_COND = "(TRIM(COALESCE(text,''))!='' OR TRIM(COALESCE(pros,''))!='' OR TRIM(COALESCE(cons,''))!='')"

QUEUE_ROWS = [
    {"id": "wb_chat", "mp": "wb", "title_ru": "Чаты с покупателями", "type": "chat",
     "table": "wb_chats", "cond": "needs_answer = true AND NOT COALESCE(is_closing, false)",
     "date_col": "waiting_since", "sku_col": "good_name", "text_col": "last_message", "id_col": "chat_id"},
    {"id": "wb_rev_neg", "mp": "wb", "title_ru": "Отзывы · негатив (≤3★)", "type": "review",
     "table": "wb_feedbacks", "cond": f"state = 'active' AND COALESCE(valuation, 0) <= 3 AND {TEXT_COND}"},
    {"id": "wb_rev_pos", "mp": "wb", "title_ru": "Отзывы · позитив (≥4★)", "type": "review",
     "table": "wb_feedbacks", "cond": f"state = 'active' AND COALESCE(valuation, 0) >= 4 AND {TEXT_COND}"},
    {"id": "wb_q", "mp": "wb", "title_ru": "Вопросы", "type": "question",
     "table": "wb_questions", "cond": "state = 'active'"},
    {"id": "ym_q", "mp": "ym", "title_ru": "Вопросы", "type": "question",
     "table": "ym_questions", "cond": "state = 'UNANSWERED'"},
]

# Отзывы без текста — отдельный блок, без SLA
NO_TEXT_ROWS = [
    {"id": "wb_rev_no_text", "mp": "wb", "title_ru": "Отзывы без текста",
     "table": "wb_feedbacks", "cond": "state = 'active' AND TRIM(COALESCE(text,''))='' AND TRIM(COALESCE(pros,''))='' AND TRIM(COALESCE(cons,''))=''"},
]

INTAKE_ROWS = [
    {"id": "ym_rev", "mp": "ym", "title_ru": "Отзывы (новые)", "type": "review",
     "table": "ym_feedbacks", "cond": "TRUE"},
]

# ── Модерация тегов (ИИ размечает → менеджер проверяет/корректирует) ─────────
_CAT_COND = "(cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)"
_NOT_CONFIRMED = "(correction IS NULL OR TRIM(correction) = '')"
_YM_FB_TEXT = "TRIM(CONCAT_WS(' ', NULLIF(pro_text,''), NULLIF(contra_text,''), NULLIF(comment,'')))"

MODERATION_ROWS = [
    {"id": "mod_wb_claims", "mp": "wb", "title_ru": "WB · Рекламации (теги)", "type": "moderation",
     "table": "wb_claims", "cond": f"{_CAT_COND} AND {_NOT_CONFIRMED}",
     "date_col": "created_dt", "id_col": "srid", "sku_col": "supplier_article", "text_col": "user_comment"},
    {"id": "mod_ym_returns", "mp": "ym", "title_ru": "ЯМ · Возвраты (теги)", "type": "moderation",
     "table": "ym_returns", "cond": f"{_CAT_COND} AND {_NOT_CONFIRMED}",
     "date_col": "created_at", "id_col": "return_id", "sku_col": "supplier_article", "text_col": "return_comment"},
    {"id": "mod_ozon_returns", "mp": "ozon", "title_ru": "Ozon · Возвраты (теги)", "type": "moderation",
     "table": "ozon_returns", "cond": f"{_CAT_COND} AND {_NOT_CONFIRMED}",
     "date_col": "return_date", "id_col": "return_id", "sku_col": "supplier_article", "text_col": "return_reason"},
    {"id": "mod_wb_fb", "mp": "wb", "title_ru": "WB · Отзывы (теги)", "type": "review",
     "table": "wb_feedbacks",
     "cond": "ai_tags ? 'processed' AND NOT (ai_tags ? 'confirmed') AND jsonb_array_length(ai_tags->'tags') > 0",
     "date_col": "created_date", "id_col": "id", "sku_col": "supplier_article", "text_col": "text"},
    {"id": "mod_ym_fb", "mp": "ym", "title_ru": "ЯМ · Отзывы (теги)", "type": "review",
     "table": "ym_feedbacks",
     "cond": "ai_tags ? 'processed' AND NOT (ai_tags ? 'confirmed') AND jsonb_array_length(ai_tags->'tags') > 0",
     "date_col": "created_date", "id_col": "id", "sku_col": "supplier_article", "text_col": _YM_FB_TEXT},
]

GAPS = []  # Ozon отзывы и чаты подключены в Фазе 3

MP_LABEL = {"wb": "Wildberries", "ym": "Яндекс Маркет", "ozon": "Озон"}


# ── Workload plan constants (from "Vremya raboty Lilii.docx") ──────────────────
# budget_min = planned daily time per channel
PLAN = [
    {"id": "wb_reviews",   "mp": "wb",   "label": "WB · Отзывы",   "budget_min": 60,
     "table": "wb_feedbacks",  "date_col": "created_date"},
    {"id": "wb_chats",     "mp": "wb",   "label": "WB · Чаты",     "budget_min": 60,
     "table": "wb_chat_pending", "date_col": "created_at"},
    {"id": "wb_questions", "mp": "wb",   "label": "WB · Вопросы",  "budget_min": 15,
     "table": "wb_questions",  "date_col": "created_date"},
    {"id": "wb_returns",   "mp": "wb",   "label": "WB · Возвраты",  "budget_min": 40,
     "table": None,             "date_col": None},
    {"id": "ozon_reviews", "mp": "ozon", "label": "Ozon · Отзывы",  "budget_min": 60,
     "table": "ozon_reviews",   "date_col": "created_at"},
    {"id": "ozon_chats",   "mp": "ozon", "label": "Ozon · Чаты",    "budget_min": 30,
     "table": "ozon_chats",     "date_col": "waiting_since"},
    {"id": "ym_chats",     "mp": "ym",   "label": "ЯМ · Чаты",      "budget_min": 30,
     "table": "ym_chats",       "date_col": "waiting_since"},
    {"id": "ozon_returns", "mp": "ozon", "label": "Ozon · Возвраты", "budget_min": 0,
     "table": "ozon_returns",   "date_col": "return_date"},
    {"id": "ym_reviews",   "mp": "ym",   "label": "ЯМ · Отзывы",   "budget_min": 20,
     "table": "ym_feedbacks",   "date_col": "created_date"},
    {"id": "ym_questions", "mp": "ym",   "label": "ЯМ · Вопросы", "budget_min": 5,
     "table": "ym_questions",   "date_col": "created_date"},
    {"id": "ym_returns",   "mp": "ym",   "label": "ЯМ · Возвраты",  "budget_min": 5,
     "table": "ym_returns",     "date_col": "created_at"},
    {"id": "mm_all",       "mp": "mm",   "label": "ММ · Все блоки",  "budget_min": 15,
     "table": None,             "date_col": None},
    {"id": "social",       "mp": "mm",   "label": "Соцсети",         "budget_min": 5,
     "table": None,             "date_col": None},
]
PLAN_TOTAL = sum(p["budget_min"] for p in PLAN)


def _date_cond(days, date_col="created_date"):
    if days:
        return f"AND {date_col} > now() - interval '{int(days)} days'"
    return ""

def _queue_stats(conn, row: dict, sla_min: int = 1440, days=None) -> dict:
    warn = int(sla_min * 0.75)
    dcol = row.get("date_col", "created_date")
    dc = _date_cond(days, dcol)
    q = text(f"""
        SELECT
            count(*)                                                                       AS waiting,
            count(*) FILTER (WHERE now()-{dcol} <= make_interval(mins=>:warn))             AS green,
            count(*) FILTER (WHERE now()-{dcol} >  make_interval(mins=>:warn)
                             AND   now()-{dcol} <= make_interval(mins=>:sla))              AS amber,
            count(*) FILTER (WHERE now()-{dcol} >  make_interval(mins=>:sla))              AS red,
            COALESCE(ROUND(EXTRACT(epoch FROM now()-min({dcol}))/60)::int, 0)              AS oldest_min,
            COALESCE(ROUND(AVG(EXTRACT(epoch FROM now()-{dcol}))/60)::int, 0)              AS avg_age_min,
            count(*) FILTER (WHERE {dcol} > now()-interval '24 hours')                     AS intake_24h,
            count(*) FILTER (WHERE {dcol} > now()-interval '7 days')                       AS intake_7d
        FROM {row['table']}
        WHERE {row['cond']} {dc}
    """)
    r = conn.execute(q, {"warn": warn, "sla": sla_min}).mappings().first() or {}
    waiting = int(r.get("waiting", 0) or 0)
    green   = int(r.get("green",   0) or 0)
    amber   = int(r.get("amber",   0) or 0)
    red     = int(r.get("red",     0) or 0)
    status  = "red" if red > 0 else ("amber" if amber > 0 else "green")
    on_time = green + amber
    compliance = round(on_time / waiting * 100) if waiting else 100

    # avg_age за 30 дней (независимо от фильтра days)
    r30 = conn.execute(text(f"""
        SELECT COALESCE(ROUND(AVG(EXTRACT(epoch FROM now()-{dcol}))/60)::int, 0) AS avg30
        FROM {row['table']}
        WHERE {row['cond']} AND {dcol} > now() - interval '30 days'
    """)).mappings().first() or {}

    return {
        "id": row["id"], "mp": row["mp"], "mp_label": MP_LABEL.get(row["mp"], row["mp"]),
        "title": row.get("title_ru", row.get("title", "")), "type": row["type"], "mode": "sla",
        "sla_min": sla_min, "warn_min": warn,
        "waiting": waiting, "green": green, "amber": amber, "red": red,
        "status": status, "compliance": compliance,
        "oldest_min":  int(r.get("oldest_min",  0) or 0),
        "avg_age_min": int(r.get("avg_age_min", 0) or 0),
        "avg_age_30d": int(r30.get("avg30", 0) or 0),
        "intake_24h":  int(r.get("intake_24h",  0) or 0),
        "intake_7d":   int(r.get("intake_7d",   0) or 0),
        "samples": _samples(conn, row, days=days),
    }

def _no_text_stats(conn, row: dict, days=None) -> dict:
    dc = _date_cond(days)
    r = conn.execute(text(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE valuation <= 3) AS negative,
               count(*) FILTER (WHERE valuation >= 4) AS positive
        FROM {row['table']} WHERE {row['cond']} {dc}
    """)).mappings().first() or {}
    return {
        "id": row["id"], "mp": row["mp"], "mp_label": MP_LABEL.get(row["mp"], row["mp"]),
        "title": row["title_ru"], "mode": "no_text",
        "total": int(r.get("total", 0) or 0),
        "negative": int(r.get("negative", 0) or 0),
        "positive": int(r.get("positive", 0) or 0),
    }


def _intake_stats(conn, row: dict) -> dict:
    q = text(f"""
        SELECT
            count(*) FILTER (WHERE created_date > now()-interval '24 hours') AS intake_24h,
            count(*) FILTER (WHERE created_date > now()-interval '7 days')   AS intake_7d
        FROM {row['table']}
        WHERE {row['cond']}
    """)
    r = conn.execute(q).mappings().first() or {}
    return {
        "id": row["id"], "mp": row["mp"], "mp_label": MP_LABEL.get(row["mp"], row["mp"]),
        "title": row.get("title_ru", ""), "type": row["type"], "mode": "intake",
        "intake_24h": int(r.get("intake_24h", 0) or 0),
        "intake_7d":  int(r.get("intake_7d",  0) or 0),
    }


def _samples(conn, row: dict, limit: int = 6, days=None) -> list:
    has_val = row["type"] == "review"
    val_col = "COALESCE(valuation, 0)" if has_val else "NULL::int"
    dcol    = row.get("date_col", "created_date")
    id_col  = row.get("id_col", "id")
    sku_col = row.get("sku_col", "supplier_article")
    txt_col = row.get("text_col", "text")
    dc = _date_cond(days, dcol)
    try:
        q = text(f"""
            SELECT {id_col}::text AS id,
                   COALESCE({sku_col}::text, '-') AS sku,
                   {val_col} AS valuation,
                   LEFT(COALESCE({txt_col}, ''), 160) AS snippet,
                   ROUND(EXTRACT(epoch FROM now()-{dcol})/60)::int AS age_min
            FROM {row['table']}
            WHERE {row['cond']} {dc}
            ORDER BY {dcol} ASC
            LIMIT {limit}
        """)
        return [{"id": x["id"], "sku": x["sku"], "valuation": x["valuation"],
                 "snippet": (x["snippet"] or "").strip(), "age_min": int(x["age_min"] or 0)}
                for x in conn.execute(q).mappings().all()]
    except Exception:
        return []


@router.get("/queues")
def get_queues(days: int = Query(None, ge=1, le=365), db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            sla_map    = _load_sla(conn)
            queues     = [_queue_stats(conn, r, sla_min=sla_map.get(r["id"], 1440), days=days) for r in QUEUE_ROWS]
            no_text    = [_no_text_stats(conn, r, days=days) for r in NO_TEXT_ROWS]
            intake     = [_intake_stats(conn, r) for r in INTAKE_ROWS]
            moderation = [_queue_stats(conn, r, sla_min=sla_map.get(r["id"], 4320), days=days) for r in MODERATION_ROWS]
            now_row  = conn.execute(text("SELECT now() AT TIME ZONE 'UTC' AS now_utc")).mappings().first()
            # Чаты, закрытые благодарностью — не требуют ответа
            dc_chat = _date_cond(days, "waiting_since")
            chat_closing = int(conn.execute(text(f"""
                SELECT count(*) FROM wb_chats
                WHERE needs_answer = true AND COALESCE(is_closing, false) = true {dc_chat}
            """)).scalar() or 0)

        # Прокидываем счётчик закрывающих в карточку чатов
        for q in queues:
            if q["id"] == "wb_chat":
                q["closing_count"] = chat_closing

        total_waiting  = sum(q["waiting"] for q in queues)
        total_breached = sum(q["red"]     for q in queues)
        total_on_time  = sum(q["green"] + q["amber"] for q in queues)
        compliance     = round(total_on_time / total_waiting * 100) if total_waiting else 100
        oldest_min     = max([q["oldest_min"] for q in queues], default=0)
        worst = max(queues, key=lambda q: (q["red"], q["oldest_min"]), default=None)

        return {
            "generated_at": str(now_row["now_utc"]) if now_row else None,
            "days_filter": days,
            "sla": sla_map,
            "summary": {
                "total_waiting": total_waiting, "total_breached": total_breached,
                "compliance": compliance, "oldest_min": oldest_min,
                "worst": (worst["mp_label"] + " · " + worst["title"])
                          if worst and worst["waiting"] else None,
            },
            "queues": queues,
            "no_text": no_text,
            "intake": intake,
            "moderation": moderation,
            "moderation_summary": {
                "total_waiting":  sum(m["waiting"] for m in moderation),
                "total_breached": sum(m["red"]     for m in moderation),
            },
            "gaps": [{**g, "mp_label": MP_LABEL.get(g["mp"], g["mp"])} for g in GAPS],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workload")
def get_workload(days: int = Query(30, ge=7, le=90), db: Session = Depends(get_db)):
    """
    Nagruzka plan/fakt.
    Vozvrashaet:
      - plan (byudzhet vremeni po kanalm iz documenta)
      - fakt: ezhednevo intake za posle N dney (seriya + avg7d / avg30d)
      - load_index: avg7d / avg30d (otnositelno nagruzka)
    """
    try:
        today = date.today()
        date_from = today - timedelta(days=days)
        all_dates = [(date_from + timedelta(days=i)).isoformat() for i in range(days + 1)]

        channels = []
        with db.bind.connect() as conn:
            for p in PLAN:
                chan: dict = {
                    "id": p["id"], "mp": p["mp"],
                    "mp_label": MP_LABEL.get(p["mp"], p["mp"]),
                    "label": p["label"],
                    "budget_min": p["budget_min"],
                    "has_data": p["table"] is not None,
                    "daily": {},
                    "avg_7d": 0, "avg_30d": 0, "load_index": None,
                }

                if p["table"] and p["date_col"]:
                    date_col = p["date_col"]
                    tbl      = p["table"]
                    # AT TIME ZONE 'UTC' for timestamptz columns (ozon_returns, ym_returns)
                    # plain cast for timestamp without tz columns
                    cast_expr = (
                        f"({date_col} AT TIME ZONE 'UTC')::date"
                        if tbl in ("ozon_returns", "ym_returns")
                        else f"{date_col}::date"
                    )
                    rows = conn.execute(text(f"""
                        SELECT {cast_expr} AS day, count(*) AS cnt
                        FROM {tbl}
                        WHERE {cast_expr} BETWEEN :df AND :dt
                        GROUP BY 1
                    """), {"df": date_from.isoformat(), "dt": today.isoformat()}).fetchall()

                    daily = {str(r[0]): int(r[1]) for r in rows}
                    chan["daily"] = {d: daily.get(d, 0) for d in all_dates}

                    vals = list(chan["daily"].values())
                    last7  = vals[-7:]  if len(vals) >= 7  else vals
                    last30 = vals[-30:] if len(vals) >= 30 else vals
                    avg7  = round(sum(last7)  / len(last7),  1) if last7  else 0
                    avg30 = round(sum(last30) / len(last30), 1) if last30 else 0
                    chan["avg_7d"]  = avg7
                    chan["avg_30d"] = avg30
                    chan["load_index"] = round(avg7 / avg30, 2) if avg30 else None

                channels.append(chan)

        # summary: total budget vs sum of avg7d (can't convert volume->time, show both)
        total_budget = PLAN_TOTAL
        channels_with_data = [c for c in channels if c["has_data"]]
        avg7_total = round(sum(c["avg_7d"] for c in channels_with_data), 1)

        return {
            "days": days,
            "dates": all_dates,
            "plan_total_min": total_budget,
            "avg7_total_items": avg7_total,
            "channels": channels,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quality")
def get_quality(days: int = Query(30, ge=7, le=90), db: Session = Depends(get_db)):
    """
    Ekran 3 - Kachestvo i ekonomika.
    Vozvrashaet:
      - tonalnost WB + YM (avg_rating, pos/neg, daily series)
      - disciplina AI-razmetki (% razmecheno za 7d / 30d / period)
      - reshipment voronka (funnel po statusam + nedelnaya dinamika)
    """
    try:
        today     = date.today()
        date_from = today - timedelta(days=days)
        all_dates = [(date_from + timedelta(days=i)).isoformat() for i in range(days + 1)]

        with db.bind.connect() as conn:
            # ── 1. Тональность WB ──────────────────────────────────────────────
            wb_rows = conn.execute(text("""
                SELECT created_date::date AS day,
                       round(avg(valuation::numeric), 2) AS avg_r,
                       count(*) AS total,
                       count(*) FILTER (WHERE valuation >= 4) AS positive,
                       count(*) FILTER (WHERE valuation <= 3) AS negative
                FROM wb_feedbacks
                WHERE created_date::date BETWEEN :df AND :dt
                GROUP BY 1
            """), {"df": date_from, "dt": today}).fetchall()

            wb_daily  = {str(r[0]): {"avg_r": float(r[1] or 0), "total": int(r[2]),
                                      "positive": int(r[3]), "negative": int(r[4])} for r in wb_rows}
            wb_totals = conn.execute(text("""
                SELECT count(*) total, count(*) FILTER (WHERE valuation>=4) positive,
                       count(*) FILTER (WHERE valuation<=3) negative,
                       round(avg(valuation::numeric),2) avg_r
                FROM wb_feedbacks WHERE created_date::date BETWEEN :df AND :dt
            """), {"df": date_from, "dt": today}).mappings().first()

            # ── 2. Тональность ЯМ ──────────────────────────────────────────────
            ym_rows = conn.execute(text("""
                SELECT created_date::date AS day,
                       round(avg(valuation::numeric), 2) AS avg_r,
                       count(*) AS total,
                       count(*) FILTER (WHERE valuation >= 4) AS positive,
                       count(*) FILTER (WHERE valuation <= 3) AS negative
                FROM ym_feedbacks
                WHERE created_date::date BETWEEN :df AND :dt
                GROUP BY 1
            """), {"df": date_from, "dt": today}).fetchall()

            ym_daily  = {str(r[0]): {"avg_r": float(r[1] or 0), "total": int(r[2]),
                                      "positive": int(r[3]), "negative": int(r[4])} for r in ym_rows}
            ym_totals = conn.execute(text("""
                SELECT count(*) total, count(*) FILTER (WHERE valuation>=4) positive,
                       count(*) FILTER (WHERE valuation<=3) negative,
                       round(avg(valuation::numeric),2) avg_r
                FROM ym_feedbacks WHERE created_date::date BETWEEN :df AND :dt
            """), {"df": date_from, "dt": today}).mappings().first()

            # ── 3. Дисциплина AI-разметки ──────────────────────────────────────
            def tagging_stats(since_days: int):
                r = conn.execute(text("""
                    SELECT count(*) total,
                           count(*) FILTER (WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed') tagged
                    FROM wb_feedbacks
                    WHERE created_date > now() - make_interval(days => :d)
                """), {"d": since_days}).mappings().first()
                total  = int(r["total"]  or 0)
                tagged = int(r["tagged"] or 0)
                return {"total": total, "tagged": tagged,
                        "pct": round(tagged / total * 100, 1) if total else 0}

            tagging = {
                "7d":  tagging_stats(7),
                "30d": tagging_stats(30),
                f"{days}d": tagging_stats(days),
            }

            # Ежедневный % разметки за период (для тренд-чарта)
            tag_daily_rows = conn.execute(text("""
                SELECT created_date::date AS day,
                       count(*) AS total,
                       count(*) FILTER (WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed') AS tagged
                FROM wb_feedbacks
                WHERE created_date::date BETWEEN :df AND :dt
                GROUP BY 1
            """), {"df": date_from, "dt": today}).fetchall()
            tag_daily = {str(r[0]): {
                "total": int(r[1]), "tagged": int(r[2]),
                "pct": round(int(r[2]) / int(r[1]) * 100, 1) if r[1] else 0
            } for r in tag_daily_rows}

            # ── 4. Воронка доотправок ──────────────────────────────────────────
            STATUS_FUNNEL = [
                ("new",             "Новая"),
                ("approved",        "Одобрена"),
                ("shipped",         "Отправлена"),
                ("in_transit",      "В пути"),
                ("ready_for_pickup","Готова к выдаче"),
                ("delivered",       "Доставлена"),
                ("closed",          "Закрыта"),
                ("rejected",        "Отказ"),
                ("returned",        "Возврат"),
            ]

            reship_by_status = conn.execute(text("""
                SELECT status, count(*) FROM reshipment_requests
                WHERE created_at::date BETWEEN :df AND :dt
                GROUP BY 1
            """), {"df": date_from, "dt": today}).fetchall()
            status_map = {r[0]: int(r[1]) for r in reship_by_status}

            reship_totals = conn.execute(text("""
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE status IN ('shipped','in_transit','ready_for_pickup','delivered','closed')) AS progressed,
                    count(*) FILTER (WHERE status IN ('delivered','closed')) AS completed,
                    count(*) FILTER (WHERE status = 'rejected') AS rejected,
                    count(*) FILTER (WHERE review_requested = true) AS review_requested,
                    count(*) FILTER (WHERE track_number IS NOT NULL AND track_number != '') AS tracked
                FROM reshipment_requests
                WHERE created_at::date BETWEEN :df AND :dt
            """), {"df": date_from, "dt": today}).mappings().first()

            # Недельная динамика доотправок
            reship_weekly = conn.execute(text("""
                SELECT date_trunc('week', created_at)::date AS wk,
                       count(*) AS created,
                       count(*) FILTER (WHERE status IN ('delivered','closed')) AS completed
                FROM reshipment_requests
                WHERE created_at::date BETWEEN :df AND :dt
                GROUP BY 1 ORDER BY 1
            """), {"df": date_from, "dt": today}).fetchall()

        def _t(m, key, default=0):
            if not m: return default
            v = m[key] if key in m.keys() else default
            return int(v or default)

        return {
            "days": days,
            "dates": all_dates,
            "sentiment": {
                "wb": {
                    "avg_rating": float(wb_totals["avg_r"] or 0) if wb_totals else 0,
                    "total":    _t(wb_totals, "total"),
                    "positive": _t(wb_totals, "positive"),
                    "negative": _t(wb_totals, "negative"),
                    "pct_positive": round(_t(wb_totals,"positive")/_t(wb_totals,"total")*100, 1) if _t(wb_totals,"total") else 0,
                    "daily": {d: wb_daily.get(d, {"avg_r":0,"total":0,"positive":0,"negative":0}) for d in all_dates},
                },
                "ym": {
                    "avg_rating": float(ym_totals["avg_r"] or 0) if ym_totals else 0,
                    "total":    _t(ym_totals, "total"),
                    "positive": _t(ym_totals, "positive"),
                    "negative": _t(ym_totals, "negative"),
                    "pct_positive": round(_t(ym_totals,"positive")/_t(ym_totals,"total")*100, 1) if _t(ym_totals,"total") else 0,
                    "daily": {d: ym_daily.get(d, {"avg_r":0,"total":0,"positive":0,"negative":0}) for d in all_dates},
                },
            },
            "tagging": {
                **tagging,
                "daily": {d: tag_daily.get(d, {"total":0,"tagged":0,"pct":0}) for d in all_dates},
            },
            "reshipment": {
                "total":          _t(reship_totals, "total"),
                "progressed":     _t(reship_totals, "progressed"),
                "completed":      _t(reship_totals, "completed"),
                "rejected":       _t(reship_totals, "rejected"),
                "review_requested": _t(reship_totals, "review_requested"),
                "tracked":        _t(reship_totals, "tracked"),
                "by_status":      {label: status_map.get(key, 0) for key, label in STATUS_FUNNEL},
                "weekly":         [{"week": str(r[0]), "created": int(r[1]), "completed": int(r[2])} for r in reship_weekly],
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts/status")
def get_alerts_status(db: Session = Depends(get_db)):
    """Vozvraschaet tekushchiy status alertov: kakie triggernuty pryamo seychas."""
    try:
        from ..cx_alert import check_alerts
        alerts = check_alerts(db)
        return {"alerts": alerts, "count": len(alerts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/test")
def send_test_alert(_=Depends(_admin_only), db: Session = Depends(get_db)):
    """Otpravlyaet testovoe soobshchenie v Telegram dlya proverki nastroyki."""
    try:
        from ..cx_alert import send_tg
        ok = send_tg("Test alert iz CX Control — Telegram nastroyen korrektno!")
        return {"sent": ok, "message": "OK" if ok else "TG_ALERT_BOT_TOKEN ili TG_ALERT_CHAT_ID ne nastroeny"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/staff")
def get_staff(db: Session = Depends(get_db)):
    """Список cs_manager-пользователей с последней активностью."""
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(text("""
                SELECT u.id, u.username, u.role, u.tg_chat_id,
                       a.last_seen, a.last_path, a.session_start
                FROM users u
                LEFT JOIN user_activity a ON a.user_id = u.id
                WHERE u.role = 'cs_manager'
                ORDER BY u.id
            """)).mappings().fetchall()
        result = []
        for r in rows:
            last_seen = r["last_seen"]
            if last_seen:
                diff_min = int((
                    __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                    - last_seen.replace(tzinfo=__import__("datetime").timezone.utc)
                ).total_seconds() / 60)
                if diff_min < 5:
                    status = "online"
                elif diff_min < 30:
                    status = "recent"
                else:
                    status = "offline"
            else:
                diff_min = None
                status = "offline"
            name = r["username"].split("@")[0].replace(".", " ").title()
            result.append({
                "id": r["id"],
                "username": r["username"],
                "name": name,
                "role": r["role"],
                "tg_chat_id": r["tg_chat_id"],
                "tg_linked": bool(r["tg_chat_id"]),
                "status": status,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "last_seen_min": diff_min,
                "last_path": r["last_path"],
                "session_start": r["session_start"].isoformat() if r["session_start"] else None,
            })
        return {"staff": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sla-settings")
def get_sla_settings(db: Session = Depends(get_db)):
    """Возвращает текущие SLA-лимиты из БД."""
    with db.bind.connect() as conn:
        sla = _load_sla(conn)
    labels = {
        "wb_chat":    "WB · Чаты с покупателями",
        "wb_rev_neg": "WB · Отзывы негатив (≤3★)",
        "wb_rev_pos": "WB · Отзывы позитив (≥4★)",
        "wb_q":       "WB · Вопросы",
        "ym_q":       "ЯМ · Вопросы",
        "mod_wb_claims":   "Модерация · WB рекламации",
        "mod_ym_returns":  "Модерация · ЯМ возвраты",
        "mod_ozon_returns":"Модерация · Ozon возвраты",
        "mod_wb_fb":       "Модерация · WB отзывы",
        "mod_ym_fb":       "Модерация · ЯМ отзывы",
    }
    order = ["wb_chat", "wb_rev_neg", "wb_rev_pos", "wb_q", "ym_q",
              "mod_wb_claims", "mod_ym_returns", "mod_ozon_returns", "mod_wb_fb", "mod_ym_fb"]
    return {"settings": [
        {"id": k, "label": labels.get(k, k), "sla_min": sla[k]}
        for k in order if k in sla
    ]}


@router.put("/sla-settings")
def update_sla_settings(body: dict, db: Session = Depends(get_db)):
    """Сохраняет SLA-лимиты. body: {queue_id: sla_min, ...}"""
    try:
        with db.bind.connect() as conn:
            for queue_id, sla_min in body.items():
                sla_min = max(5, int(sla_min))
                conn.execute(text("""
                    INSERT INTO cx_sla_settings (queue_id, sla_min, updated_at)
                    VALUES (:qid, :sla, now())
                    ON CONFLICT (queue_id) DO UPDATE
                        SET sla_min = :sla, updated_at = now()
                """), {"qid": queue_id, "sla": sla_min})
            conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/staff/{user_id}/tg")
def set_staff_tg(user_id: int, body: dict, db: Session = Depends(get_db)):
    """Привязать tg_chat_id к профилю сотрудника."""
    tg_chat_id = body.get("tg_chat_id", "").strip() or None
    try:
        with db.bind.connect() as conn:
            conn.execute(text(
                "UPDATE users SET tg_chat_id = :tg WHERE id = :id"
            ), {"tg": tg_chat_id, "id": user_id})
            conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Public router (для всех авторизованных — пинг активности) ────────────────
from pydantic import BaseModel as _BM

class _PingBody(_BM):
    path: str = "/"

public_router = APIRouter(prefix="/api/v1/cx-control", tags=["CX Control Public"])

@public_router.post("/activity/ping")
def ping_activity(body: _PingBody, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Фиксирует активность текущего пользователя (последний визит + текущая страница)."""
    try:
        with db.bind.connect() as conn:
            conn.execute(text("""
                INSERT INTO user_activity (user_id, username, last_seen, last_path, session_start)
                SELECT u.id, u.username, now(), :path,
                       COALESCE(
                           (SELECT session_start FROM user_activity WHERE user_id = u.id),
                           now()
                       )
                FROM users u WHERE u.username = :uname
                ON CONFLICT (user_id) DO UPDATE
                    SET last_seen  = now(),
                        last_path  = :path,
                        session_start = CASE
                            WHEN user_activity.last_seen < now() - interval '2 hours'
                            THEN now()
                            ELSE user_activity.session_start
                        END
            """), {"path": body.path, "uname": current_user["username"]})
            conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
