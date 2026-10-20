import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import reshipment_notify

ROOT = Path(__file__).resolve().parents[2]
NOTIFY = (ROOT / "backend/reshipment_notify.py").read_text()
MAIN = (ROOT / "backend/main.py").read_text()
OZON = (ROOT / "backend/ozon_reshipment_sync.py").read_text()
RESHIP = (ROOT / "backend/routers/reshipment.py").read_text()


class MarketplaceNotifySourceTests(unittest.TestCase):
    def test_helper_prefers_ym_chat(self):
        self.assertIn("def send_marketplace_message", NOTIFY)
        self.assertIn("ym_chat_sync.send_message", NOTIFY)
        self.assertIn("ozon_chat_sync.send_message", NOTIFY)
        self.assertIn("def release_notify", NOTIFY)
        self.assertIn("def notify_delivery_status", NOTIFY)

    def test_yandex_updater_loads_ym_chat_and_sends_there(self):
        yandex = MAIN.split("async def _yandex_status_updater", 1)[1]
        self.assertIn("ym_chat_id, ozon_chat_id, marketplace", yandex)
        self.assertIn("notify_delivery_status", yandex)

    def test_cdek_and_ozon_updaters_use_same_helper(self):
        self.assertIn("notify_delivery_status", MAIN)
        self.assertIn("notify_delivery_status", OZON)
        self.assertIn("ym_chat_id, ozon_chat_id, marketplace", OZON)
        self.assertIn("ozon_chat_id, marketplace", MAIN)
        self.assertIn("notify_delivery_status", RESHIP)

    def test_ozon_updater_syncs_shop_orders_even_if_reshipment_fails(self):
        ozon = MAIN.split("async def _ozon_status_updater", 1)[1].split("async def ", 1)[0]
        shop_pos = ozon.find("shop_orders_mod.sync_open_ozon")
        reship_pos = ozon.find("sync_open_ozon_reshipments")
        self.assertGreater(shop_pos, 0)
        self.assertGreater(reship_pos, shop_pos)
        self.assertIn("ozon_updater: shop orders:", ozon)


class NotifyDeliveryStatusTests(unittest.TestCase):
    def test_releases_claim_when_chat_fails(self):
        db = Mock()
        row = {"id": 618, "marketplace": "ozon", "ozon_chat_id": "abc"}
        with patch.object(reshipment_notify, "claim_notify", return_value=True) as claim, \
             patch.object(reshipment_notify, "release_notify") as release, \
             patch.object(reshipment_notify, "_salebot_status", return_value=False), \
             patch.object(reshipment_notify, "send_marketplace_message", return_value=False):
            ok = reshipment_notify.notify_delivery_status(
                db, row, "ready_for_pickup",
                delivery_method="ozon", track_number="T-1",
            )
        self.assertFalse(ok)
        claim.assert_called_once()
        release.assert_called_once_with(db, 618, "ready_for_pickup")

    def test_keeps_claim_when_chat_sends(self):
        db = Mock()
        row = {"id": 665, "marketplace": "wb", "wb_reply_sign": "sign"}
        with patch.object(reshipment_notify, "claim_notify", return_value=True), \
             patch.object(reshipment_notify, "release_notify") as release, \
             patch.object(reshipment_notify, "_salebot_status", return_value=False), \
             patch.object(reshipment_notify, "send_marketplace_message", return_value=True):
            ok = reshipment_notify.notify_delivery_status(
                db, row, "ready_for_pickup",
                delivery_method="ozon", track_number="T-1",
            )
        self.assertTrue(ok)
        release.assert_not_called()

    def test_already_claimed_is_silent(self):
        db = Mock()
        with patch.object(reshipment_notify, "claim_notify", return_value=False), \
             patch.object(reshipment_notify, "send_marketplace_message") as send:
            ok = reshipment_notify.notify_delivery_status(
                db, {"id": 1}, "ready_for_pickup", delivery_method="ozon",
            )
        self.assertFalse(ok)
        send.assert_not_called()

    def test_ym_retries_without_url(self):
        calls = []

        def boom_then_ok(cid, text):
            calls.append(text)
            if "http" in text:
                raise RuntimeError("links forbidden")

        row = {"id": 357, "marketplace": "ym", "ym_chat_id": "17548491"}
        with patch("backend.ym_chat_sync.send_message", side_effect=boom_then_ok):
            ok = reshipment_notify.send_marketplace_message(
                row,
                "📬 Ваша посылка готова к выдаче!\nОтследить: https://dostavka.yandex.ru/route/#abc",
            )
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("http", calls[1])

    def test_ozon_skips_closed_chat_without_api(self):
        db = Mock()
        db.execute.return_value.scalar.return_value = __import__("datetime").datetime(2026, 9, 28, 15, 0)
        row = {"id": 618, "marketplace": "ozon", "ozon_chat_id": "abc"}
        with patch("backend.ozon_chat_sync.send_message") as send:
            ok = reshipment_notify.send_marketplace_message(row, "hi", db=db)
        self.assertFalse(ok)
        send.assert_not_called()


class OzonPickupGuideTests(unittest.TestCase):
    def test_guide_is_text_only(self):
        text = reshipment_notify.format_ozon_pickup_guide()
        self.assertIn("Ozon Доставка", text)
        self.assertIn("Получения", text)
        self.assertIn("штрихкод", text.lower())
        self.assertLessEqual(len(text), 1000)
        self.assertNotIn("send_marketplace_files", NOTIFY)
        self.assertNotIn("ozon_pickup_guide_files", NOTIFY)

    def test_skips_non_ozon(self):
        self.assertFalse(reshipment_notify.send_ozon_pickup_guide({"delivery_method": "yandex"}))

    def test_approve_sends_guide(self):
        self.assertIn("send_ozon_pickup_guide", RESHIP)

    def test_chat_button_uses_the_same_site_screenshots(self):
        from backend.routers.chat_bot import _OZON_PICKUP_GUIDE_DIR
        names = [p.name for p in sorted(_OZON_PICKUP_GUIDE_DIR.glob("*.png"), key=lambda p: p.name)]
        self.assertEqual(names, [f"{i}.png" for i in range(1, 7)])
        playbook = Path(__file__).resolve().parents[1] / "routers" / "chat_playbook.py"
        src = playbook.read_text(encoding="utf-8")
        self.assertIn("def ensure_ozon_pickup_guide", src)
        self.assertIn("'ozon_pickup'", src)

    def test_sends_text_not_files(self):
        row = {
            "id": 1, "marketplace": "wb", "delivery_method": "ozon",
            "wb_reply_sign": "sign",
        }
        with patch.object(reshipment_notify, "send_marketplace_message", return_value=True) as send:
            ok = reshipment_notify.send_ozon_pickup_guide(row)
        self.assertTrue(ok)
        send.assert_called_once()
        self.assertIn("Ozon Доставка", send.call_args[0][1])

