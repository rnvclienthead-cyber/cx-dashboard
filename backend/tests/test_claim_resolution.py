import unittest

from backend.routers.cx_responses import (
    _apply_offer_page,
    _attach_articles_from_nm,
    _chat_id_variants,
    _match_claim_keys_sql,
    _offer_filter_sql,
    _offer_kind_of_item,
    _sort_sql,
    _wb_cabinet_chat_url,
    _wb_srid_core,
    _ym_cabinet_chat_url,
    _ym_chat_search_needle,
    readable_status_ex,
    resolution_from_signals,
)


class ReadableStatusExTests(unittest.TestCase):
    def test_hides_numeric_code(self):
        self.assertEqual(readable_status_ex("0"), "")
        self.assertEqual(readable_status_ex(0), "")
        self.assertEqual(readable_status_ex(" 12 "), "")

    def test_keeps_human_comment(self):
        self.assertEqual(readable_status_ex("Клиент отменил заявку"), "Клиент отменил заявку")


class WbCabinetChatUrlTests(unittest.TestCase):
    def test_uuid_from_prefixed_id(self):
        url = _wb_cabinet_chat_url("1:41d87883-8710-4ad1-b8cb-f571229846c5")
        self.assertIn("chat-with-clients?chatId=41d87883-8710-4ad1-b8cb-f571229846c5", url)

    def test_empty_opens_list(self):
        self.assertEqual(
            _wb_cabinet_chat_url(""),
            "https://seller.wildberries.ru/chat-with-clients",
        )


class YmCabinetChatUrlTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self._p_biz = patch(
            "backend.routers.cx_responses._ym_business_id", return_value="203987989",
        )
        self._p_camp = patch(
            "backend.routers.cx_responses._ym_campaign_id", return_value="146194774",
        )
        self._p_biz.start()
        self._p_camp.start()
        self.addCleanup(self._p_biz.stop)
        self.addCleanup(self._p_camp.stop)
    def test_cabinet_uuid_keeps_slashes(self):
        url = _ym_cabinet_chat_url(
            "0/43/43cfafe2-6c7f-4c55-be68-5ccd5c9f717f", "RETURN",
        )
        self.assertIn("chatGroup=RETURN", url)
        self.assertIn("chatId=0/43/43cfafe2-6c7f-4c55-be68-5ccd5c9f717f", url)
        self.assertNotIn("chatId=0%2F43%2F", url)
        self.assertNotIn("search=", url)

    def test_missing_uuid_searches_return_number(self):
        url = _ym_cabinet_chat_url(None, "RETURN", search="27867248")
        self.assertIn("chatGroup=RETURN", url)
        self.assertIn("search=27867248", url)
        self.assertNotIn("chatId=", url)

    def test_numeric_api_id_is_not_used_as_chat_id(self):
        url = _ym_cabinet_chat_url("18012922", "RETURN", search="27867248")
        self.assertNotIn("chatId=18012922", url)
        self.assertIn("search=27867248", url)

    def test_search_needle_prefers_return_id(self):
        self.assertEqual(
            _ym_chat_search_needle(
                {"return_id": 27867248, "order_id": 61296535747},
                {"claim_id": "x"},
            ),
            "27867248",
        )


class ClaimResolutionTests(unittest.TestCase):
    def test_link_from_request(self):
        r = resolution_from_signals(request={"id": 9, "status": "new"})
        self.assertEqual(r["kind"], "reshipment_link")
        self.assertIn("Ссылка", r["label"])
        self.assertEqual(r["request_id"], 9)

    def test_link_from_form_url(self):
        r = resolution_from_signals(
            seller_texts=["заполните форму — https://box.vidovito.com/reshipment/form?t=abc"]
        )
        self.assertEqual(r["kind"], "reshipment_link")

    def test_unused_token_is_not_a_sent_link(self):
        r = resolution_from_signals(token_sent=False, seller_texts=[])
        self.assertIsNone(r)

    def test_offered_without_link(self):
        r = resolution_from_signals(
            seller_texts=["Можем выслать деталь, заполните форму — напишите адрес"]
        )
        self.assertEqual(r["kind"], "reshipment_offered")
        self.assertIn("Предложили", r["label"])

    def test_deny_reshipment_is_not_offer(self):
        r = resolution_from_signals(
            seller_texts=["Деталь выслать не можем — оформите возврат"]
        )
        self.assertIsNone(r)

    def test_seller_question_without_offer(self):
        r = resolution_from_signals(
            seller_texts=["Пришлите, пожалуйста, фото сломанной детали с разных сторон"]
        )
        self.assertEqual(r["kind"], "open_question")
        self.assertIn("без решения", r["label"])

    def test_greeting_alone_is_not_a_question(self):
        r = resolution_from_signals(
            seller_texts=[
                "Здравствуйте! Здесь вы можете задать вопросы по возврату товара, если они у вас возникнут"
            ]
        )
        self.assertIsNone(r)


class ListOfferStatusHookTests(unittest.TestCase):
    def test_list_builders_attach_offer_status(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("_attach_list_offer_status("), 4)

    def test_sort_sql_new_vs_old(self):
        self.assertIn("DESC", _sort_sql("c.claim_date", "new"))
        self.assertIn("ASC", _sort_sql("c.claim_date", "old"))

    def test_offer_filter_kinds(self):
        self.assertEqual(_offer_filter_sql("wb", ""), "")
        link, params = _match_claim_keys_sql("wb", {"eBB.r7B149f3a8bb8483bb7f4a75bbbbb28d5.0.0"}, positive=True)
        self.assertIn("ANY", link)
        self.assertIn("offer_keys", params)
        self.assertIn("offer_cores", params)
        none, _ = _match_claim_keys_sql("ym", {"27867248"}, positive=False)
        self.assertIn("NOT", none)
        empty_none, empty_params = _match_claim_keys_sql("ozon", set(), positive=False)
        self.assertEqual(empty_none, "TRUE")
        self.assertEqual(empty_params, {})
        empty_link, _ = _match_claim_keys_sql("wb", set(), positive=True)
        self.assertEqual(empty_link, "FALSE")

    def test_list_claims_uses_precomputed_offer_keys(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text(encoding="utf-8")
        self.assertIn("_offer_filter_bind(", src)
        self.assertNotIn("ILIKE '%' || split_part(c.srid", src)

    def test_offer_page_keeps_badge_kind(self):
        items = [
            {"claim_id": "1", "resolution": {"kind": "open_question"}},
            {"claim_id": "2", "resolution": {"kind": "reshipment_link"}},
            {"claim_id": "3"},
            {"claim_id": "4", "reshipment": {"id": 9}},
        ]
        self.assertEqual(_offer_kind_of_item(items[0]), "question")
        self.assertEqual(_offer_kind_of_item(items[1]), "link")
        self.assertEqual(_offer_kind_of_item(items[2]), "none")
        self.assertEqual(_offer_kind_of_item(items[3]), "link")
        page, total = _apply_offer_page(items, "none", 1, 25)
        self.assertEqual(total, 1)
        self.assertEqual([x["claim_id"] for x in page], ["3"])
        page, total = _apply_offer_page(items, "link", 1, 25)
        self.assertEqual(total, 2)

    def test_wb_offer_keys_group_by_chat(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text(encoding="utf-8")
        self.assertIn("GROUP BY ch.chat_id, ch.order_rid", src)
        self.assertIn("goodCard", src)
        self.assertIn("_apply_offer_page(", src)

    def test_list_claims_accepts_sort_and_offer(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text(encoding="utf-8")
        self.assertIn('sort: str = Query("new"', src)
        self.assertIn('offer: str = Query("",', src)
        self.assertIn("sort=sort, offer=offer", src)


class WbSridCoreTests(unittest.TestCase):
    def test_middle_of_dotted_rid(self):
        self.assertEqual(
            _wb_srid_core("eBB.r7B149f3a8bb8483bb7f4a75bbbbb28d5.0.0"),
            "r7B149f3a8bb8483bb7f4a75bbbbb28d5",
        )

    def test_keeps_short_plain_value(self):
        self.assertEqual(_wb_srid_core("abc"), "abc")


class AttachArticlesFromNmTests(unittest.TestCase):
    def test_fills_empty_from_nm_map(self):
        items = [
            {"claim_id": "a", "nm_id": "243758346", "supplier_article": ""},
            {"claim_id": "b", "nm_id": "1", "supplier_article": "ШО-White"},
        ]
        persist = _attach_articles_from_nm(items, {"243758346": "2-СБ-Grey"})
        self.assertEqual(items[0]["supplier_article"], "2-СБ-Grey")
        self.assertEqual(items[1]["supplier_article"], "ШО-White")
        self.assertEqual(persist, [("a", "2-СБ-Grey")])

    def test_skips_when_nm_unknown(self):
        items = [{"claim_id": "a", "nm_id": "9", "supplier_article": ""}]
        self.assertEqual(_attach_articles_from_nm(items, {}), [])
        self.assertEqual(items[0]["supplier_article"], "")


class ChatIdVariantsTests(unittest.TestCase):
    def test_prefixed_and_bare(self):
        cid, bare, pref = _chat_id_variants("1:41d87883-8710-4ad1-b8cb-f571229846c5")
        self.assertEqual(bare, "41d87883-8710-4ad1-b8cb-f571229846c5")
        self.assertEqual(cid, "1:41d87883-8710-4ad1-b8cb-f571229846c5")
        self.assertEqual(pref, "1:41d87883-8710-4ad1-b8cb-f571229846c5")

    def test_bare_gets_prefix(self):
        cid, bare, pref = _chat_id_variants("41d87883-8710-4ad1-b8cb-f571229846c5")
        self.assertEqual(cid, bare)
        self.assertEqual(pref, "1:41d87883-8710-4ad1-b8cb-f571229846c5")
