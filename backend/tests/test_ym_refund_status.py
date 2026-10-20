import unittest

from backend.ym_returns_api import effective_refund_status


class YmRefundStatusTests(unittest.TestCase):
    def test_item_decision_does_not_hide_started_by_user(self):
        """STARTED_BY_USER — заявка покупателя, не наше решение в ЛК."""
        from backend.ym_returns_api import status_ru

        payload = {
            "refundStatus": "STARTED_BY_USER",
            "creationDate": "2026-09-16T15:45:52.618+03:00",
            "items": [{
                "decisions": [{"decisionType": "FAST_REFUND_MONEY"}],
            }],
        }
        self.assertEqual(effective_refund_status(payload), "STARTED_BY_USER")
        self.assertEqual(status_ru("STARTED_BY_USER"), "Заявка создана")
        self.assertNotEqual(status_ru("STARTED_BY_USER"), "Ждёт нашего решения")

    def test_old_started_by_user_stays_created_not_approved(self):
        payload = {
            "refundStatus": "STARTED_BY_USER",
            "creationDate": "2026-06-01T12:00:00+03:00",
            "items": [{
                "decisions": [{"decisionType": "REFUND_MONEY"}],
            }],
        }
        self.assertEqual(effective_refund_status(payload), "STARTED_BY_USER")

    def test_reject_decision_keeps_started_by_user(self):
        payload = {
            "refundStatus": "STARTED_BY_USER",
            "creationDate": "2026-09-16T15:00:00+03:00",
            "items": [{
                "decisions": [{"decisionType": "DECLINE_REFUND"}],
            }],
        }
        self.assertEqual(effective_refund_status(payload), "STARTED_BY_USER")

    def test_waiting_label_is_only_seller_queue(self):
        from backend.ym_returns_api import STATUS_RU, status_ru

        waiting = {k for k, v in STATUS_RU.items() if v == "Ждёт нашего решения"}
        self.assertEqual(
            waiting,
            {"WAITING_FOR_DECISION", "PREMODERATION_DECISION_WAITING"},
        )
        self.assertEqual(status_ru("PREMODERATION_DECISION_WAITING"), "Ждёт нашего решения")

    def test_premoderation_decision_made_stays_active_status(self):
        """После отказа ЯМ держит PREMODERATION_DECISION_MADE — не схлопывать в REJECTED."""
        payload = {
            "refundStatus": "PREMODERATION_DECISION_MADE",
            "items": [{
                "decisions": [{"decisionType": "DECLINE_REFUND"}],
            }],
        }
        self.assertEqual(effective_refund_status(payload), "PREMODERATION_DECISION_MADE")

    def test_select_delivery_is_approved_waiting_for_buyer(self):
        from backend.ym_returns_api import status_ru

        payload = {
            "refundStatus": "PREMODERATION_SELECT_DELIVERY",
            "items": [{
                "decisions": [{"decisionType": "REFUND_MONEY"}],
            }],
        }
        self.assertEqual(effective_refund_status(payload), "PREMODERATION_SELECT_DELIVERY")
        self.assertEqual(status_ru("PREMODERATION_SELECT_DELIVERY"), "Ожидает возврат")

    def test_keeps_raw_when_no_decision(self):
        self.assertEqual(
            effective_refund_status({"refundStatus": "WAITING_FOR_DECISION"}),
            "WAITING_FOR_DECISION",
        )

    def test_apply_status_code_updates_local_row(self):
        from backend import ym_returns_api

        executed = []

        class _Result:
            pass

        class _Db:
            def execute(self, statement, params=None):
                executed.append((str(statement), params or {}))
                return _Result()

            def commit(self):
                executed.append(("COMMIT", {}))

        ym_returns_api.apply_status_code(_Db(), "27494335", "REFUNDED")
        self.assertTrue(any("UPDATE ym_returns" in sql for sql, _ in executed))
        params = next(p for sql, p in executed if "UPDATE ym_returns" in sql)
        self.assertEqual(params["st"], "REFUNDED")
        self.assertEqual(params["ru"], "Одобрено")
        self.assertEqual(params["rid"], "27494335")


class DeclineReturnForReshipmentTests(unittest.TestCase):
    def test_skips_when_no_return(self):
        from backend import ym_returns_api
        from unittest.mock import Mock

        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = None
        result = ym_returns_api.decline_return_for_reshipment(db, order_id="56443919553")
        self.assertEqual(result["skipped"], "no_return")
        self.assertFalse(result["ok"])

    def test_skips_when_already_closed(self):
        from backend import ym_returns_api
        from unittest.mock import Mock

        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "return_id": "27849268",
            "order_id": "56443919553",
            "status": "REJECTED",
            "status_ru": "Отказ",
        }
        result = ym_returns_api.decline_return_for_reshipment(db, return_id="27849268")
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "already_closed")

    def test_declines_waiting_return(self):
        from backend import ym_returns_api
        from unittest.mock import Mock, patch

        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "return_id": "27849268",
            "order_id": "56443919553",
            "status": "PREMODERATION_DECISION_WAITING",
            "status_ru": "Ждёт нашего решения",
        }
        with patch.object(
            ym_returns_api, "decide_return",
            return_value={"ok": True, "action": "DECLINE_REFUND"},
        ) as decide:
            result = ym_returns_api.decline_return_for_reshipment(
                db, order_id="56443919553", ym_chat_id="17967456",
            )
        decide.assert_called_once()
        self.assertEqual(decide.call_args.args[1], "27849268")
        self.assertEqual(decide.call_args.args[2], "DECLINE_REFUND")
        self.assertIn("деталь", decide.call_args.args[3])
        self.assertIn("отправлени", decide.call_args.args[3])
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
