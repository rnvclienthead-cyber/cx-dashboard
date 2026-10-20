import unittest

from backend.routers import chat_playbook as pb


class DostavkaOurDelaySeedTests(unittest.TestCase):
    def test_named_branch_is_separate_from_marketplace(self):
        ours = [
            v for v in pb.SEED_VARIANTS
            if v[0] == "dostavka_wb" and v[1] == pb._DOSTAVKA_OUR_DELAY_KEY
        ]
        marketplace = [
            v for v in pb.SEED_VARIANTS
            if v[0] == "dostavka_wb" and not v[1]
        ]
        self.assertEqual(len(ours), 2)
        self.assertGreaterEqual(len(marketplace), 2)
        for item in ours:
            body = item[3].lower()
            self.assertIn("логист", body)
            self.assertNotIn("wildberries", body)
            self.assertNotIn("поддержк", body)

    def test_same_branch_on_ozon_and_ym(self):
        oz = [
            v for v in pb.SEED_OZON_VARIANTS
            if v[0] == "dostavka_wb" and v[1] == pb._DOSTAVKA_OUR_DELAY_KEY
        ]
        ym = [
            v for v in pb.SEED_YM_VARIANTS
            if v[0] == "dostavka_wb" and v[1] == pb._DOSTAVKA_OUR_DELAY_KEY
        ]
        self.assertEqual(len(oz), 2)
        self.assertEqual(len(ym), 2)
        self.assertEqual({v[3] for v in oz}, {v[3] for v in ym})


class PromokodyWbOnlySeedTests(unittest.TestCase):
    def test_theme_is_human_and_wb_only(self):
        theme = next(t for t in pb.SEED_THEMES if t[0] == "promokody")
        self.assertEqual(theme[1], "Промокоды")
        self.assertEqual(theme[3], "human")
        self.assertIn("promokody", pb.CHAT_THEME_USAGE_ORDER)
        variants = [v for v in pb.SEED_VARIANTS if v[0] == "promokody"]
        self.assertEqual([v[1] for v in variants], ["pct5", "pct15"])
        self.assertEqual([v[2] for v in variants], ["Скидка 5%", "Скидка 15%"])
        self.assertIn("U4FZT091", variants[0][3])
        self.assertIn("CQ4GYW7H", variants[1][3])
        self.assertIn("Wildberries", variants[0][3])
        self.assertIn("Wildberries", variants[1][3])
        self.assertEqual(
            [v for v in pb.SEED_OZON_VARIANTS if v[0] == "promokody"],
            [],
        )
        self.assertEqual(
            [v for v in pb.SEED_YM_VARIANTS if v[0] == "promokody"],
            [],
        )


class TechOtverstiyaSeedTests(unittest.TestCase):
    def test_theme_and_same_text_on_all_mps(self):
        from backend.review_policy import PAINT_HOLES_REPLY

        theme = next(t for t in pb.SEED_THEMES if t[0] == "tech_otverstiya")
        self.assertEqual(theme[1], "Технологические отверстия")
        self.assertEqual(theme[3], "auto")
        self.assertIn("tech_otverstiya", pb.CHAT_THEME_USAGE_ORDER)
        import inspect
        self.assertIn("tech_otverstiya", inspect.getsource(pb.ensure_new_themes))
        wb = [v for v in pb.SEED_VARIANTS if v[0] == "tech_otverstiya"]
        oz = [v for v in pb.SEED_OZON_VARIANTS if v[0] == "tech_otverstiya"]
        ym = [v for v in pb.SEED_YM_VARIANTS if v[0] == "tech_otverstiya"]
        self.assertEqual(len(wb), 1)
        self.assertEqual(len(oz), 1)
        self.assertEqual(len(ym), 1)
        self.assertEqual(wb[0][3], PAINT_HOLES_REPLY)
        self.assertEqual(oz[0][3], PAINT_HOLES_REPLY)
        self.assertEqual(ym[0][3], PAINT_HOLES_REPLY)
        from backend.routers import question_playbook as qpb
        self.assertIn("tech_otverstiya", qpb.QUESTION_THEME_KEYS)


if __name__ == "__main__":
    unittest.main()
