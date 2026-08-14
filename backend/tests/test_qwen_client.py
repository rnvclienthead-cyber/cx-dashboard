import json
import unittest
from unittest.mock import AsyncMock, patch

from backend.qwen_client import (
    QwenClient,
    QwenError,
    execute_tool,
    extract_phones,
    mask_pii,
    openai_tool_schemas,
)


class MaskPiiTests(unittest.TestCase):
    def test_masks_russian_phone_and_email(self):
        text = "Позвоните +7 (999) 123-45-67 или пишите anna@vidovito.team"
        masked = mask_pii(text)
        self.assertIn("[ТЕЛЕФОН]", masked)
        self.assertIn("[EMAIL]", masked)
        self.assertNotIn("999", masked)
        self.assertNotIn("anna@", masked)

    def test_masks_digits_only_phone(self):
        masked = mask_pii("тел 79991234567")
        self.assertIn("[ТЕЛЕФОН]", masked)
        self.assertNotRegex(masked, r"\d{10}")

    def test_extract_phones_keeps_digits_for_tools(self):
        phones = extract_phones("Клиент 8 999 123-45-67 ждёт")
        self.assertTrue(phones)
        self.assertGreaterEqual(len(phones[0]), 10)


class ToolRegistryTests(unittest.TestCase):
    def test_schemas_include_safe_db_tools(self):
        names = {item["function"]["name"] for item in openai_tool_schemas()}
        self.assertIn("get_client_info", names)
        self.assertIn("get_kb_article", names)

    def test_unknown_tool_returns_json_error(self):
        payload = json.loads(execute_tool("drop_table", {}))
        self.assertIn("error", payload)


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.payloads = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        self.payloads.append(json)
        return self._responses.pop(0)


class QwenClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_on_503_then_succeeds(self):
        session = _FakeSession([
            _FakeResponse(503, "busy"),
            _FakeResponse(200, json.dumps({
                "choices": [{"message": {"content": '{"ok": true}'}}]
            })),
        ])
        client = QwenClient(base_url="http://gpu.local/v1", api_key="k", model="qwen-plus")
        with patch("backend.qwen_client.asyncio.sleep", new=AsyncMock()):
            text = await client.chat(
                system="sys",
                messages=[{"role": "user", "content": "привет"}],
                session=session,
                mask_user_text=False,
            )
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(session.calls, 2)

    async def test_json_object_format_and_tool_loop(self):
        tool_call = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {
                            "name": "get_kb_article",
                            "arguments": '{"query": "нет болтов"}',
                        },
                    }],
                }
            }]
        }
        final = {
            "choices": [{
                "message": {"content": '{"results": [{"id": "REF_0", "category_ids": [1]}]}'}
            }]
        }
        session = _FakeSession([
            _FakeResponse(200, json.dumps(tool_call)),
            _FakeResponse(200, json.dumps(final)),
        ])
        client = QwenClient(base_url="http://gpu.local/v1", model="qwen-plus")
        with patch("backend.qwen_client.execute_tool", return_value='{"found": true}'):
            data = await client.chat_json(
                system="верни json",
                messages=[{"role": "user", "content": "нет болтов"}],
                tools=["get_kb_article"],
                session=session,
                mask_user_text=False,
            )
        self.assertEqual(data["results"][0]["category_ids"], [1])
        self.assertEqual(session.payloads[0]["response_format"], {"type": "json_object"})
        self.assertEqual(session.payloads[1]["messages"][-1]["role"], "tool")

    async def test_4xx_does_not_retry(self):
        session = _FakeSession([_FakeResponse(401, "no key")])
        client = QwenClient(base_url="http://gpu.local/v1", api_key="bad")
        with self.assertRaises(QwenError):
            await client.chat(messages=[{"role": "user", "content": "x"}], session=session)
        self.assertEqual(session.calls, 1)


if __name__ == "__main__":
    unittest.main()
