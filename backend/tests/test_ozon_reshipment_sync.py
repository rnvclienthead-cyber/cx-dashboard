import unittest
from unittest.mock import Mock, patch

from backend import ozon_reshipment_sync


class SyncOneTests(unittest.TestCase):
    def test_packed_on_way_becomes_in_transit_and_notifies(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "id": 406, "salebot_client_id": "sb-1", "wb_reply_sign": None,
        }
        row = {
            "id": 406,
            "status": "packed",
            "ozon_posting_number": "P8E5P5P31783-910P",
            "track_number": "YP-2B26BY474E84-332P",
            "ozon_order_number": "YP-2B26BY474E84-332P",
            "salebot_client_id": "sb-1",
            "wb_reply_sign": None,
            "ozon_pvz_address": "Можайск",
            "ozon_pvz_name": "ПВЗ",
            "client_pvz_address": "",
            "items_to_send": "крепление",
        }
        with patch.object(
            ozon_reshipment_sync.ozon_delivery, "get_order_status",
            return_value={"status": "in_transit", "raw_status": "on_way"},
        ), patch.object(
            ozon_reshipment_sync.reshipment_notify, "claim_notify", return_value=True,
        ), patch.object(
            ozon_reshipment_sync, "_notify_client",
        ) as notify:
            moved = ozon_reshipment_sync.sync_one(db, row)
        self.assertEqual(moved, "in_transit")
        notify.assert_called_once()
        self.assertEqual(notify.call_args[0][2], "in_transit")

    def test_ready_for_shipping_stays_packed(self):
        db = Mock()
        row = {
            "id": 411,
            "status": "packed",
            "ozon_posting_number": "46Y1101P15Y6-6Y1E",
            "track_number": "EE-1",
            "ozon_order_number": "EE-1",
        }
        with patch.object(
            ozon_reshipment_sync.ozon_delivery, "get_order_status",
            return_value={"status": None, "raw_status": "ready_for_shipping"},
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "needs_posting_approve", return_value=False,
        ):
            self.assertIsNone(ozon_reshipment_sync.sync_one(db, row))
        db.execute.assert_not_called()

    def test_created_posting_is_approved(self):
        db = Mock()
        row = {
            "id": 1,
            "status": "approved",
            "ozon_posting_number": "P-1",
            "track_number": "O-1",
            "ozon_order_number": "O-1",
        }
        statuses = [
            {"status": None, "raw_status": "created"},
            {"status": None, "raw_status": "ready_for_shipping"},
        ]
        with patch.object(
            ozon_reshipment_sync.ozon_delivery, "get_order_status", side_effect=statuses,
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "approve_posting", return_value={"ok": True},
        ) as approve, patch.object(
            ozon_reshipment_sync.ozon_delivery, "ensure_ready_for_shipping",
            return_value="ready_for_shipping",
        ):
            self.assertIsNone(ozon_reshipment_sync.sync_one(db, row))
        approve.assert_called_once_with("P-1")


class RecoverMissingOrdersTests(unittest.TestCase):
    def test_writes_found_posting(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [{
            "id": 603,
            "customer_name": "Аня",
            "customer_phone": "+79000000000",
            "items_to_send": "крепление",
            "ozon_pvz_code": "340817",
            "client_pvz_code": None,
            "ozon_shipment_method_id": 9,
        }]
        with patch.object(
            ozon_reshipment_sync.ozon_delivery, "is_enabled", return_value=True,
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "find_posting",
            return_value={"order_number": "YE-1", "posting_number": "P-1"},
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "create_order",
        ) as create:
            n = ozon_reshipment_sync.recover_missing_ozon_orders(db)
        self.assertEqual(n, 1)
        create.assert_not_called()
        sql = str(db.execute.call_args_list[-1][0][0])
        self.assertIn("ozon_order_number", sql)

    def test_creates_when_not_in_cabinet(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [{
            "id": 603,
            "customer_name": "Аня",
            "customer_phone": "+79000000000",
            "items_to_send": "крепление",
            "ozon_pvz_code": "340817",
            "client_pvz_code": None,
            "ozon_shipment_method_id": 9,
        }]
        with patch.object(
            ozon_reshipment_sync.ozon_delivery, "is_enabled", return_value=True,
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "find_posting", return_value=None,
        ), patch.object(
            ozon_reshipment_sync.ozon_delivery, "create_order",
            return_value={"order_number": "YE-NEW", "posting_number": "P-NEW"},
        ):
            n = ozon_reshipment_sync.recover_missing_ozon_orders(db)
        self.assertEqual(n, 1)

    def test_sync_open_heals_before_status_poll(self):
        self.assertIn(
            "recover_missing_ozon_orders",
            ozon_reshipment_sync.sync_open_ozon_reshipments.__code__.co_names,
        )


if __name__ == "__main__":
    unittest.main()
