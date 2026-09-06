from __future__ import annotations

import tkinter as tk
import unittest
from tkinter import ttk
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow
from invoice_manager.services.work_type_resolution import CanonicalWorkType


class InvoiceAllocationDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.view = SimpleNamespace(
            invoice_id=1,
            invoice_total=462000,
            invoice_total_excluded=420000,
            amount_display_mode="税抜",
            allocations=ttk.Treeview(
                self.root, columns=("code", "name", "amount", "memo", "sort_order", "tax_rate")
            ),
            allocation_ids={}, allocation_amounts={}, allocation_rates={}, allocation_gross={},
            allocation_summary_var=tk.StringVar(self.root),
            info_vars={"total_amount": tk.StringVar(self.root)},
            update_mark_selection_status=lambda: None,
        )
        for name in ("amount_for_display", "update_amount_headers", "load_allocations", "set_amount_display_mode"):
            setattr(self.view, name, MethodType(getattr(InvoiceDetailWindow, name), self.view))

    @staticmethod
    def row(number, gross, net, rate):
        return dict(id=number, code=str(number), name="試験工種", amount=gross,
                    amount_excluded=net, tax_rate=rate, memo="", sort_order=number)

    def test_unallocated_invoice_uses_net_total(self):
        with patch("invoice_manager.ui.invoice_detail_window.list_invoice_allocations", return_value=[]):
            self.view.load_allocations()
        self.assertEqual(self.view.allocation_summary_var.get(),
                         "請求金額(税抜): 420,000円 / 振分合計(税抜): 0円 / 未振分額(税抜): 420,000円")

    def test_mixed_rates_and_rounding_keep_net_when_global_mode_changes(self):
        # Net values must not be reconstructed by dividing the gross sum by 1.1.
        rows = [self.row(1, 110, 101, "10"), self.row(2, 108, 100, "8"),
                self.row(3, 100, 100, "exempt")]
        self.view.invoice_total = 318
        self.view.invoice_total_excluded = 301
        with patch("invoice_manager.ui.invoice_detail_window.list_invoice_allocations", return_value=rows):
            for mode in ("税抜", "税込", "税抜"):
                self.view.set_amount_display_mode(mode)
                self.assertEqual(self.view.allocations.heading("amount", "text"), "振分金額(税抜)")
                self.assertEqual([self.view.allocations.item(item, "values")[2]
                                  for item in self.view.allocations.get_children()], ["101", "100", "100"])
                self.assertEqual(self.view.allocation_summary_var.get(),
                                 "請求金額(税抜): 301円 / 振分合計(税抜): 301円 / 未振分額(税抜): 0円")
                self.assertEqual(list(self.view.allocation_gross.values()), [110, 108, 100])
                self.assertEqual(self.view.info_vars["total_amount"].get(),
                                 "318円" if mode == "税込" else "301円")

    def test_legacy_rows_use_each_tax_rate_and_show_net_excess(self):
        rows = [self.row(1, 110, None, "10"), self.row(2, 108, None, "8"),
                self.row(3, 100, None, "exempt")]
        self.view.invoice_total = 310
        self.view.invoice_total_excluded = 290
        with patch("invoice_manager.ui.invoice_detail_window.list_invoice_allocations", return_value=rows):
            self.view.load_allocations()
        self.assertEqual(self.view.allocation_summary_var.get(),
                         "請求金額(税抜): 290円 / 振分合計(税抜): 300円 / 超過額(税抜): 10円  ※超過しています")

    def test_dialog_accepts_digits_and_shows_official_code_before_save(self):
        self.root.invoice_id = 1
        self.root.work_type_options = {"513｜正式名称": "D513"}
        self.root.confirmed_work_types = (CanonicalWorkType("D513", "正式名称"),)
        self.root.load_work_type_options = lambda: None
        self.root.load_allocations = lambda: None
        with patch.object(tk.Toplevel, "wait_window", return_value=None):
            InvoiceDetailWindow.open_allocation_dialog(self.root)
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        combo = next(child for child in dialog.winfo_children()
                     if isinstance(child, ttk.Combobox) and int(child.grid_info()["row"]) == 0)
        self.assertEqual(str(combo.cget("state")), "normal")
        combo.set("999")
        hint = next(child for child in dialog.winfo_children()
                    if isinstance(child, tk.Label) and int(child.grid_info()["row"]) == 7)
        self.assertIn("確認できません", self.root.getvar(hint.cget("textvariable")))
        combo.set("513")
        self.assertIn("D513｜正式名称", self.root.getvar(hint.cget("textvariable")))
        amount = next(child for child in dialog.winfo_children()
                      if isinstance(child, tk.Entry) and int(child.grid_info()["row"]) == 1)
        amount.insert(0, "420000")
        buttons = next(child for child in dialog.winfo_children() if isinstance(child, tk.Frame))
        save = next(child for child in buttons.winfo_children() if child.cget("text") == "保存")
        with patch("invoice_manager.ui.invoice_detail_window.save_resolved_allocation") as saver:
            save.invoke()
        self.assertEqual(saver.call_args.kwargs["work_type_input"], "513")
        self.assertEqual(saver.call_args.kwargs["amount"], 420000)


if __name__ == "__main__":
    unittest.main()
