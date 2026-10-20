import unittest

from backend.invoice_suggestions import (
    invoice_suggestions_sql,
    is_placeholder_invoice,
    normalize_invoice_suggestions,
)


class InvoiceSuggestionsTests(unittest.TestCase):
    def test_placeholder_unclosed_invoice_is_hidden(self):
        self.assertTrue(is_placeholder_invoice('не закрытый инвойс'))
        self.assertTrue(is_placeholder_invoice('  Не закрытый инвойс  '))
        self.assertTrue(is_placeholder_invoice('не закрытые инвойсы'))
        self.assertFalse(is_placeholder_invoice('INV328-9362'))

    def test_normalize_drops_placeholders_and_duplicates(self):
        values = [
            'не закрытый инвойс',
            'не закрытый инвойс ',
            'INV2894',
            'inv2894',
            'INV28-P302',
            '',
            None,
        ]
        self.assertEqual(
            normalize_invoice_suggestions(values),
            ['INV2894', 'INV28-P302'],
        )

    def test_search_sql_looks_through_all_invoices(self):
        sql, params = invoice_suggestions_sql('INV28-P302', 80)
        self.assertIn('LIKE :q_like', sql)
        self.assertIn('не закрыт', sql)
        self.assertEqual(params['q'], 'INV28-P302')
        self.assertEqual(params['q_like'], '%inv28-p302%')
        self.assertEqual(params['lim'], 80)


if __name__ == '__main__':
    unittest.main()
