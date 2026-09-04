from __future__ import annotations

import unittest

from invoice_manager.utils.date_utils import billing_month_from_invoice_date
from invoice_manager.utils.money_utils import tax_excluded_amount, tax_included_amount


class MoneyAndDateTests(unittest.TestCase):
    def test_tax_conversion_uses_ten_percent_and_truncates(self) -> None:
        self.assertEqual(tax_excluded_amount(99_999), 90_908)
        self.assertEqual(tax_excluded_amount(-99_999), -90_908)
        self.assertEqual(tax_included_amount(90_909), 99_999)

    def test_billing_month_changes_on_the_tenth(self) -> None:
        self.assertEqual(billing_month_from_invoice_date("2026-08-09"), "2026-08")
        self.assertEqual(billing_month_from_invoice_date("2026-08-10"), "2026-09")
        self.assertEqual(billing_month_from_invoice_date("2026-12-10"), "2027-01")


if __name__ == "__main__":
    unittest.main()
