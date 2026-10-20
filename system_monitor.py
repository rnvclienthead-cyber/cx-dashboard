"""
Системный монитор CX-Dashboard → Telegram.

Два режима запуска (через cron):

  python3 system_monitor.py digest
      Утренняя сводка: только сбои, которые к моменту отправки ещё не
      закрылись успехом (последняя ошибка новее последнего успеха).
      Если актуальных сбоев нет — шлёт короткое «✅ всё чисто» (heartbeat).
      Крон: 30 5 * * *   (= 08:30 МСК, сервер в UTC)

  python3 system_monitor.py health
      Проверка «жив ли бой»: бэкенд/сайт, БД, свежесть крон-воркеров,
      медленный ответ API (>2.5с), память и диск.
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
import time
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from backend.loop_watch import classify_disk, classify_http_probe, classify_ram
from backend.rating_digest import collect_critical, format_critical
from backend.telegram_proxy import strip_tg_html
from backend.ym_ratings_freshness import YM_RATINGS_STALE_HOURS, ym_ratings_is_stale
from backend.wb_item_rating import wb_ratings_date_is_stale

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
PUBLIC_LIVE_URL = os.getenv("PUBLIC_LIVE_URL", "https://cxvo.ru/api/v1/health/live")

# Статусы в system_logs, которые считаем сбоями / предупреждениями
ERROR_STATUSES = ("ERROR", "FAIL", "CRITICAL")
WARN_STATUSES = ("WARNING",)
SUCCESS_STATUSES = ("SUCCESS", "OK")

# Если крон-воркеры вообще не писали в журнал дольше этого времени — считаем, что встали.
# Реальное расписание неравномерное (макс. дневной разрыв ~4ч), поэтому порог 8ч:
# ловит реальную смерть крон-демона, но не даёт ложных тревог в штатные окна.
WORKER_SILENCE_HOURS = 8
# Как часто повторно напоминать о продолжающемся сбое (health)
HEALTH_REMINDER_MIN = 30
# Напоминание про зависшие рейтинги ЯМ — раз в 6 часов, не каждые 30 мин.
YM_RATINGS_REMINDER_MIN = 360
# Сайт «тормозит», если лёгкая проверка дольше этого.
SLOW_MS = 2500
RAM_ALERT_PCT = 90
DISK_ALERT_GB = 2.0
# Резервная копия базы снимается в 02:00 UTC. Порог 30 ч — сутки плюс запас
# на один пропуск окна; ловит и «скрипт упал», и «крон снесли».
BACKUP_DIR = os.getenv("BACKUP_DIR", "/root/backups/db")
BACKUP_MAX_AGE_HOURS = 30
BACKUP_REMINDER_MIN = 360
LIVE_TIMEOUT_SEC = 6
DB_HEALTH_TIMEOUT_SEC = 12

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

# Утренняя сводка: ИИ-теги не показываем (Рустам, 19.10.2026).
_AI_TAG_DIGEST_RE = _re.compile(
    r"^(ИИ\b|Бэкенд\s+ИИ|AI-тегировщик|ai_tagger)",
    _re.I,
)


def _is_ai_tag_digest_action(action: str) -> bool:
    raw = (action or "").strip()
    if raw == "ozon_rule_tags":
        return True
    return bool(_AI_TAG_DIGEST_RE.match(raw))


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
                json={"chat_id": chat_id, "text": strip_tg_html(text_msg)},
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


def probe_url(url: str, timeout: float):
    """GET с замером времени. Возвращает (response_or_None, elapsed_ms, error_or_None)."""
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=timeout)
        ms = (time.monotonic() - t0) * 1000
        return r, ms, None
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        return None, ms, e


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


def ym_ratings_alert_text(last_success_at, now=None):
    """Текст алерта, если рейтинги ЯМ протухли. Иначе None."""
    if not ym_ratings_is_stale(last_success_at, now=now):
        return None
    if last_success_at is None:
        return "🔴 <b>Рейтинги ЯМ</b>: ни одного успешного сбора"
    return (
        f"🔴 <b>Рейтинги ЯМ не обновлялись</b> с {_msk(last_success_at)} МСК "
        f"(> {YM_RATINGS_STALE_HOURS} ч)"
    )


def _ym_ratings_last_success(conn):
    return conn.execute(text("""
        SELECT max(created_at) FROM system_logs
        WHERE action = 'ym_sync_ratings' AND lower(status) = 'success'
    """)).scalar()

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

        try:
            last_ok = _ym_ratings_last_success(conn)
            line = ym_ratings_alert_text(last_ok)
            if line:
                issues.append(line)
        except Exception:
            pass

        try:
            last_wb = conn.execute(text(
                "SELECT max(sys_date) FROM wb_ratings_raw"
            )).scalar()
            if wb_ratings_date_is_stale(last_wb):
                issues.append(
                    f"🔴 <b>Рейтинги WB не обновлялись</b> с {last_wb or '—'}"
                )
        except Exception:
            pass

    except Exception as e:
        print(f"[monitor] _check_business error: {e}")
    return issues


def fetch_unresolved_logs(conn, problem_statuses):
    """Модули, у которых последняя ошибка новее последнего успеха.

    Берём ошибки за сутки. Успех ищем за 30 дней — иначе вчерашний починенный
    сбой снова всплывёт, если за ночь не было нового SUCCESS.
    """
    return conn.execute(text("""
        WITH last_err AS (
            SELECT action,
                   max(created_at) AS last_at,
                   (array_agg(details ORDER BY created_at DESC))[1] AS last_detail
            FROM system_logs
            WHERE created_at > now() - interval '24 hours'
              AND upper(status) = ANY(:problem)
            GROUP BY action
        ),
        last_ok AS (
            SELECT action, max(created_at) AS last_at
            FROM system_logs
            WHERE created_at > now() - interval '30 days'
              AND upper(status) = ANY(:ok)
            GROUP BY action
        )
        SELECT e.action, e.last_at, e.last_detail
        FROM last_err e
        LEFT JOIN last_ok o ON o.action = e.action
        WHERE o.last_at IS NULL OR e.last_at > o.last_at
        ORDER BY e.last_at DESC
    """), {"problem": list(problem_statuses), "ok": list(SUCCESS_STATUSES)}).fetchall()


def _rating_section(conn) -> str:
    try:
        return format_critical(collect_critical(conn))
    except Exception as e:
        print(f"[monitor] rating digest error: {e}")
        return "⭐ <b>Критические товары по рейтингу</b>\nНе удалось собрать список."


# ── Режим DIGEST: только сбои, которые ещё не закрылись ──────────────────
def run_digest() -> None:
    admin_and_moder = get_recipients()
    with engine.connect() as conn:
        errs = [
            r for r in fetch_unresolved_logs(conn, ERROR_STATUSES)
            if not _is_ai_tag_digest_action(r.action)
        ]
        warns = [
            r for r in fetch_unresolved_logs(conn, WARN_STATUSES)
            if not _is_ai_tag_digest_action(r.action)
        ]
        biz_issues = _check_business(conn)
        rating_section = _rating_section(conn)

    all_clear = not errs and not warns and not biz_issues
    if all_clear:
        send_tg(
            "✅ <b>CX-Dashboard — утренняя сводка</b>\n"
            "На этот момент актуальных сбоев нет.\n"
            "<i>Мониторинг работает.</i>\n\n"
            f"{rating_section}",
            admin_and_moder,
        )
        return

    lines = ["📋 <b>CX-Dashboard — утренняя сводка</b>\n"]
    lines.append("Только то, что сейчас не закрыто успехом.\n")

    if errs:
        lines.append(f"⚠️ <b>Актуальные сбои:</b> {len(errs)}")
        for r in errs:
            detail = (r.last_detail or "").strip().replace("\n", " ")
            if len(detail) > 180:
                detail = detail[:180] + "…"
            lines.append(f"🔴 <b>{_label(r.action)}</b> (последний {_msk(r.last_at)} МСК)")
            if detail:
                lines.append(f"   <i>{detail}</i>")

    if warns:
        lines.append("\n🟡 <b>Актуальные предупреждения:</b>")
        for w in warns:
            lines.append(f"   • {_label(w.action)} (последний {_msk(w.last_at)} МСК)")

    if biz_issues:
        lines.append("\n📊 <b>Очереди и показатели:</b>")
        for bi in biz_issues:
            lines.append(f"   {bi}")

    lines.append(f"\n{rating_section}")
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


def _handle(conn, key: str, label: str, down: bool, detail: str, recipients,
            reminder_min: int = HEALTH_REMINDER_MIN) -> None:
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
            if not ln or (now - ln) > timedelta(minutes=reminder_min):
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


def _check_backup() -> tuple[bool, str]:
    """Свежесть резервной копии базы — по файлам на диске, а не по журналу.

    Проверяем сами файлы: запись в журнале могла остаться от прошлого раза,
    а копии — пропасть.
    """
    try:
        folder = os.path.join(BACKUP_DIR)
        if not os.path.isdir(folder):
            return True, f"Каталог копий {folder} не найден — бэкап базы не настроен."
        names = os.listdir(folder)
        # Порог размера — только чтобы не принять оборванный дамп за копию.
        # Маленькие базы (Навигатор, полка) здесь не сравниваем с порогом CX.
        checks = (
            ("cx_dashboard-", 30.0, True),
            ("kontrolmp-", 5.0, False),
            ("navigator-", 0.2, False),
            ("shared_facts-", 0.01, False),
        )
        problems = []
        for prefix, min_mb, required in checks:
            dumps = [f for f in names if f.startswith(prefix) and f.endswith(".dump")]
            if not dumps:
                if required:
                    problems.append(f"нет копии {prefix[:-1]}")
                continue
            newest = max(dumps, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
            path = os.path.join(folder, newest)
            age_h = (time.time() - os.path.getmtime(path)) / 3600
            if age_h > BACKUP_MAX_AGE_HOURS:
                problems.append(
                    f"{newest} снята {age_h:.0f} ч назад (порог {BACKUP_MAX_AGE_HOURS} ч)"
                )
                continue
            size_mb = os.path.getsize(path) / (1024 ** 2)
            if size_mb < min_mb:
                problems.append(f"{newest} весит всего {size_mb:.1f} МБ — похоже, она неполная")
        if problems:
            return True, "Ночной бэкап не отработал. " + "; ".join(problems) + "."
        return False, ""
    except Exception as e:
        return False, f"(не удалось проверить копии базы: {e})"


def run_health() -> None:
    recipients = get_recipients()

    # 1) Лёгкая проверка API (без БД) — ловит зависание процесса
    live_r, live_ms, live_err = probe_url(PUBLIC_LIVE_URL, LIVE_TIMEOUT_SEC)
    live_ok = bool(live_r is not None and live_r.ok)
    live_detail = ""
    live_legacy = bool(live_r is not None and live_r.status_code == 404)
    if live_legacy:
        live_ok = True
    elif live_err:
        live_detail = f"API не отвечает за {live_ms:.0f} мс: {live_err}"
    elif live_r is not None and not live_r.ok:
        live_detail = f"HTTP {live_r.status_code} на {PUBLIC_LIVE_URL} ({live_ms:.0f} мс)"
    live_kind = "ok" if live_legacy else classify_http_probe(live_ok, live_ms)
    site_down = live_kind == "down"
    site_slow = live_kind == "slow"
    if site_slow:
        live_detail = f"API ответил за {live_ms:.0f} мс (порог {SLOW_MS:.0f} мс)"

    # 2) Публичный health с БД (то, что видит пользователь через nginx)
    db_http_down, db_http_detail = False, ""
    try:
        r, db_ms, db_err = probe_url(PUBLIC_HEALTH_URL, DB_HEALTH_TIMEOUT_SEC)
        if db_err:
            db_http_down, db_http_detail = True, f"health/db не отвечает: {db_err}"
        elif r is None or not r.ok:
            db_http_down, db_http_detail = True, f"HTTP {getattr(r, 'status_code', '?')} на {PUBLIC_HEALTH_URL}"
        else:
            try:
                payload = r.json() or {}
                if payload.get("db_status") != "ok":
                    db_http_down, db_http_detail = True, "health-эндпоинт вернул db_status ≠ ok"
                elif classify_http_probe(True, db_ms) == "slow" and not site_down:
                    # Медленная БД — отдельный сигнал, если API в целом ещё жив
                    site_slow = True
                    live_detail = live_detail or f"База ответила за {db_ms:.0f} мс"
            except Exception:
                db_http_down, db_http_detail = True, "health-эндпоинт вернул не-JSON"
    except Exception as e:
        db_http_down, db_http_detail = True, f"Сайт cxvo.ru не отвечает: {e}"

    if db_http_down and not site_down:
        site_down = True
        live_detail = db_http_detail
    if site_down:
        site_slow = False
    site_detail = live_detail or db_http_detail

    # 3) База данных напрямую
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

    # 4) Свежесть рейтингов ЯМ (успешный сбор, не «скопировали вчера»)
    ratings_down, ratings_detail = False, ""
    if not db_down:
        try:
            with engine.connect() as c:
                last_ok = _ym_ratings_last_success(c)
            if ym_ratings_is_stale(last_ok):
                ratings_down = True
                if last_ok is None:
                    ratings_detail = "Ни одного успешного сбора рейтингов ЯМ."
                else:
                    ratings_detail = (
                        f"Последний успешный сбор: {_msk(last_ok)} МСК. "
                        "Кабинет Яндекса не пускает или задача не запускалась."
                    )
        except Exception as e:
            ratings_detail = f"(не удалось проверить рейтинги ЯМ: {e})"

    # 5) Свежесть рейтингов WB (последняя дата в wb_ratings_raw)
    wb_ratings_down, wb_ratings_detail = False, ""
    if not db_down:
        try:
            with engine.connect() as c:
                last_wb = c.execute(text("SELECT max(sys_date) FROM wb_ratings_raw")).scalar()
            if wb_ratings_date_is_stale(last_wb):
                wb_ratings_down = True
                wb_ratings_detail = (
                    f"Последний день в таблице: {last_wb or 'нет данных'}. "
                    "API оценок WB не отдал дни или задача не записала строки."
                )
        except Exception as e:
            wb_ratings_detail = f"(не удалось проверить рейтинги WB: {e})"

    ram_down, ram_detail = False, ""
    disk_down, disk_detail = False, ""
    try:
        import psutil
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        if classify_ram(ram.percent):
            ram_down = True
            ram_detail = (
                f"Память занята на {ram.percent:.0f}% "
                f"({round(ram.used / (1024**3), 1)} из {round(ram.total / (1024**3), 1)} ГБ)."
            )
        free_gb = disk.free / (1024**3)
        if classify_disk(free_gb):
            disk_down = True
            disk_detail = f"На диске свободно {free_gb:.1f} ГБ (порог {DISK_ALERT_GB:.0f} ГБ)."
    except Exception as e:
        print(f"[monitor] не удалось снять RAM/диск: {e}")

    backup_down, backup_detail = _check_backup()

    bg_down, bg_detail = False, ""
    if not db_down:
        try:
            from backend.bg_runtime import LEADER_LOCK_ID
            with engine.connect() as c:
                held = c.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_locks
                        WHERE locktype = 'advisory' AND granted
                          AND classid = 0 AND objid = :oid
                    )
                """), {"oid": LEADER_LOCK_ID}).scalar()
            if not held:
                bg_down = True
                bg_detail = (
                    "Фоновые задачи (cx-bg) не запущены. "
                    "Сайт открывается, но синки чатов/доставок могут стоять."
                )
        except Exception as e:
            bg_detail = f"(не удалось проверить фон: {e})"

    with engine.connect() as conn:
        _ensure_state_table(conn)
        _handle(conn, "site", "Сайт / бэкенд cxvo.ru", site_down, site_detail, recipients)
        _handle(
            conn, "site_slow", "Сайт отвечает медленно",
            site_slow, live_detail or f"Ответ {live_ms:.0f} мс", recipients,
        )
        _handle(conn, "db", "База данных PostgreSQL", db_down, db_detail, recipients)
        # про застой воркеров тревожим только когда БД жива (иначе дублируем db-алерт)
        _handle(conn, "cron", "Крон-воркеры (синхронизация)", cron_down, cron_detail, recipients)
        _handle(
            conn, "ym_ratings", "Рейтинги Яндекс Маркета",
            ratings_down, ratings_detail, recipients,
            reminder_min=YM_RATINGS_REMINDER_MIN,
        )
        _handle(
            conn, "wb_ratings", "Рейтинги Wildberries",
            wb_ratings_down, wb_ratings_detail, recipients,
            reminder_min=YM_RATINGS_REMINDER_MIN,
        )
        _handle(conn, "ram", "Память сервера", ram_down, ram_detail, recipients)
        _handle(conn, "disk", "Свободное место на диске", disk_down, disk_detail, recipients)
        _handle(conn, "bg", "Фоновые задачи (синк, чаты)", bg_down, bg_detail, recipients)
        _handle(
            conn, "backup", "Резервная копия базы",
            backup_down, backup_detail, recipients,
            reminder_min=BACKUP_REMINDER_MIN,
        )

    print(
        f"[monitor] health: site_down={site_down} site_slow={site_slow} "
        f"db_down={db_down} cron_down={cron_down} ratings_down={ratings_down} "
        f"wb_ratings_down={wb_ratings_down} ram_down={ram_down} disk_down={disk_down} "
        f"bg_down={bg_down} backup_down={backup_down} live_ms={live_ms:.0f}"
    )


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
