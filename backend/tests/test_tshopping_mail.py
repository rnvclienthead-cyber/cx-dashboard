import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import tshopping_mail as mail


def _raw(**kwargs):
    msg = EmailMessage()
    msg["From"] = kwargs.get("frm", "Lifestyle <lifestyle_service@emails.tinkoff.ru>")
    msg["To"] = kwargs.get("to", "help@vidovito.team")
    msg["Subject"] = kwargs.get("subject", "Обращение по заказу 1234567890")
    msg["Message-ID"] = kwargs.get("mid", "<tinkoff-1@emails.tinkoff.ru>")
    if kwargs.get("reply_to"):
        msg["Reply-To"] = kwargs["reply_to"]
    if kwargs.get("in_reply_to"):
        msg["In-Reply-To"] = kwargs["in_reply_to"]
    if kwargs.get("html"):
        msg.set_content("plain fallback")
        msg.add_alternative(kwargs["html"], subtype="html")
    else:
        msg.set_content(kwargs.get("body", "Клиент пишет: сломана ножка.\nНомер заказа: 1234567890"))
    return msg.as_bytes()


class SenderFilterTests(unittest.TestCase):
    def test_tbank_reply_allowed(self):
        self.assertTrue(mail.sender_allowed("lifestyle_service@tbank.ru"))
        self.assertTrue(mail.sender_allowed("ls_service@tbank.ru"))

    def test_tshopping_folder_is_encoded(self):
        self.assertEqual(
            mail.imap_utf7_encode("Т-шопинг"),
            "&BCI--&BEgEPgQ,BDgEPQQz-",
        )
        folders = mail.mail_folders()
        self.assertIn("INBOX", folders)
        self.assertIn("&BCI--&BEgEPgQ,BDgEPQQz-", folders)

    def test_b2b_skipped(self):
        parsed = mail.parse_rfc822(_raw(
            frm="Шопинг <lifestyle_b2b@emails.tbank.ru>",
            subject="Возвраты из Шопинга ожидают в ПВЗ",
        ))
        self.assertEqual(parsed["skip"], "sender")

    def test_other_skipped(self):
        parsed = mail.parse_rfc822(_raw(frm="client@gmail.com", subject="заказ 1"))
        self.assertEqual(parsed["skip"], "sender")

    def test_own_mailbox_skipped(self):
        parsed = mail.parse_rfc822(_raw(frm="Видовито <help@vidovito.team>"))
        self.assertEqual(parsed["skip"], "self")


class OrderExtractTests(unittest.TestCase):
    def test_from_subject_and_body(self):
        parsed = mail.parse_rfc822(_raw())
        self.assertEqual(parsed["skip"], "")
        self.assertEqual(parsed["order_number"], "1234567890")
        self.assertIn("сломана", parsed["body"])

    def test_html_only(self):
        html = "<html><body><p>Здравствуйте</p><p>Заказ № 99887766</p><p>Нет болтов</p></body></html>"
        parsed = mail.parse_rfc822(_raw(subject="Заявка", body="x", html=html))
        self.assertEqual(parsed["order_number"], "99887766")
        self.assertIn("Нет болтов", parsed["body"])

    def test_extract_helpers(self):
        self.assertEqual(mail.extract_order_number("Тема", "номер заказа: AB-1001"), "AB-1001")
        self.assertEqual(mail.extract_order_number("без номера"), "")

    def test_prefers_shop_order_not_ticket_id(self):
        subject = "Обращение по заказу 468152510480 / 0173340967-0001-1 видовито"
        body = "Номер заказа: 0173340967-0001-1\nСуть обращения:\nУточните причину отмены"
        self.assertEqual(mail.extract_order_number(subject, body), "0173340967-0001-1")


class ReplyBuildTests(unittest.TestCase):
    def test_headers_thread_and_from(self):
        chat = {
            "email_from": "lifestyle_service@emails.tinkoff.ru",
            "email_reply_to": "lifestyle_service@emails.tinkoff.ru",
            "email_subject": "Обращение по заказу 55",
            "last_inbound_message_id": "<tinkoff-1@emails.tinkoff.ru>",
            "order_number": "55",
        }
        with patch.dict("os.environ", {
            "TSHOPPING_MAIL_USER": "help@vidovito.team",
            "TSHOPPING_MAIL_PASS": "secret",
            "TSHOPPING_MAIL_FROM": "help@vidovito.team",
        }, clear=False):
            msg = mail.build_reply_message(
                chat=chat, body="Сделаем", attachments=[], upload_root=Path("/tmp"),
            )
        self.assertIn("help@vidovito.team", str(msg["From"]))
        self.assertEqual(msg["To"], "lifestyle_service@tbank.ru")
        self.assertEqual(msg["In-Reply-To"], "<tinkoff-1@emails.tinkoff.ru>")
        self.assertTrue(str(msg["Subject"]).lower().startswith("re:"))
        self.assertIn("По заказу 55", msg.get_content())
        self.assertIn("Сделаем", msg.get_content())
        self.assertTrue(msg["Date"])


class SentCopyTests(unittest.TestCase):
    def test_picks_imap_sent_folder(self):
        lines = [
            b'(\\HasNoChildren) "|" INBOX',
            b'(\\HasNoChildren \\Unmarked \\Sent) "|" Sent',
        ]
        self.assertEqual(mail.sent_mailbox(lines), "Sent")

    def test_append_sent_copy_uses_seen_flag(self):
        client = MagicMock()
        client.list.return_value = ("OK", [b'(\\Sent) "|" Sent'])
        client.append.return_value = ("OK", [b""])
        msg = EmailMessage()
        msg["From"] = "help@vidovito.team"
        msg["To"] = "lifestyle_service@tbank.ru"
        msg.set_content("hi")
        with patch.object(mail, "configured", return_value=True), \
             patch.object(mail, "_login_imap", return_value=client):
            self.assertTrue(mail.append_sent_copy(msg))
        folder, flags, _when, raw = client.append.call_args[0]
        self.assertEqual(folder, "Sent")
        self.assertIn("Seen", flags)
        self.assertIn(b"hi", raw)


class ReferenceTests(unittest.TestCase):
    def test_collects_ids(self):
        ids = mail.reference_ids({
            "in_reply_to": "<a@x>",
            "references": "<b@x> <c@x>",
        })
        self.assertEqual(ids[0], "<a@x>")
        self.assertIn("<b@x>", ids)

    def test_strips_bcc_prefix(self):
        ids = mail.reference_ids({
            "in_reply_to": "<BCC.20260914135141.0@tinkoff.pg>",
            "references": "",
        })
        self.assertIn("<BCC.20260914135141.0@tinkoff.pg>", ids)
        self.assertIn("<20260914135141.0@tinkoff.pg>", ids)

    def test_ingest_groups_closed_orders(self):
        import inspect
        src = inspect.getsource(mail.ingest_parsed)
        self.assertIn("include_closed=True", src)


class InboxFlagsTests(unittest.TestCase):
    def test_search_uses_since_not_from_charset(self):
        client = MagicMock()
        client.uid.return_value = ("OK", [b"10 11"])
        uids = mail._search_uids(client)
        self.assertEqual(uids, [10, 11])
        args = client.uid.call_args[0]
        self.assertEqual(args[0], "SEARCH")
        self.assertEqual(args[1], "SINCE")
        self.assertNotIn("UTF-8", args)
        self.assertNotIn("FROM", args)

    def test_store_seen_dedupes_uids(self):
        client = MagicMock()
        client.uid.return_value = ("OK", [b""])
        with patch.object(mail, "_connect_imap", return_value=client), \
             patch.object(mail, "configured", return_value=True):
            n = mail.flag_uids_seen([11, 10, 11, None])
        self.assertEqual(n, 2)
        args = client.uid.call_args[0]
        self.assertEqual(args[0], "STORE")
        self.assertEqual(args[1], "10,11")
        self.assertIn("Seen", args[3])


class AttachmentReplyTests(unittest.TestCase):
    def test_reply_attaches_file(self):
        from tempfile import TemporaryDirectory
        chat = {
            "email_from": "lifestyle_service@tbank.ru",
            "email_subject": "Заказ 1",
            "order_number": "1",
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.jpg").write_bytes(b"jpeg")
            with patch.dict("os.environ", {
                "TSHOPPING_MAIL_FROM": "help@vidovito.team",
            }, clear=False):
                msg = mail.build_reply_message(
                    chat=chat,
                    body="Фото",
                    attachments=[{"filename": "a.jpg", "name": "photo.jpg", "mime": "image/jpeg"}],
                    upload_root=root,
                )
        payload = msg.as_string()
        self.assertIn("help@vidovito.team", str(msg["From"]))
        self.assertIn("photo.jpg", payload)


if __name__ == "__main__":
    unittest.main()
