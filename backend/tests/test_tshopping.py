import unittest
from unittest.mock import patch, MagicMock
import os

from backend import tshopping, telegram_proxy


def _msg(text="", **kwargs):
    chat = kwargs.pop("chat", {"id": 111, "type": "private"})
    frm = kwargs.pop("from_user", {"id": 111, "first_name": "Иван", "username": "ivan", "is_bot": False})
    body = {
        "message_id": kwargs.pop("message_id", 7),
        "date": kwargs.pop("date", 1700000000),
        "chat": chat,
        "from": frm,
    }
    if text:
        body["text"] = text
    body.update(kwargs)
    return {"update_id": 50, "message": body}


class ExtractIncomingTests(unittest.TestCase):
    def test_text_message(self):
        incoming = tshopping.extract_incoming(_msg("Где заказ?"))
        self.assertEqual(incoming["tg_chat_id"], 111)
        self.assertEqual(incoming["text"], "Где заказ?")
        self.assertEqual(incoming["display_name"], "Иван")
        self.assertFalse(incoming["is_start"])

    def test_start_is_flagged_and_text_cleared(self):
        incoming = tshopping.extract_incoming(_msg("/start"))
        self.assertTrue(incoming["is_start"])
        self.assertEqual(incoming["command"], "start")
        self.assertEqual(incoming["text"], "")

    def test_new_and_cancel_commands(self):
        self.assertEqual(tshopping.extract_incoming(_msg("/new"))["command"], "new")
        self.assertEqual(tshopping.extract_incoming(_msg("/cancel"))["command"], "cancel")

    def test_welcome_is_a_request_form(self):
        text = tshopping.WELCOME_TEXT.lower()
        self.assertIn("оставить заявку", text)
        self.assertIn("кнопк", text)
        self.assertNotIn("номер заказа", text)
        self.assertNotIn("т-шопинг", text)

    def test_leave_request_phrase(self):
        incoming = tshopping.extract_incoming(_msg("Оставить заявку"))
        self.assertEqual(incoming["command"], "new")
        self.assertEqual(incoming["text"], "")

    def test_callback_new(self):
        incoming = tshopping.extract_incoming({
            "update_id": 9,
            "callback_query": {
                "id": "cb1",
                "data": "form:new",
                "from": {"id": 111, "first_name": "Иван", "is_bot": False},
                "message": {"message_id": 3, "chat": {"id": 111, "type": "private"}},
            },
        })
        self.assertEqual(incoming["command"], "new")
        self.assertEqual(incoming["callback_id"], "cb1")

    def test_group_ignored(self):
        self.assertIsNone(tshopping.extract_incoming(_msg(
            "привет", chat={"id": -100, "type": "supergroup"}
        )))

    def test_bot_ignored(self):
        self.assertIsNone(tshopping.extract_incoming(_msg(
            "hi", from_user={"id": 1, "is_bot": True, "first_name": "Bot"}
        )))

    def test_photo_without_caption(self):
        incoming = tshopping.extract_incoming(_msg(
            photo=[
                {"file_id": "small", "file_size": 10},
                {"file_id": "big", "file_size": 90},
            ]
        ))
        self.assertEqual(incoming["attachments"][0]["file_id"], "big")
        self.assertEqual(incoming["attachments"][0]["kind"], "image")

    def test_document_and_caption(self):
        incoming = tshopping.extract_incoming(_msg(
            caption="чек",
            document={"file_id": "d1", "file_name": "bill.pdf", "mime_type": "application/pdf"},
        ))
        self.assertEqual(incoming["text"], "чек")
        self.assertEqual(incoming["attachments"][0]["kind"], "document")

    def test_empty_ignored(self):
        self.assertIsNone(tshopping.extract_incoming({"update_id": 1, "message": {
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False},
        }}))

    def test_callback_ignored(self):
        self.assertIsNone(tshopping.extract_incoming({"update_id": 2, "callback_query": {}}))


class StaffSkipTests(unittest.TestCase):
    def test_env_ids(self):
        env = {
            "TG_ADMIN_CHAT_ID": "10",
            "TG_MODERATOR_CHAT_ID": "20",
            "TG_ALERT_CHAT_ID": "30",
        }
        with patch.dict(os.environ, env, clear=False):
            ids = tshopping.staff_chat_ids(None)
        self.assertEqual(ids, {"10", "20", "30"})


class WebhookSecretTests(unittest.TestCase):
    def test_explicit_secret(self):
        with patch.dict(os.environ, {"TG_WEBHOOK_SECRET": "abc"}, clear=False):
            self.assertEqual(tshopping.webhook_secret(), "abc")


class SendReplyTests(unittest.TestCase):
    def test_text_only(self):
        with patch.object(telegram_proxy, "send_text", return_value=True) as send:
            ok = tshopping.send_reply_to_telegram(111, "Ответ", [])
        self.assertTrue(ok)
        send.assert_called_once_with(111, "Ответ")

    def test_reply_prefixes_order(self):
        with patch.object(telegram_proxy, "send_text", return_value=True) as send:
            ok = tshopping.send_reply_to_telegram(111, "Сделаем", [], order_number="A-100")
        self.assertTrue(ok)
        send.assert_called_once_with(111, "По заказу A-100\n\nСделаем")

    def test_photo_uses_caption(self):
        with patch.object(telegram_proxy, "send_photo", return_value=True) as photo, \
             patch.object(telegram_proxy, "send_text") as text:
            ok = tshopping.send_reply_to_telegram(111, "Вот фото", [{
                "kind": "image", "filename": "a.jpg",
            }])
        self.assertTrue(ok)
        photo.assert_called_once()
        text.assert_not_called()

    def test_empty_fails(self):
        self.assertFalse(tshopping.send_reply_to_telegram(111, "", []))

    def test_site_ticket_skips_telegram(self):
        with patch.object(telegram_proxy, "send_text") as send:
            ok = tshopping.send_reply_to_telegram(None, "Ответ", [])
        self.assertTrue(ok)
        send.assert_not_called()


class IngestStaffSkipTests(unittest.TestCase):
    def test_staff_not_stored(self):
        conn = MagicMock()
        incoming = tshopping.extract_incoming(_msg("привет"))
        result = tshopping.ingest_incoming(conn, incoming, staff_ids={"111"})
        self.assertEqual(result["status"], "skipped_staff")
        conn.execute.assert_not_called()


class FormFlowTests(unittest.TestCase):
    def test_form_walks_to_ticket(self):
        from backend import tshopping_form as form_mod
        state = form_mod.begin_menu()
        self.assertEqual(state["step"], "idle")
        self.assertIn("Оставить заявку", state["reply"])
        self.assertTrue(state["markup"]["keyboard"])
        state = form_mod.begin_form()
        self.assertEqual(state["step"], "order")
        skipped = form_mod.apply_form("order", {}, {"text": "нет"})
        self.assertEqual(skipped["step"], "order")
        state = form_mod.apply_form("order", {}, {"text": "A-100"})
        self.assertEqual(state["draft"]["order"], "A-100")
        self.assertEqual(state["step"], "problem")
        state = form_mod.apply_form("problem", state["draft"], {"text": "сломан"})
        self.assertEqual(state["step"], "idle")
        ticket = form_mod.format_ticket(state["submit"], "Иван", "ivan")
        self.assertIn("Заявка", ticket)
        self.assertIn("A-100", ticket)
        self.assertIn("сломан", ticket)
        self.assertNotIn("стол", ticket)
        self.assertTrue(tshopping.chat_id_for_ticket(111, "A-100").endswith(":a-100"))

    def test_start_does_not_store_ticket(self):
        incoming = tshopping.extract_incoming(_msg("/start"))
        with patch.object(tshopping, "_claim_update", return_value=True), \
             patch.object(tshopping, "_load_form", return_value=("idle", {})), \
             patch.object(tshopping, "_save_form"), \
             patch.object(tshopping, "load_chat", return_value=None), \
             patch.object(tshopping, "_send_bot") as send, \
             patch.object(tshopping, "_store_ticket") as store:
            result = tshopping.handle_incoming(MagicMock(), incoming, staff_ids=set())
        self.assertEqual(result["status"], "form")
        store.assert_not_called()
        send.assert_called()
        self.assertIn("оставить заявку", send.call_args[0][1].lower())
        self.assertIn("кнопк", send.call_args[0][1].lower())

    def test_poller_returns_markup_without_server_send(self):
        incoming = tshopping.extract_incoming(_msg("/start"))
        incoming["reply_via_poller"] = True
        with patch.object(tshopping, "_claim_update", return_value=True), \
             patch.object(tshopping, "_load_form", return_value=("idle", {})), \
             patch.object(tshopping, "_save_form"), \
             patch.object(tshopping, "_send_bot") as send, \
             patch.object(tshopping, "_store_ticket") as store:
            result = tshopping.handle_incoming(MagicMock(), incoming, staff_ids=set())
        store.assert_not_called()
        send.assert_not_called()
        self.assertIn("Оставить заявку", result["bot_reply"])
        self.assertTrue(result["bot_markup"]["keyboard"])

    def test_idle_existing_chat_is_follow_up(self):
        incoming = tshopping.extract_incoming(_msg("уточнение"))
        with patch.object(tshopping, "_claim_update", return_value=True), \
             patch.object(tshopping, "_load_form", return_value=("idle", {})), \
             patch.object(tshopping, "latest_chat_for_user", return_value={"chat_id": "tg:111:a-100"}), \
             patch.object(tshopping, "ingest_incoming", return_value={"status": "ok"}) as ingest:
            result = tshopping.handle_incoming(MagicMock(), incoming, staff_ids=set())
        ingest.assert_called_once()
        self.assertEqual(ingest.call_args[0][1]["ticket_chat_id"], "tg:111:a-100")
        self.assertEqual(result["status"], "ok")

    def test_problem_stores_ticket(self):
        incoming = tshopping.extract_incoming(_msg("сломан"))
        draft = {"order": "1"}
        with patch.object(tshopping, "_claim_update", return_value=True), \
             patch.object(tshopping, "_load_form", return_value=("problem", draft)), \
             patch.object(tshopping, "_save_form"), \
             patch.object(tshopping, "_send_bot"), \
             patch.object(tshopping, "_store_ticket", return_value={"status": "ok", "chat_id": "tg:111:1"}) as store:
            result = tshopping.handle_incoming(MagicMock(), incoming, staff_ids=set())
        self.assertEqual(result["status"], "ticket")
        store.assert_called_once()


class PublicWebhookRouteTests(unittest.TestCase):
    def test_webhook_is_public(self):
        from backend.routers import tshopping as router_mod
        public_paths = [getattr(r, "path", "") for r in router_mod.public_router.routes]
        auth_paths = [getattr(r, "path", "") for r in router_mod.router.routes]
        self.assertTrue(any(p.endswith("/telegram-webhook") for p in public_paths))
        self.assertFalse(any(p.endswith("/telegram-webhook") for p in auth_paths))
        self.assertTrue(any(p.endswith("/chats") for p in auth_paths))
        self.assertTrue(any("request-close" in p for p in auth_paths))


class PartnerRoleTests(unittest.TestCase):
    def test_partner_has_only_tshopping(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [("tshopping",)]
        self.assertTrue(tshopping.is_partner(db, {"role": "tshopping_partner"}))

    def test_admin_is_not_partner(self):
        self.assertFalse(tshopping.is_partner(MagicMock(), {"role": "admin"}))

    def test_cs_manager_is_not_partner(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [("tshopping",), ("cx_responses",)]
        self.assertFalse(tshopping.is_partner(db, {"role": "cs_manager"}))

    def test_serialize_waiting_and_close_flag(self):
        row = {
            "chat_id": "web:1",
            "needs_answer": False,
            "is_closed": False,
            "unread": False,
            "unread_partner": True,
            "close_requested": True,
            "order_number": "123",
        }
        data = tshopping.serialize_chat(row)
        self.assertEqual(data["waiting_for"], "partner")
        self.assertTrue(data["close_requested"])
        self.assertTrue(data["unread_partner"])

    def test_serialize_email_source(self):
        data = tshopping.serialize_chat({
            "chat_id": "mail:1",
            "source": "email",
            "display_name": "Т-Шопинг",
            "needs_answer": True,
            "is_closed": False,
            "order_number": "123",
        })
        self.assertEqual(data["source"], "email")
        self.assertIn("почта", data["contact"])
        self.assertTrue(tshopping.is_email_chat(data))
        self.assertTrue(tshopping.can_staff_close(data, partner=False))

    def test_serialize_telegram_source(self):
        data = tshopping.serialize_chat({
            "chat_id": "tg:1",
            "source": "telegram",
            "display_name": "Анна",
            "tg_chat_id": 1,
            "needs_answer": True,
            "is_closed": False,
        })
        self.assertEqual(data["source"], "telegram")
        self.assertIn("Telegram", data["contact"])
        self.assertIn("email_subject", data)

    def test_staff_cannot_close_telegram(self):
        chat = {"chat_id": "tg:1", "source": "telegram", "tg_chat_id": 1}
        self.assertFalse(tshopping.can_staff_close(chat, partner=False))
        self.assertTrue(tshopping.can_staff_close(chat, partner=True))

    def test_outbound_email_skips_telegram(self):
        chat = {
            "chat_id": "mail:1", "source": "email",
            "email_from": "lifestyle_service@emails.tinkoff.ru",
            "order_number": "99",
        }
        with patch("backend.tshopping_mail.send_reply", return_value=(True, "<id>")) as mail, \
             patch.object(telegram_proxy, "send_text") as send:
            ok, mid = tshopping.send_outbound(chat, "Ответ", [])
        self.assertTrue(ok)
        self.assertEqual(mid, "<id>")
        mail.assert_called_once()
        send.assert_not_called()


class TelegramProxyTests(unittest.TestCase):
    def test_send_text_empty(self):
        self.assertFalse(telegram_proxy.send_text(1, "  "))

    def test_call_prefers_worker(self):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"ok": True, "result": {"message_id": 9}}
        resp.text = '{"ok": true}'
        with patch.object(telegram_proxy.requests, "post", return_value=resp) as post:
            data = telegram_proxy.call("sendMessage", {"chat_id": 1, "text": "hi"})
        self.assertEqual(data["result"]["message_id"], 9)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["chat_id"], 1)
        self.assertEqual(sent["text"], "hi")
        self.assertNotIn("reply_markup", sent)
        self.assertLessEqual(post.call_args.kwargs["timeout"], 8)

    def test_send_text_keeps_worker_payload_plain(self):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
        resp.text = '{"ok": true}'
        with patch.object(telegram_proxy.requests, "post", return_value=resp) as post:
            ok = telegram_proxy.send_text(5, "hello", reply_markup={"inline_keyboard": []})
        self.assertTrue(ok)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent, {"chat_id": 5, "text": "hello", "reply_markup": {"inline_keyboard": []}})


class ReturnTemplateTests(unittest.TestCase):
    def test_suggests_pickup_and_keep(self):
        from backend import tshopping_templates as tpl
        self.assertEqual(tpl.suggest_return_id("Клиент прислал фото брака"), "return_pickup")
        self.assertEqual(tpl.suggest_return_id("Можно оформить без забора товара"), "return_keep")
        self.assertIsNone(tpl.suggest_return_id("Уточните статус заказа"))

    def test_fills_order_and_lists_three(self):
        from backend import tshopping_templates as tpl
        rows = tpl.list_return_templates("017-1")
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["id"] for r in rows}, {"return_pickup", "return_keep", "return_reject"})
        by_id = {r["id"]: r["body"] for r in rows}
        self.assertIn("Здравствуйте! Согласовываем возврат. Просим организовать забор", by_id["return_pickup"])
        self.assertIn("без забора товара", by_id["return_keep"])
        self.assertIn("не видим оснований для возврата", by_id["return_reject"])
        self.assertTrue(all(r["body"].startswith("Здравствуйте!") for r in rows))


if __name__ == "__main__":
    unittest.main()
