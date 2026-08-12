"""
Системный монитор CX-Dashboard → Telegram.

Два режима запуска (через cron):

  python3 system_monitor.py digest
      Утренний дайджест сбоев за последние 24 часа из таблицы system_logs.
      Группирует ошибки крон-воркеров и ИИ-тегирования, шлёт отчёт в ТГ.
      Если сбоев нет — шлёт короткое «✅ всё чисто» (heartbeat).
      Крон: 30 5 * * *   (= 08:30 МСК, сервер в UTC)

  python3 system_monitor.py health
      Проверка «жив ли бой»: бэкенд/сайт, БД, свежесть крон-воркеров.
      При падении шлёт мгновенный алерт админу И модератору.
      При восстановлении — сообщение «снова в строю».
      Крон: */5 * * * *

──────────────────────────────────────────────────────────────────────
Настройка (.env на сервере /root/cx-dashboard/.env):

  TG_ADMIN_CHAT_ID=123456789          # chat_id администратора (обязателен)
  TG_MODERATOR_CHAT_ID=987654321      # chat_id модератора (для критичных боёв)
  # Отправка — через тот же Cloudflare Worker, что и cx_alert:
  CF_WORKER_URL=https://lingering-recipe-b1b3.mursalimov.workers.dev
  # (опционально) прямая отправка своим ботом вместо CF Worker:
  TG_ALERT_BOT_TOKEN=123456:ABC...

Как узнать chat_id: напишите боту любое сообщение и откройте
  https://api.telegram.org/bot<TOKEN>/getUpdates  → поле "chat":{"id":...}
──────────────────────────────────────────────────────────────────────
"""
import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# ── Конфиг ──────────────────────────────────────────────────────────────
# Рабочая БД — ТОЛЬКО локальная (как в worker.py / backend/database.py).
# ВНИМАНИЕ: переменную .env DB_URL НЕ используем — там лежит неактуальный
# Supabase-URL, а Supabase в проекте не используется.
DB_URL = os.getenv(
    "MONITOR_DB_URL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://lingering-recipe-b1b3.mursalimov.workers.dev")
TG_BOT_TOKEN = os.getenv("TG_ALERT_BOT_TOKEN", "").strip()

ADMIN_CHAT = os.getenv("TG_ADMIN_CHAT_ID", os.getenv("TG_ALERT_CHAT_ID", "")).strip()
MODER_CHAT = os.getenv("TG_MODERATOR_CHAT_ID", "").strip()

PUBLIC_HEALTH_URL = os.getenv("PUBLIC_HEALTH_URL", "https://cxvo.ru/api/v1/health/db")

# Статусы в system_logs, которые считаем сбоями / предупреждениями
ERROR_STATUSES = ("ERROR", "FAIL", "CRITICAL")
WARN_STATUSES = ("WARNING",)

# Если крон-воркеры вообще не писали в журнал дольше этого времени — считаем, что встали.
# Реальное расписание неравномерное (макс. дневной разрыв ~4ч), поэтому порог 8ч:
# ловит реальную смерть крон-демона, но не даёт ложных тревог в штатные окна.
WORKER_SILENCE_HOURS = 8
# Как часто повторно напоминать о продолжающемся сбое (health)
HEALTH_REMINDER_MIN = 30

engine = create_engine(DB_URL, pool_pre_ping=True)


# ── Словарь синков (action → читаемое название) ─────────────────────────
# Зеркало frontend/src/syncCatalog.js; обновлять синхронно.
_SYNC_LABELS = {
    "sync_feedbacks":                   "Отзывы покупателей (ЯМ)",
    "sync_assortment":                  "Ассортимент (ЯМ)",
    "Синхронизатор Претензий":          "Претензии и возвраты (WB)",
    "Синхронизатор Заказов":            "Заказы (WB)",
    "Синхронизатор Логистики":          "Логистика (WB)",
    "Воркер Логистика":                 "Логистика — запуск (WB)",
    "Синхронизатор Инвойсов":           "Инвойсы (Google Sheets)",
    "Синхронизатор Матрицы":            "Матрица товаров",
    "Синхронизатор Рейтингов":          "Рейтинги (WB)",
    "Воркер Рейтинги":                  "Рейтинги — запуск (WB)",
    "Итоги Рейтингов ВБ":               "Сводка рейтингов (WB)",
    "Синхронизатор VOC":                "Voice of Customer (WB)",
    "Финотчёт WB":                      "Финансовый отчёт (WB)",
    "Единый Воркер":                    "Общий воркер WB",
    "Модерация":                        "Модерация возвратов",
    "ym_sync_returns":                  "Возвраты (ЯМ)",
    "ym_sync_orders":                   "Заказы (ЯМ)",
    "ym_sync_questions":                "Вопросы покупателей (ЯМ)",
    "ozon_sync_questions":              "Вопросы покупателей (Ozon)",
    "ym_sync_ratings":                  "Рейтинги и отзывы (ЯМ)",
    "ym_sync_prices":                   "Цены (ЯМ)",
    # "ym_sync_supplies":                 "Поставки (ЯМ)",  # отключено

    "ym_report_realization_generate":   "Отчёт о реализации (ЯМ)",
    "ym_report_services_generate":      "Отчёт по услугам (ЯМ)",
    "ozon_sync_returns":                "Возвраты (OZON)",
    "ozon_rule_tags":                   "Авто-теги по правилам (OZON)",
}
import re as _re
_SYNC_PATTERNS = [
    (_re.compile(r'^ИИ\s+Разметка\s+WB',  _re.I), "ИИ-разметка отзывов (WB)"),
    (_re.compile(r'^ИИ\s+Разметка\s+ЯМ',  _re.I), "ИИ-разметка отзывов (ЯМ)"),
    (_re.compile(r'^ИИ\s+WB\s+пачка',     _re.I), "ИИ-разметка WB — пачка"),
    (_re.compile(r'^ИИ\s+ЯМ\s+пачка',     _re.I), "ИИ-разметка ЯМ — пачка"),
    (_re.compile(r'^ИИ\s+Отзывы',         _re.I), "ИИ-обработка отзывов"),
    (_re.compile(r'^Бэкенд\s+ИИ',         _re.I), "ИИ-бэкенд"),
    (_re.compile(r'^Отзывы\s+Пачка\s+\d', _re.I), "Отзывы — пачка"),
    (_re.compile(r'^Пачка\s+\d',          _re.I), "Обработка — пачка"),
]

def _label(action: str) -> str:
    """action из system_logs → читаемое название (с техническим именем в скобках если переведено)."""
    raw = (action or "").strip()
    human = _SYNC_LABELS.get(raw)
    if human:
        return f"{human} <code>{raw}</code>" if human != raw else raw
    for pat, lbl in _SYNC_PATTERNS:
        if pat.match(raw):
            return f"{lbl} <code>{raw}</code>"
    return raw


# ── Отправка в Telegram ─────────────────────────────────────────────────
def _send_one(chat_id: str, text_msg: str) -> bool:
    """Одному получателю. Основной путь — Cloudflare Worker (проверенный на RU-сервере,
    где прямой api.telegram.org заблокирован). Прямой бот — запасной вариант."""
    if not chat_id:
        return False
    # 1) Через Cloudflare Worker (тот же путь, что и у cx_alert)
    if CF_WORKER_URL:
        try:
            r = requests.post(
                CF_WORKER_URL,
                json={"chat_id": chat_id, "text": text_msg},
                timeout=15,
            )
            if r.ok:
                return True
            print(f"[monitor] CF Worker error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[monitor] CF Worker exception: {e}")
    # 2) Запасной путь — прямая отправка своим ботом (если задан токен и CF недоступен)
    if TG_BOT_TOKEN:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text_msg,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15,
            )
            if r.ok:
                return True
            print(f"[monitor] direct TG error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[monitor] direct TG exception: {e}")
    return False


def send_tg(text_msg: str, recipients) -> None:
    sent = 0
    for chat in recipients:
        if chat and _send_one(chat, text_msg):
            sent += 1
    print(f"[monitor] отправлено получателям: {sent}/{len([c for c in recipients if c])}")


def get_recipients() -> list:
    """Получатели алертов — профили с включённым флагом tg_alerts и заданным чатом.
    Настраивается в панели: Панель управления → Пользователи → Telegram.
    Если в БД никто не настроен — откат на .env (TG_ADMIN_CHAT_ID / TG_MODERATOR_CHAT_ID)."""
    chats = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT tg_chat_id FROM users
                WHERE COALESCE(tg_alerts, false) = true
                  AND tg_chat_id IS NOT NULL AND btrim(tg_chat_id) <> ''
            """)).fetchall()
        chats = [str(r[0]).strip() for r in rows if r[0]]
    except Exception as e:
        print(f"[monitor] не смог прочитать получателей из БД: {e}")
    if not chats:
        chats = [c for c in (ADMIN_CHAT, MODER_CHAT) if c]
        if chats:
            print("[monitor] получатели из БД не заданы — использую .env fallback")
    return chats


def _msk(dt) -> str:
    """UTC-время из БД → строка МСК (UTC+3)."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(timezone(timedelta(hours=3)))).strftime("%d.%m %H:%M")


# ── Бизнес-проверки (очереди, рейтинг, разметка) ────────────────────────
def _fmtage(minutes: int) -> str:
    if minutes < 60: return f"{minutes} мин"
    h = minutes // 60
    if h < 24: return f"{h} ч"
    return f"{h // 24} дн {h % 24} ч"

def _check_business(conn) -> list:
    """Проверяет бизнес-условия и возвращает список строк для дайджеста."""
    issues = []
    try:
        QUEUES = [
            ("WB отзывы (негатив)", "wb_feedbacks", "state='active' AND COALESCE(valuation,0)<=3", 240),
            ("WB отзывы (позитив)", "wb_feedbacks", "state='active' AND COALESCE(valuation,0)>=4", 1440),
            ("WB вопросы",          "wb_questions",  "state='active'",          240),
            ("ЯМ вопросы",          "ym_questions",  "state='UNANSWERED'",       240),
        ]
        for qlabel, tbl, cond, sla_min in QUEUES:
            try:
                r = conn.execute(text(f"""
                    SELECT
                        count(*) FILTER (WHERE now()-created_date > make_interval(mins=>{sla_min})) AS red,
                        COALESCE(ROUND(EXTRACT(epoch FROM now()-min(created_date))/60)::int, 0) AS oldest_min
                    FROM {tbl} WHERE {cond}
                """)).fetchone()
                red, oldest = int(r[0] or 0), int(r[1] or 0)
                if red > 5 and oldest > sla_min * 3:
                    issues.append(f"🔴 <b>{qlabel}</b>: {red} просрочено, старейшее {_fmtage(oldest)}")
            except Exception:
                pass

        # Разметка ИИ
        try:
            r2 = conn.execute(text("""
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed') AS tagged
                FROM wb_feedbacks WHERE created_date > now() - interval '24 hours'
            """)).fetchone()
            total_24h, tagged_24h = int(r2[0] or 0), int(r2[1] or 0)
            if total_24h > 20:
                pct = round(tagged_24h / total_24h * 100, 1)
                if pct < 40:
                    issues.append(f"🟡 <b>ИИ-разметка WB отстаёт</b>: {pct}% ({tagged_24h}/{total_24h} за 24ч)")
        except Exception:
            pass

        # Рейтинг WB
        try:
            r3 = conn.execute(text("""
                SELECT round(avg(valuation::numeric), 2) FROM wb_feedbacks
                WHERE created_date::date = current_date AND valuation IS NOT NULL
            """)).scalar()
            if r3 is not None and float(r3) < 4.3:
                issues.append(f"🟡 <b>Рейтинг WB сегодня: {r3}</b> — ниже 4.3")
        except Exception:
            pass

    except Exception as e:
        print(f"[monitor] _check_business error: {e}")
    return issues


# ── Режим DIGEST: утренний отчёт о сбоях за сутки ────────────────────────
def run_digest() -> None:
    admin_and_moder = get_recipients()
    with engine.connect() as conn:
        errs = conn.execute(text("""
            SELECT action, count(*) AS cnt, max(created_at) AS last_at,
                   (array_agg(details ORDER BY created_at DESC))[1] AS last_detail
            FROM system_logs
            WHERE created_at > now() - interval '24 hours'
              AND upper(status) = ANY(:statuses)
            GROUP BY action ORDER BY cnt DESC
        """), {"statuses": list(ERROR_STATUSES)}).fetchall()

        warns = conn.execute(text("""
            SELECT action, count(*) AS cnt
            FROM system_logs
            WHERE created_at > now() - interval '24 hours'
              AND upper(status) = ANY(:statuses)
            GROUP BY action ORDER BY cnt DESC
        """), {"statuses": list(WARN_STATUSES)}).fetchall()

        biz_issues = _check_business(conn)

    all_clear = not errs and not warns and not biz_issues
    if all_clear:
        send_tg(
            "✅ <b>CX-Dashboard — утренняя сводка</b>\n"
            "За последние 24 часа сбоев в кронах, тегировании и очередях не зафиксировано.\n"
            "<i>Мониторинг работает.</i>",
            admin_and_moder,
        )
        return

    lines = ["📋 <b>CX-Dashboard — утренняя сводка</b>\n"]

    if errs:
        total = sum(int(r.cnt) for r in errs)
        lines.append(f"⚠️ <b>Системные сбои:</b> {total} ошибок в {len(errs)} модул(ях)")
        for r in errs:
            detail = (r.last_detail or "").strip().replace("\n", " ")
            if len(detail) > 180:
                detail = detail[:180] + "…"
            lines.append(f"🔴 <b>{_label(r.action)}</b> — {r.cnt}× (последний {_msk(r.last_at)} МСК)")
            if detail:
                lines.append(f"   <i>{detail}</i>")

    if warns:
        lines.append("\n🟡 <b>Предупреждения:</b>")
        for w in warns:
            lines.append(f"   • {_label(w.action)} — {w.cnt}×")

    if biz_issues:
        lines.append("\n📊 <b>Очереди и показатели:</b>")
        for bi in biz_issues:
            lines.append(f"   {bi}")

    lines.append("\n<i>cxvo.ru — Системный журнал</i>")
    send_tg("\n".join(lines), admin_and_moder)


# ── Режим HEALTH: мгновенный контроль «боя» ──────────────────────────────
def _ensure_state_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS system_monitor_state (
            check_key     VARCHAR(80) PRIMARY KEY,
            is_down       BOOLEAN NOT NULL DEFAULT false,
            since         TIMESTAMP,
            last_notified TIMESTAMP
        )
    """))
    conn.commit()


def _handle(conn, key: str, label: str, down: bool, detail: str, recipients) -> None:
    """Единая логика: алерт при падении, напоминание, сообщение о восстановлении."""
    row = conn.execute(
        text("SELECT is_down, since, last_notified FROM system_monitor_state WHERE check_key=:k"),
        {"k": key},
    ).fetchone()
    was_down = bool(row[0]) if row else False
    since = row[1] if row else None
    last_notified = row[2] if row else None
    now = datetime.now(timezone.utc)

    def _age(ts):
        if not ts:
            return "?"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        mins = int((now - ts).total_seconds() // 60)
        if mins < 60:
            return f"{mins} мин"
        return f"{mins // 60} ч {mins % 60} мин"

    if down:
        need_notify = False
        if not was_down:
            since = now
            need_notify = True
        else:
            ln = last_notified
            if ln and ln.tzinfo is None:
                ln = ln.replace(tzinfo=timezone.utc)
            if not ln or (now - ln) > timedelta(minutes=HEALTH_REMINDER_MIN):
                need_notify = True
        conn.execute(text("""
            INSERT INTO system_monitor_state (check_key, is_down, since, last_notified)
            VALUES (:k, true, :since, :ln)
            ON CONFLICT (check_key) DO UPDATE SET is_down=true, since=:since,
                last_notified = CASE WHEN :notify THEN :now ELSE system_monitor_state.last_notified END
        """), {"k": key, "since": since, "ln": now if need_notify else last_notified,
               "notify": need_notify, "now": now})
        conn.commit()
        if need_notify:
            dur = _age(since) if was_down else "только что"
            send_tg(
                f"🚨 <b>CX-Dashboard: СБОЙ</b>\n"
                f"<b>{label}</b>\n{detail}\n"
                f"<i>Длится: {dur}. Проверьте систему.</i>",
                recipients,
            )
    else:
        if was_down:
            conn.execute(text("""
                UPDATE system_monitor_state SET is_down=false, since=NULL, last_notified=:now
                WHERE check_key=:k
            """), {"k": key, "now": now})
            conn.commit()
            send_tg(
                f"✅ <b>CX-Dashboard: восстановлено</b>\n"
                f"<b>{label}</b> снова в строю (простой {_age(since)}).",
                recipients,
            )


def run_health() -> None:
    recipients = get_recipients()

    # 1) Публичный сайт + бэкенд (то, что видит пользователь)
    site_down, site_detail = False, ""
    try:
        r = requests.get(PUBLIC_HEALTH_URL, timeout=12)
        if not r.ok:
            site_down, site_detail = True, f"HTTP {r.status_code} на {PUBLIC_HEALTH_URL}"
        else:
            try:
                if (r.json() or {}).get("db_status") != "ok":
                    site_down, site_detail = True, "health-эндпоинт вернул db_status ≠ ok"
            except Exception:
                site_down, site_detail = True, "health-эндпоинт вернул не-JSON"
    except Exception as e:
        site_down, site_detail = True, f"Сайт cxvo.ru не отвечает: {e}"

    # 2) База данных напрямую
    db_down, db_detail = False, ""
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        db_down, db_detail = True, f"Не удалось подключиться к PostgreSQL: {e}"

    # 3) Свежесть крон-воркеров (по последней записи в журнале)
    cron_down, cron_detail = False, ""
    last_log_at = None
    if not db_down:
        try:
            with engine.connect() as c:
                last_log_at = c.execute(text("SELECT max(created_at) FROM system_logs")).scalar()
            if last_log_at is None:
                cron_down, cron_detail = True, "В system_logs нет ни одной записи"
            else:
                ref = last_log_at.replace(tzinfo=timezone.utc) if last_log_at.tzinfo is None else last_log_at
                if datetime.now(timezone.utc) - ref > timedelta(hours=WORKER_SILENCE_HOURS):
                    cron_down = True
                    cron_detail = (f"Воркеры молчат с {_msk(last_log_at)} МСК "
                                   f"(> {WORKER_SILENCE_HOURS} ч). Возможно, встал cron-демон.")
        except Exception as e:
            cron_detail = f"(не удалось проверить свежесть воркеров: {e})"

    with engine.connect() as conn:
        _ensure_state_table(conn)
        _handle(conn, "site", "Сайт / бэкенд cxvo.ru", site_down, site_detail, recipients)
        _handle(conn, "db", "База данных PostgreSQL", db_down, db_detail, recipients)
        # про застой воркеров тревожим только когда БД жива (иначе дублируем db-алерт)
        _handle(conn, "cron", "Крон-воркеры (синхронизация)", cron_down, cron_detail, recipients)

    print(f"[monitor] health: site_down={site_down} db_down={db_down} cron_down={cron_down}")


# ── Точка входа ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if not ADMIN_CHAT and not MODER_CHAT:
        print("[monitor] ⚠️  Не заданы TG_ADMIN_CHAT_ID / TG_MODERATOR_CHAT_ID в .env — некому слать.")
    if mode == "digest":
        run_digest()
    elif mode == "health":
        run_health()
    else:
        print("Использование: python3 system_monitor.py [digest|health]")
        sys.exit(1)
