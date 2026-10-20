import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.routers.cx_responses import _make_ai_draft


class ReviewAiDraftQuotaTests(unittest.TestCase):
    def test_quota_message_is_human(self):
        with patch(
            "backend.llm.complete_sync",
            side_effect=RuntimeError(
                'Qwen HTTP 403: {"error":{"message":"Free quota exhausted","code":"insufficient_quota"}}'
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                _make_ai_draft(
                    item_type="review", mp="wb", body="супер", rating=5,
                    pros="", cons="", sku="", has_media="", db=None,
                )
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("бесплатный лимит", ctx.exception.detail)
        self.assertNotIn("insufficient_quota", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
