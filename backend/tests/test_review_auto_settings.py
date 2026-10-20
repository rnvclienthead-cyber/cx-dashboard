import unittest
from unittest.mock import Mock, patch

from backend.routers.cx_responses import (
    parse_review_auto_flags,
    parse_review_auto_scenarios,
    _run_auto_replies,
)


class ParseReviewAutoFlagsTests(unittest.TestCase):
    def test_defaults_off(self):
        self.assertEqual(
            parse_review_auto_flags([]),
            {"wb": False, "ym": False, "ozon": False},
        )

    def test_true_false_and_unknown_keys(self):
        flags = parse_review_auto_flags([
            {"key": "auto_wb", "value": "true"},
            {"key": "auto_ym", "value": "false"},
            {"key": "auto_ozon", "value": "ON"},
            {"key": "auto_other", "value": "true"},
        ])
        self.assertEqual(flags, {"wb": True, "ym": False, "ozon": True})

    def test_tuple_rows(self):
        flags = parse_review_auto_flags([("auto_wb", "1"), ("auto_ym", "yes")])
        self.assertTrue(flags["wb"])
        self.assertTrue(flags["ym"])
        self.assertFalse(flags["ozon"])


class ParseReviewAutoScenariosTests(unittest.TestCase):
    def test_defaults_on_when_keys_missing(self):
        self.assertEqual(
            parse_review_auto_scenarios([]),
            {"thanks_5": True, "thanks_4": True, "sorry": True},
        )
        self.assertEqual(
            parse_review_auto_scenarios([{"key": "auto_wb", "value": "true"}]),
            {"thanks_5": True, "thanks_4": True, "sorry": True},
        )

    def test_partial_override(self):
        flags = parse_review_auto_scenarios([
            {"key": "auto_sc_thanks_5", "value": "false"},
            {"key": "auto_sc_sorry", "value": "1"},
        ])
        self.assertEqual(flags, {"thanks_5": False, "thanks_4": True, "sorry": True})


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def scalar(self):
        return 0


class _Conn:
    def __init__(self, flags, templates):
        self.flags = flags
        self.templates = templates
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "cx_review_settings" in sql:
            return _Result([{"key": k, "value": v} for k, v in self.flags.items()])
        if "cx_response_templates" in sql:
            return _Result(self.templates)
        return _Result([])

    def commit(self):
        pass


class _Db:
    def __init__(self, conn):
        self.bind = Mock()
        self.bind.connect.return_value = conn


class RunAutoRepliesGateTests(unittest.TestCase):
    def test_all_off_does_not_send(self):
        conn = _Conn(
            flags={"auto_wb": "false", "auto_ym": "false", "auto_ozon": "false"},
            templates=[{
                "id": 1, "title": "t", "body": "ok", "mp": "all", "item_type": "review",
                "min_rating": 5, "max_rating": 5, "require_no_text": True,
                "stop_words": "", "ignore_questions": False, "requires_confirmation": False,
            }],
        )
        with patch("backend.routers.cx_responses.wb_client") as wb, \
             patch("backend.routers.cx_responses._wb_key", return_value="k"):
            result = _run_auto_replies(_Db(conn))
        self.assertTrue(result.get("disabled"))
        self.assertEqual(result["sent"], 0)
        wb.answer_feedback.assert_not_called()

    def test_wb_on_queries_feedbacks(self):
        conn = _Conn(
            flags={"auto_wb": "true", "auto_ym": "false", "auto_ozon": "false"},
            templates=[],
        )
        with patch("backend.routers.cx_responses.wb_client") as wb, \
             patch("backend.routers.cx_responses._wb_key", return_value="k"), \
             patch("backend.routers.cx_responses._ym_key", return_value=""), \
             patch("backend.routers.cx_responses._oz_key", return_value=""):
            wb.answer_feedback.side_effect = RuntimeError("no network")
            result = _run_auto_replies(_Db(conn))
        self.assertFalse(result.get("disabled"))
        self.assertTrue(any("wb_feedbacks" in s for s in conn.statements))
        self.assertFalse(any("ym_feedbacks" in s for s in conn.statements))
        self.assertFalse(any("cx_response_templates" in s for s in conn.statements))


class AdminGuardSourceTests(unittest.TestCase):
    def test_auto_settings_and_templates_require_admin(self):
        import inspect
        from backend.routers import cx_responses
        for fn in (
            cx_responses.update_review_auto_settings,
            cx_responses.create_template,
            cx_responses.update_template,
            cx_responses.delete_template,
            cx_responses.run_auto_replies,
        ):
            self.assertIn("_require_admin", inspect.getsource(fn), fn.__name__)

    def test_all_scenarios_off_does_not_send(self):
        conn = _Conn(
            flags={
                "auto_wb": "true",
                "auto_sc_thanks_5": "false",
                "auto_sc_thanks_4": "false",
                "auto_sc_sorry": "false",
            },
            templates=[],
        )
        with patch("backend.routers.cx_responses.wb_client") as wb, \
             patch("backend.routers.cx_responses._wb_key", return_value="k"):
            result = _run_auto_replies(_Db(conn))
        self.assertTrue(result.get("disabled"))
        self.assertEqual(result["sent"], 0)
        wb.answer_feedback.assert_not_called()
        self.assertFalse(any("wb_feedbacks" in s for s in conn.statements))


class StatsItemTypeSqlTests(unittest.TestCase):
    def test_stats_endpoint_accepts_item_type(self):
        import inspect
        from backend.routers.cx_responses import get_stats
        src = inspect.getsource(get_stats)
        self.assertIn("item_type", src)
        self.assertIn("auto_reply%", src)
        self.assertIn("AND item_type = :item_type", src)


class ArchiveFilterSqlTests(unittest.TestCase):
    def test_archive_accepts_text_search_and_rating(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text()
        archive = src[src.index("def get_archive"):src.index("class DismissPayload")]
        self.assertIn('q: str = Query("")', archive)
        self.assertIn("COALESCE(a.body,'') ILIKE :q", archive)
        self.assertIn('ratings: str = Query("")', archive)
        self.assertIn("a.rating = ANY(:ratings)", archive)
        self.assertIn("LEFT JOIN wb_feedbacks wf", archive)
        self.assertIn("LEFT JOIN ym_feedbacks yf", archive)
        self.assertIn("LEFT JOIN ozon_reviews oz", archive)


class ReviewProductPhotoTests(unittest.TestCase):
    def test_wb_uses_nm_cache_not_ozon_sku(self):
        class _Conn:
            def execute(self, statement, params=None):
                sql = str(statement)
                class _R:
                    def scalar(self_inner):
                        if "wb_product_photos" in sql:
                            return "https://basket.wb/rack.webp"
                        return "https://ir.ozone.ru/mop.jpg"
                return _R()

        from backend.routers.cx_responses import resolve_review_product_photo
        url = resolve_review_product_photo(_Conn(), "wb", "СО-ligGS", "197296830")
        self.assertEqual(url, "https://basket.wb/rack.webp")

    def test_wb_does_not_fall_back_to_ozon_when_nm_known(self):
        class _Conn:
            def execute(self, statement, params=None):
                class _R:
                    def scalar(self_inner):
                        return "https://ir.ozone.ru/mop.jpg" if "product_photos" in str(statement) and "wb_product_photos" not in str(statement) else None
                return _R()

        from backend.routers.cx_responses import resolve_review_product_photo
        url = resolve_review_product_photo(_Conn(), "wb", "СО-ligGS", "197296830")
        self.assertIsNone(url)


class PackagingTemplateSeedTests(unittest.TestCase):
    def test_seeds_packaging_chip_and_kb_chat_cta(self):
        from backend.routers.cx_responses import (
            PACKAGING_KB_SOLUTION,
            PACKAGING_REVIEW_TEMPLATE_BODY,
            PACKAGING_REVIEW_TEMPLATE_TITLE,
            ensure_packaging_review_templates,
        )

        class _Engine:
            def __init__(self):
                self.conn = _Conn({}, [])

            def connect(self):
                return self.conn

        engine = _Engine()
        ensure_packaging_review_templates(engine)
        sql = "\n".join(engine.conn.statements)
        self.assertIn("cx_response_templates", sql)
        self.assertIn("cx_kb_rules", sql)
        self.assertEqual(PACKAGING_REVIEW_TEMPLATE_TITLE, "Упаковка")
        self.assertIn("Чат с продавцом", PACKAGING_REVIEW_TEMPLATE_BODY)
        self.assertIn("Чат с продавцом", PACKAGING_KB_SOLUTION)
        self.assertIn("1, 3, false", sql)
        self.assertIn("max_rating = 3", sql)


class PaintHolesTemplateSeedTests(unittest.TestCase):
    def test_seeds_review_chip_and_kb_rule(self):
        from backend.review_policy import PAINT_HOLES_REPLY
        from backend.routers.cx_responses import (
            PAINT_HOLES_REVIEW_TEMPLATE_BODY,
            PAINT_HOLES_REVIEW_TEMPLATE_TITLE,
            ensure_paint_holes_review_templates,
        )

        class _Engine:
            def __init__(self):
                self.conn = _Conn({}, [])

            def connect(self):
                return self.conn

        engine = _Engine()
        ensure_paint_holes_review_templates(engine)
        sql = "\n".join(engine.conn.statements)
        self.assertIn("cx_response_templates", sql)
        self.assertIn("cx_kb_rules", sql)
        self.assertEqual(PAINT_HOLES_REVIEW_TEMPLATE_TITLE, "Технологические отверстия")
        self.assertIn(PAINT_HOLES_REPLY, PAINT_HOLES_REVIEW_TEMPLATE_BODY)
        self.assertIn("1, 5, false", sql)
        self.assertIn("max_rating = 5", sql)


if __name__ == "__main__":
    unittest.main()
