import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests
from fastapi import HTTPException

from backend import ozon_client, wb_client
from backend.routers import cx_responses
from backend.upload_guard import ChatAttachmentContentLengthMiddleware


class DummyUpload:
    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self._content = content
        self.content_type = content_type
        self.read_calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return self._content if size < 0 else self._content[:size]


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return Mock()

    def commit(self):
        self.commits += 1


class RecordingDb:
    def __init__(self):
        self.connection = RecordingConnection()
        self.bind = Mock()
        self.bind.connect.return_value = self.connection


class ChatAttachmentValidationTests(unittest.TestCase):
    def validate(self, *files):
        return asyncio.run(cx_responses._validate_chat_attachments(list(files)))

    def test_accepts_png_and_sanitizes_filename(self):
        upload = DummyUpload("../screen.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")
        result = self.validate(upload)
        self.assertEqual(result[0]["name"], "screen.png")
        self.assertEqual(result[0]["content_type"], "image/png")

    def test_accepts_empty_declared_mime_when_magic_and_extension_match(self):
        result = self.validate(DummyUpload("scan.pdf", b"%PDF-1.7\n", ""))
        self.assertEqual(result[0]["content_type"], "application/pdf")

    def test_rejects_file_larger_than_five_mib(self):
        content = b"\xff\xd8\xff" + b"0" * (cx_responses._CHAT_ATTACHMENT_MAX_BYTES - 2)
        with self.assertRaises(HTTPException) as caught:
            self.validate(DummyUpload("large.jpg", content, "image/jpeg"))
        self.assertEqual(caught.exception.status_code, 413)

    def test_rejects_declared_type_that_does_not_match_content(self):
        with self.assertRaises(HTTPException) as caught:
            self.validate(DummyUpload("fake.png", b"%PDF-1.7\n", "image/png"))
        self.assertEqual(caught.exception.status_code, 415)

    def test_rejects_count_bomb_before_reading_files(self):
        uploads = [DummyUpload(f"{index}.png", b"\x89PNG\r\n\x1a\n", "image/png") for index in range(7)]
        with self.assertRaises(HTTPException) as caught:
            self.validate(*uploads)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertTrue(all(upload.read_calls == 0 for upload in uploads))

    def test_rejects_total_size_over_limit(self):
        uploads = [DummyUpload(f"{index}.png", b"\x89PNG\r\n\x1a\n", "image/png") for index in range(3)]
        with (
            patch.object(cx_responses, "_CHAT_ATTACHMENT_MAX_BYTES", 12),
            patch.object(cx_responses, "_CHAT_ATTACHMENT_MAX_TOTAL_BYTES", 20),
            self.assertRaises(HTTPException) as caught,
        ):
            self.validate(*uploads)
        self.assertEqual(caught.exception.status_code, 413)


class MarketplaceClientPayloadTests(unittest.TestCase):
    def test_route_rejects_count_bomb_before_marketplace_or_file_reads(self):
        uploads = [DummyUpload(f"{index}.png", b"\x89PNG\r\n\x1a\n", "image/png") for index in range(7)]
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(cx_responses.send_ozon_chat_message_with_files(
                "chat", "", uploads, Mock(), {"username": "manager"}
            ))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertTrue(all(upload.read_calls == 0 for upload in uploads))

    def test_route_rejects_total_size_before_marketplace(self):
        uploads = [DummyUpload(f"{index}.png", b"\x89PNG\r\n\x1a\n", "") for index in range(3)]
        with (
            patch.object(cx_responses, "_CHAT_ATTACHMENT_MAX_BYTES", 12),
            patch.object(cx_responses, "_CHAT_ATTACHMENT_MAX_TOTAL_BYTES", 20),
            self.assertRaises(HTTPException) as caught,
        ):
            asyncio.run(cx_responses.send_ozon_chat_message_with_files(
                "chat", "", uploads, Mock(), {"username": "manager"}
            ))
        self.assertEqual(caught.exception.status_code, 413)

    def test_route_rejects_mime_extension_magic_mismatch(self):
        upload = DummyUpload("photo.png", b"%PDF-1.7\n", "image/png")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(cx_responses.send_ozon_chat_message_with_files(
                "chat", "", [upload], Mock(), {"username": "manager"}
            ))
        self.assertEqual(caught.exception.status_code, 415)

    @patch("backend.wb_client.http_client.request")
    def test_wb_sends_repeated_multipart_file_fields(self, req_mock):
        response = Mock()
        req_mock.return_value = response

        wb_client.send_chat_message(
            "reply-sign",
            "Текст",
            attachments=[
                ("one.png", b"png", "image/png"),
                ("two.pdf", b"pdf", "application/pdf"),
            ],
            key="token",
        )

        multipart = req_mock.call_args.kwargs["files"]
        self.assertEqual([name for name, _ in multipart], ["replySign", "message", "file", "file"])
        self.assertEqual(multipart[2][1], ("one.png", b"png", "image/png"))
        response.raise_for_status.assert_called_once_with()

    @patch("backend.wb_client.http_client.request")
    def test_wb_allows_file_without_text(self, req_mock):
        req_mock.return_value = Mock()
        wb_client.send_chat_message(
            "reply-sign",
            attachments=[("manual.pdf", b"pdf", "application/pdf")],
            key="token",
        )
        multipart = req_mock.call_args.kwargs["files"]
        self.assertEqual([name for name, _ in multipart], ["replySign", "file"])

    @patch("backend.ozon_client.post")
    @patch("backend.ozon_client.api_key", return_value="key")
    @patch("backend.ozon_client.client_id", return_value="client")
    def test_ozon_file_payload_matches_api_contract(self, _client_id, _api_key, post_mock):
        ozon_client.send_chat_file("chat-1", "manual.pdf", "YmFzZTY0", timeout=17)
        post_mock.assert_called_once_with(
            "/v1/chat/send/file",
            {"chat_id": "chat-1", "name": "manual.pdf", "base64_content": "YmFzZTY0"},
            timeout=17,
        )

    def test_ozon_routes_text_before_each_file_and_returns_partial_progress(self):
        calls = []
        persisted = []
        attachments = [
            {"name": "a.png", "content": b"a", "content_type": "image/png"},
            {"name": "b.pdf", "content": b"b", "content_type": "application/pdf"},
            {"name": "c.jpg", "content": b"c", "content_type": "image/jpeg"},
        ]
        def send_file(cid, name, content, timeout):
            calls.append(("file", cid, name, content))
            if name == "b.pdf":
                raise requests.HTTPError("rejected")

        async def inline_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch("backend.ozon_chat_sync.send_message", side_effect=lambda cid, text: calls.append(("text", cid, text))),
            patch("backend.routers.cx_responses.ozon_client.send_chat_file", side_effect=send_file),
            patch("backend.routers.cx_responses.run_in_threadpool", new=AsyncMock(side_effect=inline_threadpool)) as offload,
        ):
            progress = asyncio.run(cx_responses._run_ozon_chat_send_steps(
                "chat-2",
                "Сначала текст",
                attachments,
                lambda kind, value: persisted.append((kind, value)),
            ))

        self.assertEqual([call[0] for call in calls], ["text", "file", "file"])
        self.assertEqual(calls[1], ("file", "chat-2", "a.png", "YQ=="))
        self.assertEqual(calls[2], ("file", "chat-2", "b.pdf", "Yg=="))
        self.assertEqual(persisted, [("text", "Сначала текст"), ("file", "a.png")])
        self.assertEqual(progress["status"], "partial")
        self.assertEqual(progress["text"]["state"], "confirmed")
        self.assertEqual(
            [step["state"] for step in progress["files"]],
            ["confirmed", "failed", "not_attempted"],
        )
        self.assertGreaterEqual(offload.await_count, 5)

    def test_ozon_timeout_is_unknown_and_never_attempts_files(self):
        attachments = [{"name": "a.png", "content": b"a", "content_type": "image/png"}]
        with patch("backend.ozon_chat_sync.send_message", side_effect=requests.Timeout("late response")):
            progress = asyncio.run(cx_responses._run_ozon_chat_send_steps(
                "chat-3", "Текст", attachments, lambda *_args: None
            ))
        self.assertEqual(progress["status"], "uncertain")
        self.assertEqual(progress["text"]["state"], "unknown")
        self.assertEqual(progress["files"][0]["state"], "not_attempted")

    def test_db_failure_stops_after_confirmed_marketplace_step(self):
        attachments = [{"name": "a.png", "content": b"a", "content_type": "image/png"}]
        with patch("backend.ozon_chat_sync.send_message", return_value=None):
            progress = asyncio.run(cx_responses._run_ozon_chat_send_steps(
                "chat-4",
                "Текст",
                attachments,
                lambda *_args: (_ for _ in ()).throw(RuntimeError("db down")),
            ))
        self.assertEqual(progress["text"]["state"], "confirmed")
        self.assertEqual(progress["files"][0]["state"], "not_attempted")
        self.assertEqual(progress["persistence"], "failed")

    def test_confirmed_step_persistence_never_inserts_synthetic_history(self):
        db = RecordingDb()
        cx_responses._persist_confirmed_chat_step(
            db, mp="ozon", chat_id="chat", username="manager", kind="file", value="manual.pdf"
        )
        sql = "\n".join(statement for statement, _ in db.connection.statements).lower()
        self.assertIn("cx_response_log", sql)
        self.assertNotIn("insert into ozon_chat_messages", sql)
        self.assertNotIn("insert into wb_chat_messages", sql)

    def test_typed_attachment_mapping_keeps_pdf_out_of_images(self):
        wb_items = cx_responses._extract_wb_attachments({
            "images": [{"downloadID": "image-id", "name": "photo.jpg"}],
            "files": [{"downloadID": "pdf-id", "name": "manual.pdf"}],
        })
        self.assertEqual([item["type"] for item in wb_items], ["image", "file"])
        ozon_items = cx_responses._typed_ozon_attachments(
            "https://api-seller.ozon.ru/v2/chat/file/document.pdf", False, []
        )
        self.assertEqual(ozon_items[0]["type"], "file")

    def test_ozon_route_returns_machine_readable_207(self):
        progress = {
            "status": "partial",
            "text": {"state": "confirmed"},
            "files": [{"index": 0, "name": "a.png", "state": "failed"}],
            "warnings": [],
            "persistence": "confirmed",
        }
        upload = DummyUpload("a.png", b"\x89PNG\r\n\x1a\n", "image/png")
        with (
            patch.object(cx_responses, "_oz_key", return_value="key"),
            patch.object(cx_responses, "_oz_cid", return_value="cid"),
            patch.object(cx_responses, "_run_ozon_chat_send_steps", new=AsyncMock(return_value=progress)),
        ):
            response = asyncio.run(cx_responses.send_ozon_chat_message_with_files(
                "chat", "text", [upload], Mock(), {"username": "manager"}
            ))
        self.assertEqual(response.status_code, 207)
        self.assertEqual(json.loads(response.body)["files"][0]["state"], "failed")

    def test_wb_route_offloads_sync_client_and_returns_progress(self):
        upload = DummyUpload("a.png", b"\x89PNG\r\n\x1a\n", "image/png")

        async def inline_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch.object(cx_responses, "_wb_key", return_value="key"),
            patch.object(cx_responses, "_resolve_wb_reply_sign", return_value="reply"),
            patch.object(cx_responses.wb_client, "send_chat_message", return_value=Mock()) as send_message,
            patch.object(cx_responses, "_persist_confirmed_chat_step", return_value=None),
            patch.object(cx_responses, "run_in_threadpool", new=AsyncMock(side_effect=inline_threadpool)) as offload,
        ):
            response = asyncio.run(cx_responses.send_wb_chat_message_with_files(
                "chat", "", [upload], Mock(), {"username": "manager"}
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["files"][0]["state"], "confirmed")
        send_message.assert_called_once()
        self.assertGreaterEqual(offload.await_count, 3)

    def test_yandex_attachment_route_sends_files(self):
        upload = DummyUpload("a.png", b"\x89PNG\r\n\x1a\n", "image/png")
        progress = {
            "status": "complete",
            "text": {"state": "not_requested"},
            "files": [{"index": 0, "name": "a.png", "state": "confirmed"}],
            "warnings": [],
            "persistence": "confirmed",
        }
        with (
            patch.object(cx_responses, "_ym_key", return_value="key"),
            patch.object(cx_responses, "_ym_business_id", return_value="biz"),
            patch.object(cx_responses, "_run_ym_chat_send_steps", new=AsyncMock(return_value=progress)),
        ):
            response = asyncio.run(cx_responses.send_ym_chat_message_with_files(
                "ym-chat", "", [upload], Mock(), {"username": "manager"}
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["files"][0]["state"], "confirmed")

    def test_ym_client_send_chat_file_posts_multipart(self):
        from backend import ym_client

        with patch.object(ym_client.requests, "post") as post_mock:
            post_mock.return_value = Mock(status_code=200, raise_for_status=Mock())
            ym_client.send_chat_file(
                "42", "photo.png", b"\x89PNG\r\n\x1a\n",
                content_type="image/png", biz="99", key="secret",
            )
        self.assertEqual(
            post_mock.call_args.args[0],
            "https://api.partner.market.yandex.ru/v2/businesses/99/chats/file/send",
        )
        self.assertEqual(post_mock.call_args.kwargs["params"]["chatId"], 42)
        self.assertNotIn("Content-Type", post_mock.call_args.kwargs["headers"])
        self.assertIn("file", post_mock.call_args.kwargs["files"])


class UploadGuardTests(unittest.TestCase):
    def test_rejects_oversized_content_length_before_downstream(self):
        downstream_called = False

        async def downstream(_scope, _receive, _send):
            nonlocal downstream_called
            downstream_called = True

        middleware = ChatAttachmentContentLengthMiddleware(downstream, max_body_bytes=100)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(middleware({
            "type": "http",
            "method": "POST",
            "path": "/api/v1/cx-responses/ozon/chats/id/message-with-files",
            "headers": [(b"content-length", b"101")],
        }, receive, send))
        self.assertFalse(downstream_called)
        self.assertEqual(sent[0]["status"], 413)


class YmChatAttachmentDisplayTests(unittest.TestCase):
    def test_proxy_covers_messenger_host(self):
        url = cx_responses._ym_proxy_or_direct(
            "https://files.messenger.yandex.net/file/70d368d2-a6c4-ec8f-81ad-7dd3f4d9886e"
        )
        self.assertIn("/chat-download/ym?url=", url)
        self.assertIn("files.messenger.yandex.net", url)

    def test_newline_photo_stays_image(self):
        items = cx_responses._typed_ym_attachments(
            "https://avatars.mds.yandex.net/get-market-chat/123/photo.jpg"
        )
        self.assertEqual(items[0]["type"], "image")
        self.assertIn("/chat-download/ym?url=", items[0]["url"])

    def test_json_payload_keeps_name(self):
        items = cx_responses._typed_ym_attachments(
            '[{"url":"https://avatars.mds.yandex.net/get-market-chat/1/a.png","name":"a.png","size":12}]'
        )
        self.assertEqual(items[0]["type"], "image")
        self.assertEqual(items[0]["name"], "a.png")

    def test_messenger_video_is_not_shown_as_image(self):
        with patch.object(cx_responses, "_sniff_ym_content_type", return_value="video/mp4") as sniff:
            items = cx_responses._typed_ym_attachments(
                "https://files.messenger.yandex.net/file/70d368d2-a6c4-ec8f-81ad-7dd3f4d9886e"
            )
        sniff.assert_called_once()
        self.assertEqual(items[0]["type"], "video")
        self.assertEqual(items[0]["name"], "Видео из чата")
        self.assertIn("/chat-download/ym?url=", items[0]["url"])


if __name__ == "__main__":
    unittest.main()
