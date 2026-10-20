import unittest

from backend.claim_staff_tags import (
    ai_cat_ids_from_row,
    attach_staff_tag_fields,
    make_payload,
    normalize_tag_ids,
    staff_ids_from,
    tags_mismatch,
)


class NormalizeTagIdsTests(unittest.TestCase):
    def test_keeps_valid_unique_sorted(self):
        self.assertEqual(normalize_tag_ids([7, 1, 7, "4", 0, 99]), [1, 4, 7])

    def test_empty(self):
        self.assertEqual(normalize_tag_ids(None), [])
        self.assertEqual(normalize_tag_ids([]), [])


class StaffIdsFromTests(unittest.TestCase):
    def test_json_object(self):
        self.assertEqual(staff_ids_from({"ids": [7, 4], "by": "rustam"}), [4, 7])

    def test_json_string(self):
        self.assertEqual(staff_ids_from('{"ids": [12]}'), [12])

    def test_plain_list(self):
        self.assertEqual(staff_ids_from([7]), [7])


class MismatchTests(unittest.TestCase):
    def test_same_is_not_mismatch(self):
        self.assertFalse(tags_mismatch([7, 4], [4, 7]))

    def test_diff_is_mismatch(self):
        self.assertTrue(tags_mismatch([7], [4, 7]))

    def test_missing_side_is_not_mismatch(self):
        self.assertFalse(tags_mismatch([], [7]))
        self.assertFalse(tags_mismatch([7], []))


class AttachFieldsTests(unittest.TestCase):
    def test_flags_mismatch_and_ai_ids(self):
        row = attach_staff_tag_fields({
            "staff_tags": {"ids": [7], "by": "anna"},
            "cat_7": False,
            "cat_4": True,
        })
        self.assertEqual(row["staff_tag_ids"], [7])
        self.assertEqual(row["staff_by"], "anna")
        self.assertEqual(ai_cat_ids_from_row(row), [4])
        self.assertTrue(row["mismatch"])

    def test_payload_has_who_and_ids(self):
        p = make_payload([7, 99], " rustam ")
        self.assertEqual(p["ids"], [7])
        self.assertEqual(p["by"], "rustam")
        self.assertTrue(p["at"])


class DecisionPayloadSourceTests(unittest.TestCase):
    def test_decision_does_not_save_staff_tags(self):
        import inspect
        from backend.routers.cx_responses import decide_claim
        src = inspect.getsource(decide_claim)
        self.assertNotIn("_save_decision_staff_tags", src)
        self.assertNotIn("save_staff_tags", src)

    def test_moderation_queue_selects_staff_tags(self):
        import inspect
        from backend.routers.ai import get_moderation_queue
        src = inspect.getsource(get_moderation_queue)
        self.assertIn("staff_tags", src)
        self.assertIn("attach_staff_tag_fields", src)


if __name__ == "__main__":
    unittest.main()
