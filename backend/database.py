from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
# Пул: base 10 + overflow 20 = до 30 соединений на процесс uvicorn.
# При --workers 2 это максимум 60, что безопасно под Postgres max_connections=100
# (остаётся запас на кроны и psql). pre_ping отсеивает "протухшие" соединения,
# recycle пересоздаёт их раз в 30 мин — это убирает ошибки "QueuePool limit reached",
# которые копились, пока фоновые воркеры блокировали обработку запросов.
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)

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