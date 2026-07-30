"""
Воркер синхронизации фото товаров из OZON API v3.
Сохраняет первое фото по supplier_article в таблицу product_photos.
Запускается раз в 24 часа из main.py.
"""
import asyncio
import logging
from sqlalchemy import text

from . import ozon_client

logger = logging.getLogger("ozon_photo_sync")

SYNC_INTERVAL = 604800  # 7 дней


def _cid() -> str:
    return ozon_client.client_id()


def _key() -> str:
    return ozon_client.api_key()


def _post(path: str, body: dict) -> dict:
    return ozon_client.post(path, body, timeout=30)


def _collect_articles(get_db_func) -> list[str]:
    """Собирает все известные supplier_article из БД (wb_assortment + ozon_returns)."""
    db = next(get_db_func())
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT supplier_article FROM (
                    SELECT supplier_article FROM wb_assortment WHERE supplier_article IS NOT NULL
                    UNION
                    SELECT supplier_article FROM ozon_returns  WHERE supplier_article IS NOT NULL
                ) t
            """)).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        logger.error("[OzonPhotoSync] ошибка чтения артикулов: %s", e)
        return []
    finally:
        db.close()


def _get_images_by_offer_ids(offer_ids: list[str]) -> dict[str, str]:
    """POST /v3/product/info/list → {offer_id: first_image_url}."""
    result = {}
    for i in range(0, len(offer_ids), 100):
        batch = offer_ids[i:i + 100]
        try:
            data = _post("/v3/product/info/list", {"offer_id": batch})
            for item in data.get("items", []):
                oid = item.get("offer_id", "")
                if not oid:
                    continue
                # primary_image — список; images — список
                pi = item.get("primary_image") or []
                imgs = item.get("images") or []
                url = ""
                if isinstance(pi, list) and pi:
                    url = pi[0]
                elif isinstance(pi, str) and pi:
                    url = pi
                elif imgs:
                    url = imgs[0]
                if url:
                    result[oid] = url
        except Exception as e:
            logger.warning("[OzonPhotoSync] ошибка batch %d: %s", i, e)
    return result


def sync_ozon_photos(get_db_func) -> int:
    """Основной проход: загружает фото OZON и сохраняет в product_photos."""
    if not _cid() or not _key():
        logger.warning("[OzonPhotoSync] нет OZON_CLIENT_ID / OZON_API_KEY")
        return 0

    offer_ids = _collect_articles(get_db_func)
    if not offer_ids:
        logger.info("[OzonPhotoSync] нет артикулов в БД")
        return 0

    logger.info("[OzonPhotoSync] запрашиваем фото для %d артикулов", len(offer_ids))
    images = _get_images_by_offer_ids(offer_ids)
    if not images:
        logger.warning("[OzonPhotoSync] API не вернул ни одного фото")
        return 0

    saved = 0
    try:
        db = next(get_db_func())
        try:
            with db.bind.connect() as conn:
                for article, url in images.items():
                    conn.execute(text("""
                        INSERT INTO product_photos (supplier_article, photo_url, source, updated_at)
                        VALUES (:art, :url, 'ozon', now())
                        ON CONFLICT (supplier_article) DO UPDATE
                        SET photo_url  = EXCLUDED.photo_url,
                            source     = 'ozon',
                            updated_at = now()
                    """), {"art": article, "url": url})
                conn.commit()
                saved = len(images)
        finally:
            db.close()
    except Exception as e:
        logger.error("[OzonPhotoSync] ошибка записи в БД: %s", e)

    logger.info("[OzonPhotoSync] сохранено %d фото", saved)
    return saved


async def start_ozon_photo_sync_worker(get_db_func):
    """Фоновый воркер: первый прогон через 90с после старта, потом раз в 24ч."""
    await asyncio.sleep(90)
    while True:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: sync_ozon_photos(get_db_func)
            )
        except Exception as e:
            logger.error("[OzonPhotoSync] воркер: %s", e)
        await asyncio.sleep(SYNC_INTERVAL)
