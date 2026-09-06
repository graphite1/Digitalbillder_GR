from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow


class InvoiceRoundingUiTests(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(
            invoice_id=1, allocations=SimpleNamespace(selection=lambda: ("row",)),
            allocation_ids={"row": 2}, load_allocations=Mock(), on_saved=Mock(),
        )
        self.preview = SimpleNamespace(
            code="D570", name="その他工事", net_amount=22956, difference=1,
            tax_before=2295, tax_after=2296, gross_before=25251, gross_after=25252,
        )

    def test_confirmed_adjustment_uses_reviewed_snapshot_and_reloads(self):
        with patch("invoice_manager.services.allocation_rounding.preview_rounding_adjustment", return_value=self.preview), \
                patch("invoice_manager.services.allocation_rounding.apply_rounding_adjustment") as apply, \
                patch("invoice_manager.ui.invoice_detail_window.messagebox.askyesno", return_value=True) as confirm:
            InvoiceDetailWindow.adjust_tax_rounding(self.window)
        self.assertIn("22,956", confirm.call_args.args[1])
        self.assertIn("2,295円 → 2,296円", confirm.call_args.args[1])
        apply.assert_called_once_with(1, 2, self.preview)
        self.window.load_allocations.assert_called_once()

    def test_cancel_leaves_allocations_untouched(self):
        with patch("invoice_manager.services.allocation_rounding.preview_rounding_adjustment", return_value=self.preview), \
                patch("invoice_manager.services.allocation_rounding.apply_rounding_adjustment") as apply, \
                patch("invoice_manager.ui.invoice_detail_window.messagebox.askyesno", return_value=False):
            InvoiceDetailWindow.adjust_tax_rounding(self.window)
        apply.assert_not_called()
        self.window.load_allocations.assert_not_called()

    def test_stale_snapshot_error_is_visible_without_success_reload(self):
        with patch("invoice_manager.services.allocation_rounding.preview_rounding_adjustment", return_value=self.preview), \
                patch("invoice_manager.services.allocation_rounding.apply_rounding_adjustment", side_effect=ValueError("変更されています")), \
                patch("invoice_manager.ui.invoice_detail_window.messagebox.askyesno", return_value=True), \
                patch("invoice_manager.ui.invoice_detail_window.messagebox.showerror") as error:
            InvoiceDetailWindow.adjust_tax_rounding(self.window)
        self.assertIn("変更されています", error.call_args.args[1])
        self.window.load_allocations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
