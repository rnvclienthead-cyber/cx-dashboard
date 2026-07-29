"""
Антидубль клиентских уведомлений по статусам доотправки.

Одна заявка + один статус = одно сообщение клиенту (WB или Salebot),
даже если сработали и вебхук, и опросник, и два процесса uvicorn.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger("reshipment_notify")


def init_notify_table(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reshipment_client_notifies (
                request_id  INTEGER NOT NULL,
                status      TEXT    NOT NULL,
                channel     TEXT    DEFAULT '',
                created_at  TIMESTAMP DEFAULT now(),
                PRIMARY KEY (request_id, status)
            )
        """))
        conn.commit()


def claim_notify(db: Session, request_id: int, status: str) -> bool:
    """
    Забирает право отправить уведомление по статусу.
    True  — ещё не слали, можно отправлять.
    False — уже слали (или ошибка) — молчать.
    """
    if not request_id or not status:
        return False
    try:
        row = db.execute(text("""
            INSERT INTO reshipment_client_notifies (request_id, status)
            VALUES (:id, :st)
            ON CONFLICT (request_id, status) DO NOTHING
            RETURNING request_id
        """), {"id": int(request_id), "st": status}).fetchone()
        db.commit()
        return row is not None
    except Exception as e:
        logger.warning("claim_notify #%s %s: %s", request_id, status, e)
        try:
            db.rollback()
        except Exception:
            pass
        return False
