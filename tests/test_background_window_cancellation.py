from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_background_read_windows as fixtures
from invoice_manager.services.operation_cancellation import begin_commit, check_cancelled
from invoice_manager.ui.digital_billder_sync_window import DigitalBillderSyncWindow
from invoice_manager.ui.historical_cost_window import HistoricalCostWindow
from invoice_manager.ui.web_allocation_preview_window import WebAllocationPreviewWindow
from invoice_manager.ui.background_activity import ActivityPanel


class BackgroundWindowCancellationTests(unittest.TestCase):
    setUp = fixtures.BackgroundReadWindowsTests.setUp
    start_patch = fixtures.BackgroundReadWindowsTests.start_patch
    pump_until = fixtures.BackgroundReadWindowsTests.pump_until
    assert_no_dialogs = fixtures.BackgroundReadWindowsTests.assert_no_dialogs

    def make_window(self, kind, callback):
        if kind == "web":
            self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan",
                             side_effect=lambda plan, progress: callback(progress))
            window = WebAllocationPreviewWindow(self.root, 1)
            return window, window.read_current, window.read_button, window.messages
        if kind == "history":
            window = HistoricalCostWindow(self.root, on_refresh_history=callback)
            return window, window._run_refresh, window.refresh_button, window.events
        self.start_patch("invoice_manager.ui.digital_billder_sync_window.list_candidates", return_value=[])
        window = DigitalBillderSyncWindow(self.root)
        return window, lambda: window.start(callback), window.buttons[0], window.events

    def test_each_window_waits_for_worker_then_marks_cancelled_without_applying_results(self):
        for kind in ("web", "history", "sync"):
            with self.subTest(kind=kind):
                self.release.clear()
                self.entered.clear()

                def callback(progress):
                    progress("取得中")
                    self.entered.set()
                    self.release.wait(5)
                    check_cancelled()
                    raise AssertionError("Cancelled work must not return results")

                window, start, button, events = self.make_window(kind, callback)
                with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
                    start()
                    self.pump_until(self.entered.is_set)
                    token = window.cancellation
                    self.assertTrue(token.request())
                    events.put(("progress", "中断前の古い通知"))
                    self.pump_until(events.empty)
                    self.assertTrue(window.busy)
                    self.assertTrue(window.activity.running)
                    self.assertEqual(str(button["state"]), "disabled")
                    if kind != "history":
                        label = window.progress if kind == "web" else window.status
                        self.assertIn("中断を待っています", label.get())
                    window.close()
                    self.assertEqual(window.state(), "withdrawn")
                    self.release.set()
                    self.pump_until(lambda: not window.busy)
                    self.assertFalse(window.activity.running)
                    finish.assert_called_once()
                    self.assertTrue(finish.call_args.kwargs["cancelled"])
                    self.assertEqual(str(button["state"]), "normal")
                    if kind == "web":
                        self.assertEqual(window.diff_tree.get_children(), ())
                window.close()
        self.assert_no_dialogs()

    def test_exception_after_cancel_is_cancelled_instead_of_failed(self):
        def callback(progress):
            self.entered.set()
            self.release.wait(5)
            raise RuntimeError("Browser stopped during cancellation")

        window, start, _, _ = self.make_window("sync", callback)
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            start()
            self.pump_until(self.entered.is_set)
            window.cancellation.request()
            self.release.set()
            self.pump_until(lambda: not window.busy)
            self.assertTrue(finish.call_args.kwargs["cancelled"])
            self.assertNotIn("Browser stopped", window.status.get())
        window.close()

    def test_retry_uses_fresh_token_and_completed_result_is_displayed(self):
        calls = []

        def callback(progress):
            calls.append(1)
            if len(calls) == 1:
                self.entered.set()
                self.release.wait(5)
                check_cancelled()
            return SimpleNamespace(lines=(), archived=True)

        window, start, _, _ = self.make_window("web", callback)
        start()
        self.pump_until(self.entered.is_set)
        previous = window.cancellation
        previous.request()
        self.release.set()
        self.pump_until(lambda: not window.busy)
        start()
        self.assertIsNot(window.cancellation, previous)
        self.assertFalse(window.cancellation.requested)
        self.pump_until(lambda: not window.busy)
        self.assertIn("差分 0項目", window.progress.get())
        window.close()

    def test_commit_boundary_rejects_cancel_and_reports_actual_completion(self):
        def callback(progress):
            begin_commit()
            self.entered.set()
            self.release.wait(5)
            return "保存完了"

        window, start, _, _ = self.make_window("sync", callback)
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            start()
            self.pump_until(self.entered.is_set)
            self.assertFalse(window.cancellation.request())
            self.assertTrue(window.busy)
            self.release.set()
            self.pump_until(lambda: not window.busy)
            self.assertEqual(window.status.get(), "保存完了")
            self.assertFalse(finish.call_args.kwargs.get("cancelled", False))
        window.close()

    def test_done_already_queued_is_not_relabelled_cancelled(self):
        window, start, _, events = self.make_window("sync", lambda progress: "完了済み")
        with patch.object(window.activity, "finish", wraps=window.activity.finish) as finish:
            start()
            deadline = time.monotonic() + 5
            # Deliberately do not pump Tk until the worker has queued completion.
            while events.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(events.empty())
            window.cancellation.request()
            self.pump_until(lambda: not window.busy)
            self.assertEqual(window.status.get(), "完了済み")
            self.assertFalse(finish.call_args.kwargs.get("cancelled", False))
        window.close()

    def test_web_result_queued_before_cancel_is_discarded_without_comparison(self):
        window, start, button, events = self.make_window(
            "web", lambda progress: SimpleNamespace(lines=(), archived=True)
        )
        with patch("invoice_manager.ui.web_allocation_preview_window.compare_allocations") as compare:
            start()
            deadline = time.monotonic() + 5
            # Finish the worker while deliberately leaving Tk's result poll pending.
            while events.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(events.empty())
            self.assertTrue(window.busy)
            window.activity.request_cancel()
            self.assertTrue(window.cancellation.requested)
            self.assertTrue(window.activity.running)
            self.pump_until(lambda: not window.busy)
            compare.assert_not_called()
            self.assertTrue(window.activity.cancelled)
            self.assertFalse(window.activity.failed)
            self.assertFalse(window.activity.running)
            self.assertIn("中断しました", window.progress.get())
            self.assertEqual(window.diff_tree.get_children(), ())
            self.assertEqual(str(button["state"]), "normal")
        window.close()
        self.assert_no_dialogs()

    def test_shared_panel_identifies_invoices_and_cancels_only_selected_read(self):
        plans = {
            1: replace(self.plan, vendor_name="合成取引先A", invoice_date="2026-08-31"),
            2: replace(self.plan, external_id="test-2", vendor_name="合成取引先B", invoice_date="2026-09-01"),
        }
        self.build_plan.side_effect = plans.__getitem__

        def read(plan, progress):
            self.release.wait(5)
            check_cancelled()
            return SimpleNamespace(lines=(), archived=True)

        self.start_patch("invoice_manager.ui.web_allocation_preview_window.read_for_plan", side_effect=read)
        first = WebAllocationPreviewWindow(self.root, 1)
        second = WebAllocationPreviewWindow(self.root, 2)
        first.read_current()
        second.read_current()
        panel = ActivityPanel(self.root)
        labels = panel.selector.cget("values")
        self.assertEqual(len(labels), 2)
        self.assertNotEqual(labels[0], labels[1])
        for label, plan in zip(labels, plans.values()):
            self.assertIn(plan.vendor_name, label)
            self.assertIn(plan.invoice_date, label)
        panel.selector.current(1)
        panel._select_activity()
        panel.cancel_button.invoke()
        self.assertFalse(first.cancellation.requested)
        self.assertTrue(second.cancellation.requested)
        self.assertTrue(first.busy)
        self.assertTrue(second.busy)
        self.assertIn("中断待ち", panel.status_label.cget("text"))
        self.release.set()
        self.pump_until(lambda: not first.busy and not second.busy)
        self.assertFalse(first.activity.cancelled)
        self.assertIn("差分 0項目", first.progress.get())
        self.assertTrue(second.activity.cancelled)
        self.assertEqual(second.diff_tree.get_children(), ())
        panel.destroy()
        first.close()
        second.close()
        self.assert_no_dialogs()


if __name__ == "__main__":
    unittest.main()
