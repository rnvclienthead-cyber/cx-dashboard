import unittest
from unittest.mock import Mock, patch

from backend.chat_fulfillment import (
    classify_wb_warehouse,
    fulfillment_payload,
    resolve_chat_fulfillment,
)


class WarehouseClassifyTests(unittest.TestCase):
    def test_seller_fbs(self):
        self.assertEqual(classify_wb_warehouse("КитАлл ФБС"), "FBS")
        self.assertEqual(classify_wb_warehouse("FBS Казань"), "FBS")

    def test_wb_fbo_centers(self):
        self.assertEqual(classify_wb_warehouse("Коледино"), "FBO")
        self.assertEqual(classify_wb_warehouse("СЦ Казань Столбище"), "FBO")
        self.assertEqual(classify_wb_warehouse("Склад WB РФ"), "FBO")

    def test_empty(self):
        self.assertIsNone(classify_wb_warehouse(""))
        self.assertIsNone(classify_wb_warehouse(None))


class PayloadTests(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(fulfillment_payload("fbs")["label"], "FBS · наш склад")
        self.assertEqual(fulfillment_payload("FBY")["code"], "FBY")
        self.assertIsNone(fulfillment_payload("xyz"))

    def test_dostavka_branch_by_fulfillment(self):
        from backend.chat_fulfillment import dostavka_branch_for_fulfillment, DOSTAVKA_OUR_DELAY_KEY
        self.assertEqual(dostavka_branch_for_fulfillment("FBS"), DOSTAVKA_OUR_DELAY_KEY)
        self.assertEqual(dostavka_branch_for_fulfillment("fbs"), DOSTAVKA_OUR_DELAY_KEY)
        self.assertEqual(dostavka_branch_for_fulfillment("FBO"), "")
        self.assertEqual(dostavka_branch_for_fulfillment("FBY"), "")
        self.assertEqual(dostavka_branch_for_fulfillment("DBW"), "")
        self.assertIsNone(dostavka_branch_for_fulfillment(""))
        self.assertIsNone(dostavka_branch_for_fulfillment(None))


class ResolveChatFulfillmentTests(unittest.TestCase):
    def test_ozon_fbo(self):
        conn = Mock()
        conn.execute.return_value.first.return_value = ("Fbo",)
        out = resolve_chat_fulfillment(conn, "ozon", "chat-1")
        self.assertEqual(out["code"], "FBO")

    def test_wb_logistics_fbo(self):
        conn = Mock()
        chat = Mock()
        chat.mappings.return_value.first.return_value = {"order_rid": "eB5.iaaa.0.0", "nm_id": 1}
        logistics = Mock()
        logistics.first.return_value = ("Коледино",)
        conn.execute.side_effect = [chat, logistics]
        out = resolve_chat_fulfillment(conn, "wb", "1:abc")
        self.assertEqual(out["code"], "FBO")

    def test_wb_rid_without_warehouse_is_fbs(self):
        conn = Mock()
        chat = Mock()
        chat.mappings.return_value.first.return_value = {"order_rid": "eB5.iaaa.0.0", "nm_id": 1}
        logistics = Mock()
        logistics.first.return_value = None
        dbw = Mock()
        dbw.scalar.return_value = None
        conn.execute.side_effect = [chat, logistics, dbw]
        out = resolve_chat_fulfillment(conn, "wb", "1:abc")
        self.assertEqual(out["code"], "FBS")

    def test_wb_uses_rid_from_messages_when_chat_column_empty(self):
        conn = Mock()
        chat = Mock()
        chat.mappings.return_value.first.return_value = {"order_rid": "", "nm_id": 169887124}
        msg_rid = Mock()
        msg_rid.first.return_value = ("134439885122330222.0.0",)
        logistics = Mock()
        logistics.first.return_value = ("Самара (Новосемейкино)",)
        conn.execute.side_effect = [chat, msg_rid, logistics]
        out = resolve_chat_fulfillment(conn, "wb", "1:abc")
        self.assertEqual(out["code"], "FBO")
