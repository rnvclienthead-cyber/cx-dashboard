"""
Ozon воркер — фоновая синхронизация данных.
API: https://api-seller.ozon.ru  Auth: Client-Id + Api-Key заголовки.
Все возвраты — FBO (product.offer_id = артикул продавца, нет фото/комментариев).
Пагинация возвратов: cursor-based через last_id (не page tokens!).

Запуск (PYTHONPATH=/root/cx-dashboard):
  python3 ozon_worker.py hourly   — возвраты, статусы   (cron: ежедневно 22:00 UTC)
  python3 ozon_worker.py sticky   — только очередь согласования/в пути (cron: */30)
  python3 ozon_worker.py daily    — FBO заказы (знаменатель PPM)  (cron: 0 3 * * *)
  python3 ozon_worker.py backfill — исторический сбор всех возвратов (вручную)
  python3 ozon_worker.py backfill_orders [дней] — история FBO заказов (по умолч. 180)

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
from backend import ozon_client

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
    return ozon_client.headers(OZON_CLIENT_ID, OZON_API_KEY)


# Единая реализация — backend/ozon_client.py (используется и routers/cx_responses.py,
# и ozon_chat_sync.py). Тонкая обёртка сохранена, чтобы не переписывать вызовы
# _post(...) по всему файлу.
def _post(path, body):
    return ozon_client.post(path, body, timeout=30)


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
    from backend import ozon_returns_api

    log.info(f"Синхронизация возвратов Ozon ({'полная' if full else 'инкрементальная'})...")
    ozon_returns_api.ensure_schema(engine)

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
                p = ozon_returns_api.parse_api_item(r)
                rid = p["return_id"]
                row = conn.execute(text("""
                    INSERT INTO ozon_returns
                        (return_id, order_id, order_number, posting_number,
                         supplier_article, product_name, ozon_sku,
                         return_reason, status_sys, status_ru,
                         is_opened, return_date,
                         return_type, schema_code, barcode, place_name, target_place_name,
                         price, status_changed_at, source_id, compensation_status,
                         synced_at)
                    VALUES
                        (:return_id, :order_id, :order_number, :posting_number,
                         :supplier_article, :product_name, :ozon_sku,
                         :return_reason, :status_sys, :status_ru,
                         :is_opened, :return_date,
                         :return_type, :schema_code, :barcode, :place_name, :target_place_name,
                         :price, :status_changed_at, :source_id, :compensation_status,
                         NOW())
                    ON CONFLICT (return_id) DO UPDATE SET
                        order_number = EXCLUDED.order_number,
                        posting_number = EXCLUDED.posting_number,
                        supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_returns.supplier_article),
                        product_name = COALESCE(EXCLUDED.product_name, ozon_returns.product_name),
                        ozon_sku = COALESCE(EXCLUDED.ozon_sku, ozon_returns.ozon_sku),
                        return_reason = COALESCE(EXCLUDED.return_reason, ozon_returns.return_reason),
                        status_sys = EXCLUDED.status_sys,
                        status_ru  = EXCLUDED.status_ru,
                        is_opened = EXCLUDED.is_opened,
                        return_date = COALESCE(EXCLUDED.return_date, ozon_returns.return_date),
                        return_type = EXCLUDED.return_type,
                        schema_code = EXCLUDED.schema_code,
                        barcode = COALESCE(EXCLUDED.barcode, ozon_returns.barcode),
                        place_name = COALESCE(EXCLUDED.place_name, ozon_returns.place_name),
                        target_place_name = COALESCE(EXCLUDED.target_place_name, ozon_returns.target_place_name),
                        price = COALESCE(EXCLUDED.price, ozon_returns.price),
                        status_changed_at = COALESCE(EXCLUDED.status_changed_at, ozon_returns.status_changed_at),
                        source_id = COALESCE(EXCLUDED.source_id, ozon_returns.source_id),
                        compensation_status = EXCLUDED.compensation_status,
                        synced_at  = NOW()
                    RETURNING (xmax = 0) AS is_new
                """), p).fetchone()

                if row and row[0]:
                    new_rows += 1
                    if new_rows <= 5:
                        log.info(f"  NEW {rid} | {p.get('supplier_article')} | {(p.get('return_reason') or '')[:40]} | {p.get('status_ru')}")
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

    # Инкремент last_id-1000 не видит старые OnSellerApproval → статусы залипают.
    # Явно перетягиваем очередь согласования (+ открытые «в пути») по id.
    try:
        refreshed = _refresh_sticky_returns()
        if refreshed:
            log.info(f"Возвраты Ozon: обновлено sticky-статусов {refreshed}")
    except Exception as e:
        log.error(f"Ошибка refresh sticky returns: {e}")
        _log_db("ozon_sync_returns_sticky", "error", str(e))

    # Автоматическая правиловая разметка новых возвратов
    if new_rows > 0:
        try:
            apply_rule_tags()
        except Exception as e:
            _log_db("ozon_rule_tags", "error", f"Сбой разметки: {e}")


def _refresh_sticky_returns(limit: int = 200) -> int:
    """Перечитать из API заявки, которые иначе выпадают из инкрементального окна.

    Без этого «На согласовании» остаётся в БД после утилизации/отмены в ЛК.
    """
    from backend import ozon_returns_api

    sticky = (
        "OnSellerApproval",
        "WaitingShipment",
        "MovingToOzon",
        "ArrivedAtReturnPlace",
        "MovingToSeller",
    )
    ph = ", ".join(f":s{i}" for i in range(len(sticky)))
    params = {f"s{i}": s for i, s in enumerate(sticky)}
    params["lim"] = limit
    with engine.connect() as conn:
        ids = [
            int(r[0])
            for r in conn.execute(text(f"""
                SELECT return_id FROM ozon_returns
                WHERE status_sys IN ({ph})
                ORDER BY COALESCE(status_changed_at, synced_at) ASC NULLS FIRST
                LIMIT :lim
            """), params).fetchall()
        ]
    if not ids:
        return 0

    n = 0
    with engine.begin() as conn:
        for rid in ids:
            try:
                data = _post(
                    "/v1/returns/list",
                    {"filter": {}, "last_id": max(0, rid - 1), "limit": 30},
                )
            except Exception as e:
                log.warning(f"sticky refresh {rid}: {e}")
                continue
            hit = next(
                (x for x in (data.get("returns") or []) if int(x.get("id") or 0) == rid),
                None,
            )
            if not hit:
                continue
            p = ozon_returns_api.parse_api_item(hit)
            conn.execute(text("""
                UPDATE ozon_returns SET
                    status_sys = :status_sys,
                    status_ru = :status_ru,
                    is_opened = :is_opened,
                    return_date = COALESCE(:return_date, return_date),
                    return_type = COALESCE(:return_type, return_type),
                    schema_code = COALESCE(:schema_code, schema_code),
                    barcode = COALESCE(:barcode, barcode),
                    place_name = COALESCE(:place_name, place_name),
                    target_place_name = COALESCE(:target_place_name, target_place_name),
                    price = COALESCE(:price, price),
                    status_changed_at = COALESCE(:status_changed_at, status_changed_at),
                    source_id = COALESCE(:source_id, source_id),
                    compensation_status = :compensation_status,
                    supplier_article = COALESCE(:supplier_article, supplier_article),
                    product_name = COALESCE(:product_name, product_name),
                    ozon_sku = COALESCE(:ozon_sku, ozon_sku),
                    return_reason = COALESCE(:return_reason, return_reason),
                    order_id = COALESCE(:order_id, order_id),
                    order_number = COALESCE(:order_number, order_number),
                    posting_number = COALESCE(:posting_number, posting_number),
                    synced_at = NOW()
                WHERE return_id = :return_id
            """), p)
            n += 1
            time.sleep(0.05)
    return n


# ── Правиловая разметка возвратов Ozon (без AI) ─────────────────────────────
# Причины Ozon стандартизованы → прямой маппинг в категории дефектов
_OZON_RULE_TAGS = {
    'Товар в неполной комплектации':                                    [1, 2],
    'Покупатель отказался при вручении: неполная комплектация':         [1, 2],
    'Упаковка и товар повреждены':                                       [3, 5],
    'Товар не работает / брак':                                          [4],
    'Проблемы с качеством товара (брак товара)':                         [4],
    'Покупатель отказался при вручении: недоволен качеством товара':    [4, 9],
    'Товар поврежден, но упаковка цела':                                 [5],
    'Отказ клиента: поврежден товар':                                    [5],
    'Товар сломался при эксплуатации':                                   [4, 5],
    'Блокирующее повреждение':                                           [5],
    'Покупатель получил не те товары':                                   [8],
    'Покупатель отказался при вручении: в заказе не тот товар':         [8],
    'Привезли другой товар':                                             [8],
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


# ── 2. Синхронизация заказов (знаменатель PPM) ────────────────────────────────
# Возвраты у нас FBO → знаменатель PPM тоже должен быть FBO.
# /v3/posting/fbo/list: cursor-пагинация, limit ≤ 100.
# Окно API — без жёсткого лимита 30 дней (в отличие от FBS), но для
# стабильности режем на куски ≤ 30 дней.

_FBO_CANCELLED = {"cancelled", "cancelled_from_split"}


def _ensure_orders_schema():
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE ozon_orders ADD COLUMN IF NOT EXISTS schema_code TEXT"))
        conn.execute(text("ALTER TABLE ozon_orders ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1"))
        conn.execute(text("UPDATE ozon_orders SET schema_code = 'Fbs' WHERE schema_code IS NULL"))


def _upsert_fbo_postings(postings, schema_code="Fbo"):
    """Пишет postings в ozon_orders. quantity учитывается в PPM."""
    total = 0
    with engine.begin() as conn:
        for p in postings:
            status = (p.get("status") or "").strip()
            created = p.get("in_process_at") or p.get("created_at")
            for prod in (p.get("products") or [{}]):
                article = (prod.get("offer_id") or "").strip()
                if not article:
                    continue
                qty = int(prod.get("quantity") or 1)
                raw_price = prod.get("price")
                if isinstance(raw_price, dict):
                    raw_price = raw_price.get("amount") or raw_price.get("price") or raw_price.get("value")
                try:
                    price = float(raw_price) if raw_price is not None and raw_price != "" else None
                except (TypeError, ValueError):
                    price = None
                sname = prod.get("name")
                if isinstance(sname, dict):
                    sname = sname.get("name") or str(sname)
                conn.execute(text("""
                    INSERT INTO ozon_orders
                        (posting_number, order_id, order_number, status,
                         supplier_article, sku_name, created_at, price,
                         schema_code, quantity)
                    VALUES
                        (:pnum, :oid, :onum, :status,
                         :article, :sname, :created, :price,
                         :schema, :qty)
                    ON CONFLICT (posting_number) DO UPDATE SET
                        status           = EXCLUDED.status,
                        supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_orders.supplier_article),
                        sku_name         = COALESCE(EXCLUDED.sku_name, ozon_orders.sku_name),
                        created_at       = COALESCE(EXCLUDED.created_at, ozon_orders.created_at),
                        price            = COALESCE(EXCLUDED.price, ozon_orders.price),
                        schema_code      = EXCLUDED.schema_code,
                        quantity         = EXCLUDED.quantity,
                        synced_at        = NOW()
                """), {
                    "pnum":    str(p.get("posting_number") or ""),
                    "oid":     p.get("order_id"),
                    "onum":    str(p.get("order_number") or "") if p.get("order_number") is not None else None,
                    "status":  status,
                    "article": article,
                    "sname":   sname,
                    "created": created if not isinstance(created, dict) else None,
                    "price":   price,
                    "schema":  schema_code,
                    "qty":     qty,
                })
                total += 1
    return total


def sync_fbo_orders(days_back=7):
    """Синхронизирует FBO-отправления за последние days_back дней (куски по 7д)."""
    _ensure_orders_schema()
    log.info(f"Синхронизация FBO заказов Ozon (последние {days_back} дн)...")
    now = datetime.now(timezone.utc)
    total = 0
    # Короткие окна: у FBO за 30 дней легко >50k отправлений (лимит API offset/page).
    remaining = days_back
    window_end = now
    while remaining > 0:
        chunk = min(7, remaining)
        window_start = window_end - timedelta(days=chunk)
        since = window_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to = window_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        cursor = None
        pages = 0
        window_rows = 0
        truncated = False
        while pages < 2000:
            body = {
                "dir": "ASC",
                "filter": {"since": since, "to": to},
                "limit": 100,
            }
            if cursor:
                body["cursor"] = cursor
            try:
                data = _post("/v3/posting/fbo/list", body)
            except Exception as e:
                log.error(f"Ошибка FBO list [{since}..{to}] page={pages}: {e}")
                break
            postings = data.get("postings") or []
            if postings:
                n = _upsert_fbo_postings(postings, "Fbo")
                total += n
                window_rows += n
            pages += 1
            if not data.get("has_next"):
                break
            cursor = data.get("cursor")
            if not cursor:
                break
            if pages >= 2000:
                truncated = True
                break
            time.sleep(0.2)
        flag = " TRUNCATED" if truncated else ""
        log.info(f"  окно {since[:10]}…{to[:10]}: {pages} стр., {window_rows} строк{flag}")
        window_end = window_start
        remaining -= chunk

    log.info(f"FBO заказы Ozon: {total} строк upsert")
    _log_db("ozon_sync_fbo_orders", "success", f"{total} строк / {days_back}д")
    return total


def sync_orders(days_back=7):
    """Синхронизирует FBS заказы (справочно; PPM использует FBO)."""
    _ensure_orders_schema()
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
                             supplier_article, sku_name, created_at, price,
                             schema_code, quantity)
                        VALUES
                            (:pnum, :oid, :onum, :status,
                             :article, :sname, :created, :price,
                             'Fbs', :qty)
                        ON CONFLICT (posting_number) DO UPDATE SET
                            status      = EXCLUDED.status,
                            schema_code = COALESCE(ozon_orders.schema_code, 'Fbs'),
                            quantity    = COALESCE(EXCLUDED.quantity, ozon_orders.quantity),
                            synced_at   = NOW()
                    """), {
                        "pnum":    p.get("posting_number"),
                        "oid":     p.get("order_id"),
                        "onum":    p.get("order_number"),
                        "status":  p.get("status"),
                        "article": prod.get("offer_id"),
                        "sname":   prod.get("name"),
                        "created": p.get("in_process_at"),
                        "price":   prod.get("price"),
                        "qty":     int(prod.get("quantity") or 1),
                    })
                    total += 1

        if not result.get("has_next"):
            break
        offset += 50
        time.sleep(0.3)

    log.info(f"FBS заказы Ozon: {total} строк")
    _log_db("ozon_sync_orders", "success", f"{total} строк")


# ── 3. Синхронизация отзывов Ozon ────────────────────────────────────────────

def sync_reviews():
    """Забирает отзывы через v2/review/list (курсорная пагинация) и сохраняет в ozon_reviews.
    Требует подписку Ozon «Управление отзывами» или Premium Pro.
    """
    log.info("Синхронизация отзывов Ozon (v2)...")
    total   = 0
    last_id = None
    limit   = 100

    while True:
        body = {
            "limit":    limit,
            "sort_dir": "DESC",
            "filters":  {},
        }
        if last_id:
            body["last_id"] = last_id

        try:
            data = _post("/v2/review/list", body)
        except Exception as e:
            log.error(f"Ошибка запроса отзывов Ozon: {e}")
            _log_db("ozon_sync_reviews", "error", str(e))
            return

        # v2: поля прямо в корне ответа (не внутри result)
        reviews = data.get("reviews") or []
        if not reviews:
            break

        with engine.begin() as conn:
            for rev in reviews:
                rev_id  = str(rev.get("uuid") or rev.get("review_id") or rev.get("id") or "")
                if not rev_id:
                    continue
                sku     = rev.get("sku") or rev.get("item_id")
                rating  = rev.get("rating") or rev.get("score")
                created = rev.get("created_at") or rev.get("date")

                comment_block = rev.get("comment") or {}
                if isinstance(comment_block, dict):
                    pros = comment_block.get("positive") or comment_block.get("pros") or ""
                    cons = comment_block.get("negative") or comment_block.get("cons") or ""
                    text_body = comment_block.get("overall") or comment_block.get("text") or ""
                else:
                    pros = rev.get("pros") or ""
                    cons = rev.get("cons") or ""
                    text_body = str(comment_block) or rev.get("text") or ""

                conn.execute(text("""
                    INSERT INTO ozon_reviews
                        (review_id, sku, rating, review_text, pros, cons, created_at)
                    VALUES (:rid, :sku, :rating, :body, :pros, :cons, :created)
                    ON CONFLICT (review_id) DO UPDATE SET
                        rating      = EXCLUDED.rating,
                        review_text = COALESCE(EXCLUDED.review_text, ozon_reviews.review_text),
                        synced_at   = NOW()
                """), {
                    "rid":     rev_id,
                    "sku":     sku,
                    "rating":  rating,
                    "body":    text_body[:2000] if text_body else None,
                    "pros":    pros[:500]  if pros else None,
                    "cons":    cons[:500]  if cons else None,
                    "created": created,
                })
                total += 1

        # v2: курсорная пагинация через has_next + last_id
        if not data.get("has_next"):
            break
        last_id = data.get("last_id")
        if not last_id:
            break
        time.sleep(0.5)

    log.info(f"Отзывы Ozon: {total} строк")
    _log_db("ozon_sync_reviews", "success", f"{total} отзывов")


# ── Точка входа ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from backend import cron_lock
    task = sys.argv[1] if len(sys.argv) > 1 else "hourly"

    if not OZON_CLIENT_ID or not OZON_API_KEY:
        log.error("Не заданы OZON_CLIENT_ID / OZON_API_KEY в .env")
        sys.exit(1)

    # Защита от повторного запуска: если предыдущий вызов ozon_worker.py (любая
    # задача) ещё не завершился, новый процесс тут же выходит — см. Фазу 5.
    # sticky — отдельный замок, чтобы не ждать суточный hourly.
    _lock_name = "ozon_worker_sticky" if task == "sticky" else "ozon_worker"
    _lock_conn = cron_lock.acquire_or_exit(engine, _lock_name, log_fn=log.info, log_db=_log_db)

    def run(fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.error(f"Ошибка в {fn.__name__}: {e}")
            _log_db(fn.__name__, "error", str(e))

    if task == "hourly":
        run(sync_returns)
        # sync_reviews отключён: Ozon Review API требует подписку «Управление отзывами»
        # run(sync_reviews)
    elif task == "sticky":
        # Лёгкий проход: OnSellerApproval / в пути — иначе статусы залипают до суточного hourly
        try:
            n = _refresh_sticky_returns()
            log.info(f"sticky refresh: {n}")
            _log_db("ozon_sync_returns_sticky", "success", f"обновлено {n}")
        except Exception as e:
            log.error(f"sticky refresh error: {e}")
            _log_db("ozon_sync_returns_sticky", "error", str(e))
            sys.exit(1)
    elif task == "daily":
        # PPM честный по FBO: знаменатель = FBO postings
        run(sync_fbo_orders, days_back=14)
        run(apply_rule_tags)
    elif task == "backfill":
        run(sync_returns, full=True)
        run(apply_rule_tags)
    elif task == "backfill_orders":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 180
        run(sync_fbo_orders, days_back=days)
    elif task == "rule_tags":
        run(apply_rule_tags)
    else:
        log.error(f"Неизвестная задача: '{task}'. Используй: hourly | sticky | daily | backfill | backfill_orders | rule_tags")
        sys.exit(1)
