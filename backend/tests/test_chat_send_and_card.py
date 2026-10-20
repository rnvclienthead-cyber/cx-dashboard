import unittest
from unittest.mock import Mock, patch

import requests

from backend import wb_chat
from backend.routers import cx_responses


class WbSridCoreTests(unittest.TestCase):
    def test_middle_token(self):
        self.assertEqual(
            cx_responses._wb_srid_core("eB5.iad266e5c7bafbe30b275e7379eae5fbf.0.0"),
            "iad266e5c7bafbe30b275e7379eae5fbf",
        )

    def test_empty(self):
        self.assertEqual(cx_responses._wb_srid_core(""), "")


class ChatCardDictTests(unittest.TestCase):
    def test_wb_sku_from_nm(self):
        d = cx_responses._chat_card_dict("wb", {"id": "1:abc", "nm_id": 12, "good_name": None})
        self.assertEqual(d["mp"], "wb")
        self.assertEqual(d["item_type"], "chat")
        self.assertEqual(d["sku"], "Арт. 12")
        self.assertFalse(d["pre_purchase"])

    def test_ym_keeps_sku(self):
        d = cx_responses._chat_card_dict("ym", {"id": "9", "sku": "ART-1"})
        self.assertEqual(d["mp"], "ym")
        self.assertEqual(d["sku"], "ART-1")


class SellerReplySourceTests(unittest.TestCase):
    def test_bot_message_id(self):
        src = cx_responses.match_seller_reply_source("текст", [], message_id="bot-1-2")
        self.assertEqual(src["repliedBy"], "bot")
        self.assertEqual(src["repliedByName"], "Автоответ")

    def test_manager_log(self):
        src = cx_responses.match_seller_reply_source(
            "Здравствуйте! Отправим деталь.",
            [{"answered_by": "olga", "answer_text": "Здравствуйте! Отправим деталь."}],
        )
        self.assertEqual(src["repliedBy"], "manager")
        self.assertEqual(src["repliedByName"], "olga")

    def test_chat_bot_log(self):
        src = cx_responses.match_seller_reply_source(
            "Статус смотрите в карточке",
            [{"answered_by": "chat_bot", "answer_text": "Статус смотрите в карточке заказа."}],
        )
        self.assertEqual(src["repliedBy"], "bot")


class GetChatsCacheTests(unittest.TestCase):
    def setUp(self):
        wb_chat.invalidate_chats_cache()

    def tearDown(self):
        wb_chat.invalidate_chats_cache()

    def test_second_call_uses_cache(self):
        payload = [{"chatID": "1:x", "replySign": "abc"}]
        with patch.object(wb_chat.wb_client, "get_chats", return_value=payload) as mocked:
            with patch.object(wb_chat, "_key", return_value="k"):
                first = wb_chat.get_chats()
                second = wb_chat.get_chats()
        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(mocked.call_count, 1)

    def test_429_falls_back_to_cache(self):
        payload = [{"chatID": "1:x"}]
        err = requests.HTTPError("429")
        err.response = Mock(status_code=429)
        with patch.object(wb_chat.wb_client, "get_chats", side_effect=[payload, err, err, err, err]) as mocked:
            with patch.object(wb_chat, "_key", return_value="k"):
                with patch.object(wb_chat.time, "sleep"):
                    wb_chat.get_chats()
                    wb_chat._chats_cache["ts"] = 0
                    again = wb_chat.get_chats()
        self.assertEqual(again, payload)
        self.assertGreaterEqual(mocked.call_count, 2)


def _db_with_reply_sign(sign):
    db = Mock()
    conn = Mock()
    conn.execute.return_value.first.return_value = (sign,) if sign else None
    ctx = Mock()
    ctx.__enter__ = Mock(return_value=conn)
    ctx.__exit__ = Mock(return_value=False)
    db.bind.connect.return_value = ctx
    return db


class ResolveWbReplySignTests(unittest.TestCase):
    def test_uses_db_sign_without_wb_list(self):
        db = _db_with_reply_sign("stored-sign")
        with patch.object(wb_chat, "resolve_reply_sign") as mocked:
            sign = cx_responses._resolve_wb_reply_sign("1:abc", db)
        self.assertEqual(sign, "stored-sign")
        mocked.assert_not_called()

    def test_asks_wb_when_db_empty(self):
        db = _db_with_reply_sign("")
        with patch.object(wb_chat, "resolve_reply_sign", return_value="fresh") as mocked:
            sign = cx_responses._resolve_wb_reply_sign("1:abc", db)
        self.assertEqual(sign, "fresh")
        mocked.assert_called_once()

    def test_refreshes_from_wb_when_asked(self):
        db = _db_with_reply_sign("stored-sign")
        with patch.object(wb_chat, "resolve_reply_sign", return_value="fresh") as mocked:
            sign = cx_responses._resolve_wb_reply_sign("1:abc", db, refresh=True)
        self.assertEqual(sign, "fresh")
        mocked.assert_called_once()


class SendWbRetryTests(unittest.TestCase):
    def test_retries_after_429_then_ok(self):
        err = requests.HTTPError("429")
        err.response = Mock(status_code=429, text="rate limit")
        db = Mock()
        payload = Mock(text="привет")
        user = {"username": "rustam"}
        with (
            patch.object(cx_responses, "_wb_key", return_value="k"),
            patch.object(cx_responses, "_resolve_wb_reply_sign", return_value="sign"),
            patch.object(cx_responses.wb_client, "send_chat_message", side_effect=[err, Mock()]) as send,
            patch.object(cx_responses, "_publish_seller_replied"),
            patch.object(wb_chat, "invalidate_chats_cache"),
            patch.object(cx_responses.time, "sleep"),
        ):
            conn = Mock()
            conn.execute.return_value.first.return_value = None
            ctx = Mock()
            ctx.__enter__ = Mock(return_value=conn)
            ctx.__exit__ = Mock(return_value=False)
            db.bind.connect.return_value = ctx
            result = cx_responses.send_wb_chat_message("1:abc", payload, db, user)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(send.call_count, 2)


class PrePurchaseFlagTests(unittest.TestCase):
    def test_skips_tiny_chat_list(self):
        out = wb_chat.refresh_pre_purchase_flags(
            [{"chatID": "1:a", "lastMessage": {"addTimestamp": 1}}]
        )
        self.assertFalse(out["ok"])

    def test_chats_queue_exposes_flag(self):
        self.assertIn("pre_purchase", cx_responses._CHATS_QUEUE_VIEW_SQL)


class ReshipmentChatCardTests(unittest.TestCase):
    def test_ozon_shows_order_number_and_eta_not_wb_srid(self):
        card = cx_responses._reshipment_chat_card({
            "id": 402,
            "status": "in_transit",
            "delivery_method": "ozon",
            "track_number": "PE-132B37Y16622-E5BE",
            "ozon_order_number": "PE-132B37Y16622-E5BE",
            "ozon_posting_number": "2BB368Y76B02-04YE",
            "ozon_pvz_address": "Новосибирск, Широкая улица, 23",
            "client_pvz_address": "Новосибирск, Широкая улица, 23",
            "matched_srid": "ebE.r7d2d61cc3b9f4f8a9cad46e580cdcfde.0.0",
            "eta_date": "2026-09-18T14:00:00Z",
            "created_at": None,
            "marketplace": "wb",
            "customer_name": "Виктория",
        })
        self.assertEqual(card["order_number"], "PE-132B37Y16622-E5BE")
        self.assertEqual(card["track_number"], "")
        self.assertEqual(card["matched_srid"], "")
        self.assertEqual(card["eta_label"], "18 сентября")
        self.assertIn("Широкая", card["pvz"])

    def test_cdek_keeps_track(self):
        card = cx_responses._reshipment_chat_card({
            "id": 416,
            "status": "in_transit",
            "delivery_method": "cdek",
            "cdek_number": "10319240272",
            "track_number": "",
            "client_pvz_address": "Алексеевка",
            "matched_srid": "abc",
            "eta_date": "2026-09-17",
            "created_at": None,
            "marketplace": "wb",
        })
        self.assertEqual(card["track_number"], "10319240272")
        self.assertEqual(card["order_number"], "")
        self.assertEqual(card["matched_srid"], "abc")
        self.assertEqual(card["eta_label"], "17 сентября")
