import unittest
from unittest.mock import Mock, patch

from backend import wb_client


class WbQuestionVisibilityTests(unittest.TestCase):
    @patch("backend.wb_client.requests.patch")
    def test_public_answer_uses_wbru_state(self, patch):
        patch.return_value = Mock()

        wb_client.answer_question("question-1", "Текст", public=True, key="test")

        self.assertEqual(patch.call_args.kwargs["json"]["state"], "wbRu")

    @patch("backend.wb_client.requests.patch")
    def test_private_answer_uses_none_state(self, patch):
        patch.return_value = Mock()

        wb_client.answer_question("question-1", "Текст", public=False, key="test")

        self.assertEqual(patch.call_args.kwargs["json"]["state"], "none")
