import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Доступ к базе (с паролем) берём из .env → DATABASE_URL.
# Запасное значение оставлено намеренно: если переменная не задана, всё
# продолжит работать как раньше и выкладка не сломается. На сервере стоит
# прописать DATABASE_URL в /root/cx-dashboard/.env и убрать пароль отсюда.
_FALLBACK_DB_URL = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
DATABASE_URL = os.getenv("DATABASE_URL", _FALLBACK_DB_URL)
# Пул: base 10 + overflow 20 = до 30 соединений на процесс uvicorn.
# При --workers 2 это максимум 60, что безопасно под Postgres max_connections=100
# (остаётся запас на кроны и psql). pre_ping отсеивает "протухшие" соединения,
# recycle пересоздаёт их раз в 30 мин — это убирает ошибки "QueuePool limit reached",
# которые копились, пока фоновые воркеры блокировали обработку запросов.
# idle_in_transaction: если воркер открыл транзакцию и ушёл в HTTP/сон,
# Postgres сам её снимет через 30с — иначе ALTER чатов встаёт в очередь
# и сайт показывает «нет связи с сервером».
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"options": "-c idle_in_transaction_session_timeout=30000"},
)


def column_exists(conn, table: str, column: str) -> bool:
    """Проверка колонки без замка на таблицу. ALTER IF NOT EXISTS всё равно
    берёт ACCESS EXCLUSIVE — даже если колонка уже есть."""
    return bool(conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :t
          AND column_name = :c
    """), {"t": table, "c": column}).scalar())


def index_exists(conn, name: str) -> bool:
    return bool(conn.execute(text("""
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = :n
    """), {"n": name}).scalar())


def add_column_if_missing(conn, table: str, column: str, ddl: str) -> bool:
    """Добавить колонку только когда её нет. Не использовать IF NOT EXISTS
    на горячих таблицах (wb_chats): он блокирует все чтения."""
    if column_exists(conn, table, column):
        return False
    conn.execute(text(ddl))
    return True

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def db_session():
    """Контекстный менеджер для фоновых воркеров — гарантирует закрытие соединения."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()