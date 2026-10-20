import unittest

from backend.wb_dbw_delivery import (
    COURIER_REPLACED_LABEL,
    HEADER,
    build_history_sheet_rows,
    build_sheet_rows,
    collect_in_delivery,
    courier_fingerprint,
    courier_query_match,
    filter_delivery_rows_by_courier,
    format_courier,
    format_courier_change,
    format_delivery,
    format_phone,
    format_status,
    import_delivery_rows_as_snapshot,
    is_active_in_delivery,
    merge_order_meta,
    next_tracked_ids,
    pick_rows_to_archive,
    sheet_rows_to_map,
)


class FormatHelpersTests(unittest.TestCase):
    def test_phone_and_delivery(self):
        self.assertEqual(format_phone("79061234567"), "+7 906 123-45-67")
        self.assertEqual(
            format_delivery({"dDate": "2026-08-20", "dTimeFrom": "06:00", "dTimeTo": "20:00"}),
            "20.08.2026, 06:00–20:00",
        )

    def test_status_moves_to_delivery_when_courier_appears(self):
        # раньше статус «залипал» на ожидании из-за previous_ui_status
        self.assertEqual(
            format_status(
                "complete",
                "waiting",
                {"contacts": {"phone": "1"}, "mustBeAssigned": True},
                previous_ui_status="Ожидает курьера",
            ),
            "В доставке",
        )
        self.assertEqual(
            format_status(
                "complete",
                "waiting",
                {"contacts": {"phone": "1"}},
                previous_ui_status="В доставке",
            ),
            "В доставке",
        )
        self.assertEqual(
            format_status(
                "complete",
                "waiting",
                {"contacts": None, "mustBeAssigned": True},
                previous_ui_status="В доставке",
            ),
            "Ожидает курьера",
        )
        self.assertEqual(
            format_status("complete", "canceled_by_client", {"contacts": {"phone": "1"}}),
            "",
        )

    def test_courier_change_label(self):
        self.assertIn(COURIER_REPLACED_LABEL, format_courier_change(True, "2026-08-25T09:24:46+03:00"))
        self.assertEqual(courier_fingerprint({"contacts": None}), "")


class ArchiveRulesTests(unittest.TestCase):
    def test_archive_only_when_api_says_terminal(self):
        previous = sheet_rows_to_map([
            HEADER,
            ["10", "111", "SKU-A", "A", "20.08.2026", "В доставке", "Иван", ""],
            ["11", "222", "SKU-B", "B", "21.08.2026", "Ожидает курьера", "", ""],
            ["12", "333", "SKU-C", "C", "22.08.2026", "В доставке", "Пётр", ""],
        ])
        # 10 исчез: complete + canceled_by_client — в архив (кабинет уже убрал)
        # 11 остаётся в доставке
        # 12 исчез и API receive — архивируем с новым статусом
        archived = pick_rows_to_archive(
            previous,
            current_ids={"11"},
            already_archived=set(),
            statuses={
                10: {"supplierStatus": "complete", "wbStatus": "canceled_by_client"},
                12: {"supplierStatus": "receive", "wbStatus": "sold"},
            },
        )
        self.assertEqual({r[0] for r in archived}, {"10", "12"})
        by_id = {r[0]: r for r in archived}
        self.assertEqual(by_id["10"][5], "Отменён покупателем")
        self.assertEqual(by_id["12"][5], "Получен покупателем")
        self.assertEqual(by_id["12"][2], "SKU-C")
        self.assertEqual(by_id["12"][1], "333")
        self.assertTrue(by_id["12"][8])

        again = pick_rows_to_archive(
            previous, current_ids={"11"}, already_archived={"10", "12"},
            statuses={12: {"supplierStatus": "receive", "wbStatus": "sold"}},
        )
        self.assertEqual(again, [])

    def test_sheet_rows_to_map_skips_header(self):
        mapped = sheet_rows_to_map([
            HEADER,
            ["552", "999", "SKU1", "name", "", "В доставке", "", ""],
        ])
        self.assertIn("552", mapped)
        self.assertEqual(mapped["552"][1], "999")
        self.assertEqual(mapped["552"][2], "SKU1")
        # формат с артикулом, без стикера
        mid = sheet_rows_to_map([
            ["Заказ", "Артикул", "Наименование", "Доставим покупателю", "Статус", "Данные курьера", "Смена курьера"],
            ["554", "SKU2", "name2", "дата", "В доставке", "Иван", ""],
        ])
        self.assertEqual(mid["554"][1], "")
        self.assertEqual(mid["554"][2], "SKU2")
        # старый формат без артикула и стикера
        old = sheet_rows_to_map([
            ["Заказ", "Наименование", "Доставим покупателю", "Статус", "Данные курьера", "Смена курьера"],
            ["553", "name", "дата", "В доставке", "Иван", ""],
        ])
        self.assertEqual(old["553"][1], "")
        self.assertEqual(old["553"][2], "")
        self.assertEqual(old["553"][3], "name")
        self.assertEqual(sheet_rows_to_map([[], []]), {})
        self.assertTrue(is_active_in_delivery("complete", "waiting"))
        self.assertFalse(is_active_in_delivery("complete", "canceled_by_client"))



class SnapshotRulesTests(unittest.TestCase):
    def test_import_snapshot_keeps_xlsx_status(self):
        snapshot = import_delivery_rows_as_snapshot([
            {
                "id": 1,
                "sticker": "57232120242",
                "article": "A",
                "name": "Стол",
                "ui_status": "Ожидает курьера",
                "delivery": {"dDate": "2026-08-20", "dTimeFrom": "06:00", "dTimeTo": "20:00"},
                "courier": {"contacts": {"fullName": "Иван", "phone": "79001112233", "carNumber": "A001"}},
            }
        ])
        sheet = build_sheet_rows(snapshot)
        self.assertEqual(sheet[0][1], "57232120242")
        self.assertEqual(sheet[0][2], "A")
        self.assertEqual(sheet[0][5], "Ожидает курьера")
        self.assertIn("Иван", sheet[0][6])

    def test_left_order_disappears_from_delivery(self):
        second = collect_in_delivery(
            new_orders=[],
            period_orders=[{"id": 10, "article": "SKU"}],
            tracked=[{"id": 10, "article": "SKU", "ui_status": "В доставке"}],
            statuses={
                10: {"supplierStatus": "complete", "wbStatus": "waiting"},
                11: {"supplierStatus": "receive", "wbStatus": "sold"},
            },
            couriers={10: {"contacts": {"fullName": "Второй", "phone": "79000000002"}}},
            delivery_dates={10: {"dDate": "2026-08-25"}},
            names={},
            previous_ui_statuses={10: "В доставке"},
            stickers={10: "111222"},
        )
        sheet = build_sheet_rows(second)
        self.assertEqual([row[0] for row in sheet], ["10"])
        self.assertEqual(sheet[0][1], "111222")
        self.assertEqual(sheet[0][5], "В доставке")

    def test_tracked_ids_drop_finished(self):
        keep = next_tracked_ids(
            {
                1: {"supplierStatus": "new"},
                2: {"supplierStatus": "complete"},
                3: {"supplierStatus": "receive"},
            },
            [1, 2, 3, 4],
        )
        self.assertEqual(keep, [1, 2, 4])

    def test_history_rows(self):
        rows = build_history_sheet_rows([
            {
                "order_id": 10,
                "full_name": "Старый",
                "phone": "79001110000",
                "car_number": "A001AA77",
                "started_at": "2026-08-20T10:00:00+00:00",
                "ended_at": "2026-08-25T09:00:00+00:00",
                "note": COURIER_REPLACED_LABEL,
            },
        ])
        self.assertEqual(rows[0][1], "Старый")
        self.assertEqual(rows[0][6], COURIER_REPLACED_LABEL)

    def test_courier_format(self):
        self.assertEqual(
            format_courier({"contacts": {"fullName": "А", "phone": "79061234540", "carNumber": "C1"}}),
            "А, +7 906 123-45-40, C1",
        )

    def test_filter_delivery_rows_by_partial_name(self):
        rows = [
            ["1", "111", "SKU1", "Стол", "", "В доставке", "Иван Петров, +7 900 111-11-11, A1", ""],
            ["2", "222", "SKU2", "Стул", "", "В доставке", "Пётр Сидоров, +7 900 222-22-22, B2", ""],
            ["3", "333", "SKU3", "Полка", "", "Ожидает курьера", "", ""],
        ]
        self.assertTrue(courier_query_match("Иван Петров, +7", "петров"))
        self.assertTrue(courier_query_match("Пётр Сидоров", "петр"))  # ё→е
        matched = filter_delivery_rows_by_courier(rows, "Сидор")
        self.assertEqual([r[0] for r in matched], ["2"])
        self.assertEqual(filter_delivery_rows_by_courier(rows, ""), [])
        self.assertEqual(
            filter_delivery_rows_by_courier(rows, "иван петр"),
            [["1", "111", "SKU1", "Стол", "", "В доставке", "Иван Петров, +7 900 111-11-11, A1", ""]],
        )


if __name__ == "__main__":
    unittest.main()
