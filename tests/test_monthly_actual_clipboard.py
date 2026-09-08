from types import SimpleNamespace
import unittest

from invoice_manager.ui.monthly_work_type_summary_window import monthly_actuals_tsv


class MonthlyActualClipboardTests(unittest.TestCase):
    def test_copies_the_visible_columns_as_excel_matrix(self) -> None:
        text = monthly_actuals_tsv((
            SimpleNamespace(
                work_type_code="D301", work_type_name="仮設工", invoice_count=2,
                allocation_line_count=3, net_amount=500_000,
            ),
        ))

        self.assertEqual(text, (
            "工種コード\t工種名\t請求書数\t振分行数\t振分金額(税抜)\n"
            "D301\t仮設工\t2\t3\t500000"
        ))


if __name__ == "__main__":
    unittest.main()
