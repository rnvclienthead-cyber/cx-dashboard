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
    "worker": 87442210,       # WB (worker.py)
    "ym_worker": 87442211,    # Яндекс Маркет (ym_worker.py)
    "ozon_worker": 87442212,  # OZON (ozon_worker.py)
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
