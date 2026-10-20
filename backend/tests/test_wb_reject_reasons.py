import unittest

from backend.wb_claims_api import (
    WB_REJECT_REASONS,
    comment_for_reject_reason,
    map_actions,
)


class WbRejectReasonsTests(unittest.TestCase):
    def test_comment_comes_from_selected_reason(self):
        self.assertIn("не подтверждена", comment_for_reject_reason("not_confirmed"))
        self.assertEqual(comment_for_reject_reason(""), "")
        self.assertEqual(comment_for_reject_reason("unknown", "свой текст"), "свой текст")

    def test_rejectcustom_asks_for_reason_not_free_text(self):
        mapped = map_actions(["rejectcustom"])
        self.assertEqual(len(mapped), 1)
        self.assertFalse(mapped[0]["needs_comment"])
        values = [r["value"] for r in mapped[0]["reason_types"]]
        self.assertEqual(values, [r["value"] for r in WB_REJECT_REASONS])


if __name__ == "__main__":
    unittest.main()
