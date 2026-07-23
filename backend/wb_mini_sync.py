"""Лёгкий воркер: частое обновление отзывов и возвратов WB (каждые 5 мин)."""
import os
import asyncio
import requests as _req
from datetime import datetime, timedelta
from sqlalchemy import text
from backend.database import db_session
from backend import broadcaster

FEEDBACKS_BASE = "https://feedbacks-api.wildberries.ru"
RETURNS_BASE   = "https://returns-api.wildberries.ru"


def _key() -> str:
    return os.getenv("WB_API_KEY", "").strip('"')


def _h() -> dict:
    return {"Authorization": _key()}


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


def sync_recent_feedbacks(db) -> int:
    """Забирает последние 100 активных отзывов и обновляет wb_feedbacks."""
    key = _key()
    if not key:
        return 0
    try:
        r = _req.get(
            f"{FEEDBACKS_BASE}/api/v1/feedbacks",
            params={"isAnswered": "false", "take": 100, "skip": 0, "order": "dateDesc"},
            headers=_h(), timeout=30,
        )
        r.raise_for_status()
        feedbacks = r.json().get("data", {}).get("feedbacks") or []
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
            r = _req.get(
                f"{FEEDBACKS_BASE}/api/v1/feedbacks",
                params={"isAnswered": "true", "take": 50, "skip": 0, "order": "dateDesc", "nm": int(nm_id)},
                headers=_h(), timeout=20,
            )
            r.raise_for_status()
            feedbacks = r.json().get("data", {}).get("feedbacks") or []
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


def sync_recent_claims(db) -> int:
    """Забирает последние 200 активных возвратов и обновляет wb_claims."""
    key = _key()
    if not key:
        return 0

    status_map = {"1": "На рассмотрении", "2": "Одобрено", "3": "Отказ"}

    try:
        r = _req.get(
            f"{RETURNS_BASE}/api/v1/claims",
            params={"limit": 200, "offset": 0, "is_archive": "false"},
            headers=_h(), timeout=30,
        )
        r.raise_for_status()
        claims = r.json().get("claims") or []
    except Exception as e:
        print(f"[MiniSync] claims API error: {e}")
        return 0

    count = 0
    for item in claims:
        srid = str(item.get("srid", "")).strip()
        if not srid:
            continue
        raw_st  = str(item.get("status", "")).strip()
        st_ex   = str(item.get("status_description") or item.get("status_ex") or "")
        status  = status_map.get(raw_st, raw_st)
        if status in ("", "None", "Архивная", "Активная"):
            el = st_ex.lower()
            if "одобрено" in el or "возврат" in el: status = "Одобрено"
            elif "отклон" in el or "отказ" in el:   status = "Отказ"
            else:                                     status = "На рассмотрении"

        nm_id   = str(item.get("nm_id") or item.get("nmId") or "").strip() or None
        article = str(item.get("supplier_article") or item.get("supplierArticle") or item.get("saName") or "")
        photos  = " ".join(u for u in (_photo_url(p) for p in (item.get("photos") or [])) if u) or None
        income  = str(item.get("income_id") or item.get("incomeId") or "0")

        try:
            db.execute(text("""
                INSERT INTO wb_claims
                    (srid, claim_id, created_dt, supplier_article, nm_id, user_comment,
                     status, claim_type, income_id, is_archive, last_sync, photos,
                     order_date, claim_date)
                VALUES
                    (:srid, :claim_id, :created_dt, :article, :nm_id, :comment,
                     :status, :claim_type, :income_id, false, NOW(), :photos,
                     :order_date, :claim_date)
                ON CONFLICT (srid) DO UPDATE SET
                    status       = EXCLUDED.status,
                    user_comment = COALESCE(EXCLUDED.user_comment, wb_claims.user_comment),
                    photos       = CASE WHEN EXCLUDED.photos IS NOT NULL
                                        THEN EXCLUDED.photos ELSE wb_claims.photos END,
                    nm_id        = COALESCE(EXCLUDED.nm_id, wb_claims.nm_id),
                    income_id    = COALESCE(EXCLUDED.income_id, wb_claims.income_id),
                    last_sync    = NOW()
            """), {
                "srid":       srid,
                "claim_id":   str(item.get("id") or item.get("claim_id") or ""),
                "created_dt": item.get("dt") or item.get("createdDate"),
                "article":    article,
                "nm_id":      nm_id,
                "comment":    item.get("user_comment") or item.get("userComment") or "",
                "status":     status,
                "claim_type": str(item.get("claim_type") or item.get("claimType") or ""),
                "income_id":  income,
                "photos":     photos,
                "order_date": item.get("order_dt") or item.get("orderDate"),
                "claim_date": item.get("dt") or item.get("claimDate"),
            })
            count += 1
        except Exception as e:
            print(f"[MiniSync] claim insert error: {e}")

    if count:
        db.commit()
        print(f"[MiniSync] claims synced: {count}")
    return count


def scan_unlinked_tokens(db) -> int:
    """Сканирует список WB-чатов и линкует незалинкованные токены по lastMessage и nm_id."""
    key = _key()
    if not key:
        return 0

    from sqlalchemy import text
    import re
    TOKEN_PAT = re.compile(r"box\.vidovito\.com\?t=([\w\-]+)")

    try:
        # Все незалинкованные токены (форма заполнена, но chatID ещё нет)
        rows = db.execute(text("""
            SELECT t.token, r.id as req_id, r.wb_nm_id
            FROM reshipment_chat_tokens t
            LEFT JOIN reshipment_requests r ON r.chat_token = t.token
            WHERE t.chat_id IS NULL
        """)).mappings().fetchall()
        if not rows:
            return 0
        unlinked = {r["token"]: r for r in rows}

        # Список WB чатов
        resp = _req.get(
            f"https://buyer-chat-api.wildberries.ru/api/v1/seller/chats",
            headers={"Authorization": key}, timeout=15
        )
        resp.raise_for_status()
        chats = (resp.json() or {}).get("result") or []
    except Exception as e:
        print(f"[MiniSync] scan_tokens error: {e}")
        return 0

    linked = 0
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
                "UPDATE reshipment_chat_tokens SET chat_id=:cid WHERE token=:token AND chat_id IS NULL"
            ), {"cid": chat_id, "token": token_found})
            req_id = unlinked[token_found].get("req_id")
            if req_id:
                db.execute(text(
                    "UPDATE reshipment_requests SET wb_chat_id=:cid, wb_nm_id=COALESCE(wb_nm_id, :nm) WHERE id=:id AND wb_chat_id IS NULL"
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
            r = _req.get(
                f"{FEEDBACKS_BASE}/api/v1/feedbacks",
                params={"isAnswered": "true", "take": 1000, "skip": skip, "order": "dateDesc"},
                headers=_h(), timeout=30,
            )
            r.raise_for_status()
            feedbacks = r.json().get("data", {}).get("feedbacks") or []
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
        resp = _req.patch(
            f"{FEEDBACKS_BASE}/api/v1/feedbacks",
            headers={"Authorization": f"Bearer {key}"},
            json={"id": feedback_id, "text": text_to_post},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        print(f"[MiniSync] WB feedback reply error {resp.status_code}: {resp.text[:200]}")
        return False
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


async def start_mini_sync_worker(get_db) -> None:
    """Фоновая задача FastAPI: синхронизация отзывов и возвратов каждые 5 минут."""
    import datetime as _dt
    print("[MiniSync] Worker started (every 5 min)")
    _last_daily = None
    while True:
        await asyncio.sleep(300)
        if not _key():
            continue
        try:
            with db_session() as db:
                sync_recent_feedbacks(db)
                await asyncio.sleep(2)
                sync_answered_feedbacks_for_active_requests(db)
                await asyncio.sleep(2)
                sync_recent_claims(db)
                await asyncio.sleep(2)
                scan_unlinked_tokens(db)
                await asyncio.sleep(2)
                sync_reshipment_review_snapshots(db)
                await asyncio.sleep(2)
                send_pickup_reminders(db)
                await asyncio.sleep(2)
                send_review_requests(db)
                await asyncio.sleep(2)
                send_rejection_promo(db)

                # Ежедневно: ре-синк отвеченных + архивирование старых
                today = _dt.date.today()
                if _last_daily != today:
                    await asyncio.sleep(5)
                    sync_all_answered_wb(db)
                    await asyncio.sleep(3)
                    archive_old_active_feedbacks(db)
                    _last_daily = today

            # Уведомляем фронтенд об обновлении данных
            await broadcaster.broadcast("voc")
            await broadcaster.broadcast("moderation")
            await broadcaster.broadcast("dashboard")
            await broadcaster.broadcast("sync")
        except Exception as e:
            print(f"[MiniSync] cycle error: {e}")
