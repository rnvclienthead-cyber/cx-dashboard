"""
Яндекс Маркет воркер — фоновая синхронизация данных.
Поля и структура проверены через прямые curl-тесты 2026-06-09.

Запуск (PYTHONPATH=/root/cx-dashboard):
  python3 ym_worker.py hourly     — заказы, возвраты, отзывы, вопросы  (cron: ночью)
  python3 ym_worker.py questions  — только вопросы без ответа (cron: каждые 20 мин)
  python3 ym_worker.py returns    — свежие возвраты (cron: каждые 20 мин)
  python3 ym_worker.py daily    — ассортимент + цены + запуск фин-отчётов (cron: 0 3 * * *)
  python3 ym_worker.py reports  — опрос статуса и скачивание отчётов (cron: 0 5,9,13 * * *)

Переменные в .env:
  YM_API_KEY, YM_CAMPAIGN_ID, YM_BUSINESS_ID, DATABASE_URL_LOCAL
"""

import csv
import io
import logging
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

# Директория для хранения фото возвратов ЯМ (nginx раздаёт /static/)
YM_PHOTO_DIR = "/root/cx-dashboard/static/ym_returns"
YM_PHOTO_RETENTION_DAYS = int(os.getenv("YM_PHOTO_RETENTION_DAYS", "90"))

# Только возвраты начиная с этой даты
YM_RETURNS_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from backend import ym_client
from backend.review_updates import YM_MARK
from backend.ym_returns_api import effective_refund_status, status_ru as ym_status_ru

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [YM] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ym_worker")

YM_API_KEY     = os.getenv("YM_API_KEY", "").strip()
YM_CAMPAIGN_ID = os.getenv("YM_CAMPAIGN_ID", "").strip()
YM_BUSINESS_ID = os.getenv("YM_BUSINESS_ID", "").strip()
DATABASE_URL   = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)

BASE   = "https://api.partner.market.yandex.ru"
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Маппинг refundStatus ЯМ → читаемый статус
YM_REFUND_STATUS_MAP = {
    "WAITING_FOR_DECISION":  "Ждёт нашего решения",
    "PREMODERATION_DECISION_WAITING": "Ждёт нашего решения",
    "PREMODERATION_DECISION_MADE": "Ждём Яндекс",
    "PREMODERATION_SELECT_DELIVERY": "Ожидает возврат",
    "PREMODERATION_DISPUTE": "Спор",
    "STARTED_BY_USER":       "Заявка создана",
    "REFUND_IN_PROGRESS":    "Одобрено",
    "REFUNDED":              "Одобрено",
    "WAITING_FOR_RETURN":    "Ожидает возврат",
    "RETURNED":              "Ожидает возврат",
    "NOT_RETURNED":          "Отказ",
    "FAILED":                "Отказ",
    "REJECTED":              "Отказ",
    "CANCELLED":             "Отменено",
}


def _hdr():
    return ym_client.headers(YM_API_KEY)


# Единая реализация — backend/ym_client.py (используется и routers/cx_responses.py,
# и ym_chat_sync.py). Здесь оставлены только тонкие обёртки, чтобы не переписывать
# полтора десятка вызовов _get(...)/_post(...) ниже по файлу.
RateLimitError = ym_client.RateLimitError


def _get(path, params=None):
    return ym_client.get(path, params=params, key=YM_API_KEY)


def _post(path, body):
    return ym_client.post(path, body, key=YM_API_KEY)


def _log_db(action, status, details=""):
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:a,:s,:d)"),
                {"a": action, "s": status, "d": str(details)[:2000]},
            )
    except Exception:
        pass


def cleanup_old_ym_photos(days=None):
    """Удаляет локальные фото возвратов ЯМ старше N дней."""
    days = YM_PHOTO_RETENTION_DAYS if days is None else days
    if days <= 0 or not os.path.isdir(YM_PHOTO_DIR):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(YM_PHOTO_DIR):
        path = os.path.join(YM_PHOTO_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    if removed:
        log.info(f"Удалено старых фото ЯМ: {removed} (>{days} дн.)")
        _log_db("ym_photo_cleanup", "success", f"Удалено {removed} файлов старше {days} дн.")
    return removed


def _parse_ym_date(s):
    """Парсит дату ЯМ: ISO ('2025-05-29T22:54:05+03:00' или '2025-05-29 22:54:05')
    и нестандартный формат API ('29-05-2025 22:54:05')."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:len(fmt)+5].strip(), fmt).isoformat()
        except ValueError:
            continue
    return s  # вернём как есть если не распарсилось


# ── 1. Заказы ────────────────────────────────────────────────────────────────
# ym_orders: UNIQUE (order_id, supplier_article) — один заказ может иметь несколько SKU
def sync_orders():
    log.info("Синхронизация заказов ЯМ...")
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%d-%m-%Y")
    page_token = None
    total = 0

    while True:
        params = {"fromDate": since, "limit": 50}
        if page_token:
            params["page_token"] = page_token

        data   = _get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders", params=params)
        orders = data.get("orders", [])
        if not orders:
            break

        with engine.begin() as conn:
            for o in orders:
                for item in (o.get("items") or [{}]):
                    conn.execute(text("""
                        INSERT INTO ym_orders
                            (order_id, status, supplier_article, sku_name,
                             created_at, price, buyer_total, currency, region_name)
                        VALUES
                            (:order_id, :status, :article, :name,
                             :created_at, :price, :buyer_total, :currency, :region)
                        ON CONFLICT (order_id, supplier_article) DO UPDATE SET
                            status    = EXCLUDED.status,
                            synced_at = NOW()
                    """), {
                        "order_id":    o["id"],
                        "status":      o.get("status"),
                        "article":     item.get("offerId"),
                        "name":        item.get("offerName"),
                        "created_at":  _parse_ym_date(o.get("creationDate")),
                        "price":       item.get("price"),
                        "buyer_total": o.get("buyerTotal"),
                        "currency":    o.get("currency", "RUR"),
                        "region":      o.get("deliveryRegion", {}).get("name"),
                    })
                    total += 1

        # paging.nextPageToken (не pager!)
        page_token = (data.get("paging") or {}).get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)

    log.info(f"Заказы ЯМ: {total} строк")
    _log_db("ym_sync_orders", "success", f"{total} строк заказов")


def sync_orders_backfill(from_date_str: str = "2025-01-01"):
    """Исторический сбор заказов ЯМ по 30-дневным окнам начиная с from_date_str."""
    log.info(f"Бэкфилл заказов ЯМ с {from_date_str}...")
    start = datetime.strptime(from_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = datetime.now(timezone.utc)
    total = 0
    window_start = start

    while window_start < end:
        window_end = min(window_start + timedelta(days=29), end)
        from_str = window_start.strftime("%d-%m-%Y")
        to_str   = window_end.strftime("%d-%m-%Y")
        log.info(f"  Окно: {from_str} → {to_str}")

        page_token = None
        while True:
            params = {"fromDate": from_str, "toDate": to_str, "limit": 50}
            if page_token:
                params["page_token"] = page_token

            data   = _get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders", params=params)
            orders = data.get("orders", [])
            if not orders:
                break

            with engine.begin() as conn:
                for o in orders:
                    for item in (o.get("items") or [{}]):
                        conn.execute(text("""
                            INSERT INTO ym_orders
                                (order_id, status, supplier_article, sku_name,
                                 created_at, price, buyer_total, currency, region_name)
                            VALUES
                                (:order_id, :status, :article, :name,
                                 :created_at, :price, :buyer_total, :currency, :region)
                            ON CONFLICT (order_id, supplier_article) DO UPDATE SET
                                status    = EXCLUDED.status,
                                synced_at = NOW()
                        """), {
                            "order_id":    o["id"],
                            "status":      o.get("status"),
                            "article":     item.get("offerId"),
                            "name":        item.get("offerName"),
                            "created_at":  _parse_ym_date(o.get("creationDate")),
                            "price":       item.get("price"),
                            "buyer_total": o.get("buyerTotal"),
                            "currency":    o.get("currency", "RUR"),
                            "region":      o.get("deliveryRegion", {}).get("name"),
                        })
                        total += 1

            page_token = (data.get("paging") or {}).get("nextPageToken")
            if not page_token:
                break
            time.sleep(0.3)

        time.sleep(0.5)
        window_start = window_end + timedelta(days=1)

    log.info(f"Бэкфилл заказов ЯМ завершён: {total} строк")
    _log_db("ym_orders_backfill", "success", f"{total} строк за период {from_date_str}→сегодня")


# ── 2. Возвраты ──────────────────────────────────────────────────────────────
# Структура (проверено curl 2026-06-09):
#   returnType: "RETURN" (реальный) или "UNREDEEMED" (не выкупил из ПВЗ)
#   items[].shopSku                      — артикул продавца
#   items[].decisions[].returnItemId     — itemId для endpoint фото
#   items[].decisions[].comment          — текст от покупателя ✓
#   items[].decisions[].images[]         — хэши фотографий ✓
#   refundStatus                         — статус решения по возврату
#
# Логика фильтрации:
#   • только с 01.01.2026
#   • пропускаем UNREDEEMED (не выкупы) — нет текста/фото
#   • сохраняем только те, у кого ЕСТЬ комментарий ИЛИ фотографии
#   • cat_1..cat_13 оставляем NULL — заполнит ИИ-тегирование

def _download_ym_photo(order_id, return_id, item_id, image_hash, campaign_id=None):
    """
    Скачивает фото возврата ЯМ и сохраняет в статичную папку.
    Возвращает URL-путь для фронтенда или None при ошибке.
    Если файл уже скачан — возвращает путь без повторной загрузки.

    Ретрай: до 3 попыток с нарастающей паузой на 420 (rate limit) и сетевые
    ошибки — иначе при массовой синхронизации фото молча терялись.
    404 — фото действительно нет, ретрай не нужен.
    """
    os.makedirs(YM_PHOTO_DIR, exist_ok=True)
    safe_hash = str(image_hash)[:40]
    filename  = f"{return_id}_{safe_hash[:16]}.jpg"
    save_path = os.path.join(YM_PHOTO_DIR, filename)

    # Уже скачано — сразу возвращаем путь
    if os.path.exists(save_path):
        return f"/static/ym_returns/{filename}"

    camp = str(campaign_id or YM_CAMPAIGN_ID or "").strip()
    if not camp:
        return None
    url = (
        f"{BASE}/v2/campaigns/{camp}"
        f"/orders/{order_id}/returns/{return_id}"
        f"/decision/{item_id}/image/{image_hash}"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_hdr(), timeout=20)
            if r.status_code == 404:
                return None
            if r.status_code == 420:
                # rate limit — ждём и пробуем снова
                wait = 2 * (attempt + 1)
                log.warning(f"    420 на фото {safe_hash[:8]}…, ждём {wait}с (попытка {attempt+1}/3)")
                time.sleep(wait)
                continue
            r.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(r.content)
            log.debug(f"    Фото сохранено: {filename}")
            return f"/static/ym_returns/{filename}"
        except Exception as e:
            log.warning(f"    Ошибка скачивания фото {safe_hash[:8]}… (попытка {attempt+1}/3): {e}")
            time.sleep(1.5 * (attempt + 1))
    return None


def sync_returns(max_pages: int | None = None):
    """Тянем возвраты по всем AVAILABLE кампаниям (FBY + FBS), не только YM_CAMPAIGN_ID.

    max_pages — обрезать пагинацию (частый cron). Ночной hourly ходит все страницы.
    """
    from backend.ym_returns_api import ensure_schema

    log.info("Синхронизация возвратов ЯМ (все кампании FBY/FBS)...")
    ensure_schema(engine)

    # Неактуальный «висяк» от 23.03 — в активных не показываем, синк статус не оживит
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE ym_returns
            SET force_archive = true,
                status = 'CANCELLED',
                status_ru = 'Отменено',
                synced_at = NOW()
            WHERE return_id::text = '25786228'
              AND COALESCE(force_archive, false) = false
        """))

    campaigns = ym_client.available_campaigns()
    if not campaigns and YM_CAMPAIGN_ID:
        campaigns = [{"id": int(YM_CAMPAIGN_ID), "placementType": "FBY", "domain": "default"}]
    log.info(f"  Кампании для возвратов: {[(c.get('id'), c.get('placementType')) for c in campaigns]}")

    scanned = 0
    new_rows = 0
    updated = 0
    skipped_unredeemed = 0
    skipped_no_content = 0
    skipped_old = 0

    for camp in campaigns:
        camp_id = camp.get("id")
        placement = str(camp.get("placementType") or "").upper() or "UNKNOWN"
        if not camp_id:
            continue
        log.info(f"  → кампания {camp_id} ({placement})")
        page_token = None
        page_no = 0

        while True:
            page_no += 1
            params = {"limit": 50}
            if page_token:
                params["page_token"] = page_token

            try:
                data = _get(f"/v2/campaigns/{camp_id}/returns", params=params)
            except Exception as e:
                log.error(f"  Ошибка списка возвратов camp={camp_id}: {e}")
                break

            result = data.get("result", {})
            returns = result.get("returns", [])
            if not returns:
                break

            for r in returns:
                scanned += 1
                return_id = r.get("id")
                order_id = r.get("orderId")
                return_type = r.get("returnType", "")

                created_str = r.get("creationDate") or ""
                try:
                    created_dt = datetime.fromisoformat(created_str)
                    created_utc = created_dt.astimezone(timezone.utc)
                except Exception:
                    created_dt = None
                    created_utc = None

                if created_utc and created_utc < YM_RETURNS_FROM:
                    skipped_old += 1
                    continue

                if return_type == "UNREDEEMED":
                    skipped_unredeemed += 1
                    continue

                items = r.get("items") or []
                if not items:
                    skipped_no_content += 1
                    continue

                # Уже есть в БД с фото — не качаем снова (иначе FBY-страницы висят часами)
                existing_photos = None
                try:
                    with engine.connect() as conn:
                        existing_photos = conn.execute(text("""
                            SELECT photos FROM ym_returns WHERE return_id = :rid LIMIT 1
                        """), {"rid": return_id}).scalar()
                except Exception:
                    existing_photos = None
                skip_photo_download = bool(existing_photos)

                article = ""
                comments = []
                photo_urls = []
                reason_type = None
                subreason_type = None

                for item in items:
                    if not article:
                        article = (item.get("shopSku") or "").strip()
                    for decision in (item.get("decisions") or []):
                        if reason_type is None:
                            reason_type = decision.get("reasonType")
                            subreason_type = decision.get("subreasonType")
                        c = (decision.get("comment") or "").strip()
                        if c and c not in comments:
                            comments.append(c)
                        if skip_photo_download:
                            continue
                        for img_hash in (decision.get("images") or []):
                            url = _download_ym_photo(
                                order_id, return_id,
                                decision.get("returnItemId"), img_hash,
                                campaign_id=camp_id,
                            )
                            if url:
                                photo_urls.append(url)
                            time.sleep(0.15)

                comment = "\n\n".join(comments)
                if skip_photo_download and existing_photos:
                    photo_urls = [u for u in str(existing_photos).split() if u]

                if not comment and not photo_urls and not reason_type:
                    skipped_no_content += 1
                    continue

                photos_str = " ".join(photo_urls) if photo_urls else None
                refund_status = (
                    effective_refund_status(r)
                    or r.get("refundStatus")
                    or r.get("shipmentStatus")
                    or ""
                )
                status_ru = ym_status_ru(
                    refund_status,
                    YM_REFUND_STATUS_MAP.get(refund_status, "На рассмотрении"),
                )

                try:
                    with engine.begin() as conn:
                        row = conn.execute(text("""
                            INSERT INTO ym_returns
                                (return_id, order_id, supplier_article,
                                 return_type, status, status_ru,
                                 created_at, updated_at,
                                 return_comment, photos,
                                 reason_type, subreason_type,
                                 campaign_id, placement_type)
                            VALUES
                                (:rid, :oid, :article,
                                 :rtype, :status, :status_ru,
                                 :created, :updated,
                                 :comment, :photos,
                                 :reason, :subreason,
                                 :camp, :placement)
                            ON CONFLICT (return_id) DO UPDATE SET
                                status = CASE
                                    WHEN COALESCE(ym_returns.force_archive, false)
                                    THEN ym_returns.status
                                    ELSE EXCLUDED.status
                                END,
                                status_ru = CASE
                                    WHEN COALESCE(ym_returns.force_archive, false)
                                    THEN ym_returns.status_ru
                                    ELSE EXCLUDED.status_ru
                                END,
                                order_id = EXCLUDED.order_id,
                                supplier_article = COALESCE(EXCLUDED.supplier_article, ym_returns.supplier_article),
                                updated_at = EXCLUDED.updated_at,
                                return_comment = COALESCE(EXCLUDED.return_comment, ym_returns.return_comment),
                                photos = COALESCE(EXCLUDED.photos, ym_returns.photos),
                                reason_type = COALESCE(EXCLUDED.reason_type, ym_returns.reason_type),
                                subreason_type = COALESCE(EXCLUDED.subreason_type, ym_returns.subreason_type),
                                campaign_id = COALESCE(EXCLUDED.campaign_id, ym_returns.campaign_id),
                                placement_type = COALESCE(EXCLUDED.placement_type, ym_returns.placement_type),
                                synced_at = NOW()
                            RETURNING (xmax = 0) AS is_new
                        """), {
                            "rid": return_id,
                            "oid": order_id,
                            "article": article,
                            "rtype": return_type,
                            "status": refund_status,
                            "status_ru": status_ru,
                            "created": created_dt,
                            "updated": r.get("updateDate"),
                            "comment": comment or None,
                            "photos": photos_str,
                            "reason": reason_type,
                            "subreason": subreason_type,
                            "camp": int(camp_id),
                            "placement": placement,
                        }).fetchone()

                    if row and row[0]:
                        new_rows += 1
                        log.info(
                            f"  NEW {return_id} | {placement} | {article} | "
                            f"текст: {'✓' if comment else '–'} | фото: {len(photo_urls)} | {status_ru}"
                        )
                    else:
                        updated += 1

                except Exception as e:
                    log.error(f"  Ошибка записи возврата {return_id}: {e}")

            page_token = result.get("paging", {}).get("nextPageToken")
            if not page_token:
                break
            if max_pages and page_no >= max_pages:
                log.info(f"  кампания {camp_id}: стоп после {page_no} стр. (частый проход)")
                break
            time.sleep(0.5)

        time.sleep(0.3)

    summary = (
        f"кампаний {len(campaigns)}, просмотрено {scanned}, новых {new_rows}, обновлено {updated} | "
        f"пропущено: до2026={skipped_old} unredeemed={skipped_unredeemed} без-контента={skipped_no_content}"
        + (f" | max_pages={max_pages}" if max_pages else "")
    )
    log.info(f"Возвраты ЯМ: {summary}")
    _log_db("ym_sync_returns", "success", summary)


def sync_recent_returns():
    """Дневной проход: первые страницы, чтобы очередь CX не ждала ночной hourly."""
    sync_returns(max_pages=40)


def _feedback_answered_at(fb):
    """В очереди CX оставляем, пока у отзыва нет комментария магазина.

    needReaction=false в API ЯМ значит только «прочитан в ЛК» / skip-reaction,
    без ответа. Раньше такие отзывы пропадали с сайта, хотя в кабинете они
    ещё висят без комментария.
    """
    stats = fb.get("statistics") or {}
    try:
        comments = int(stats.get("commentsCount") or 0)
    except (TypeError, ValueError):
        comments = 0
    if comments > 0:
        return datetime.now(timezone.utc)
    return None


def _insert_ym_feedback(conn, fb, *, answered_at=None):
    desc = fb.get("description") or {}
    stats = fb.get("statistics") or {}
    idents = fb.get("identifiers") or {}
    fid = str(fb.get("feedbackId") or "").strip()
    if not fid:
        return False
    conn.execute(text("""
        INSERT INTO ym_feedbacks
            (feedback_id, supplier_article, created_date, valuation,
             pro_text, contra_text, comment, answered_at)
        VALUES
            (:fid, :article, :created_date, :val,
             :pro, :contra, :comment, :answered)
        ON CONFLICT (feedback_id) DO UPDATE SET
            valuation   = EXCLUDED.valuation,
            pro_text    = EXCLUDED.pro_text,
            contra_text = EXCLUDED.contra_text,
            comment     = EXCLUDED.comment,
            """ + YM_MARK + """,
            answered_at = CASE
                WHEN ym_feedbacks.answered_at IS NOT NULL AND EXCLUDED.answered_at IS NULL
                     AND COALESCE(ym_feedbacks.answer_text, '') = ''
                     AND ym_feedbacks.answered_at > now() - interval '60 days'
                THEN NULL
                WHEN ym_feedbacks.answered_at IS NOT NULL AND EXCLUDED.answered_at IS NULL
                     AND (
                         COALESCE(ym_feedbacks.comment,'') IS DISTINCT FROM COALESCE(EXCLUDED.comment,'')
                         OR ym_feedbacks.valuation IS DISTINCT FROM EXCLUDED.valuation
                         OR COALESCE(ym_feedbacks.pro_text,'') IS DISTINCT FROM COALESCE(EXCLUDED.pro_text,'')
                         OR COALESCE(ym_feedbacks.contra_text,'') IS DISTINCT FROM COALESCE(EXCLUDED.contra_text,'')
                     )
                THEN NULL
                ELSE COALESCE(ym_feedbacks.answered_at, EXCLUDED.answered_at)
            END
    """), {
        "fid": fid,
        "article": str(idents.get("offerId") or ""),
        "created_date": fb.get("createdAt"),
        "val": stats.get("rating"),
        "pro": desc.get("advantages", ""),
        "contra": desc.get("disadvantages", ""),
        "comment": desc.get("comment", ""),
        "answered": answered_at,
    })
    return True


# ── 3. Отзывы (VOC) ─────────────────────────────────────────────────────────
# Реальная структура (проверено curl):
#   feedbackId  (не id!)
#   identifiers.offerId — артикул
#   statistics.rating   — оценка (не grades.product!)
#   description.advantages / description.disadvantages
#   Пагинация: query ?limit=&pageToken= (nextPageToken). В теле pageToken
#   ЯМ игнорирует и снова отдаёт первую страницу.
#   Без дат API отдаёт только последние 6 месяцев (лимит метода).
_YM_FEEDBACK_PAGE = 50
_YM_FEEDBACK_WINDOW = timedelta(days=180)
_YM_FEEDBACK_LOOKBACK = timedelta(days=730)  # 4 окна по ~6 мес.


def _iso_msk(dt: datetime) -> str:
    return dt.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%S+03:00")


def _post_goods_feedback(body: dict, page_token: str | None = None) -> dict:
    """POST goods-feedback. limit и pageToken — только в query, не в JSON."""
    params = {"limit": _YM_FEEDBACK_PAGE}
    if page_token:
        params["pageToken"] = page_token
    return ym_client.post(
        f"/v2/businesses/{YM_BUSINESS_ID}/goods-feedback",
        body,
        params=params,
        key=YM_API_KEY,
    )


def _feedback_pages(body: dict):
    """Страницы отзывов. Повтор того же pageToken или тех же id — стоп."""
    page_token = None
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    while True:
        if page_token:
            if page_token in seen_tokens:
                log.warning("Отзывы ЯМ: pageToken повторился, останавливаюсь")
                break
            seen_tokens.add(page_token)
        data = _post_goods_feedback(body, page_token=page_token)
        items = (data.get("result") or {}).get("feedbacks") or []
        fresh = []
        for fb in items:
            fid = str(fb.get("feedbackId") or "").strip()
            if not fid or fid in seen_ids:
                continue
            seen_ids.add(fid)
            fresh.append(fb)
        if not fresh:
            break
        yield fresh
        page_token = ((data.get("result") or {}).get("paging") or {}).get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.6)


def _sync_feedbacks_window(date_from: datetime, date_to: datetime) -> int:
    body = {
        "reactionStatus": "ALL",
        "dateTimeFrom": _iso_msk(date_from),
        "dateTimeTo": _iso_msk(date_to),
    }
    total = 0
    for items in _feedback_pages(body):
        with engine.begin() as conn:
            for fb in items:
                _insert_ym_feedback(conn, fb, answered_at=_feedback_answered_at(fb))
                total += 1
    return total


def sync_feedback_need_reaction() -> int:
    """Все отзывы, на которые кабинет ЯМ ещё ждёт ответ — в очередь CX."""
    log.info("ЯМ: отзывы, которые ждут ответа...")
    total = 0
    for items in _feedback_pages({"reactionStatus": "NEED_REACTION"}):
        with engine.begin() as conn:
            for fb in items:
                _insert_ym_feedback(conn, fb, answered_at=_feedback_answered_at(fb))
                total += 1
    log.info("ЯМ NEED_REACTION: %s", total)
    _log_db("ym_sync_feedback_need", "success", f"{total} отзывов ждут ответа")
    return total


def sync_feedbacks():
    log.info("Синхронизация отзывов ЯМ...")
    total = 0
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    stop = end - _YM_FEEDBACK_LOOKBACK
    while end > stop:
        start = max(stop, end - _YM_FEEDBACK_WINDOW)
        n = _sync_feedbacks_window(start, end)
        log.info("Отзывы ЯМ окно %s…%s: %s", _iso_msk(start), _iso_msk(end), n)
        total += n
        end = start
        if n == 0 and start <= stop + timedelta(days=1):
            break
        if n == 0:
            # Пустое старое окно — дальше скорее всего тоже пусто.
            break

    log.info(f"Отзывы ЯМ: {total}")
    _log_db("ym_sync_feedbacks", "success", f"{total} отзывов")


def sync_feedback_queue():
    """Сверяем очередь CX с кабинетом ЯМ по уже известным отзывам.

    Если в кабинете на отзыв есть комментарий — ставим answered_at.
    Новые отзывы сюда не добавляем. Прочитан в ЛК без комментария — не закрываем.
    """
    log.info("Сверка очереди отзывов ЯМ с кабинетом...")
    with engine.connect() as conn:
        ids = [
            str(r[0]).strip()
            for r in conn.execute(text(
                "SELECT feedback_id FROM ym_feedbacks WHERE answered_at IS NULL"
            )).fetchall()
            if r[0]
        ]
    marked = 0
    for i in range(0, len(ids), 50):
        chunk = [x for x in ids[i:i + 50] if x.isdigit()]
        if not chunk:
            continue
        body = {"feedbackIds": [int(x) for x in chunk]}
        data = None
        for attempt in range(4):
            try:
                data = _post(f"/v2/businesses/{YM_BUSINESS_ID}/goods-feedback", body)
                break
            except RateLimitError:
                raise
            except Exception as exc:
                if attempt == 3:
                    log.warning("Не удалось сверить отзывы %s…: %s", chunk[0], exc)
                    data = None
                    break
                time.sleep(6 * (attempt + 1))
        if not data:
            continue
        if str(data.get("status") or "").upper() == "ERROR":
            continue
        items = (data.get("result") or {}).get("feedbacks") or []
        with engine.begin() as conn:
            for fb in items:
                if not _feedback_answered_at(fb):
                    continue
                fid = str(fb.get("feedbackId") or "").strip()
                res = conn.execute(text("""
                    UPDATE ym_feedbacks
                    SET answered_at = now()
                    WHERE feedback_id = :fid AND answered_at IS NULL
                """), {"fid": fid})
                marked += res.rowcount or 0
            # Не закрываем id, которых нет в ответе: пустая пачка часто значит
            # сбой API, а не «отзыва больше нет».
        log.info("Сверка отзывов ЯМ: %s/%s, закрыто %s", min(i + 50, len(ids)), len(ids), marked)
        time.sleep(0.25)

    log.info("Сверка отзывов ЯМ готова: проверено %s, закрыто %s", len(ids), marked)
    _log_db("ym_sync_feedback_queue", "success", f"проверено {len(ids)}, закрыто {marked}")


# ── 4. Вопросы ───────────────────────────────────────────────────────────────
# Метод POST (не GET!)
# questionIdentifiers.id и questionIdentifiers.offerId — не просто id и offerId
# needAnswer=true — только вопросы, которые ждут ответа продавца (иначе API
# отдаёт всю историю, синк идёт часами и свежие вопросы не попадают в очередь).
def _question_market_sku(q: dict) -> str:
    qid = q.get("questionIdentifiers") if isinstance(q.get("questionIdentifiers"), dict) else {}
    product = q.get("product") if isinstance(q.get("product"), dict) else {}
    for src in (qid, product, q):
        if not isinstance(src, dict):
            continue
        for key in ("marketSku", "skuId"):
            val = src.get(key)
            if val not in (None, ""):
                return str(val).strip()
    return ""


def _offer_market_skus(offer_ids: list[str]) -> dict[str, str]:
    seen: list[str] = []
    for raw in offer_ids:
        oid = str(raw or "").strip()
        if oid and oid not in seen:
            seen.append(oid)
    out: dict[str, str] = {}
    for i in range(0, len(seen), 50):
        chunk = seen[i:i + 50]
        try:
            data = _post(f"/v2/businesses/{YM_BUSINESS_ID}/offer-mappings", {"offerIds": chunk})
        except Exception:
            log.warning("ЯМ: не удалось сопоставить marketSku для %s", chunk, exc_info=True)
            continue
        result = data.get("result") if isinstance(data, dict) else {}
        mappings = (result or {}).get("offerMappings") or []
        for row in mappings:
            offer = row.get("offer") if isinstance(row, dict) else {}
            mapping = row.get("mapping") if isinstance(row, dict) else {}
            oid = str((offer or {}).get("offerId") or "").strip()
            msku = (mapping or {}).get("marketSku")
            if oid and msku not in (None, ""):
                out[oid] = str(msku)
    return out


def sync_questions():
    log.info("Синхронизация вопросов ЯМ (только без ответа)...")
    page_token = None
    total = 0
    open_ids: set[str] = set()
    finished = False

    try:
        while True:
            body = {"pageSize": 100, "needAnswer": True}
            if page_token:
                body["pageToken"] = page_token

            data = _post(f"/v1/businesses/{YM_BUSINESS_ID}/goods-questions", body)
            result = data.get("result", {})
            questions = result.get("questions", [])
            if not questions:
                finished = True
                break

            missing_offers: list[str] = []
            with engine.begin() as conn:
                for q in questions:
                    qid = q.get("questionIdentifiers") or {}
                    question_id = str(qid.get("id") or "").strip()
                    if not question_id:
                        continue
                    answers = q.get("answers") or []
                    answer_text = answers[0].get("text") if answers else None
                    open_ids.add(question_id)
                    article = str(qid.get("offerId") or "")
                    market_sku = _question_market_sku(q)
                    if not market_sku and article:
                        missing_offers.append(article)

                    conn.execute(text("""
                        INSERT INTO ym_questions
                            (question_id, supplier_article, created_date, text,
                             answer_text, state, market_sku, synced_at)
                        VALUES
                            (:qid, :article, :created_date, :text,
                             :answer, 'UNANSWERED', :market_sku, now())
                        ON CONFLICT (question_id) DO UPDATE SET
                            supplier_article = EXCLUDED.supplier_article,
                            text             = EXCLUDED.text,
                            answer_text      = COALESCE(EXCLUDED.answer_text, ym_questions.answer_text),
                            state            = 'UNANSWERED',
                            market_sku       = COALESCE(EXCLUDED.market_sku, ym_questions.market_sku),
                            synced_at        = now()
                    """), {
                        "qid": question_id,
                        "article": article,
                        "created_date": q.get("createdAt"),
                        "text": q.get("text") or "",
                        "answer": answer_text,
                        "market_sku": market_sku or None,
                    })
                    total += 1

            resolved = _offer_market_skus(missing_offers)
            if resolved:
                with engine.begin() as conn:
                    for article, msku in resolved.items():
                        conn.execute(text("""
                            UPDATE ym_questions
                            SET market_sku = :msku, synced_at = now()
                            WHERE supplier_article = :article
                              AND coalesce(market_sku, '') = ''
                        """), {"msku": msku, "article": article})

            page_token = result.get("paging", {}).get("nextPageToken")
            if not page_token:
                finished = True
                break
            time.sleep(0.5)
    except Exception:
        # Не закрываем очередь по частичному снимку — иначе живые вопросы
        # пропадут из раздела, если API оборвался посередине.
        raise

    closed = 0
    if finished:
        with engine.begin() as conn:
            if open_ids:
                result = conn.execute(text("""
                    UPDATE ym_questions
                    SET state = 'ANSWERED', synced_at = now()
                    WHERE state = 'UNANSWERED'
                      AND NOT (question_id = ANY(:ids))
                """), {"ids": list(open_ids)})
            else:
                result = conn.execute(text("""
                    UPDATE ym_questions
                    SET state = 'ANSWERED', synced_at = now()
                    WHERE state = 'UNANSWERED'
                """))
            closed = result.rowcount or 0

    summary = f"{total} без ответа, закрыто устаревших {closed}"
    log.info(f"Вопросы ЯМ: {summary}")
    _log_db("ym_sync_questions", "success", summary)


# ── 5. Ассортимент ───────────────────────────────────────────────────────────
def sync_assortment():
    log.info("Синхронизация ассортимента ЯМ...")
    page_token = None
    total = 0

    while True:
        body = {"limit": 200}
        if page_token:
            body["page_token"] = page_token

        data   = _post(f"/v2/businesses/{YM_BUSINESS_ID}/offer-mappings", body)
        result = data.get("result", {})
        offers = result.get("offerMappings", [])
        if not offers:
            break

        with engine.begin() as conn:
            for o in offers:
                offer   = o.get("offer", {})
                mapping = o.get("mapping", {})
                conn.execute(text("""
                    INSERT INTO ym_assortment
                        (offer_id, supplier_article, name, category, market_sku)
                    VALUES
                        (:oid, :article, :name, :cat, :msku)
                    ON CONFLICT (offer_id) DO UPDATE SET
                        name       = EXCLUDED.name,
                        category   = EXCLUDED.category,
                        market_sku = EXCLUDED.market_sku,
                        synced_at  = NOW()
                """), {
                    "oid":     offer.get("offerId", ""),
                    "article": offer.get("offerId", ""),
                    "name":    offer.get("name"),
                    "cat":     offer.get("category"),
                    "msku":    mapping.get("marketSku"),
                })
                total += 1

        log.info("Ассортимент ЯМ: уже %s позиций", total)
        page_token = result.get("paging", {}).get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)

    log.info(f"Ассортимент ЯМ: {total}")
    _log_db("ym_sync_assortment", "success", f"{total} позиций")


# ── 6. Цены ──────────────────────────────────────────────────────────────────
# price.value — в рублях (не копейках!)
def sync_prices():
    log.info("Синхронизация цен ЯМ...")
    page_token = None
    total = 0

    while True:
        body = {"limit": 200}
        if page_token:
            body["page_token"] = page_token

        data   = _post(f"/v2/businesses/{YM_BUSINESS_ID}/offer-prices", body)
        result = data.get("result", {})
        offers = result.get("offers", [])
        if not offers:
            break

        with engine.begin() as conn:
            for o in offers:
                p = o.get("price", {})
                conn.execute(text("""
                    INSERT INTO ym_prices
                        (supplier_article, price, discount_base, currency)
                    VALUES
                        (:article, :price, :base, :cur)
                    ON CONFLICT (supplier_article) DO UPDATE SET
                        price         = EXCLUDED.price,
                        discount_base = EXCLUDED.discount_base,
                        updated_at    = NOW()
                """), {
                    "article": o.get("offerId", ""),
                    "price":   p.get("value"),           # уже в рублях
                    "base":    p.get("discountBase"),
                    "cur":     p.get("currencyId", "RUR"),
                })
                total += 1

        page_token = result.get("paging", {}).get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)

    log.info(f"Цены ЯМ: {total}")
    _log_db("ym_sync_prices", "success", f"{total} позиций")


# ── 7. Финансовые отчёты (асинхронные) ──────────────────────────────────────
def generate_finance_reports():
    yesterday = date.today() - timedelta(days=1)
    month_ago = yesterday   - timedelta(days=30)
    prev_month_date = date.today().replace(day=1) - timedelta(days=1)
    report_year  = prev_month_date.year
    report_month = prev_month_date.month

    report_configs = [
        ("realization", "/v2/reports/goods-realization/generate", {
            "campaignId": int(YM_CAMPAIGN_ID),
            "year":       report_year,
            "month":      report_month,
        }),
        ("services", "/v2/reports/united-marketplace-services/generate", {
            "businessId": int(YM_BUSINESS_ID),
            "dateFrom":   month_ago.isoformat(),
            "dateTo":     yesterday.isoformat(),
        }),
    ]

    for rtype, endpoint, body in report_configs:
        with engine.connect() as conn:
            if rtype == "realization":
                # Месячный отчёт — пропускаем, если уже есть pending или downloaded за этот месяц
                already = conn.execute(
                    text("""SELECT 1 FROM ym_finance_reports
                            WHERE report_type=:rt
                            AND status IN ('pending', 'downloaded')
                            AND EXTRACT(month FROM period_from) = :month
                            AND EXTRACT(year  FROM period_from) = :year
                            LIMIT 1"""),
                    {"rt": rtype, "month": report_month, "year": report_year}
                ).fetchone()
            else:
                already = conn.execute(
                    text("SELECT 1 FROM ym_finance_reports WHERE report_type=:rt AND status='pending' LIMIT 1"),
                    {"rt": rtype}
                ).fetchone()
        if already:
            log.info(f"Отчёт {rtype} уже в очереди или скачан — пропускаем генерацию.")
            continue

        try:
            resp = _post(endpoint, body)
            report_id = resp.get("result", {}).get("reportId")
            if report_id:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO ym_finance_reports
                            (report_type, report_id, status, period_from, period_to)
                        VALUES (:rtype, :rid, 'pending', :pfrom, :pto)
                    """), {"rtype": rtype, "rid": report_id,
                            "pfrom": month_ago, "pto": yesterday})
                log.info(f"Отчёт {rtype} запущен: {report_id}")
                _log_db(f"ym_report_{rtype}_generate", "success", report_id)
        except Exception as e:
            log.error(f"Ошибка запуска отчёта {rtype}: {e}")
            _log_db(f"ym_report_{rtype}_generate", "error", str(e))


def poll_finance_reports():
    with engine.connect() as conn:
        pending = conn.execute(
            text("SELECT id, report_id, report_type FROM ym_finance_reports WHERE status='pending'")
        ).mappings().all()

    if not pending:
        log.info("Нет ожидающих финансовых отчётов.")
        return

    for row in pending:
        try:
            resp   = _get(f"/v2/reports/info/{row['report_id']}")
            info   = resp.get("result", {})
            status = info.get("status")

            if status == "DONE":
                url = info.get("file")
                revenue, commissions = _parse_report(row["report_type"], url)
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE ym_finance_reports
                           SET status='downloaded', download_url=:url,
                               revenue=:rev, commissions=:comm, downloaded_at=NOW()
                         WHERE id=:id
                    """), {"url": url, "rev": revenue, "comm": commissions, "id": row["id"]})
                log.info(f"Отчёт {row['report_type']} скачан: выручка={revenue}, комиссии={commissions}")
            elif status == "FAILED":
                with engine.begin() as conn:
                    conn.execute(text("UPDATE ym_finance_reports SET status='failed' WHERE id=:id"),
                                  {"id": row["id"]})
                log.warning(f"Отчёт {row['report_id']} завершился ошибкой на стороне ЯМ.")
            else:
                log.info(f"Отчёт {row['report_id']} ещё не готов (status={status}).")
        except Exception as e:
            log.error(f"Ошибка опроса отчёта {row['report_id']}: {e}")


def _parse_report(report_type, url):
    if not url:
        return None, None
    try:
        r  = requests.get(url, headers=_hdr(), timeout=120)
        r.raise_for_status()
        raw = r.content

        # ЯМ отдаёт отчёт united-marketplace-services в формате XLSX (это zip, начинается
        # с сигнатуры 'PK'), хотя content-type = application/octet-stream. Старый код
        # декодировал его как текстовый TSV и падал («'utf-8' codec can't decode byte 0x8a
        # in position 10»). Поэтому определяем формат по сигнатуре, а не по content-type.
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                if "[Content_Types].xml" in names:              # настоящий XLSX
                    return _parse_finance_xlsx(report_type, raw)
                content = z.read(names[0]).decode("utf-8-sig")   # zip с TSV внутри
        else:
            content = raw.decode("utf-8-sig")                    # обычный TSV

        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        revenue = commissions = 0.0
        for row in reader:
            def _num(k):
                v = str(row.get(k, "") or "").replace(",", ".").replace("\xa0", "").replace(" ", "")
                try:   return float(v)
                except ValueError: return 0.0
            if report_type == "realization":
                revenue     += _num("Стоимость продаж")
                commissions += _num("Агентское вознаграждение")
            else:
                commissions += _num("Сумма")
        return round(revenue, 2), round(commissions, 2)
    except Exception as e:
        log.warning(f"Парсинг отчёта {report_type} упал: {e}")
        return None, None


def _parse_finance_xlsx(report_type, raw):
    """Парсит XLSX-отчёт ЯМ «О стоимости услуг маркетплейса».
    Лист «Сводка» у этого аккаунта приходит пустым (нули) — реальные начисления лежат
    на детальных листах по каждому виду услуги (Доставка, Приём платежа, Хранение…),
    в колонке «Стоимость услуги, ₽». Суммируем эту колонку по всем листам.
    Возвращает (revenue, commissions); у services выручки нет → revenue=None,
    суммарную стоимость услуг кладём в commissions.
    ВАЖНО: грузим openpyxl БЕЗ read_only — в read_only-режиме этот файл читается
    некорректно и колонки не находятся."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    COST_HEADER = "Стоимость услуги, ₽"

    total = 0.0
    found_header = False
    for name in wb.sheetnames:
        if name == "Сводка":
            continue
        ws = wb[name]
        col = None
        for row in ws.iter_rows(values_only=True):
            if col is None:
                # ищем строку заголовков с колонкой «Стоимость услуги, ₽»
                for idx, c in enumerate(row):
                    if isinstance(c, str) and c.strip() == COST_HEADER:
                        col = idx
                        found_header = True
                        break
                continue
            if col < len(row) and isinstance(row[col], (int, float)):
                total += row[col]

    if not found_header:
        log.warning(f"XLSX-отчёт {report_type}: колонка '{COST_HEADER}' не найдена ни на "
                    f"одном листе (листы={wb.sheetnames})")
        return None, None

    commissions = round(total, 2)
    revenue = None if report_type == "services" else commissions
    log.info(f"XLSX-отчёт {report_type}: суммарная стоимость услуг = {commissions} ₽")
    return revenue, commissions



# ── 8. Поставки (supply requests) ────────────────────────────────────────────
# Тянем все заявки на поставку ЯМ + товарный состав.
# Используется для связи возврат → поставка → инвойс (эвристика: SKU + склад + дата).
#
# Форматы в Google Sheet "Номер поставки" для МП=Яндекс:
#   • "30725002"     → marketplaceRequestId (номер с акта приёмки)
#   • "ВРЦ-8299161"  → ВРЦ- + request_id (VDC-родительская заявка)
# Оба формата обрабатываются в запросе production.py.
def sync_supplies():
    log.info("Синхронизация поставок ЯМ...")
    saved_supplies = 0
    saved_items    = 0

    # Берём большой диапазон — всё что есть; limit=200 чтобы не пагинировать лишний раз
    data = _post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/supply-requests", {
        "limit": 200,
        "dateFrom": "2024-01-01",
        "dateTo": (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%d"),
    })

    requests_list = data.get("result", {}).get("requests", [])
    if not requests_list:
        log.info("Поставки ЯМ: нет данных")
        return

    for req in requests_list:
        rid_obj = req.get("id", {})
        request_id = rid_obj.get("id")
        if not request_id:
            continue

        marketplace_request_id = rid_obj.get("marketplaceRequestId")
        warehouse_request_id   = rid_obj.get("warehouseRequestId")

        parent_link = req.get("parentLink") or {}
        parent_id   = (parent_link.get("id") or {}).get("id")

        target         = req.get("targetLocation") or {}
        warehouse_id   = target.get("serviceId")
        warehouse_name = target.get("name")
        requested_date = target.get("requestedDate")

        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ym_supplies
                        (request_id, marketplace_request_id, warehouse_request_id,
                         parent_request_id, warehouse_id, warehouse_name,
                         requested_date, status, subtype)
                    VALUES
                        (:rid, :mprid, :whrid, :parent,
                         :whid, :whnm, :rdate, :status, :subtype)
                    ON CONFLICT (request_id) DO UPDATE SET
                        marketplace_request_id = COALESCE(EXCLUDED.marketplace_request_id, ym_supplies.marketplace_request_id),
                        warehouse_request_id   = COALESCE(EXCLUDED.warehouse_request_id,   ym_supplies.warehouse_request_id),
                        parent_request_id      = COALESCE(EXCLUDED.parent_request_id,      ym_supplies.parent_request_id),
                        status    = EXCLUDED.status,
                        synced_at = NOW()
                """), {
                    "rid":    request_id,
                    "mprid":  marketplace_request_id,
                    "whrid":  warehouse_request_id,
                    "parent": parent_id,
                    "whid":   warehouse_id,
                    "whnm":   warehouse_name,
                    "rdate":  requested_date,
                    "status": req.get("status"),
                    "subtype":req.get("subtype"),
                })
            saved_supplies += 1
        except Exception as e:
            log.warning(f"  Ошибка записи поставки {request_id}: {e}")
            continue

        # Товарный состав — только у заявок с marketplaceRequestId (VDC-родители пустые)
        if not marketplace_request_id:
            continue
        try:
            items_data = _post(
                f"/v2/campaigns/{YM_CAMPAIGN_ID}/supply-requests/items",
                {"requestId": request_id, "limit": 200}
            )
            items = items_data.get("result", {}).get("items", [])
            for item in items:
                offer_id = item.get("offerId")
                if not offer_id:
                    continue
                cnt = item.get("counters") or {}
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO ym_supply_items
                            (request_id, offer_id, plan_count, fact_count, defect_count)
                        VALUES (:rid, :oid, :plan, :fact, :defect)
                        ON CONFLICT (request_id, offer_id) DO UPDATE SET
                            plan_count   = EXCLUDED.plan_count,
                            fact_count   = COALESCE(EXCLUDED.fact_count,   ym_supply_items.fact_count),
                            defect_count = COALESCE(EXCLUDED.defect_count, ym_supply_items.defect_count),
                            synced_at    = NOW()
                    """), {
                        "rid":    request_id,
                        "oid":    offer_id,
                        "plan":   cnt.get("planCount"),
                        "fact":   cnt.get("factCount"),
                        "defect": cnt.get("defectCount"),
                    })
                saved_items += 1
            time.sleep(0.3)
        except Exception as e:
            log.warning(f"  Ошибка items поставки {request_id}: {e}")

    log.info(f"Поставки ЯМ: {saved_supplies} поставок, {saved_items} позиций")
    _log_db("ym_sync_supplies", "success", f"{saved_supplies} поставок, {saved_items} позиций")



def _parse_ratings_xlsx(content_bytes, report_date):
    """
    Парсит 'business_rating_report_*.xlsx' (лист 'Рейтинг товаров').
    Структура: строки-группы (col0 заполнен, col2 пустой) → рейтинг витрины + дельта.
               строки-SKU (col0 пустой, col2 заполнен) → артикул + звёзды + кол-во.
    Столбцы: 0=Название, 1=Ссылка, 2=SKU, 3=Рейтинг, 4=Δнед, 5=Всего, 6=5★…10=1★
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)

    if "Рейтинг товаров" not in wb.sheetnames:
        log.warning(f"Лист 'Рейтинг товаров' не найден. Листы: {wb.sheetnames}")
        return []

    ws = wb["Рейтинг товаров"]
    log.info(f"Парсим лист 'Рейтинг товаров' ({ws.max_row} строк)")

    current_group_rating = None
    current_weekly_delta = None
    current_product_name = None
    rows = []

    for row_vals in ws.iter_rows(min_row=3, values_only=True):
        name  = row_vals[0] if len(row_vals) > 0 else None
        sku   = row_vals[2] if len(row_vals) > 2 else None
        rat   = row_vals[3] if len(row_vals) > 3 else None
        delta = row_vals[4] if len(row_vals) > 4 else None
        total = row_vals[5] if len(row_vals) > 5 else None
        s5    = row_vals[6] if len(row_vals) > 6 else None
        s4    = row_vals[7] if len(row_vals) > 7 else None
        s3    = row_vals[8] if len(row_vals) > 8 else None
        s2    = row_vals[9] if len(row_vals) > 9 else None
        s1    = row_vals[10] if len(row_vals) > 10 else None

        if name and str(name).strip():
            current_group_rating = float(rat)   if isinstance(rat,   (int, float)) else None
            current_weekly_delta = float(delta) if isinstance(delta, (int, float)) else None
            current_product_name = str(name).strip()
        elif sku and str(sku).strip():
            rows.append({
                "report_date":      report_date,
                "supplier_article": str(sku).strip(),
                "product_name":     current_product_name,
                "group_rating":     current_group_rating,
                "weekly_delta":     current_weekly_delta,
                "review_count":     int(total) if isinstance(total, (int, float)) else None,
                "stars_5":          int(s5)    if isinstance(s5,    (int, float)) else None,
                "stars_4":          int(s4)    if isinstance(s4,    (int, float)) else None,
                "stars_3":          int(s3)    if isinstance(s3,    (int, float)) else None,
                "stars_2":          int(s2)    if isinstance(s2,    (int, float)) else None,
                "stars_1":          int(s1)    if isinstance(s1,    (int, float)) else None,
            })

    return rows


# ── 10. Автоматическая загрузка рейтингов из кабинета ЯМ ────────────────────
# Использует веб-API partner.market.yandex.ru через Playwright (автологин).
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ym_session.json")


def _ym_login_get_session():
    """Открывает кабинет ЯМ через Playwright. Использует сохранённую сессию если есть."""
    from playwright.sync_api import sync_playwright

    bid  = int(YM_BUSINESS_ID)
    path = f"/business/{bid}/reviews-new?campaignId={YM_CAMPAIGN_ID}&activeTab=products&ratingDrawer=opened"
    ua   = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ])

        # Загружаем сохранённую сессию если файл есть
        session_exists = os.path.exists(SESSION_FILE)
        ctx_kwargs = dict(user_agent=ua, locale="ru-RU", timezone_id="Europe/Moscow")
        if session_exists:
            ctx_kwargs["storage_state"] = SESSION_FILE
            log.info("Playwright: загружаем сохранённую сессию ЯМ...")

        context = browser.new_context(**ctx_kwargs)
        page    = context.new_page()

        log.info("Playwright: переходим в кабинет ЯМ...")
        page.goto(f"https://partner.market.yandex.ru{path}", wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)

        content = page.content()
        sk_m = re.search(r'"sk":"(u[a-f0-9]+)"', content)

        if not sk_m and session_exists:
            # Сессия протухла. Повторный заход без пароля бессмысленный
            # (откроется страница «Авторизация»), а вход по паролю почти
            # всегда просит 2FA — это не может пройти в кроне.
            log.warning("Сохранённая сессия ЯМ устарела — нужен новый вход в кабинет")
            try:
                os.remove(SESSION_FILE)
            except OSError:
                pass

        cookies = context.cookies()
        cookie_str = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if any(d in c["domain"] for d in ["yandex", "market"])
        )

        if sk_m:
            # Обновляем сохранённую сессию
            context.storage_state(path=SESSION_FILE)
            log.info("Playwright: сессия обновлена")

        browser.close()

    if not sk_m:
        snippet = re.sub(r'<[^>]+>', ' ', content)[:400].strip()
        raise RuntimeError(f"sk-токен не найден. Начало страницы: {snippet}")
    return cookie_str, sk_m.group(1)


def sync_business_rating_report():
    """Скачивает BUSINESS_RATING_REPORT через автологин Playwright."""
    today = date.today()
    bid   = int(YM_BUSINESS_ID)
    ua    = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    partner_base = "https://partner.market.yandex.ru"
    path  = f"/business/{bid}/reviews-new?campaignId={YM_CAMPAIGN_ID}&activeTab=products&ratingDrawer=opened"

    try:
        cookie, sk = _ym_login_get_session()
    except Exception as e:
        log.error(f"Не удалось залогиниться в ЯМ: {e}")
        _log_db("ym_sync_ratings", "error", f"login failed: {e}")
        # Не копируем вчерашние цифры на сегодня: дашборд тогда выглядит
        # «живым», а свежих рейтингов нет.
        return

    hdrs = {
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Accept": "*/*",
        "Origin": partner_base,
        "Referer": f"{partner_base}{path}",
        "User-Agent": ua,
        "sk": sk,
    }

    # Шаг 2: запросить генерацию (обязателен reportParams с language!)
    log.info("Запрашиваем генерацию BUSINESS_RATING_REPORT...")
    gen_resp = requests.post(
        f"{partner_base}/api/resolve/?r=mbiPartner/asyncReports/generateReport:resolveGenerateReport",
        headers=hdrs,
        json={
            "params": [{"businessId": bid, "reportType": "BUSINESS_RATING_REPORT",
                        "reportParams": {"businessId": bid, "language": "ru"}}],
            "path": path,
        },
        timeout=30,
    )
    gen_resp.raise_for_status()
    gen_data = gen_resp.json()

    try:
        report_id = gen_data["results"][0]["data"][0]["id"]
    except (KeyError, IndexError, TypeError):
        log.error(f"Не удалось получить reportId: {str(gen_data)[:300]}")
        _log_db("ym_sync_ratings", "error", f"no reportId: {str(gen_data)[:200]}")
        return

    log.info(f"Отчёт запущен, reportId={report_id}. Ждём готовности...")

    # Шаг 3: опрашиваем статус (раз в 10 сек, до 10 минут)
    ready = False
    for attempt in range(60):
        time.sleep(10)
        poll_resp = requests.post(
            f"{partner_base}/api/resolve/?r=mbiPartner/asyncReports/getReport:resolveReport",
            headers=hdrs,
            json={"params": [{"businessId": bid, "reportId": report_id}], "path": path},
            timeout=30,
        )
        try:
            state = poll_resp.json()["results"][0]["data"][0].get("state", "")
        except (KeyError, IndexError, TypeError):
            state = ""
        log.info(f"  попытка {attempt+1}: state={state}")
        if state == "DONE":
            ready = True
            break
        if state in ("ERROR", "FAILED", "FAILURE"):
            log.error(f"Отчёт завершился ошибкой: {poll_resp.text[:200]}")
            _log_db("ym_sync_ratings", "error", f"report state={state}")
            return

    if not ready:
        log.error("Отчёт не готов за 10 минут — таймаут")
        _log_db("ym_sync_ratings", "error", "timeout")
        return

    # Шаг 4: скачиваем файл (ждём 10 сек — данные заполняются чуть позже DONE)
    time.sleep(10)
    download_url = f"{partner_base}/api/files/stat/async-reports/{report_id}?businessId={bid}"
    log.info("Скачиваем отчёт...")
    dl = requests.get(download_url, headers={"Cookie": cookie, "User-Agent": ua,
                                             "Referer": f"{partner_base}/"}, timeout=120)
    dl.raise_for_status()

    # Шаг 5: парсим и сохраняем
    rows = _parse_ratings_xlsx(dl.content, today)
    if not rows:
        log.error(f"Парсинг вернул 0 строк (размер файла: {len(dl.content)} байт)")
        _log_db("ym_sync_ratings", "error", "0 rows parsed")
        return

    _ingest_ratings_rows(rows)
    # Заполняем пропущенные SKU данными с предыдущего дня
    filled = _fill_missing_skus(today)
    log.info(f"Рейтинги ЯМ обновлены: {len(rows)} SKU из отчёта + {filled} SKU перенесены с предыдущего дня")
    _log_db("ym_sync_ratings", "success", f"{len(rows)} SKU за {today}, filled={filled}")


def _ingest_ratings_rows(rows):
    """Upsert строк в ym_ratings_report по (report_date, supplier_article)."""
    if not rows:
        return
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT INTO ym_ratings_report
                    (report_date, supplier_article, product_name, group_rating,
                     weekly_delta, review_count, stars_5, stars_4, stars_3, stars_2, stars_1)
                VALUES
                    (:report_date, :supplier_article, :product_name, :group_rating,
                     :weekly_delta, :review_count, :stars_5, :stars_4, :stars_3, :stars_2, :stars_1)
                ON CONFLICT (report_date, supplier_article) DO UPDATE SET
                    product_name = EXCLUDED.product_name,
                    group_rating = EXCLUDED.group_rating,
                    weekly_delta = EXCLUDED.weekly_delta,
                    review_count = EXCLUDED.review_count,
                    stars_5 = EXCLUDED.stars_5, stars_4 = EXCLUDED.stars_4,
                    stars_3 = EXCLUDED.stars_3, stars_2 = EXCLUDED.stars_2,
                    stars_1 = EXCLUDED.stars_1,
                    synced_at = NOW()
            """), r)


def _fill_missing_skus(report_date):
    """Для SKU из ym_prices которых нет в отчёте — переносим данные с ближайшего предыдущего дня."""
    with engine.begin() as conn:
        missing = conn.execute(text("""
            SELECT p.supplier_article
            FROM ym_prices p
            WHERE NOT EXISTS (
                SELECT 1 FROM ym_ratings_report r
                WHERE r.supplier_article = p.supplier_article
                  AND r.report_date = :dt
            )
        """), {"dt": report_date}).fetchall()

        filled = 0
        for (sku,) in missing:
            prev = conn.execute(text("""
                SELECT report_date, product_name, group_rating, weekly_delta,
                       review_count, stars_5, stars_4, stars_3, stars_2, stars_1
                FROM ym_ratings_report
                WHERE supplier_article = :sku AND report_date < :dt
                ORDER BY report_date DESC LIMIT 1
            """), {"sku": sku, "dt": report_date}).fetchone()

            if prev:
                conn.execute(text("""
                    INSERT INTO ym_ratings_report
                        (report_date, supplier_article, product_name, group_rating,
                         weekly_delta, review_count, stars_5, stars_4, stars_3, stars_2, stars_1)
                    VALUES (:dt, :sku, :product_name, :group_rating, 0,
                            :review_count, :stars_5, :stars_4, :stars_3, :stars_2, :stars_1)
                    ON CONFLICT (report_date, supplier_article) DO NOTHING
                """), {"dt": report_date, "sku": sku, "product_name": prev[1],
                       "group_rating": prev[2], "review_count": prev[4],
                       "stars_5": prev[5], "stars_4": prev[6], "stars_3": prev[7],
                       "stars_2": prev[8], "stars_1": prev[9]})
                filled += 1
            else:
                # Новый SKU без истории — вставляем нулевую запись
                conn.execute(text("""
                    INSERT INTO ym_ratings_report (report_date, supplier_article, group_rating, review_count)
                    VALUES (:dt, :sku, 0, 0)
                    ON CONFLICT (report_date, supplier_article) DO NOTHING
                """), {"dt": report_date, "sku": sku})
                filled += 1

    return filled


# ── Исторический сбор отзывов (backfill) ────────────────────────────────────
# Запускается вручную один раз: python3 ym_worker.py backfill
# Загружает все отзывы с начала года с повторными попытками при 420.
def sync_feedbacks_backfill():
    log.info("Исторический сбор отзывов ЯМ (backfill)...")
    total = 0
    for items in _feedback_pages({"reactionStatus": "ALL"}):
        page_new = 0
        with engine.begin() as conn:
            for fb in items:
                desc   = fb.get("description") or {}
                stats  = fb.get("statistics") or {}
                idents = fb.get("identifiers") or {}
                r = conn.execute(text("""
                    INSERT INTO ym_feedbacks
                        (feedback_id, supplier_article, created_date, valuation,
                         pro_text, contra_text, comment)
                    VALUES
                        (:fid, :article, :created_date, :val,
                         :pro, :contra, :comment)
                    ON CONFLICT (feedback_id) DO NOTHING
                """), {
                    "fid":          str(fb.get("feedbackId", "")),
                    "article":      str(idents.get("offerId") or ""),
                    "created_date": fb.get("createdAt"),
                    "val":          stats.get("rating"),
                    "pro":          desc.get("advantages", ""),
                    "contra":       desc.get("disadvantages", ""),
                    "comment":      desc.get("comment", ""),
                })
                page_new += r.rowcount
        total += page_new
        log.info(f"Backfill: +{page_new} новых ({total} всего), продолжаем...")

    log.info(f"Backfill завершён: {total} новых отзывов добавлено")
    _log_db("ym_backfill_feedbacks", "success", f"{total} новых отзывов")


# ── Точка входа ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from backend import cron_lock
    task = sys.argv[1] if len(sys.argv) > 1 else "hourly"

    if not YM_API_KEY or not YM_CAMPAIGN_ID or not YM_BUSINESS_ID:
        log.error("Не заданы YM_API_KEY / YM_CAMPAIGN_ID / YM_BUSINESS_ID в .env")
        sys.exit(1)

    # У каждой задачи свой замок: hourly может идти часами (отзывы),
    # и не должен глушить ежедневные рейтинги / отчёты.
    _YM_TASK_LOCKS = {
        "hourly": "ym_worker",
        "daily": "ym_worker_daily",
        "ratings": "ym_worker_ratings",
        "reports": "ym_worker_reports",
        "backfill": "ym_worker_backfill",
        "orders-backfill": "ym_worker_backfill",
        "feedback-queue": "ym_worker_feedback_queue",
        "questions": "ym_worker_questions",
        "returns": "ym_worker_returns",
    }
    _lock_conn = cron_lock.acquire_or_exit(
        engine, _YM_TASK_LOCKS.get(task, "ym_worker"), log_fn=log.info, log_db=_log_db
    )

    def run(fn):
        action = {
            "sync_orders": "ym_sync_orders",
            "sync_returns": "ym_sync_returns",
            "sync_recent_returns": "ym_sync_returns",
            "sync_feedback_queue": "ym_sync_feedback_queue",
            "sync_feedback_need_reaction": "ym_sync_feedback_need",
            "sync_feedbacks": "ym_sync_feedbacks",
            "sync_questions": "ym_sync_questions",
            "sync_assortment": "ym_sync_assortment",
            "sync_prices": "ym_sync_prices",
            "sync_business_rating_report": "ym_sync_ratings",
            "sync_feedbacks_backfill": "ym_backfill_feedbacks",
            "cleanup_old_ym_photos": "ym_photo_cleanup",
        }.get(fn.__name__, fn.__name__)
        try:
            fn()
        except RateLimitError as e:
            log.warning(f"Пропуск задачи из-за rate limit: {e}")
            _log_db(action, "rate_limit", str(e))
        except Exception as e:
            log.error(f"Ошибка в {fn.__name__}: {e}")
            _log_db(action, "error", str(e))

    if task == "hourly":
        run(sync_orders)
        run(sync_returns)
        run(sync_feedback_need_reaction)
        run(sync_feedback_queue)
        run(sync_feedbacks)
        run(sync_questions)
        # Автоматическое ИИ-тегирование новых возвратов ЯМ после синхронизации
        try:
            import subprocess
            script_dir = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(
                [sys.executable, os.path.join(script_dir, "ai_tagger.py"), "ym"],
                timeout=3600
            )
        except Exception as e:
            _log_db("ai_tagger_ym", "error", f"Сбой запуска: {e}")
        # Автотегирование отзывов ЯМ (поиск идей)
        try:
            from backend.feedback_tagger_job import spawn_feedback_job
            ok, msg = spawn_feedback_job("ym", "qwen-reviews", 20)
            _log_db("ai_tagger_ym_feedbacks", "success" if ok else "info", msg)
        except Exception as e:
            _log_db("ai_tagger_ym_feedbacks", "error", f"Сбой запуска: {e}")
    elif task == "daily":
        run(sync_assortment)
        run(sync_prices)
        run(generate_finance_reports)
        # sync_supplies отключён: таблица/эвристика поставок ЯМ неактуальны,
        # в API нет связки возврат→поставка. Не трогаем ym_supplies.
        run(cleanup_old_ym_photos)

    elif task == "ratings":
        run(sync_business_rating_report)
    elif task == "reports":
        run(poll_finance_reports)
    elif task == "backfill":
        run(sync_feedbacks_backfill)
    elif task == "orders-backfill":
        from_date = sys.argv[2] if len(sys.argv) > 2 else "2025-01-01"
        sync_orders_backfill(from_date)
    elif task == "feedback-queue":
        run(sync_feedback_queue)
    elif task == "questions":
        run(sync_feedback_need_reaction)
        run(sync_questions)
    elif task == "returns":
        run(sync_recent_returns)
    else:
        log.error(f"Неизвестная задача: '{task}'. Используй: hourly | daily | ratings | reports | backfill | orders-backfill | feedback-queue | questions | returns [YYYY-MM-DD]")
        sys.exit(1)
