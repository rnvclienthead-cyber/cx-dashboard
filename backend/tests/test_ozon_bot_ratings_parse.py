import unittest

from ozon_bot_ratings import pager_pages, parse_rating, parse_review_count, parse_sku_and_article, row_from_cabinet


class OzonCabinetRatingParseTests(unittest.TestCase):
    def test_rating_uses_comma(self):
        self.assertEqual(parse_rating("4,8"), 4.8)
        self.assertIsNone(parse_rating("0"))
        self.assertIsNone(parse_rating("нет оценок"))

    def test_review_count_ignores_the_caption(self):
        self.assertEqual(parse_review_count("13752\nСобрали мнения"), 13752)
        self.assertEqual(parse_review_count("1 836"), 1836)

    def test_article_and_sku(self):
        sku, article = parse_sku_and_article("2-СБ-Black\nSKU 1739844706\nОбъединён (32)")
        self.assertEqual(article, "2-СБ-Black")
        self.assertEqual(sku, 1739844706)

    def test_pager_skips_page_size_chip(self):
        self.assertEqual(pager_pages(["50", "1", "2", "3"]), [1, 2, 3])

    def test_row_keeps_a_real_rating(self):
        row = row_from_cabinet(
            {
                "article": "BB-C-Black\nSKU 5882317768",
                "name": "Вешалка\nкатегория",
                "reviews": "0",
                "rating": "4,8",
            },
            "2026-10-10",
        )
        self.assertEqual(row["supplier_article"], "BB-C-Black")
        self.assertEqual(row["average_rating"], 4.8)
        self.assertEqual(row["review_count"], 0)
        self.assertIsNone(row_from_cabinet({"article": "X\nSKU 1", "rating": "0", "reviews": "0", "name": ""}, "2026-10-10"))
