import unittest
from datetime import date

from backend.wb_pvz_input_sheet import (
    aggregate_arrived,
    bootstrap_records,
    is_arrived_at_pvz,
    load_seller_articles,
    merge_groups,
    parse_sheet_rows,
    plan_updates,
    report_window,
    surplus_quantities,
)


CATALOG = {"169887122": "BO-White-2", "10": "Shelf-white-5/43"}


def item(srid, status, day, nm_id=169887122, vendor=""):
    return {
        "srid": srid,
        "status": status,
        "order_dt": day,
        "nm_id": nm_id,
        "vendor_code": vendor or None,
    }


class ArrivedFilterTests(unittest.TestCase):
    def test_only_arrived_status(self):
        self.assertTrue(is_arrived_at_pvz("Готов к выдаче"))
        self.assertTrue(is_arrived_at_pvz("Прибыл на ПВЗ"))
        self.assertFalse(is_arrived_at_pvz("В пути в пвз"))
        self.assertFalse(is_arrived_at_pvz("Выдано"))
        self.assertFalse(is_arrived_at_pvz("Готово"))

    def test_one_row_per_day_and_article_with_quantity(self):
        groups, skipped = aggregate_arrived([
            item("a", "Готов к выдаче", "2026-09-17"),
            item("b", "Готов к выдаче", "2026-09-17"),
            item("c", "Готов к выдаче", "2026-09-17", nm_id=10),
            item("d", "В пути в пвз", "2026-09-17"),
            item("e", "Выдано", "2026-09-16", nm_id=10),
        ], CATALOG)
        self.assertEqual(groups, [
            ("2026-09-17", "BO-White-2", 2),
            ("2026-09-17", "Shelf-white-5/43", 1),
        ])
        self.assertEqual(skipped, [])

    def test_catalog_reads_sheet_headers(self):
        catalog = load_seller_articles([
            ["Артикул", "Артикул ВБ"],
            ["BO-White-2", "169887122"],
            ["", "1"],
            ["Пустой nm", ""],
        ])
        self.assertEqual(catalog, {"169887122": "BO-White-2"})

    def test_window_is_31_days(self):
        start, end = report_window(date(2026, 9, 23))
        self.assertEqual(end, "2026-09-23")
        self.assertEqual(start, "2026-08-24")

    def test_sheet_date_with_dots(self):
        rows = parse_sheet_rows([["17.09.2026", "BO-White-2", "2"]])
        self.assertEqual(rows, [("2026-09-17", "BO-White-2", 2)])


class StatusChangeTests(unittest.TestCase):
    def test_new_arrival_on_old_order_is_added_once(self):
        first, records, _skipped = plan_updates([
            item("old", "В пути в пвз", "2026-08-28"),
        ], CATALOG, {})
        self.assertEqual(first, [])
        known = {row["srid"]: {"status": row["status"], "counted": row["counted"]} for row in records}

        second, records2, _skipped = plan_updates([
            item("old", "Готов к выдаче", "2026-08-28"),
        ], CATALOG, known)
        self.assertEqual(second, [("2026-08-28", "BO-White-2", 1)])
        self.assertTrue(records2[0]["counted"])

        third, _records3, _skipped = plan_updates([
            item("old", "Выдано", "2026-08-28"),
        ], CATALOG, {"old": {"status": "Готов к выдаче", "counted": True}})
        self.assertEqual(third, [])

    def test_issued_between_runs_still_counts(self):
        deltas, records, _skipped = plan_updates([
            item("s1", "Выдано", "2026-09-01"),
        ], CATALOG, {"s1": {"status": "В пути в пвз", "counted": False}})
        self.assertEqual(deltas, [("2026-09-01", "BO-White-2", 1)])
        self.assertTrue(records[0]["counted"])

    def test_first_seen_issued_is_not_backfilled(self):
        deltas, records, _skipped = plan_updates([
            item("s1", "Выдано", "2026-09-01"),
        ], CATALOG, {})
        self.assertEqual(deltas, [])
        self.assertFalse(records[0]["counted"])

    def test_merge_keeps_rows_that_left_the_report(self):
        merged = merge_groups(
            [("2026-09-01", "BO-White-2", 4)],
            [("2026-09-19", "Shelf-white-5/43", 1)],
        )
        self.assertEqual(merged, [
            ("2026-09-01", "BO-White-2", 4),
            ("2026-09-19", "Shelf-white-5/43", 1),
        ])

    def test_bootstrap_does_not_double_the_sheet(self):
        report = [("2026-09-17", "BO-White-2", 4), ("2026-09-19", "Shelf-white-5/43", 1)]
        sheet = [("2026-09-17", "BO-White-2", 4)]
        self.assertEqual(surplus_quantities(report, sheet), [
            ("2026-09-19", "Shelf-white-5/43", 1),
        ])
        records = bootstrap_records([
            item("a", "Готов к выдаче", "2026-09-17"),
            item("b", "В пути в пвз", "2026-09-17"),
            item("c", "Выдано", "2026-09-01"),
        ], CATALOG)
        by_srid = {row["srid"]: row["counted"] for row in records}
        self.assertTrue(by_srid["a"])
        self.assertFalse(by_srid["b"])
        self.assertFalse(by_srid["c"])


if __name__ == "__main__":
    unittest.main()
