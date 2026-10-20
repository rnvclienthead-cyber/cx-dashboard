import unittest

from backend.claim_auto_reject import (
    COMMENT_LINK,
    COMMENT_OFFERED,
    THREE_DAYS_SEC,
    effective_kind,
    reject_comment,
    should_auto_reject,
)


class ShouldAutoRejectTests(unittest.TestCase):
    def test_link_does_not_auto_reject_until_reshipment_approved(self):
        self.assertIsNone(should_auto_reject("link", age_sec=0, client_replied=True))
        self.assertIsNone(should_auto_reject("link", age_sec=0, client_replied=False))

    def test_offered_only_after_three_days_without_reply(self):
        self.assertIsNone(should_auto_reject("offered", age_sec=THREE_DAYS_SEC, client_replied=False))
        self.assertEqual(
            should_auto_reject("offered", age_sec=THREE_DAYS_SEC + 1, client_replied=False),
            "offered_stale",
        )
        self.assertIsNone(should_auto_reject("offered", age_sec=THREE_DAYS_SEC + 1, client_replied=True))

    def test_question_not_in_rule_unless_offer_text(self):
        self.assertIsNone(should_auto_reject("question", age_sec=10 * THREE_DAYS_SEC, client_replied=False))
        self.assertIsNone(should_auto_reject("none", age_sec=10 * THREE_DAYS_SEC, client_replied=False))
        self.assertEqual(
            effective_kind("question", ["Можем отправить вам недостающую деталь — доставим бесплатно."]),
            "offered",
        )
        self.assertEqual(
            effective_kind("question", ["Отдельно выслать её по этому товару у нас нет возможности."]),
            "question",
        )
        self.assertEqual(effective_kind("link", ["что угодно"]), "link")

    def test_filled_form_closes_return_only_after_approve(self):
        from backend.claim_auto_reject import form_claim_action
        self.assertEqual(form_claim_action("approved"), "reject")
        self.assertIsNone(form_claim_action("new"))
        self.assertIsNone(form_claim_action("matched"))
        self.assertEqual(form_claim_action("delivered"), "reject")
        self.assertEqual(form_claim_action("rejected"), "approve")
        self.assertIsNone(form_claim_action(""))

    def test_comments(self):
        self.assertGreaterEqual(len(reject_comment("link")), 10)
        self.assertIn("доотправ", COMMENT_LINK.lower())
        self.assertIn("чат", COMMENT_OFFERED.lower())

    def test_auto_comment_detector(self):
        from backend.claim_auto_reject import is_auto_reject_comment
        self.assertTrue(is_auto_reject_comment(COMMENT_LINK))
        self.assertTrue(is_auto_reject_comment(COMMENT_OFFERED))
        self.assertFalse(is_auto_reject_comment("Клиент отменил заявку"))
        self.assertFalse(is_auto_reject_comment(""))

    def test_link_comment_requires_approved_form(self):
        import inspect
        from backend.claim_auto_reject import collect_reject_targets
        src = inspect.getsource(collect_reject_targets)
        self.assertIn("if reason == \"link\" and form_action != \"reject\"", src)


if __name__ == "__main__":
    unittest.main()
