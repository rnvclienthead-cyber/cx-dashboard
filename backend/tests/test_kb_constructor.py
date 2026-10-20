import unittest

from backend.routers.kb import (
    _body_without_signer,
    _norm,
    _pick_article_group,
    _sign_answer,
    BuildReq,
)


class SignerHelpersTests(unittest.TestCase):
    def test_sign_appends_name(self):
        text = _sign_answer("Спасибо за отзыв.", "Ольга")
        self.assertTrue(text.endswith("Ольга, команда Видовито"))

    def test_sign_without_signer_still_has_a_name(self):
        text = _sign_answer("Спасибо за отзыв.")
        self.assertRegex(text, r"(Ольга|Софья|Александр), команда Видовито$")
        self.assertFalse(text.endswith("\n\nКоманда Видовито"))

    def test_replaces_nameless_team_sign(self):
        text = _sign_answer("Жаль, что разочаровали.\n\nКоманда Видовито", "Александр")
        self.assertTrue(text.endswith("Александр, команда Видовито"))
        self.assertNotIn("\n\nКоманда Видовито", text)

    def test_keeps_existing_named_sign(self):
        original = "Спасибо.\n\nСофья, команда Видовито"
        self.assertEqual(_sign_answer(original, "Ольга"), original)

    def test_signers_are_three_names(self):
        from backend.routers.kb import TEMPLATE_SIGNERS
        self.assertEqual(set(TEMPLATE_SIGNERS), {"Ольга", "Софья", "Александр"})

    def test_strip_signer_keeps_body(self):
        signed = "Жаль, что разочаровали.\n\nСофья, команда Видовито"
        self.assertEqual(_body_without_signer(signed), "Жаль, что разочаровали.")

    def test_avoid_compares_bodies_not_names(self):
        a = _sign_answer("Один и тот же текст", "Ольга")
        b = _sign_answer("Один и тот же текст", "Софья")
        self.assertEqual(_body_without_signer(a), _body_without_signer(b))
        self.assertNotEqual(a, b)

    def test_build_req_defaults_skip_overlay(self):
        req = BuildReq()
        self.assertFalse(req.use_overlay)
        self.assertEqual(req.avoid, "")
        self.assertEqual(req.topic, "")


class KbWriteAdminTests(unittest.TestCase):
    def test_admin_may_write(self):
        from backend.routers.kb import _require_admin
        _require_admin({"role": "admin"})

    def test_manager_cannot_write(self):
        from fastapi import HTTPException
        from backend.routers.kb import _require_admin
        with self.assertRaises(HTTPException) as ctx:
            _require_admin({"role": "cs_manager"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_write_endpoints_call_require_admin(self):
        import inspect
        from backend.routers import kb
        for fn in (kb.import_kb, kb.create_rule, kb.update_rule, kb.delete_rule, kb.update_phrase):
            self.assertIn("_require_admin", inspect.getsource(fn), fn.__name__)
        self.assertNotIn("_require_admin", inspect.getsource(kb.get_kb))
        self.assertNotIn("_require_admin", inspect.getsource(kb.build_endpoint))


class ArticleGroupMatchTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"article": "pb-401b", "id": 1, "name": "Вешалки"},
            {"article": "bb-black", "id": 2, "name": "Этажерки"},
            {"article": "s-04-full-black", "id": 3, "name": "Обувницы"},
        ]

    def test_exact(self):
        self.assertEqual(_pick_article_group(self.rows, _norm("PB-401B"))["name"], "Вешалки")

    def test_name_prefix_contains_article(self):
        hit = _pick_article_group(self.rows, _norm("Collectable PB-401B"))
        self.assertEqual(hit["article"], "pb-401b")

    def test_prefix_sku(self):
        hit = _pick_article_group(self.rows, _norm("S-04-Full-Black-NEW"))
        self.assertEqual(hit["id"], 3)

    def test_unknown_returns_none(self):
        self.assertIsNone(_pick_article_group(self.rows, _norm("UNKNOWN-SKU")))


class FiveStarComposeTests(unittest.TestCase):
    def test_five_star_skips_solution_tips(self):
        from backend.routers.kb import _compose_rule
        rule = {
            "empathy": "Спасибо за пять звёзд!",
            "solution": "Полки можно просто убрать, если они не нужны.",
            "energy": "Возвращайтесь за новинками!",
        }
        five = _compose_rule("Здравствуйте!", rule, "Приятного пользования!", 5)
        self.assertIn("Спасибо за пять звёзд", five)
        self.assertNotIn("Полки", five)
        self.assertNotIn("убрать", five)
        three = _compose_rule("Здравствуйте.", rule, "", 3)
        self.assertIn("Полки", three)


if __name__ == "__main__":
    unittest.main()
