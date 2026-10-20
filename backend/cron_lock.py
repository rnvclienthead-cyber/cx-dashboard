"""
Защита cron-воркеров (worker.py/ym_worker.py/ozon_worker.py) от повторного
запуска — Фаза 5 унификации.

Использует тот же механизм, что и лидер-election в backend/main.py
(pg_try_advisory_lock), но со своим ID на каждый воркер, чтобы не
пересекаться друг с другом и с backend'ом (там занят ID 87442201).

Сессионный advisory lock Postgres снимается автоматически, когда закрывается
соединение — в том числе если процесс воркера завис и был убит по таймауту
или упал с исключением, поэтому отдельный pid-файл не нужен: он мог бы
остаться "висячим" после kill -9, а lock в БД — не может.
"""
import sys
from sqlalchemy import text

WORKER_LOCK_IDS = {
    "worker": 87442210,            # WB (worker.py)
    "ym_worker": 87442211,         # ЯМ hourly (заказы/возвраты/отзывы/вопросы)
    "ozon_worker": 87442212,       # OZON (ozon_worker.py)
    "ozon_worker_sticky": 87442213,  # OZON sticky статусы (каждые 30 мин)
    "ym_worker_daily": 87442214,   # ЯМ daily (ассортимент/цены)
    "ym_worker_ratings": 87442215, # ЯМ рейтинги из кабинета
    "ym_worker_reports": 87442216, # ЯМ фин-отчёты
    "ym_worker_backfill": 87442217,  # ЯМ ручной backfill
    "ym_worker_feedback_queue": 87442222,  # ЯМ очередь отзывов NEED_REACTION
    "ym_worker_questions": 87442223,  # ЯМ только вопросы без ответа
    "ym_worker_returns": 87442224,    # ЯМ возвраты (частый проход)
    "wb_dbw_delivery": 87442218,     # WB DBW «В доставке» → Google Sheets
    "wb_dbw_ingest": 87442219,       # WB DBW быстрый захват /new (без Sheets)
    "worker_ratings": 87442220,      # WB рейтинги (item-rating)
    "worker_logistics": 87442221,    # WB логистика (продажи/заказы)
    "marketplace_sheet": 87442225,   # ежедневная выгрузка заказов/продаж в Google Sheets
    "wb_pvz_input": 87442226,        # ежедневно: прибыло на ПВЗ → лист InputAPIRustamWB
}


def acquire_or_exit(engine, worker_name, *, log_fn=print, log_db=None):
    """
    Захватывает advisory lock для воркера `worker_name` на всё время жизни
    процесса (соединение не закрывается явно — держим его до выхода).

    Если lock уже занят предыдущим, ещё не завершившимся запуском того же
    скрипта — печатает сообщение через `log_fn` и завершает процесс с кодом 0
    (это ожидаемый пропуск, а не сбой: следующий запуск cron всё исправит).

    Возвращает открытое соединение — вызывающий код должен сохранить ссылку
    на него (например, в переменную), чтобы Python не закрыл его сборщиком
    мусора раньше времени.
    """
    lock_id = WORKER_LOCK_IDS[worker_name]
    conn = engine.connect()
    # Сессионный lock. AUTOCOMMIT: иначе соединение на часы остаётся
    # «idle in transaction», VACUUM не чистит таблицы, сайт начинает
    # грузиться по минуте (как в сентябре 2026).
    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
    got_lock = bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
    )
    if not got_lock:
        msg = f"{worker_name}: предыдущий запуск ещё выполняется — пропуск (advisory lock {lock_id} занят)"
        log_fn(f"⏭️ {msg}")
        if log_db:
            log_db(f"Воркер {worker_name}", "INFO", msg)
        conn.close()
        sys.exit(0)
    return conn


def acquire_with_wait(engine, worker_name, *, timeout_sec=180, poll_sec=5, log_fn=print):
    """Ждёт advisory lock до timeout_sec (для ручного/авто импорта)."""
    import time

    lock_id = WORKER_LOCK_IDS[worker_name]
    deadline = time.time() + timeout_sec
    while True:
        conn = engine.connect()
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        got_lock = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
        )
        if got_lock:
            return conn
        conn.close()
        if time.time() >= deadline:
            raise TimeoutError(
                f"{worker_name}: не удалось взять lock {lock_id} за {timeout_sec}с"
            )
        log_fn(f"⏳ {worker_name}: lock занят, жду…")
        time.sleep(poll_sec)
