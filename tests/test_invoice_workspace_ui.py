from __future__ import annotations

import threading
import time
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import Mock, patch

from invoice_manager.ui.background_activity import ActivityPanel
from invoice_manager.ui.digital_billder_sync_window import DigitalBillderSyncWindow
from invoice_manager.ui.invoice_list_window import InvoiceListWindow
from invoice_manager.ui.main_window import open_management_hub


class InvoiceWorkspaceUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.release = threading.Event()
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.release.set)
        self.rows = [self.row(1, "2026-08"), self.row(2, "2026-09"),
                     self.row(3, "2026-08"), self.row(4, "2026-10")]
        self.patched("invoice_manager.ui.invoice_list_window.get_app_setting", return_value="")
        self.save_setting = self.patched("invoice_manager.ui.invoice_list_window.set_app_setting")
        self.can_use_test_tools = self.patched("invoice_manager.ui.invoice_list_window.can_use_test_tools", return_value=True)
        self.require_test_tools = self.patched("invoice_manager.ui.invoice_list_window.require_test_tools_access")
        self.patched("invoice_manager.ui.invoice_list_window.list_projects", return_value=[])
        self.patched("invoice_manager.ui.invoice_list_window.list_vendors", return_value=[])
        self.patched("invoice_manager.ui.invoice_list_window.list_billing_months", return_value=["2026-08", "2026-09", "2026-10"])
        self.patched("invoice_manager.ui.invoice_list_window.list_invoice_dates", return_value=["2026-08-31"])
        self.list_invoices = self.patched("invoice_manager.ui.invoice_list_window.list_invoices", return_value=self.rows)
        self.patched("invoice_manager.ui.digital_billder_sync_window.list_candidates", return_value=[])
        self.ask = self.patched("tkinter.messagebox.askyesno", return_value=False)
        self.patched("tkinter.messagebox.showinfo")
        self.patched("tkinter.messagebox.showerror")
        self.window = InvoiceListWindow(self.root, open_hub=open_management_hub)

    def patched(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    @staticmethod
    def row(identifier, month):
        return dict(id=identifier, billing_month=month, project_code="P001", project_name="試験工事",
                    vendor_name="試験取引先", invoice_date="2026-08-31", total_amount=110,
                    total_amount_excluded=100, file_count=1, local_memo="合成データのメモ",
                    contact_name="試験担当", email="test@example.invalid", phone="000-0000")

    def pump_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(condition(), "UI background operation did not finish")

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            if not isinstance(child, tk.Toplevel):
                yield child
                yield from InvoiceWorkspaceUiTests.descendants(child)

    def test_widgets_fit_both_workspace_sizes(self):
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.on_select()
        for width, height in ((1440, 840), (1100, 740)):
            with self.subTest(size=(width, height)):
                self.window.action_tabs.select(0)
                self.window.geometry(f"{width}x{height}+0+0")
                self.root.update()
                wx, wy = self.window.winfo_rootx(), self.window.winfo_rooty()
                clipped = []
                for widget in self.descendants(self.window):
                    if not widget.winfo_ismapped():
                        continue
                    x, y = widget.winfo_rootx()-wx, widget.winfo_rooty()-wy
                    if x < -1 or y < -1 or x+widget.winfo_width() > width+1 or y+widget.winfo_height() > height+1:
                        clipped.append((str(widget), widget.winfo_class(), x, y, widget.winfo_width(), widget.winfo_height()))
                self.assertEqual(clipped, [])
                self.assertGreater(self.window.tree.winfo_height(), 100)
                self.assertGreater(self.window.sidebar.winfo_width(), 200)
                for control in (self.window.project_combo, self.window.vendor_combo,
                                self.window.amount_display_combo, self.window.detail_button,
                                self.window.memo_entry, self.window.memo_button, self.window.delete_button):
                    self.assertTrue(control.winfo_ismapped(), str(control))
                    self.assertGreater(control.winfo_height(), 10, str(control))
                self.window.action_tabs.select(self.window.trial_frame)
                self.root.update()
                for control in (self.window.reset_month_button, self.window.recalculate_month_button):
                    self.assertTrue(control.winfo_ismapped(), str(control))
                    self.assertGreater(control.winfo_height(), 10, str(control))
                    self.assertLessEqual(control.winfo_rootx()-wx+control.winfo_width(), width)
                    self.assertLessEqual(control.winfo_rooty()-wy+control.winfo_height(), height)

    def test_destroying_hub_cancels_idle_sync_poll(self):
        hub = open_management_hub(self.window)
        sync = DigitalBillderSyncWindow(hub)
        pending = sync.poll_id
        self.assertIn(pending, self.root.tk.call("after", "info"))
        hub.destroy()
        self.assertNotIn(pending, self.root.tk.call("after", "info"))
        self.assertIsNone(sync.poll_id)

    def test_sync_list_refresh_failure_keeps_poll_and_retry_available(self):
        hub = open_management_hub(self.window)
        sync = DigitalBillderSyncWindow(hub)
        sync.after_cancel(sync.poll_id)
        sync.activity.start("取得中")
        sync.busy = True
        sync.events.put(("done", "取込完了: 1件"))
        with patch.object(sync, "refresh", side_effect=RuntimeError("DB read failed")):
            sync.poll()
        self.assertFalse(sync.busy)
        self.assertTrue(sync.activity.failed)
        self.assertIn("取込完了: 1件", sync.status.get())
        self.assertIn("一覧再表示に失敗", sync.status.get())
        self.assertIn(sync.poll_id, self.root.tk.call("after", "info"))
        self.assertEqual(str(sync.buttons[0]["state"]), "normal")

    def test_month_colors_follow_month_groups_even_when_rows_are_not_adjacent(self):
        children = self.window.tree.get_children()
        tags = [self.window.tree.item(item, "tags") for item in children]
        self.assertEqual(tags[0], tags[2])
        self.assertNotEqual(tags[0], tags[1])
        self.assertNotEqual(tags[1], tags[3])
        self.assertEqual(tags[0], tags[3])

    def test_trial_controls_are_separate_and_reset_only_displayed_ids_after_confirmation(self):
        labels = [self.window.action_tabs.tab(tab, "text") for tab in self.window.action_tabs.tabs()]
        self.assertEqual(labels, ["通常操作", "試験用（管理者）"])
        self.assertNotEqual(self.window.detail_button.master, self.window.trial_frame)
        self.window.action_tabs.select(self.window.trial_frame)
        trial_buttons = [w for w in self.window.trial_frame.winfo_children() if isinstance(w, ttk.Button)]
        reset_button = next(w for w in trial_buttons if "自動判定" in str(w["text"]))
        reset = self.patched("invoice_manager.repositories.reset_invoice_billing_months_to_auto", return_value=2)
        self.list_invoices.return_value = [self.rows[0], self.rows[2]]
        self.window.refresh()
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        reset_button.invoke()
        reset.assert_not_called()
        self.ask.return_value = True
        reset_button.invoke()
        reset.assert_called_once_with([1, 3])

    def test_selection_details_and_amount_mode_reach_detail_window(self):
        item = self.window.tree.get_children()[0]
        self.window.tree.selection_set(item)
        self.window.on_select()
        self.assertIn("試験取引先", self.window.selected_info_var.get())
        self.assertIn("工事コード: P001", self.window.selected_info_var.get())
        self.assertEqual(self.window.memo_var.get(), "合成データのメモ")
        self.assertEqual(str(self.window.detail_button["state"]), "normal")
        detail = self.patched("invoice_manager.ui.invoice_detail_window.InvoiceDetailWindow")
        self.window.open_detail()
        self.assertEqual(detail.call_args.kwargs["amount_display_mode"], "税抜")
        existing_detail = tk.Toplevel(self.window)
        existing_detail.withdraw()
        existing_detail.set_amount_display_mode = Mock()
        self.window.amount_display_var.set("税込")
        self.window.on_amount_display_selected()
        existing_detail.set_amount_display_mode.assert_called_once_with("税込")
        self.assertEqual(self.window.tree.heading("total_amount", "text"), "請求金額(税込)")
        self.assertIn("110", self.window.tree.item(self.window.tree.get_children()[0], "values"))
        self.assertIn("税込", self.window.summary_var.get())

    def test_non_admin_trial_controls_are_disabled_and_direct_reset_is_rejected(self):
        self.can_use_test_tools.return_value = False
        self.require_test_tools.side_effect = PermissionError("管理者用の操作です")
        self.window._refresh_test_access()
        self.assertEqual(str(self.window.reset_month_button["state"]), "disabled")
        self.assertEqual(str(self.window.recalculate_month_button["state"]), "disabled")
        self.assertEqual(self.window.action_tabs.tab(self.window.trial_frame, "state"), "disabled")
        reset = self.patched("invoice_manager.repositories.reset_invoice_billing_months_to_auto")
        self.window.reset_displayed_billing_months()
        reset.assert_not_called()
        self.ask.assert_not_called()

    def test_fetch_leaves_invoice_input_usable_and_hidden_worker_can_be_reopened(self):
        entered = threading.Event()

        def fetch(progress):
            progress("合成データを確認中")
            entered.set()
            self.release.wait(5)
            return 0

        self.patched("invoice_manager.ui.digital_billder_sync_window.fetch_candidates", side_effect=fetch)
        hub = open_management_hub(self.window)
        self.assertIs(open_management_hub(self.window), hub)
        sync = DigitalBillderSyncWindow(hub)
        sync.fetch()
        self.pump_until(lambda: entered.is_set() and sync.activity.message == "合成データを確認中")
        item = self.window.tree.get_children()[0]
        self.window.tree.selection_set(item)
        self.window.on_select()
        self.root.update()
        self.window.memo_entry.delete(0, tk.END)
        self.window.memo_entry.insert(0, "取得中も入力可能")
        self.assertEqual(self.window.memo_var.get(), "取得中も入力可能")
        self.assertIsNone(self.window.grab_current())
        sync.close()
        hub.withdraw()
        self.assertTrue(sync.busy)
        self.assertEqual(sync.state(), "withdrawn")
        panel = next(w for w in self.window.winfo_children() if isinstance(w, ActivityPanel))
        panel._render()
        panel._show_owner()
        self.root.update()
        self.assertEqual(sync.state(), "normal")
        self.assertEqual(hub.state(), "normal")
        self.release.set()
        self.pump_until(lambda: not sync.busy)
        self.assertFalse(sync.activity.running)
        self.assertTrue(self.window.winfo_exists())
        self.assertEqual(self.window.memo_var.get(), "取得中も入力可能")
        sync.close()


if __name__ == "__main__":
    unittest.main()
