from __future__ import annotations

import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from invoice_manager.services.web_allocation_plan import AllocationPlan
from invoice_manager.ui.historical_cost_window import HistoricalCostWindow
from invoice_manager.ui.web_allocation_preview_window import WebAllocationPreviewWindow


class BackgroundReadWindowsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.release = threading.Event()
        self.entered = threading.Event()
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.release.set)
        self.start_patch("invoice_manager.ui.historical_cost_window.list_historical_cost_filter_options",
                         return_value=SimpleNamespace(projects=(), work_types=(), vendors=()))
        self.start_patch("invoice_manager.ui.historical_cost_window.get_historical_sync_status",
                         return_value=SimpleNamespace(last_successful_refresh=None, active_invoice_count=0))
        self.start_patch("invoice_manager.ui.historical_cost_window.has_historical_costs", return_value=False)
        self.start_patch("invoice_manager.ui.historical_cost_window.list_costs", return_value=[])
        self.start_patch("invoice_manager.ui.web_allocation_preview_window.WebWriteGuard",
                         return_value=SimpleNamespace(status=lambda: SimpleNamespace(state="unverified", reason="test")))
        self.plan = AllocationPlan("test", "P1", "Test Vendor", "2026-09-06", 0, (), ())
        self.build_plan = self.start_patch("invoice_manager.ui.web_allocation_preview_window.build_allocation_plan",
                                          return_value=self.plan)
        self.show_error = self.start_patch("tkinter.messagebox.showerror")
        self.show_info = self.start_patch("tkinter.messagebox.showinfo")
        self.show_warning = self.start_patch("tkinter.messagebox.showwarning")

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def pump_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition(), "Background task did not reach the expected state")

    def assert_other_window_usable(self):
        other = tk.Toplevel(self.root)
        other.withdraw()
        value = tk.StringVar(other)
        entry = tk.Entry(other, textvariable=value)
        button = tk.Button(other, command=lambda: entry.insert(0, "editable"))
        button.invoke()
        self.assertEqual(value.get(), "editable")
        self.assertIsNone(other.grab_current())
        other.destroy()

    def assert_no_dialogs(self):
        self.show_error.assert_not_called()
        self.show_info.assert_not_called()
        self.show_warning.assert_not_called()

    def test_history_continues_when_hidden_and_other_window_remains_usable(self):
        worker_thread = []

        def refresh(progress):
            worker_thread.append(threading.current_thread())
            progress("Test fetch in progress")
            self.entered.set()
            self.release.wait(5)
            return "Test history saved"

        window = HistoricalCostWindow(self.root, on_refresh_history=refresh)
        with patch.object(window.activity, "update", wraps=window.activity.update) as update, \
                patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            window._run_refresh()
            self.pump_until(lambda: self.entered.is_set() and update.called)
            self.assertTrue(window.activity.running)
            self.assertFalse(worker_thread[0].daemon)
            self.assertNotEqual(worker_thread[0], threading.current_thread())
            window.reload()
            self.assertTrue(window.activity.running)
            self.assert_other_window_usable()
            window.close()
            self.assertEqual(window.state(), "withdrawn")
            self.assertTrue(window.busy)
            self.assertFalse(window.closing)
            self.release.set()
            self.pump_until(lambda: not window.busy)
            self.assertFalse(window.activity.running)
            finish.assert_called_once_with("Test history saved")
            self.assertEqual(str(window.refresh_button["state"]), "normal")
            self.assertTrue(window.winfo_exists())
        window.close()
        self.assert_no_dialogs()

    def test_web_read_continues_when_hidden_and_reports_completion_without_dialog(self):
        def read(plan, progress):
            progress("Test web read in progress")
            self.entered.set()
            self.release.wait(5)
            return SimpleNamespace(lines=(), archived=True)

        self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan", side_effect=read)
        window = WebAllocationPreviewWindow(self.root, 1)
        window.read_current()
        self.pump_until(lambda: "Test web" in window.progress.get())
        self.assertTrue(window.activity.running)
        self.assert_other_window_usable()
        window.close()
        self.assertEqual(window.state(), "withdrawn")
        self.release.set()
        self.pump_until(lambda: not window.busy)
        self.assertFalse(window.activity.running)
        self.assertIn("差分 0項目", window.progress.get())
        self.assertEqual(str(window.read_button["state"]), "normal")
        window.close()
        self.assert_no_dialogs()

    def test_history_failure_finishes_activity_and_enables_retry(self):
        def fail(progress):
            raise RuntimeError("Test history failure")

        window = HistoricalCostWindow(self.root, on_refresh_history=fail)
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            window._run_refresh()
            self.pump_until(lambda: not window.busy)
            self.assertFalse(window.activity.running)
            self.assertTrue(finish.call_args.kwargs["failed"])
            self.assertIn("Test history failure", finish.call_args.args[0])
            self.assertEqual(str(window.refresh_button["state"]), "normal")
        window.close()
        self.assert_no_dialogs()

    def test_web_failure_finishes_activity_and_enables_retry(self):
        self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan",
                         side_effect=RuntimeError("Test web failure"))
        window = WebAllocationPreviewWindow(self.root, 1)
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            window.read_current()
            self.pump_until(lambda: not window.busy)
            self.assertFalse(window.activity.running)
            self.assertTrue(finish.call_args.kwargs["failed"])
            self.assertIn("Test web failure", window.progress.get())
            self.assertEqual(str(window.read_button["state"]), "normal")
        window.close()
        self.assert_no_dialogs()

    def test_changed_plan_during_web_read_marks_result_stale(self):
        def read(plan, progress):
            self.entered.set()
            self.release.wait(5)
            return SimpleNamespace(lines=(), archived=True)

        self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan", side_effect=read)
        window = WebAllocationPreviewWindow(self.root, 1)
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            window.read_current()
            self.pump_until(self.entered.is_set)
            self.build_plan.return_value = SimpleNamespace(fingerprint="changed")
            self.release.set()
            self.pump_until(lambda: not window.busy)
            self.assertFalse(window.activity.running)
            self.assertTrue(finish.call_args.kwargs["failed"])
            self.assertIn("ローカル振分が変わりました", window.progress.get())
            self.assertEqual(window.diff_tree.get_children(), ())
        window.close()
        self.assert_no_dialogs()

    def test_changed_plan_before_read_never_starts_web_worker(self):
        read = self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan")
        window = WebAllocationPreviewWindow(self.root, 1)
        self.build_plan.return_value = SimpleNamespace(fingerprint="changed")
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            window.read_current()
            self.assertFalse(window.busy)
            self.assertFalse(window.activity.running)
            self.assertTrue(finish.call_args.kwargs["failed"])
        read.assert_not_called()
        window.close()
        self.assert_no_dialogs()


if __name__ == "__main__":
    unittest.main()
