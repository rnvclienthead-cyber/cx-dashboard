import unittest

from backend.factory_ratings import (
    build_attributed_ratings,
    build_factory_ratings,
    confidence_level,
    merge_attributed_into_factories,
    reliable_rating,
)


class FactoryRatingsTests(unittest.TestCase):
    def test_weighted_average_follows_review_volume(self):
        report = build_factory_ratings(
            [("a", "Завод 1"), ("b", "Завод 1")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.5, "reviews": 1000, "platform": "wb"},
                {"art": "b", "supplier_article": "B", "rating": 4.9, "reviews": 50, "platform": "wb"},
            ],
        )
        factory = report["factories"][0]
        self.assertEqual(factory["review_count"], 1050)
        self.assertEqual(factory["average_rating"], 4.52)
        self.assertEqual(factory["sku_count"], 2)
        self.assertEqual(factory["confidence"], "mid")

    def test_large_sample_outranks_lucky_small_sample(self):
        """4,5 при 1000 оценках надёжнее, чем 4,7 при 50."""
        self.assertGreater(reliable_rating(4.5, 1000), reliable_rating(4.7, 50))
        report = build_factory_ratings(
            [("a", "Много"), ("b", "Мало")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.5, "reviews": 1000, "platform": "wb"},
                {"art": "b", "supplier_article": "B", "rating": 4.7, "reviews": 50, "platform": "wb"},
            ],
        )
        self.assertEqual([f["factory_name"] for f in report["factories"]], ["Много", "Мало"])
        self.assertEqual(report["factories"][1]["confidence"], "low")

    def test_confidence_separates_a_thousand_from_hundreds_of_thousands(self):
        self.assertEqual(confidence_level(1_300), "mid")
        self.assertEqual(confidence_level(13_000), "high")
        self.assertEqual(confidence_level(250_000), "top")
        self.assertEqual(confidence_level(999), "low")

    def test_two_factories_stay_out_of_the_average(self):
        report = build_factory_ratings(
            [("a", "Один"), ("b", "Раз"), ("b", "Два")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.0, "reviews": 100, "platform": "wb"},
                {"art": "b", "supplier_article": "B", "rating": 5.0, "reviews": 500, "platform": "wb"},
            ],
        )
        self.assertEqual(len(report["factories"]), 1)
        self.assertEqual(report["factories"][0]["factory_name"], "Один")
        self.assertEqual(report["factories"][0]["average_rating"], 4.0)
        self.assertEqual(report["factories"][0]["review_count"], 100)
        self.assertEqual(report["shared"][0]["supplier_article"], "B")
        self.assertEqual(report["shared"][0]["factories"], ["Два", "Раз"])
        self.assertEqual(report["shared"][0]["review_count"], 500)
        self.assertEqual(report["shared_only_factories"], ["Два", "Раз"])

    def test_three_platforms_add_into_one_average(self):
        report = build_factory_ratings(
            [("a", "Завод")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.0, "reviews": 100, "platform": "wb"},
                {"art": "a", "supplier_article": "A", "rating": 5.0, "reviews": 100, "platform": "ym"},
                {"art": "a", "supplier_article": "A", "rating": 3.0, "reviews": 800, "platform": "ozon"},
            ],
        )
        factory = report["factories"][0]
        self.assertEqual(factory["sku_count"], 1)
        self.assertEqual(factory["review_count"], 1000)
        self.assertEqual(factory["average_rating"], 3.3)
        self.assertEqual(factory["products"][0]["platforms"], ["ozon", "wb", "ym"])

    def test_same_article_on_two_platforms_is_one_product(self):
        report = build_factory_ratings(
            [("a", "Завод")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.0, "reviews": 100, "platform": "wb"},
                {"art": "a", "supplier_article": "A", "rating": 5.0, "reviews": 100, "platform": "ym"},
            ],
        )
        factory = report["factories"][0]
        self.assertEqual(factory["sku_count"], 1)
        self.assertEqual(factory["review_count"], 200)
        self.assertEqual(factory["average_rating"], 4.5)
        self.assertEqual(factory["products"][0]["platforms"], ["wb", "ym"])

    def test_article_without_factory_is_counted_aside(self):
        report = build_factory_ratings(
            [],
            [{"art": "a", "supplier_article": "A", "rating": 4.2, "reviews": 10, "platform": "wb"}],
        )
        self.assertEqual(report["factories"], [])
        self.assertEqual(report["shared"], [])
        self.assertEqual(report["unassigned_sku_count"], 1)
        self.assertEqual(report["unassigned_review_count"], 10)

    def test_attributed_review_is_kept_once_and_split_reviews_are_dropped(self):
        report = build_attributed_ratings([
            {"id": "1", "art": "a", "supplier_article": "A", "rating": 5, "factory_name": "Раз"},
            {"id": "1", "art": "a", "supplier_article": "A", "rating": 5, "factory_name": "Раз"},
            {"id": "2", "art": "a", "supplier_article": "A", "rating": 1, "factory_name": "Раз"},
            {"id": "2", "art": "a", "supplier_article": "A", "rating": 1, "factory_name": "Два"},
            {"id": "3", "art": "b", "supplier_article": "B", "rating": 4, "factory_name": "Два"},
        ], source_review_count=10)
        self.assertEqual(report["review_count"], 2)
        self.assertEqual(report["source_review_count"], 10)
        by_name = {f["factory_name"]: f for f in report["factories"]}
        self.assertEqual(by_name["Раз"]["review_count"], 1)
        self.assertEqual(by_name["Раз"]["average_rating"], 5.0)
        self.assertEqual(by_name["Два"]["review_count"], 1)
        self.assertEqual(by_name["Два"]["products"][0]["supplier_article"], "B")

    def test_traced_reviews_change_the_factory_average(self):
        report = build_factory_ratings(
            [("a", "Один"), ("b", "Раз"), ("b", "Два")],
            [
                {"art": "a", "supplier_article": "A", "rating": 4.0, "reviews": 100, "platform": "wb"},
                {"art": "b", "supplier_article": "B", "rating": 5.0, "reviews": 500, "platform": "wb"},
            ],
        )
        attributed = build_attributed_ratings([
            {"id": "1", "art": "b", "supplier_article": "B", "rating": 2, "factory_name": "Два"},
        ], source_review_count=10)
        merged = merge_attributed_into_factories(report, attributed)
        by_name = {f["factory_name"]: f for f in merged["factories"]}
        self.assertEqual(by_name["Один"]["average_rating"], 4.0)
        self.assertEqual(by_name["Один"]["review_count"], 100)
        self.assertEqual(by_name["Два"]["average_rating"], 2.0)
        self.assertEqual(by_name["Два"]["review_count"], 1)
        self.assertTrue(by_name["Два"]["products"][0]["partial"])
        self.assertEqual(merged["shared_only_factories"], ["Раз"])
        self.assertEqual(report["factories"][0]["review_count"], 100)

    def test_placeholder_factory_does_not_assign_the_article(self):
        report = build_factory_ratings(
            [("a", "Уточняется")],
            [{"art": "a", "supplier_article": "A", "rating": 4.2, "reviews": 10, "platform": "wb"}],
        )
        self.assertEqual(report["factories"], [])
        self.assertEqual(report["unassigned_sku_count"], 1)
