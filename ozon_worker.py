"""
Ozon воркер — фоновая синхронизация данных.
API: https://api-seller.ozon.ru  Auth: Client-Id + Api-Key заголовки.
Все возвраты — FBO (product.offer_id = артикул продавца, нет фото/комментариев).
Пагинация возвратов: cursor-based через last_id (не page tokens!).

Запуск (PYTHONPATH=/root/cx-dashboard):
  python3 ozon_worker.py hourly   — возвраты, статусы   (cron: 0 */2 * * *)
  python3 ozon_worker.py daily    — заказы (FBS, 30-дн окна)  (cron: 0 3 * * *)
  python3 ozon_worker.py backfill — исторический сбор всех возвратов (вручную)

Переменные в .env:
  OZON_CLIENT_ID, OZON_API_KEY, DATABASE_URL_LOCAL
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OZ] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ozon_worker")

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "").strip()
OZON_API_KEY   = os.getenv("OZON_API_KEY", "").strip()
DATABASE_URL   = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)

BASE   = "https://api-seller.ozon.ru"
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _hdr():
    return {
        "Client-Id":    OZON_CLIENT_ID,
        "Api-Key":      OZON_API_KEY,
        "Content-Type": "application/json",
    }


def _post(path, body):
    r = requests.post(f"{BASE}{path}", headers=_hdr(), json=body, timeout=30)
    if r.status_code == 429:
        raise RuntimeError(f"Rate limit (429) на {path}")
    r.raise_for_status()
    return r.json()


def _log_db(action, status, details=""):
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:a,:s,:d)"),
                {"a": action, "s": status, "d": str(details)[:2000]},
            )
    except Exception:
        pass


# ── Маппинг статусов Ozon → читаемые значения ────────────────────────────────
# Ozon уже даёт display_name на русском, но ряд технических sys_name → сведём к 3 состояниям
_OZON_STATUS_MAP = {
    "ReturnedToOzon":              "На складе Ozon",
    "ReturnedToSeller":            "Возвращён продавцу",
    "Created":                     "Создан",
    "InTransit":                   "В пути",
    "WaitingForSeller":            "Ожидает продавца",
    "Cancelled":                   "Отменён",
    "CancelledWithCompensation":   "Отменён с компенсацией",
    "UtilizedByOzon":              "Утилизирован Ozon",
}

# Причины возврата — Ozon даёт их на русском языке в return_reason_name,
# поэтому маппинга кода→русский не требуется (в отличие от ЯМ).


# ── 1. Синхронизация возвратов (FBO) ────────────────────────────────────────
def _get_last_synced_id():
    """Последний return_id который мы уже сохранили. При hourly — пропускаем уже виденные."""
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MAX(return_id) FROM ozon_returns")).fetchone()
    return row[0] or 0


def sync_returns(full=False):
    """
    Синхронизирует возвраты Ozon FBO.
    full=False (hourly): начинает с MAX(return_id)-1000 чтобы поймать обновления статусов.
    full=True  (backfill): сканирует всё с нуля.
    """
    log.info(f"Синхронизация возвратов Ozon ({'полная' if full else 'инкрементальная'})...")

    if full:
        start_id = 0
    else:
        # Начинаем чуть раньше последнего — для обновления статусов недавних возвратов
        start_id = max(0, _get_last_synced_id() - 1000)

    last_id  = start_id
    total    = 0
    new_rows = 0
    updated  = 0

    while True:
        try:
            data = _post("/v1/returns/list", {"filter": {}, "last_id": last_id, "limit": 100})
        except Exception as e:
            log.error(f"Ошибка запроса возвратов Ozon (last_id={last_id}): {e}")
            _log_db("ozon_sync_returns", "error", str(e))
            break

        returns = data.get("returns", [])
        if not returns:
            break

        with engine.begin() as conn:
            for r in returns:
                rid     = r["id"]
                product = r.get("product", {})
                logistic = r.get("logistic", {})
                visual  = r.get("visual", {})
                vstatus = visual.get("status", {})
                add_info = r.get("additional_info", {})

                status_sys = vstatus.get("sys_name", "")
                status_ru  = vstatus.get("display_name") or _OZON_STATUS_MAP.get(status_sys, status_sys)
                offer_id   = (product.get("offer_id") or "").strip()

                row = conn.execute(text("""
                    INSERT INTO ozon_returns
                        (return_id, order_id, order_number, posting_number,
                         supplier_article, product_name, ozon_sku,
                         return_reason, status_sys, status_ru,
                         is_opened, return_date, synced_at)
                    VALUES
                        (:rid, :oid, :onum, :pnum,
                         :article, :pname, :osku,
                         :reason, :ssys, :sru,
                         :opened, :rdate, NOW())
                    ON CONFLICT (return_id) DO UPDATE SET
                        status_sys = EXCLUDED.status_sys,
                        status_ru  = EXCLUDED.status_ru,
                        synced_at  = NOW()
                    RETURNING (xmax = 0) AS is_new
                """), {
                    "rid":     rid,
                    "oid":     r.get("order_id"),
                    "onum":    r.get("order_number"),
                    "pnum":    r.get("posting_number"),
                    "article": offer_id,
                    "pname":   product.get("name"),
                    "osku":    product.get("sku"),
                    "reason":  r.get("return_reason_name"),
                    "ssys":    status_sys,
                    "sru":     status_ru,
                    "opened":  add_info.get("is_opened"),
                    "rdate":   logistic.get("return_date"),
                }).fetchone()

                if row and row[0]:
                    new_rows += 1
                    if new_rows <= 5:
                        log.info(f"  NEW {rid} | {offer_id} | {r.get('return_reason_name','')[:40]} | {status_ru}")
                else:
                    updated += 1

        total    += len(returns)
        last_id   = returns[-1]["id"]

        if len(returns) < 100:
            break
        time.sleep(0.3)

    summary = f"просмотрено {total}, новых {new_rows}, обновлено {updated}"
    log.info(f"Возвраты Ozon: {summary}")
    _log_db("ozon_sync_returns", "success", summary)

    # Автоматическая правиловая разметка новых возвратов
    if new_rows > 0:
        try:
            apply_rule_tags()
        except Exception as e:
            _log_db("ozon_rule_tags", "error", f"Сбой разметки: {e}")


# ── Правиловая разметка возвратов Ozon (без AI) ─────────────────────────────
# Причины Ozon стандартизованы → прямой маппинг в категории дефектов
_OZON_RULE_TAGS = {
    'Товар в неполной комплектации':                                    [1, 2],
    'Покупатель отказался при вручении: неполная комплектация':         [1, 2],
    'Упаковка и товар повреждены':                                       [3, 5],
    'Товар не работает / брак':                                          [4],
    'Покупатель отказался при вручении: недоволен качеством товара':    [4, 9],
    'Товар поврежден, но упаковка цела':                                 [5],
    'Товар сломался при эксплуатации':                                   [4, 5],
    'Блокирующее повреждение':                                           [5],
    'Покупатель получил не те товары':                                   [8],
    'Покупатель отказался при вручении: в заказе не тот товар':         [8],
    'Неправильно указаны ОВХ товара':                                    [10, 11],
    'Товар поддельный':                                                  [13],
    'Изменил решение о покупке/Товар не подошёл':                        [12],
}


def apply_rule_tags():
    """Правиловая разметка ozon_returns — заполняет cat_* без AI."""
    tagged = 0
    with engine.begin() as conn:
        for reason, cats in _OZON_RULE_TAGS.items():
            set_parts = [f"cat_{c} = true" for c in cats]
            # Тегируем только ещё не размеченные строки с этой причиной
            result = conn.execute(text(f"""
                UPDATE ozon_returns
                SET {', '.join(set_parts)}, audit_status = 'Правиловая разметка'
                WHERE return_reason = :reason
                  AND cat_1 IS NULL AND cat_2 IS NULL AND cat_3 IS NULL
                  AND cat_4 IS NULL AND cat_5 IS NULL AND cat_6 IS NULL
                  AND cat_7 IS NULL AND cat_8 IS NULL AND cat_9 IS NULL
                  AND cat_10 IS NULL AND cat_11 IS NULL AND cat_12 IS NULL AND cat_13 IS NULL
            """), {"reason": reason})
            tagged += result.rowcount
    _log_db("ozon_rule_tags", "success", f"Правиловая разметка: {tagged} новых записей")
    log.info(f"Правиловая разметка Ozon: {tagged} строк")
    return tagged


# ── 2. Синхронизация FBS заказов (знаменатель PPM) ───────────────────────────
# Ozon не позволяет запрашивать более 30 дней за раз по FBS.
# FBO заказы требуют расширенных прав (роль не включена в ключ).
def sync_orders(days_back=7):
    """Синхронизирует FBS заказы за последние days_back дней."""
    log.info(f"Синхронизация FBS заказов Ozon (последние {days_back} дн)...")
    now   = datetime.now(timezone.utc)
    since = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to    = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    total = 0
    offset = 0

    while True:
        try:
            data = _post("/v3/posting/fbs/list", {
                "filter": {"since": since, "to": to},
                "limit":  50,
                "offset": offset,
            })
        except Exception as e:
            log.error(f"Ошибка запроса FBS заказов Ozon: {e}")
            break

        result   = data.get("result", {})
        postings = result.get("postings", [])
        if not postings:
            break

        with engine.begin() as conn:
            for p in postings:
                for prod in (p.get("products") or [{}]):
                    conn.execute(text("""
                        INSERT INTO ozon_orders
                            (posting_number, order_id, order_number, status,
                             supplier_article, sku_name, created_at, price)
                        VALUES
                            (:pnum, :oid, :onum, :status,
                             :article, :sname, :created, :price)
                        ON CONFLICT (posting_number) DO UPDATE SET
                            status    = EXCLUDED.status,
                            synced_at = NOW()
                    """), {
                        "pnum":    p.get("posting_number"),
                        "oid":     p.get("order_id"),
                        "onum":    p.get("order_number"),
                        "status":  p.get("status"),
                        "article": prod.get("offer_id"),
                        "sname":   prod.get("name"),
                        "created": p.get("in_process_at"),
                        "price":   prod.get("price"),
                    })
                    total += 1

        if not result.get("has_next"):
            break
        offset += 50
        time.sleep(0.3)

    log.info(f"FBS заказы Ozon: {total} строк")
    _log_db("ozon_sync_orders", "success", f"{total} строк")


# ── Точка входа ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "hourly"

    if not OZON_CLIENT_ID or not OZON_API_KEY:
        log.error("Не заданы OZON_CLIENT_ID / OZON_API_KEY в .env")
        sys.exit(1)

    def run(fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.error(f"Ошибка в {fn.__name__}: {e}")
            _log_db(fn.__name__, "error", str(e))

    if task == "hourly":
        run(sync_returns)
    elif task == "daily":
        run(sync_orders, days_back=7)
    elif task == "backfill":
        run(sync_returns, full=True)
    elif task == "rule_tags":
        run(apply_rule_tags)
    else:
        log.error(f"Неизвестная задача: '{task}'. Используй: hourly | daily | backfill | rule_tags")
        sys.exit(1)
