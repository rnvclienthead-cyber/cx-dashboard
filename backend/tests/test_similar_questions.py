import unittest

from backend.routers.cx_responses import _group_similar_by_category


class SimilarQuestionsGroupTests(unittest.TestCase):
    def test_keyword_match_beats_unrelated_category(self):
        rows = [
            {"id": "1", "mp": "wb", "sku": "A", "text": "как собрать вешалку", "answer": "сначала ножки"},
            {"id": "2", "mp": "wb", "sku": "A", "text": "вешалку как крепить к стене", "answer": "не нужно"},
            {"id": "3", "mp": "ym", "sku": "B", "text": "высота стеллажа", "answer": "180 см"},
            {"id": "4", "mp": "ozon", "sku": "", "text": "где трек доставки", "answer": "в заказе"},
        ]
        groups = _group_similar_by_category(
            rows, {"a": "Вешалки", "b": "Стеллажи"}, ["собрать", "вешалку"],
        )
        self.assertEqual([g["category"] for g in groups], ["Вешалки"])
        self.assertEqual(groups[0]["items"][0]["answer"], "сначала ножки")
        self.assertGreaterEqual(groups[0]["items"][0]["score"], groups[0]["items"][1]["score"])

    def test_word_forms_count_as_the_same_keyword(self):
        rows = [
            {"id": "1", "mp": "wb", "sku": "A", "text": "вешалка не встаёт ровно", "answer": "сначала ножки"},
            {"id": "2", "mp": "wb", "sku": "B", "text": "высота стеллажа", "answer": "180 см"},
        ]
        groups = _group_similar_by_category(
            rows, {"a": "Вешалки", "b": "Стеллажи"}, ["вешалку"],
        )
        self.assertEqual([g["category"] for g in groups], ["Вешалки"])


if __name__ == "__main__":
    unittest.main()
