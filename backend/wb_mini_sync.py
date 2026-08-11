"""Лёгкий воркер: частое обновление отзывов и возвратов WB (каждые 5 мин)."""
import asyncio
import hashlib
import io
import os
import time
from datetime import datetime, timedelta

import requests
from PIL import Image
from sqlalchemy import text

from backend.database import db_session
from backend import broadcaster, wb_client

WB_PHOTO_DIR = os.getenv("WB_PHOTO_DIR", "/root/cx-dashboard/static/wb_claims").strip()
WB_PHOTO_RETENTION_DAYS = int(os.getenv("WB_PHOTO_RETENTION_DAYS", "90"))


def _key() -> str:
    return wb_client.api_key()


def _photo_url(p) -> str:
    if isinstance(p, str):
        u = p
    elif isinstance(p, dict):
        u = p.get("fullSize") or p.get("url") or p.get("miniSize") or ""
    else:
        return ""
    if u and not u.startswith("http"):
        u = "https:" + u
    return u


def _is_cached_photos(photos: str | None) -> bool:
    """Уже локальные или S3-превью — не перезаписываем сырым CDN."""
    if not photos:
        return False
    return ("/static/wb_claims/" in photos) or ("storage.yandexcloud.net" in photos)


def _cache_wb_photo(cdn_url: str) -> str:
    """Скачивает фото WB, сжимает, кладёт в /static/wb_claims/. Возвращает превью|оригинал."""
    if not cdn_url:
        return ""
    if cdn_url.startswith("//"):
        cdn_url = "https:" + cdn_url
    if not cdn_url.startswith("http"):
        return cdn_url
    try:
        os.makedirs(WB_PHOTO_DIR, exist_ok=True)
        digest = hashlib.sha1(cdn_url.encode("utf-8")).hexdigest()[:20]
        filename = f"{digest}.jpg"
        local_path = os.path.join(WB_PHOTO_DIR, filename)
        local_url = f"/static/wb_claims/{filename}"
        if os.path.isfile(local_path) and os.path.getsize(local_path) > 1000:
            return f"{local_url}|{cdn_url}"

        r = requests.get(cdn_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1000, 1000))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", optimize=True, quality=85)
        with open(local_path, "wb") as f:
            f.write(buf.getvalue())
        return f"{local_url}|{cdn_url}"
    except Exception as e:
        print(f"[MiniSync] photo cache fail: {e}")
        return cdn_url


def cleanup_old_wb_photos(days: int | None = None) -> int:
    days = WB_PHOTO_RETENTION_DAYS if days is None else days
    if days <= 0 or not os.path.isdir(WB_PHOTO_DIR):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(WB_PHOTO_DIR):
        path = os.path.join(WB_PHOTO_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    if removed:
        print(f"[MiniSync] cleanup wb photos: removed {removed} (> {days}d)")
    return removed


def _build_claim_photos(item: dict, existing_photos: str | None) -> str | None:
    """Не затираем уже закэшированные фото; новые — качаем на диск."""
    if _is_cached_photos(existing_photos):
        return existing_photos
    raw = [_photo_url(p) for p in (item.get("photos") or [])]
    urls = [u for u in raw if u]
    if not urls:
        return existing_photos or None
    cached = [_cache_wb_photo(u) for u in urls]
    return " ".join(cached) or None


def sync_recent_feedbacks(db) -> int:
    """Забирает последние 100 активных отзывов и обновляет wb_feedbacks."""
    key = _key()
    if not key:
        return 0
    try:
        feedbacks = wb_client.list_feedbacks(is_answered=False, take=100, skip=0, key=key)
    except Exception as e:
        print(f"[MiniSync] feedbacks API error: {e}")
        return 0

    count = 0
    for fb in feedbacks:
        fb_id = str(fb.get("id", "")).strip()
        if not fb_id:
            continue
        details  = fb.get("productDetails") or {}
        nm_id    = str(details.get("nmId") or "").strip() or None
        sku      = str(details.get("supplierArticle") or "").strip() or "Без артикула"
        photos   = " ".join(u for u in (_photo_url(p) for p in (fb.get("photoLinks") or [])) if u) or None
        ans_obj  = fb.get("answer") or {}
        shk_id   = fb.get("lastOrderShkId") or None
        try:
            db.execute(text("""
                INSERT INTO wb_feedbacks
                    (id, supplier_article, nm_id, created_date, valuation, text, answer_text,
                     photos, state, pros, cons, order_status, imt_id, last_order_shk_id, last_order_created_at)
                VALUES (:id, :sku, :nm_id, :created, :val, :text, :ans,
                        :photos, 'active', :pros, :cons, :ord_status, :imt_id, :shk_id, :shk_dt)
                ON CONFLICT (id) DO UPDATE SET
                    valuation             = EXCLUDED.valuation,
                    text                  = EXCLUDED.text,
                    answer_text           = COALESCE(EXCLUDED.answer_text, wb_feedbacks.answer_text),
                    photos                = COALESCE(EXCLUDED.photos, wb_feedbacks.photos),
                    nm_id                 = COALESCE(EXCLUDED.nm_id, wb_feedbacks.nm_id),
                    pros                  = COALESCE(EXCLUDED.pros, wb_feedbacks.pros),
                    cons                  = COALESCE(EXCLUDED.cons, wb_feedbacks.cons),
                    order_status          = COALESCE(EXCLUDED.order_status, wb_feedbacks.order_status),
                    imt_id                = COALESCE(EXCLUDED.imt_id, wb_feedbacks.imt_id),
                    last_order_shk_id     = COALESCE(EXCLUDED.last_order_shk_id, wb_feedbacks.last_order_shk_id),
                    last_order_created_at = COALESCE(EXCLUDED.last_order_created_at, wb_feedbacks.last_order_created_at),
                    state                 = 'active'
            """), {
                "id":         fb_id,
                "sku":        sku,
                "nm_id":      nm_id,
                "created":    fb.get("createdDate"),
                "val":        fb.get("productValuation"),
                "text":       fb.get("text", ""),
                "ans":        ans_obj.get("text") if ans_obj else None,
                "photos":     photos,
                "pros":       fb.get("pros") or None,
                "cons":       fb.get("cons") or None,
                "ord_status": fb.get("orderStatus") or None,
                "imt_id":     details.get("imtId") or None,
                "shk_id":     shk_id,
                "shk_dt":     fb.get("lastOrderCreatedAt") or None,
            })
            count += 1
        except Exception as e:
            print(f"[MiniSync] feedback insert error: {e}")

    if count:
        db.commit()
        print(f"[MiniSync] feedbacks synced: {count}")
    return count


def sync_recent_questions(db) -> int:
    """Забирает последние 100 неотвеченных вопросов и обновляет wb_questions.

    Раньше вопросы тянул только ночной worker.py (раз в 2ч/сутки) — теперь это
    единственный источник для вопросов WB, обновляется каждые 5 минут вместе
    с отзывами и претензиями (см. sync_recent_feedbacks выше)."""
    key = _key()
    if not key:
        return 0
    try:
        questions = wb_client.list_questions(is_answered=False, take=100, skip=0, key=key)
    except Exception as e:
        print(f"[MiniSync] questions API error: {e}")
        return 0

    count = 0
    for q in questions:
        qid = str(q.get("id", "")).strip()
        if not qid:
            continue
        q_text = (q.get("text") or "").strip()
        if not q_text:
            continue
        sku = (q.get("productDetails") or {}).get("supplierArticle") or "Без артикула"
        ans_obj = q.get("answer") or {}
        try:
            db.execute(text("""
                INSERT INTO wb_questions (id, supplier_article, created_date, text, answer_text, state)
                VALUES (:id, :sku, :created, :text, :ans, 'active')
                ON CONFLICT (id) DO UPDATE SET
                    text        = EXCLUDED.text,
                    answer_text = COALESCE(EXCLUDED.answer_text, wb_questions.answer_text),
                    state       = 'active'
            """), {
                "id":      qid,
                "sku":     sku,
                "created": q.get("createdDate"),
                "text":    q_text,
                "ans":     ans_obj.get("text") if ans_obj else None,
            })
            count += 1
        except Exception as e:
            print(f"[MiniSync] question insert error: {e}")

    if count:
        db.commit()
        print(f"[MiniSync] questions synced: {count}")
    return count


def sync_all_answered_questions(db) -> int:
    """
    Тянет отвеченные вопросы WB (isAnswered=true) с пагинацией (до 14 дней назад)
    и сохраняет их как state='archive'. Запускается раз в день — аналог
    sync_all_answered_wb, но для вопросов.
    """
    key = _key()
    if not key:
        return 0

    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    total = 0

    for skip in range(0, 10001, 1000):
        try:
            questions = wb_client.list_questions(is_answered=True, take=1000, skip=skip, key=key)
        except Exception as e:
            print(f"[MiniSync] sync_all_answered_questions error skip={skip}: {e}")
            break

        if not questions:
            break

        try:
            for q in questions:
                qid = str(q.get("id", "")).strip()
                if not qid:
                    continue
                q_text = (q.get("text") or "").strip()
                sku = (q.get("productDetails") or {}).get("supplierArticle") or "Без артикула"
                ans_obj = q.get("answer") or {}
                db.execute(text("""
                    INSERT INTO wb_questions (id, supplier_article, created_date, text, answer_text, state)
                    VALUES (:id, :sku, :created, :text, :ans, 'archive')
                    ON CONFLICT (id) DO UPDATE SET
                        answer_text = COALESCE(EXCLUDED.answer_text, wb_questions.answer_text),
                        state       = 'archive'
                """), {
                    "id":      qid,
                    "sku":     sku,
                    "created": q.get("createdDate"),
                    "text":    q_text,
                    "ans":     ans_obj.get("text") if ans_obj else None,
                })
                total += 1
            db.commit()
        except Exception as e:
            print(f"[MiniSync] sync_all_answered_questions DB error: {e}")
            break

        dates = [q.get("createdDate", "")[:10] for q in questions if q.get("createdDate")]
        if dates and min(dates) < cutoff:
            break
        if len(questions) < 1000:
            break

    print(f"[MiniSync] sync_all_answered_questions: {total} processed")
    return total


def sync_answered_feedbacks_for_active_requests(db) -> int:
    """Синхронизирует отвеченные отзывы по nm_id активных заявок (чтобы найти отзыв конкретного клиента)."""
    key = _key()
    if not key:
        return 0
    from sqlalchemy import text
    try:
        nm_ids = db.execute(text("""
            SELECT DISTINCT wb_nm_id::text as nm_id FROM reshipment_requests
            WHERE wb_nm_id IS NOT NULL AND status NOT IN ('closed','rejected')
        """)).scalars().all()
    except Exception as e:
        print(f"[MiniSync] answered feedbacks nm_ids error: {e}")
        return 0

    total = 0
    for nm_id in nm_ids:
        try:
            feedbacks = wb_client.list_feedbacks(is_answered=True, take=50, skip=0, nm_id=int(nm_id), key=key, timeout=20)
        except Exception as e:
            print(f"[MiniSync] answered feedbacks API error nm={nm_id}: {e}")
            continue

        for fb in feedbacks:
            fb_id = str(fb.get("id", "")).strip()
            if not fb_id:
                continue
            details = fb.get("productDetails") or {}
            photos = " ".join(u for u in (_photo_url(p) for p in (fb.get("photoLinks") or [])) if u) or None
            ans_obj = fb.get("answer") or {}
            shk_id  = fb.get("lastOrderShkId") or None
            try:
                db.execute(text("""
                    INSERT INTO wb_feedbacks
                        (id, supplier_article, nm_id, created_date, valuation, text, answer_text,
                         photos, state, pros, cons, order_status, imt_id, last_order_shk_id, last_order_created_at)
                    VALUES (:id, :sku, :nm_id, :created, :val, :text, :ans,
                            :photos, 'answered', :pros, :cons, :ord_status, :imt_id, :shk_id, :shk_dt)
                    ON CONFLICT (id) DO UPDATE SET
                        valuation             = EXCLUDED.valuation,
                        text                  = EXCLUDED.text,
                        answer_text           = COALESCE(EXCLUDED.answer_text, wb_feedbacks.answer_text),
                        photos                = COALESCE(EXCLUDED.photos, wb_feedbacks.photos),
                        pros                  = COALESCE(EXCLUDED.pros, wb_feedbacks.pros),
                        cons                  = COALESCE(EXCLUDED.cons, wb_feedbacks.cons),
                        order_status          = COALESCE(EXCLUDED.order_status, wb_feedbacks.order_status),
                        imt_id                = COALESCE(EXCLUDED.imt_id, wb_feedbacks.imt_id),
                        last_order_shk_id     = COALESCE(EXCLUDED.last_order_shk_id, wb_feedbacks.last_order_shk_id),
                        last_order_created_at = COALESCE(EXCLUDED.last_order_created_at, wb_feedbacks.last_order_created_at),
                        state                 = 'answered'
                """), {
                    "id":         fb_id,
                    "sku":        str(details.get("supplierArticle") or ""),
                    "nm_id":      str(details.get("nmId") or nm_id),
                    "created":    fb.get("createdDate"),
                    "val":        fb.get("productValuation"),
                    "text":       fb.get("text", ""),
                    "ans":        ans_obj.get("text") if ans_obj else None,
                    "photos":     photos,
                    "pros":       fb.get("pros") or None,
                    "cons":       fb.get("cons") or None,
                    "ord_status": fb.get("orderStatus") or None,
                    "imt_id":     details.get("imtId") or None,
                    "shk_id":     shk_id,
                    "shk_dt":     fb.get("lastOrderCreatedAt") or None,
                })
                total += 1
            except Exception as e:
                print(f"[MiniSync] answered feedback insert error: {e}")

        if feedbacks:
            db.commit()

    if total:
        print(f"[MiniSync] answered feedbacks synced: {total}")
    return total


def _map_claim_status(raw_st: str, st_ex: str = "", wb_comment: str = "") -> str:
    # Актуальные коды returns-api: 0=на рассмотрении, 1=отказ, 2=одобрено.
    # Старый код "3" оставляем как отказ на случай устаревших ответов.
    # status_ex: 1=отказ, 2/5/10=варианты одобрения (см. живые ответы API).
    # «Закрыта» — локальный ярлык (заявка пропала из окна API), не статус WB.
    status_map = {
        "0": "На рассмотрении",
        "1": "Отказ",
        "2": "Одобрено",
        "3": "Отказ",
    }
    raw = str(raw_st or "").strip()
    if raw in ("1", "1.0", "3"):
        return "Отказ"
    if raw in ("2", "2.0"):
        return "Одобрено"

    el = f"{st_ex or ''} {wb_comment or ''} {raw}".lower()
    ex = str(st_ex or "").strip()

    # Отмена клиентом / площадкой
    if (
        raw in ("Отменена", "Отменено", "Cancelled", "Canceled")
        or "отмен" in el
        or "cancel" in el
    ):
        # Не путать отказ (отклонено) с отменой заявки
        if "отклон" not in el and "отказ" not in el:
            return "Отменена"

    # Числовой status_ex / текст важнее «залипшего» «На рассмотрении» / кода 0.
    if ex in ("1", "1.0") or "отклон" in el or "отказ" in el:
        return "Отказ"
    if ex in ("2", "2.0", "5", "10") or "одобрен" in el or "придут деньги" in el:
        return "Одобрено"

    if raw in ("Одобрено", "Отказ", "Закрыта", "Отменена"):
        return raw
    if status_map.get(raw) == "На рассмотрении" or raw in ("На рассмотрении", "Открыта", ""):
        return "На рассмотрении"
    return status_map.get(raw, "На рассмотрении")


def _status_ex_label(raw_ex, wb_comment: str = "") -> str:
    """Читаемая расшифровка: числовой status_ex + комментарий WB."""
    ex = str(raw_ex if raw_ex is not None else "").strip()
    comment = (wb_comment or "").strip()
    # Если пришёл только код — оставляем код; комментарий важнее для UI
    if comment:
        if ex and ex not in comment and not ex.isdigit():
            return f"{ex} | {comment}"[:500]
        return comment[:500]
    return ex[:500]


def _upsert_claim_row(db, item: dict, *, is_archive: bool) -> bool:
    srid = str(item.get("srid", "")).strip()
    if not srid:
        return False
    wb_comment = str(item.get("wb_comment") or item.get("wbComment") or "")
    st_ex_raw = item.get("status_description") or item.get("status_ex") or ""
    st_ex = _status_ex_label(st_ex_raw, wb_comment)
    status = _map_claim_status(item.get("status", ""), str(st_ex_raw), wb_comment)
    nm_id = str(item.get("nm_id") or item.get("nmId") or "").strip() or None
    article = str(item.get("supplier_article") or item.get("supplierArticle") or item.get("saName") or "")
    existing_photos = db.execute(
        text("SELECT photos FROM wb_claims WHERE srid = :srid"),
        {"srid": srid},
    ).scalar()
    photos = _build_claim_photos(item, existing_photos)
    income = str(item.get("income_id") or item.get("incomeId") or "0")
    claim_id = str(item.get("id") or item.get("claim_id") or "")
    try:
        db.execute(text("""
            INSERT INTO wb_claims
                (srid, claim_id, created_dt, supplier_article, nm_id, user_comment,
                 status, status_ex, claim_type, income_id, is_archive, last_sync, photos,
                 order_date, claim_date)
            VALUES
                (:srid, :claim_id, :created_dt, :article, :nm_id, :comment,
                 :status, :status_ex, :claim_type, :income_id, :is_archive, NOW(), :photos,
                 :order_date, :claim_date)
            ON CONFLICT (srid) DO UPDATE SET
                claim_id     = COALESCE(NULLIF(EXCLUDED.claim_id, ''), wb_claims.claim_id),
                status       = EXCLUDED.status,
                status_ex    = CASE
                                 WHEN EXCLUDED.status_ex IS NOT NULL AND EXCLUDED.status_ex <> ''
                                 THEN EXCLUDED.status_ex
                                 ELSE wb_claims.status_ex
                               END,
                user_comment = COALESCE(EXCLUDED.user_comment, wb_claims.user_comment),
                photos       = CASE
                                 WHEN wb_claims.photos IS NOT NULL AND wb_claims.photos <> ''
                                      AND (wb_claims.photos LIKE '%/static/wb_claims/%'
                                           OR wb_claims.photos LIKE '%storage.yandexcloud.net%')
                                 THEN wb_claims.photos
                                 WHEN EXCLUDED.photos IS NOT NULL AND EXCLUDED.photos <> ''
                                 THEN EXCLUDED.photos
                                 ELSE wb_claims.photos
                               END,
                nm_id        = COALESCE(EXCLUDED.nm_id, wb_claims.nm_id),
                income_id    = COALESCE(EXCLUDED.income_id, wb_claims.income_id),
                is_archive   = EXCLUDED.is_archive,
                last_sync    = NOW()
        """), {
            "srid":       srid,
            "claim_id":   claim_id,
            "created_dt": item.get("dt") or item.get("createdDate"),
            "article":    article,
            "nm_id":      nm_id,
            "comment":    item.get("user_comment") or item.get("userComment") or "",
            "status":     status,
            "status_ex":  st_ex,
            "claim_type": str(item.get("claim_type") or item.get("claimType") or ""),
            "income_id":  income,
            "is_archive": is_archive,
            "photos":     photos,
            "order_date": item.get("order_dt") or item.get("orderDate"),
            "claim_date": item.get("dt") or item.get("claimDate"),
        })
        return True
    except Exception as e:
        print(f"[MiniSync] claim insert error: {e}")
        return False


def _fetch_claims_page(
    *,
    is_archive: bool,
    limit: int = 200,
    offset: int = 0,
    claim_id=None,
    nm_id=None,
) -> list:
    r = wb_client.get_claims_page_raw(is_archive=is_archive, limit=limit, offset=offset, claim_id=claim_id, nm_id=nm_id)
    if r.status_code == 429:
        import time as _time
        _time.sleep(25)
        r = wb_client.get_claims_page_raw(is_archive=is_archive, limit=limit, offset=offset, claim_id=claim_id, nm_id=nm_id)
    r.raise_for_status()
    return (r.json() or {}).get("claims") or []


def _find_claim_in_api(*, claim_id: str = "", srid: str = "", nm_id: str = "") -> tuple[dict | None, bool]:
    """
    Ищет заявку в Returns API. id-фильтр часто пустой для старых заявок —
    тогда берём по nm_id и матчим claim_id/srid.
    Returns: (item, is_archive) или (None, False).
    """
    cid = str(claim_id or "").strip()
    rid = str(srid or "").strip()
    nm = str(nm_id or "").strip()

    def _match(items: list) -> dict | None:
        for it in items:
            iid = str(it.get("id") or it.get("claim_id") or "").strip()
            isrid = str(it.get("srid") or "").strip()
            if cid and iid == cid:
                return it
            if rid and isrid == rid:
                return it
        return None

    # 1) Прямой id (active + archive)
    if cid:
        for is_arch in (True, False):
            try:
                items = _fetch_claims_page(is_archive=is_arch, limit=1, offset=0, claim_id=cid)
            except Exception:
                items = []
            hit = _match(items)
            if hit:
                return hit, is_arch

    # 2) По nm_id — надёжнее для заявок в окне ~14 дней
    if nm:
        for is_arch in (True, False):
            try:
                items = _fetch_claims_page(is_archive=is_arch, limit=50, offset=0, nm_id=nm)
            except Exception:
                items = []
            hit = _match(items)
            if hit:
                return hit, is_arch
            # Если искали по id/srid и не нашли — не берём «чужую» заявку того же nm
            if not cid and not rid and items:
                return items[0], is_arch

    return None, False


def _chat_claim_rid_sql() -> str:
    """rid возврата для чата: order_rid или goodCard.rid из сообщений."""
    return """
        COALESCE(
          NULLIF(c.order_rid, ''),
          (SELECT m.attachments->'goodCard'->>'rid'
           FROM wb_chat_messages m
           WHERE m.chat_id = c.chat_id
             AND coalesce(m.attachments->'goodCard'->>'rid','') <> ''
           ORDER BY m.add_ts ASC NULLS LAST LIMIT 1)
        )
    """


def _normalize_local_claim_statuses(db) -> int:
    """Чинит залипшие строки по уже известному status_ex / числовому status."""
    fixes = [
        ("""
            UPDATE wb_claims SET status = 'Отказ', last_sync = NOW()
            WHERE status IN ('На рассмотрении', '0', '0.0', 'Открыта', '1', '1.0', '3')
              AND (
                trim(coalesce(status_ex, '')) IN ('1', '1.0', '3')
                OR lower(coalesce(status_ex, '')) LIKE '%%отказ%%'
                OR lower(coalesce(status_ex, '')) LIKE '%%отклон%%'
              )
        """, "ex→Отказ"),
        ("""
            UPDATE wb_claims SET status = 'Одобрено', last_sync = NOW()
            WHERE status IN ('На рассмотрении', '0', '0.0', 'Открыта', '2', '2.0')
              AND (
                trim(coalesce(status_ex, '')) IN ('2', '2.0', '5', '10')
                OR lower(coalesce(status_ex, '')) LIKE '%%одобрен%%'
                OR lower(coalesce(status_ex, '')) LIKE '%%придут деньги%%'
              )
        """, "ex→Одобрено"),
        ("""
            UPDATE wb_claims SET status = 'Отказ', last_sync = NOW()
            WHERE status IN ('1', '1.0', '3')
        """, "code→Отказ"),
        ("""
            UPDATE wb_claims SET status = 'Одобрено', last_sync = NOW()
            WHERE status IN ('2', '2.0')
        """, "code→Одобрено"),
        ("""
            UPDATE wb_claims SET status = 'На рассмотрении', last_sync = NOW()
            WHERE status IN ('0', '0.0', 'Открыта')
        """, "code→рассмотрении"),
    ]
    total = 0
    for sql, _label in fixes:
        res = db.execute(text(sql))
        total += res.rowcount or 0
    return total


def refresh_stale_claim_statuses(db, *, limit: int = 40) -> int:
    """
    Точечно обновляет «открытые» заявки по claim_id / nm_id (active + archive).
    Приоритет: привязанные к чатам «ждёт ответа», затем остальные.
    Лимит умеренный: Returns API легко отдаёт 429.
    """
    fixed = _normalize_local_claim_statuses(db)
    if fixed:
        db.commit()
        print(f"[MiniSync] claim statuses normalized locally: {fixed}")

    rid_expr = _chat_claim_rid_sql()
    rows = db.execute(text(f"""
        WITH waiting_rids AS (
          SELECT DISTINCT {rid_expr} AS rid
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
            AND c.needs_answer = true
        ),
        pending AS (
          SELECT srid, claim_id, nm_id, coalesce(claim_date, created_dt) AS dt,
                 CASE WHEN EXISTS (SELECT 1 FROM waiting_rids w WHERE w.rid = srid)
                      THEN 0 ELSE 1 END AS prio
          FROM wb_claims
          WHERE status IN ('На рассмотрении', '0', '0.0', 'Открыта')
            AND (
              coalesce(nullif(trim(claim_id), ''), '') <> ''
              OR coalesce(nullif(trim(nm_id), ''), '') <> ''
            )
            -- Старше окна WB (~14д) API уже не отдаёт — не жжём лимит
            AND coalesce(claim_date, created_dt, last_sync) > NOW() - INTERVAL '20 days'
        )
        SELECT srid, claim_id, nm_id FROM pending
        ORDER BY prio ASC, dt DESC NULLS LAST
        LIMIT :lim
    """), {"lim": limit}).fetchall()
    if not rows:
        return 0

    import time as _time
    updated = 0
    for srid, claim_id, nm_id in rows:
        cid = str(claim_id or "").strip()
        try:
            item, is_arch = _find_claim_in_api(claim_id=cid, srid=srid, nm_id=nm_id)
        except Exception as e:
            print(f"[MiniSync] claim id={cid} fetch error: {e}")
            if "429" in str(e):
                db.commit()
                print(f"[MiniSync] stale claims refreshed: {updated}/{len(rows)} (stopped on 429)")
                return updated
            continue
        if item:
            if _upsert_claim_row(db, item, is_archive=is_arch):
                updated += 1
        else:
            # Ушла из окна API: в архиве, но не «на рассмотрении»
            db.execute(text("""
                UPDATE wb_claims
                SET is_archive = true,
                    status = CASE
                      WHEN status IN ('На рассмотрении', '0', '0.0', 'Открыта')
                      THEN 'Закрыта'
                      ELSE status
                    END,
                    status_ex = CASE
                      WHEN status IN ('На рассмотрении', '0', '0.0', 'Открыта')
                           AND coalesce(nullif(trim(status_ex), ''), '') = ''
                      THEN 'Статус не получен из WB API (заявка вне окна выдачи)'
                      ELSE status_ex
                    END,
                    last_sync = NOW()
                WHERE srid = :srid
            """), {"srid": srid})
        _time.sleep(0.35)
    if updated or rows:
        db.commit()
        print(f"[MiniSync] stale claims refreshed: {updated}/{len(rows)}")
    return updated


def archive_waiting_chats_with_approved_claims(db) -> int:
    """
    Чаты «ждёт ответа» с возвратом в статусе «Одобрено» → в архив
    (cx_dismissed_items) и needs_answer=false.
    """
    rid_expr = _chat_claim_rid_sql()
    targets = db.execute(text(f"""
        WITH waiting AS (
          SELECT c.chat_id, {rid_expr} AS rid
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
            AND c.needs_answer = true
            AND c.last_sender = 'client'
        )
        SELECT w.chat_id, w.rid
        FROM waiting w
        JOIN wb_claims cl ON cl.srid = w.rid
        WHERE w.rid IS NOT NULL AND w.rid <> '' AND cl.status = 'Одобрено'
    """)).mappings().fetchall()
    if not targets:
        return 0

    note = "Авто: возврат Одобрено"
    user = "auto_approved_return"
    closed = 0
    for t in targets:
        db.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :cid, 'wb', :u, :n)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
        """), {"cid": t["chat_id"], "u": user, "n": note})
        db.execute(text("""
            UPDATE wb_chats SET needs_answer = false, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": t["chat_id"]})
        closed += 1
    db.commit()
    print(f"[MiniSync] archived waiting chats (approved claim): {closed}")
    return closed


# Ротация глубокого архива между циклами (offset страниц × 200).
_archive_page_cursor = 0


def sync_recent_claims(db) -> int:
    """
    Обновляет wb_claims из WB Returns API.
    Активные + несколько страниц архива (с ротацией глубже) + точечный
    рефреш «На рассмотрении» для чатов. Иначе статус «Отказ/Одобрено»
    не доезжает: заявка уходит из active, у нас остаётся «На рассмотрении».
    """
    global _archive_page_cursor
    key = _key()
    if not key:
        return 0

    count = 0
    active_claims: list = []
    archive_claims: list = []
    try:
        for off in (0, 200):
            batch = _fetch_claims_page(is_archive=False, limit=200, offset=off)
            active_claims.extend(batch)
            if len(batch) < 200:
                break

        # Всегда свежий хвост + 2 страницы глубже по курсору (до ~offset 4000).
        deep_base = (_archive_page_cursor % 20) * 200  # 0..3800
        _archive_page_cursor += 1
        archive_offsets = [0, 200, deep_base, deep_base + 200]
        # Уникальные, сохраняя порядок
        seen_off = set()
        ordered_offs = []
        for off in archive_offsets:
            if off not in seen_off:
                seen_off.add(off)
                ordered_offs.append(off)

        for off in ordered_offs:
            batch = _fetch_claims_page(is_archive=True, limit=200, offset=off)
            archive_claims.extend(batch)
            if len(batch) < 200 and off > 0:
                # Курсор ушёл за конец архива — сброс
                if off >= deep_base:
                    _archive_page_cursor = 0
    except Exception as e:
        print(f"[MiniSync] claims API error: {e}")
        return 0

    # Дедуп по srid (глубокие страницы могут пересечься со свежими)
    by_srid: dict = {}
    for item in active_claims:
        srid = str(item.get("srid", "")).strip()
        if srid:
            by_srid[srid] = (item, False)
    for item in archive_claims:
        srid = str(item.get("srid", "")).strip()
        if srid:
            by_srid[srid] = (item, True)

    for item, is_arch in by_srid.values():
        if _upsert_claim_row(db, item, is_archive=is_arch):
            count += 1

    if count:
        db.commit()
        print(
            f"[MiniSync] claims synced: {count} "
            f"(active={len(active_claims)} archive_raw={len(archive_claims)} "
            f"unique={len(by_srid)} deep_off={deep_base})"
        )

    archive_by_srid = {
        str(i.get("srid", "")).strip(): i for i in archive_claims if i.get("srid")
    }
    active_srids = [str(i.get("srid", "")).strip() for i in active_claims if i.get("srid")]
    if active_srids:
        from sqlalchemy import bindparam
        missing = db.execute(
            text("""
                SELECT srid FROM wb_claims
                WHERE is_archive = false
                  AND srid NOT IN :srids
            """).bindparams(bindparam("srids", expanding=True)),
            {"srids": active_srids},
        ).fetchall()
        for (srid,) in missing:
            item = archive_by_srid.get(srid)
            if item:
                _upsert_claim_row(db, item, is_archive=True)
            else:
                # Ушла из active, но нет в просканированных страницах архива —
                # не оставляем «На рассмотрении» (это и есть залипание в чатах).
                row = db.execute(text(
                    "SELECT claim_id, nm_id FROM wb_claims WHERE srid = :srid"
                ), {"srid": srid}).mappings().first()
                hit = None
                is_arch = True
                if row:
                    hit, is_arch = _find_claim_in_api(
                        claim_id=row.get("claim_id") or "",
                        srid=srid,
                        nm_id=row.get("nm_id") or "",
                    )
                if hit:
                    _upsert_claim_row(db, hit, is_archive=is_arch)
                else:
                    db.execute(text("""
                        UPDATE wb_claims
                        SET is_archive = true,
                            status = CASE
                              WHEN status IN ('На рассмотрении', '0', '0.0', 'Открыта')
                              THEN 'Закрыта'
                              ELSE status
                            END,
                            status_ex = CASE
                              WHEN status IN ('На рассмотрении', '0', '0.0', 'Открыта')
                                   AND coalesce(nullif(trim(status_ex), ''), '') = ''
                              THEN 'Статус не получен из WB API (заявка вне окна выдачи)'
                              ELSE status_ex
                            END,
                            last_sync = NOW()
                        WHERE srid = :srid AND is_archive = false
                    """), {"srid": srid})
        db.commit()
        if missing:
            print(f"[MiniSync] claims left active list: {len(missing)}")

    try:
        refresh_stale_claim_statuses(db)
    except Exception as e:
        print(f"[MiniSync] refresh_stale_claim_statuses: {e}")

    try:
        archive_waiting_chats_with_approved_claims(db)
    except Exception as e:
        print(f"[MiniSync] archive_waiting_chats_with_approved_claims: {e}")

    return count


def scan_unlinked_tokens(db) -> int:
    """Сканирует список WB-чатов и линкует незалинкованные токены по lastMessage, истории и nm_id."""
    key = _key()
    if not key:
        return 0

    from sqlalchemy import text
    import re
    TOKEN_PAT = re.compile(
        r"box\.vidovito\.com(?:/reshipment/form)?\?t=([\w\-]+)",
        re.IGNORECASE,
    )

    try:
        # Все незалинкованные токены (форма заполнена, но chatID ещё нет)
        rows = db.execute(text("""
            SELECT t.token, r.id as req_id, r.wb_nm_id
            FROM reshipment_chat_tokens t
            LEFT JOIN reshipment_requests r ON r.chat_token = t.token
            WHERE t.chat_id IS NULL OR t.chat_id = ''
        """)).mappings().fetchall()
        if not rows:
            return 0
        unlinked = {r["token"]: r for r in rows}

        # 0) Ищем токены прямо в сохранённой истории сообщений (надёжнее lastMessage)
        for tok in list(unlinked.keys()):
            hit = db.execute(text("""
                SELECT chat_id FROM wb_chat_messages
                WHERE text ILIKE :pat
                ORDER BY add_ts DESC LIMIT 1
            """), {"pat": f"%t={tok}%"}).mappings().first()
            if not hit:
                continue
            chat_id = str(hit["chat_id"])
            try:
                db.execute(text(
                    "UPDATE reshipment_chat_tokens SET chat_id=:cid, mp=COALESCE(mp, 'wb') WHERE token=:token AND (chat_id IS NULL OR chat_id='')"
                ), {"cid": chat_id, "token": tok})
                req_id = unlinked[tok].get("req_id")
                if req_id:
                    chat_row = db.execute(text(
                        "SELECT nm_id, order_rid, reply_sign FROM wb_chats WHERE chat_id=:cid"
                    ), {"cid": chat_id}).mappings().first()
                    nm = (chat_row or {}).get("nm_id")
                    rid = (chat_row or {}).get("order_rid")
                    sign = (chat_row or {}).get("reply_sign")
                    db.execute(text("""
                        UPDATE reshipment_requests SET
                            wb_chat_id = :cid,
                            wb_nm_id = COALESCE(wb_nm_id, :nm),
                            wb_reply_sign = COALESCE(wb_reply_sign, :sign),
                            matched_srid = COALESCE(matched_srid, :rid),
                            marketplace = COALESCE(NULLIF(marketplace, ''), 'wb')
                        WHERE id = :id AND (wb_chat_id IS NULL OR wb_chat_id = '')
                    """), {"cid": chat_id, "nm": nm, "sign": sign, "rid": rid, "id": req_id})
                db.commit()
                print(f"[MiniSync] Token linked via messages: {tok} → chat {chat_id}")
                del unlinked[tok]
            except Exception as e:
                print(f"[MiniSync] token link via messages error: {e}")

        if not unlinked:
            return len(rows) - len(unlinked)

        # Список WB чатов
        chats = wb_client.get_chats(key=key, timeout=15)
    except Exception as e:
        print(f"[MiniSync] scan_tokens error: {e}")
        return 0

    linked = len(rows) - len(unlinked)
    for chat in chats:
        chat_id = str(chat.get("chatID", ""))
        if not chat_id:
            continue

        # Метод 1: ищем токен в последнем сообщении
        last_msg = chat.get("lastMessage") or {}
        last_text = last_msg.get("text") or ""
        m = TOKEN_PAT.search(last_text)
        token_found = m.group(1) if m else None

        # Метод 2: совпадение по nm_id, если запрос ещё без chatID
        if not token_found:
            nm_id = str((chat.get("goodCard") or {}).get("nmID") or "")
            if nm_id:
                for tok, row in unlinked.items():
                    if str(row.get("wb_nm_id") or "") == nm_id:
                        token_found = tok
                        break

        if not token_found or token_found not in unlinked:
            continue

        try:
            nm_from_chat = str((chat.get("goodCard") or {}).get("nmID") or "")
            db.execute(text(
                "UPDATE reshipment_chat_tokens SET chat_id=:cid, mp=COALESCE(mp, 'wb') WHERE token=:token AND (chat_id IS NULL OR chat_id='')"
            ), {"cid": chat_id, "token": token_found})
            req_id = unlinked[token_found].get("req_id")
            if req_id:
                db.execute(text(
                    "UPDATE reshipment_requests SET wb_chat_id=:cid, wb_nm_id=COALESCE(wb_nm_id, :nm), marketplace=COALESCE(NULLIF(marketplace,''),'wb') WHERE id=:id AND (wb_chat_id IS NULL OR wb_chat_id='')"
                ), {"cid": chat_id, "nm": int(nm_from_chat) if nm_from_chat else None, "id": req_id})
            db.commit()
            linked += 1
            print(f"[MiniSync] Token linked: {token_found} → chat {chat_id}")
            del unlinked[token_found]
        except Exception as e:
            print(f"[MiniSync] token link error: {e}")

    return linked


def sync_reshipment_review_snapshots(db) -> int:
    """Фиксирует снепшоты отзыва клиента ДО отправки (before) и ПОСЛЕ получения (after).

    Связь: reshipment_requests.matched_srid → wb_orders.shk_id → wb_feedbacks.last_order_shk_id.
    Снепшот 'before': берётся пока заявка ещё не отправлена (shipped_at IS NULL).
    Снепшот 'after':  берётся когда статус = 'delivered' И отзыв написан ПОСЛЕ даты доставки.
    Оба снепшота хранятся в wb_feedback_snapshots и не перезаписываются после первой фиксации.
    """
    try:
        rows = db.execute(text("""
            SELECT r.id, r.matched_srid, r.status, r.shipped_at,
                   COALESCE(r.confirmed_at, r.updated_at) AS delivered_at,
                   (SELECT 1 FROM wb_feedback_snapshots s WHERE s.request_id=r.id AND s.snapshot_type='before' LIMIT 1) AS has_before,
                   (SELECT 1 FROM wb_feedback_snapshots s WHERE s.request_id=r.id AND s.snapshot_type='after'  LIMIT 1) AS has_after
            FROM reshipment_requests r
            WHERE r.matched_srid IS NOT NULL
              AND r.status NOT IN ('rejected', 'returned')
        """)).mappings().fetchall()
    except Exception as e:
        print(f"[MiniSync] review_snapshots: fetch error: {e}")
        return 0

    captured = 0
    for row in rows:
        need_before = not row["has_before"] and not row["shipped_at"]
        need_after  = not row["has_after"]  and row["status"] == "delivered"
        if not need_before and not need_after:
            continue

        try:
            if need_before:
                feedback = db.execute(text("""
                    SELECT f.id, f.valuation, f.text, f.photos
                    FROM wb_feedbacks f
                    JOIN wb_orders o ON (
                        o.shk_id = f.last_order_shk_id
                        OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                            AND o.supplier_article = f.supplier_article)
                    )
                    WHERE o.srid = :srid
                    ORDER BY f.created_date ASC
                    LIMIT 1
                """), {"srid": row["matched_srid"]}).mappings().first()

                if feedback:
                    db.execute(text("""
                        INSERT INTO wb_feedback_snapshots
                            (feedback_id, valuation, text, photos, request_id, snapshot_type, captured_at)
                        VALUES (:fid, :val, :text, :photos, :rid, 'before', NOW())
                        ON CONFLICT DO NOTHING
                    """), {"fid": feedback["id"], "val": feedback["valuation"],
                           "text": feedback["text"], "photos": feedback["photos"],
                           "rid": row["id"]})
                    captured += 1

            if need_after:
                # Берём только отзыв написанный ПОСЛЕ даты доставки (с допуском 3 часа на МСК)
                feedback_after = db.execute(text("""
                    SELECT f.id, f.valuation, f.text, f.photos
                    FROM wb_feedbacks f
                    JOIN wb_orders o ON (
                        o.shk_id = f.last_order_shk_id
                        OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                            AND o.supplier_article = f.supplier_article)
                    )
                    WHERE o.srid = :srid
                      AND f.created_date > :delivered_at - INTERVAL '3 hours'
                    ORDER BY f.created_date DESC
                    LIMIT 1
                """), {"srid": row["matched_srid"], "delivered_at": row["delivered_at"]}).mappings().first()

                if feedback_after:
                    db.execute(text("""
                        INSERT INTO wb_feedback_snapshots
                            (feedback_id, valuation, text, photos, request_id, snapshot_type, captured_at)
                        VALUES (:fid, :val, :text, :photos, :rid, 'after', NOW())
                        ON CONFLICT DO NOTHING
                    """), {"fid": feedback_after["id"], "val": feedback_after["valuation"],
                           "text": feedback_after["text"], "photos": feedback_after["photos"],
                           "rid": row["id"]})
                    captured += 1
        except Exception as e:
            print(f"[MiniSync] review_snapshots: error req#{row['id']}: {e}")

    if captured:
        db.commit()
        print(f"[MiniSync] review snapshots captured: {captured}")
    return captured


def send_pickup_reminders(db) -> int:
    """Шлёт повторное напоминание клиентам у которых посылка ждёт в ПВЗ > 48 часов."""
    try:
        rows = db.execute(text("""
            SELECT r.id, r.customer_name, r.cdek_uuid, r.wb_reply_sign,
                   r.salebot_client_id, r.salebot_messenger, r.salebot_blocked,
                   l.changed_at AS ready_at
            FROM reshipment_requests r
            JOIN reshipment_status_log l ON l.request_id = r.id AND l.new_status = 'ready_for_pickup'
            WHERE r.status = 'ready_for_pickup'
              AND r.pickup_reminder_sent_at IS NULL
              AND l.changed_at < NOW() - INTERVAL '48 hours'
            ORDER BY l.changed_at
        """)).mappings().fetchall()
    except Exception as e:
        print(f"[MiniSync] pickup_reminders: fetch error: {e}")
        return 0

    sent = 0
    for row in rows:
        # Пытаемся получить срок хранения из СДЭК
        keep_until_str = ""
        if row["cdek_uuid"]:
            try:
                from .cdek import _req, CDEK_BASE, _h
                r = _req.get(f"{CDEK_BASE}/orders/{row['cdek_uuid']}", headers=_h(), timeout=10)
                entity = r.json().get("entity", {})
                raw = entity.get("keep_free_until", "")
                if raw:
                    from datetime import datetime, timezone, timedelta
                    dt_utc = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    dt_msk = dt_utc.astimezone(timezone(timedelta(hours=3)))
                    keep_until_str = dt_msk.strftime("%-d %B до %H:%M (МСК)").replace(
                        "January","января").replace("February","февраля").replace("March","марта"
                        ).replace("April","апреля").replace("May","мая").replace("June","июня"
                        ).replace("July","июля").replace("August","августа").replace("September","сентября"
                        ).replace("October","октября").replace("November","ноября").replace("December","декабря")
            except Exception as e:
                print(f"[MiniSync] pickup_reminders: cdek error req#{row['id']}: {e}")

        msg = "📦 Напоминаем: ваша посылка ожидает вас в пункте выдачи СДЭК."
        if keep_until_str:
            msg += f"\nСрок бесплатного хранения: {keep_until_str}."
        msg += "\nПожалуйста, заберите посылку, чтобы не пропустить срок хранения."

        msg_sent = False

        # WB чат
        if row["wb_reply_sign"]:
            try:
                from . import wb_chat as _wbc
                _wbc.send_message(row["wb_reply_sign"], msg)
                msg_sent = True
            except Exception as e:
                print(f"[MiniSync] pickup_reminders: wb_chat error req#{row['id']}: {e}")

        # Salebot (TG или MAX)
        if row["salebot_client_id"] and not row["salebot_blocked"]:
            try:
                from .salebot import send_message as sb_send, is_enabled
                if is_enabled():
                    ok = sb_send(row["salebot_client_id"], msg, db=db, req_id=row["id"])
                    if ok:
                        msg_sent = True
            except Exception as e:
                print(f"[MiniSync] pickup_reminders: salebot error req#{row['id']}: {e}")

        if msg_sent:
            db.execute(text("""
                UPDATE reshipment_requests SET pickup_reminder_sent_at = NOW() WHERE id = :id
            """), {"id": row["id"]})
            sent += 1
            print(f"[MiniSync] pickup reminder sent req#{row['id']} ({row['customer_name']}), keep_until={keep_until_str or 'n/a'}")

    if sent:
        db.commit()
    return sent


def sync_all_answered_wb(db) -> int:
    """
    Тянет ответы WB с пагинацией (до 14 дней назад) и ставит state='answered'.
    Запускается раз в день.
    """
    key = _key()
    if not key:
        return 0

    total_updated = 0
    cutoff = "2026-01-01"  # не уходить глубже этой даты
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()

    for skip in range(0, 10001, 1000):
        try:
            feedbacks = wb_client.list_feedbacks(is_answered=True, take=1000, skip=skip, key=key)
        except Exception as e:
            print(f"[MiniSync] sync_all_answered error skip={skip}: {e}")
            break

        if not feedbacks:
            break

        ids = [str(fb.get("id", "")).strip() for fb in feedbacks if fb.get("id")]
        try:
            res = db.execute(text("""
                UPDATE wb_feedbacks SET state = 'answered'
                WHERE id = ANY(:ids) AND state = 'active'
            """), {"ids": ids})
            db.commit()
            total_updated += res.rowcount
        except Exception as e:
            print(f"[MiniSync] sync_all_answered DB error: {e}")
            break

        # Стоп если дошли до записей старше 14 дней
        dates = [fb.get("createdDate", "")[:10] for fb in feedbacks if fb.get("createdDate")]
        if dates and min(dates) < cutoff:
            break

    # Дополнительно: если answer_text уже есть — тоже помечаем
    try:
        db.execute(text("""
            UPDATE wb_feedbacks SET state = 'answered'
            WHERE state = 'active'
              AND answer_text IS NOT NULL AND TRIM(answer_text) != ''
        """))
        db.commit()
    except Exception:
        pass

    print(f"[MiniSync] sync_all_answered: total updated {total_updated} -> answered")
    return total_updated


def archive_old_active_feedbacks(db) -> int:
    """
    Pomechaet kak 'archive' vse otzryvov s state='active' starshe 60 dney.
    WB ne pozvolyaet otvetat' na starye otzyvy, poetomu oni ne mogut byt' 'active'.
    """
    try:
        result = db.execute(text("""
            UPDATE wb_feedbacks SET state = 'archive'
            WHERE state = 'active' AND created_date < now() - interval '60 days'
        """))
        db.commit()
        count = result.rowcount
        if count:
            print(f"[MiniSync] archive_old: {count} feedbacks archived (>60d)")
        return count
    except Exception as e:
        print(f"[MiniSync] archive_old error: {e}")
        return 0


_MSG_REVIEW_REPLY = (
    "Здравствуйте! Рады, что недостающие детали дошли до вас. "
    "Надеемся, они решили проблему, и теперь товар работает так, как должен. "
    "Будем рады, если вы вернётесь к нам или загляните в наш каталог — "
    "там найдёте ещё много интересного! "
    "С уважением, Александр из команды Видовито."
)

_MSG_REVIEW_NEW = (
    "Здравствуйте! Детали уже у вас. Приносим извинения за доставленные хлопоты. "
    "Будем очень признательны, если вы оставите положительный отзыв о товаре. "
    "С уважением, Александр из команды Видовито."
)
_MSG_REVIEW_UPDATE = (
    "Здравствуйте! Мы выслали недостающие детали и постарались исправить ситуацию. "
    "Будем очень благодарны, если вы обновите свой отзыв — для нас это важно! "
    "С уважением, Александр из команды Видовито."
)
_MSG_REVIEW_REMINDER = (
    "Здравствуйте! Хотели уточнить — всё в порядке с полученными деталями? "
    "Будем очень признательны, если найдёте минуту и оставите отзыв о товаре. "
    "С уважением, Александр из команды Видовито."
)
_MSG_REVIEW_FINAL = (
    "Здравствуйте! Последний раз обращаемся с этой просьбой — отзыв о товаре "
    "очень поможет нам стать лучше. Будем искренне благодарны! "
    "С уважением, Александр из команды Видовито."
)


def _post_wb_feedback_reply(feedback_id: str, existing_answer: str | None) -> bool:
    """Опубликовать или обновить ответ на отзыв WB через PATCH."""
    key = _key()
    if not key:
        return False
    if existing_answer and existing_answer.strip():
        text_to_post = existing_answer.rstrip() + "\n\nОбновлено:\n" + _MSG_REVIEW_REPLY
    else:
        text_to_post = _MSG_REVIEW_REPLY
    try:
        wb_client.answer_feedback(feedback_id, text_to_post, key=key, timeout=10)
        return True
    except Exception as e:
        print(f"[MiniSync] WB feedback reply exception: {e}")
        return False


def _send_review_msg_to_client(row: dict, msg: str) -> bool:
    """Отправить сообщение клиенту во все доступные каналы (WB чат + Salebot)."""
    ok = False
    if row.get("wb_reply_sign"):
        try:
            from backend import wb_chat
            wb_chat.send_message(row["wb_reply_sign"], msg)
            ok = True
        except Exception as e:
            print(f"[MiniSync] WB review msg req#{row['id']}: {e}")
    if row.get("salebot_client_id") and not row.get("salebot_blocked"):
        try:
            from backend import salebot
            if salebot.is_enabled():
                salebot.send_message(row["salebot_client_id"], msg)
                ok = True
        except Exception as e:
            print(f"[MiniSync] Salebot review msg req#{row['id']}: {e}")
    return ok


def send_review_requests(db) -> int:
    """Автоматическая цепочка запросов на отзыв после доставки.

    Step 0 → 1: первое сообщение через 2ч после доставки
    Step 1 → 2: напоминание через 24ч после первого
    Step 2 → 3: финальное через 72ч после напоминания
    Step 3: завершено, не трогаем
    Если появился after-снепшот — цепочка прекращается (step=3).
    """
    try:
        rows = db.execute(text("""
            SELECT r.id, r.review_step, r.review_last_sent_at,
                   r.confirmed_at, r.updated_at, r.matched_srid,
                   r.wb_reply_sign, r.salebot_client_id, r.salebot_blocked,
                   (SELECT 1 FROM wb_feedbacks f
                    JOIN wb_orders o ON (
                        o.shk_id = f.last_order_shk_id
                        OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                            AND o.supplier_article = f.supplier_article)
                    )
                    WHERE o.srid = r.matched_srid
                      AND f.created_date < COALESCE(r.confirmed_at, r.updated_at) - INTERVAL '3 hours'
                    ORDER BY f.created_date ASC
                    LIMIT 1) AS had_review_before,
                   (SELECT 1 FROM wb_feedback_snapshots s
                    WHERE s.request_id = r.id
                      AND s.snapshot_type = 'after' LIMIT 1) AS has_review_after
            FROM reshipment_requests r
            WHERE r.status = 'delivered'
              AND r.review_step < 3
              AND (r.wb_reply_sign IS NOT NULL
                   OR (r.salebot_client_id IS NOT NULL
                       AND NOT COALESCE(r.salebot_blocked, FALSE)))
        """)).mappings().fetchall()
    except Exception as e:
        print(f"[MiniSync] send_review_requests: fetch error: {e}")
        return 0

    sent = 0
    now = datetime.utcnow()

    for row in rows:
        req_id = row["id"]
        step = row["review_step"] or 0

        if row["has_review_after"]:
            db.execute(text(
                "UPDATE reshipment_requests SET review_step = 3 WHERE id = :id"
            ), {"id": req_id})
            continue

        delivered_at = row["confirmed_at"] or row["updated_at"]
        if not delivered_at:
            continue
        delivered_dt = delivered_at.replace(tzinfo=None) if hasattr(delivered_at, 'tzinfo') else delivered_at
        last_sent = row["review_last_sent_at"]
        last_sent_dt = last_sent.replace(tzinfo=None) if last_sent and hasattr(last_sent, 'tzinfo') else last_sent

        msg = None
        if step == 0 and now - delivered_dt >= timedelta(hours=2):
            msg = _MSG_REVIEW_UPDATE if row["had_review_before"] else _MSG_REVIEW_NEW
        elif step == 1 and last_sent_dt and now - last_sent_dt >= timedelta(hours=24):
            msg = _MSG_REVIEW_REMINDER
        elif step == 2 and last_sent_dt and now - last_sent_dt >= timedelta(hours=72):
            msg = _MSG_REVIEW_FINAL

        if not msg:
            continue

        ok = _send_review_msg_to_client(dict(row), msg)

        # При первом сообщении — дополнительно публикуем ответ на отзыв WB
        if ok and step == 0 and row["had_review_before"]:
            try:
                fb = db.execute(text("""
                    SELECT f.id, f.answer_text
                    FROM wb_feedbacks f
                    JOIN wb_orders o ON (
                        o.shk_id = f.last_order_shk_id
                        OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                            AND o.supplier_article = f.supplier_article)
                    )
                    WHERE o.srid = :srid
                    ORDER BY f.created_date ASC
                    LIMIT 1
                """), {"srid": row.get("matched_srid")}).mappings().first()
                if fb:
                    _post_wb_feedback_reply(str(fb["id"]), fb["answer_text"])
            except Exception as e:
                print(f"[MiniSync] feedback reply req#{req_id}: {e}")
        if ok:
            msg_col = {0: 'review_msg1_at', 1: 'review_msg2_at', 2: 'review_msg3_at'}[step]
            db.execute(text(f"""
                UPDATE reshipment_requests
                SET review_step = :step, review_last_sent_at = NOW(), {msg_col} = NOW()
                WHERE id = :id
            """), {"step": step + 1, "id": req_id})
            sent += 1

    if sent:
        db.commit()
        print(f"[MiniSync] review requests sent: {sent}")
    return sent


def send_rejection_promo(db) -> int:
    """Второе сообщение после отказа: промокод + просьба обновить отзыв (если есть).
    Страховочный воркер: основная отправка идёт через фоновую задачу (10 сек) в reject_request,
    этот проход — запасной вариант, если фоновая задача не выполнилась (например, рестарт сервера).
    """
    _RU_MONTHS_GEN = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
        7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }

    try:
        rows = db.execute(text("""
            SELECT r.id, r.promo_code,
                   r.wb_reply_sign, r.salebot_client_id, r.salebot_blocked,
                   r.matched_srid,
                   (SELECT s.expires_at FROM reshipment_settings s WHERE s.key = 'promo_code') AS promo_expires_at,
                   (SELECT 1 FROM wb_feedbacks f
                    JOIN wb_orders o ON (
                        o.shk_id = f.last_order_shk_id
                        OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                            AND o.supplier_article = f.supplier_article)
                    )
                    WHERE o.srid = r.matched_srid LIMIT 1) AS has_review
            FROM reshipment_requests r
            WHERE r.status = 'rejected'
              AND r.promo_code IS NOT NULL
              AND r.promo_sent_at IS NULL
              AND r.processed_at < NOW() - INTERVAL '2 minutes'
        """)).mappings().all()
    except Exception as e:
        print(f"[MiniSync] send_rejection_promo: fetch error: {e}")
        return 0

    sent = 0
    for row in rows:
        row = dict(row)
        code = row["promo_code"]
        has_review = row.get("has_review")
        expires_at = row.get("promo_expires_at")
        expiry_str = f" Промокод действителен до {expires_at.day} {_RU_MONTHS_GEN.get(expires_at.month, '')}." if expires_at else ""

        if has_review:
            msg = (
                f"В качестве извинений мы подготовили для вас персональный промокод на следующую покупку "
                f"со скидкой 15%: {code}.{expiry_str} Применить его можно при оформлении заказа на Wildberries.\n\n"
                "Также будем очень признательны, если вы найдёте возможность обновить отзыв о товаре — "
                "для нас это очень важно!\n\nС уважением, Александр из команды Видовито."
            )
        else:
            msg = (
                f"В качестве извинений мы подготовили для вас персональный промокод на следующую покупку "
                f"со скидкой 15%: {code}.{expiry_str} Применить его можно при оформлении заказа на Wildberries.\n\n"
                "Будем рады снова видеть вас среди покупателей Видовито!\n\nС уважением, Александр из команды Видовито."
            )

        ok = _send_review_msg_to_client(row, msg)
        if ok:
            db.execute(text(
                "UPDATE reshipment_requests SET promo_sent_at = NOW() WHERE id = :id"
            ), {"id": row["id"]})
            sent += 1

    if sent:
        db.commit()
        print(f"[MiniSync] rejection promo sent: {sent}")
    return sent


def sync_yandex_statuses(db) -> int:
    """Только sharing_url для Яндекс NDD.
    Статусы и уведомления клиенту — в _yandex_status_updater (main.py).
    Иначе мини-синк меняет статус раньше опросника, и письмо клиенту теряется."""
    try:
        rows = db.execute(text("""
            SELECT id, track_number, yandex_sharing_url
            FROM reshipment_requests
            WHERE delivery_method = 'yandex'
              AND track_number IS NOT NULL AND track_number != ''
              AND (yandex_sharing_url IS NULL OR yandex_sharing_url = '')
              AND status NOT IN ('delivered', 'returned', 'rejected')
        """)).mappings().fetchall()
    except Exception as e:
        print(f"[MiniSync] sync_yandex: fetch error: {e}")
        return 0

    if not rows:
        return 0

    try:
        from .yandex_ndd import get_order_status
    except Exception as e:
        print(f"[MiniSync] sync_yandex: import error: {e}")
        return 0

    updated = 0
    for row in rows:
        try:
            info = get_order_status(row["track_number"])
            sharing_url = info.get("sharing_url") or ""
            if not sharing_url:
                continue
            db.execute(text("""
                UPDATE reshipment_requests SET yandex_sharing_url = :url
                WHERE id = :id AND (yandex_sharing_url IS NULL OR yandex_sharing_url = '')
            """), {"url": sharing_url, "id": row["id"]})
            db.commit()
            updated += 1
            print(f"[MiniSync] Yandex sharing_url req#{row['id']}")
        except Exception as e:
            print(f"[MiniSync] sync_yandex req#{row['id']}: {e}")

    return updated


async def start_mini_sync_worker(get_db) -> None:
    """Фоновая задача FastAPI: синхронизация отзывов и возвратов каждые 5 минут."""
    import datetime as _dt
    print("[MiniSync] Worker started (every 5 min)")
    _last_daily = None
    while True:
        await asyncio.sleep(300)
        if not _key():
            continue
        new_feedbacks = 0
        try:
            # Каждый sync_* делает блокирующие HTTP-запросы к API WB (таймауты до 30с).
            # Выполняем их в отдельном потоке через asyncio.to_thread, иначе они
            # замораживают event loop uvicorn и весь сайт перестаёт отвечать на эти секунды.
            with db_session() as db:
                new_feedbacks = await asyncio.to_thread(sync_recent_feedbacks, db) or 0
                await asyncio.sleep(2)
                await asyncio.to_thread(sync_answered_feedbacks_for_active_requests, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(sync_recent_claims, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(sync_recent_questions, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(scan_unlinked_tokens, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(sync_reshipment_review_snapshots, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(send_pickup_reminders, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(send_review_requests, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(send_rejection_promo, db)
                await asyncio.sleep(2)
                await asyncio.to_thread(sync_yandex_statuses, db)

                # Ежедневно: ре-синк отвеченных + архивирование старых
                today = _dt.date.today()
                if _last_daily != today:
                    await asyncio.sleep(5)
                    await asyncio.to_thread(sync_all_answered_wb, db)
                    await asyncio.sleep(3)
                    await asyncio.to_thread(archive_old_active_feedbacks, db)
                    await asyncio.sleep(3)
                    await asyncio.to_thread(sync_all_answered_questions, db)
                    await asyncio.sleep(2)
                    await asyncio.to_thread(cleanup_old_wb_photos)
                    _last_daily = today

            # Уведомляем фронтенд об обновлении данных
            await broadcaster.broadcast("voc")
            await broadcaster.broadcast("moderation")
            await broadcaster.broadcast("dashboard")
            await broadcaster.broadcast("sync")
            await broadcaster.broadcast("chats")

            # Centrifugo: реалтайм-сигнал разделу отзывов о новых отзывах
            if new_feedbacks > 0:
                try:
                    from backend import centrifugo
                    await asyncio.to_thread(centrifugo.publish, "reviews", {"type": "reviews_updated", "count": new_feedbacks})
                except Exception as _e:
                    print(f"[MiniSync] centrifugo publish failed: {_e}")
        except Exception as e:
            print(f"[MiniSync] cycle error: {e}")
