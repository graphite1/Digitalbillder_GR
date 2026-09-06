import threading
import tkinter as tk
import unittest

from invoice_manager.services.operation_cancellation import (
    CancellationToken, OperationCancelled, begin_commit, cancellation_scope,
    check_cancelled, current_token,
)
from invoice_manager.ui.background_activity import ActivityPanel, BackgroundActivity, running_activities


class CancellationTests(unittest.TestCase):
    def test_requested_before_save_prevents_commit(self):
        token = CancellationToken()
        self.assertTrue(token.request())
        self.assertFalse(token.request())
        with self.assertRaises(OperationCancelled):
            token.begin_commit()

    def test_save_boundary_rejects_late_request(self):
        token = CancellationToken()
        with cancellation_scope(token):
            begin_commit()
            self.assertFalse(token.request())
            check_cancelled()
        self.assertFalse(token.can_cancel)
        self.assertFalse(token.requested)

    def test_cancel_and_save_race_has_only_one_winner(self):
        for _ in range(20):
            token = CancellationToken()
            barrier = threading.Barrier(2)
            result = []
            def cancel():
                barrier.wait()
                result.append(token.request())
            worker = threading.Thread(target=cancel)
            worker.start()
            barrier.wait()
            try:
                token.begin_commit()
                committed = True
            except OperationCancelled:
                committed = False
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [not committed])

    def test_scope_restores_parent_and_does_not_leak_to_other_threads(self):
        outer, inner = CancellationToken(), CancellationToken()
        values = []
        with cancellation_scope(outer):
            with cancellation_scope(inner):
                self.assertIs(current_token(), inner)
                worker = threading.Thread(target=lambda: values.append(current_token()))
                worker.start()
                worker.join(2)
            self.assertIs(current_token(), outer)
        self.assertIsNone(current_token())
        self.assertEqual(values, [None])

    def test_scope_is_restored_even_when_entering_cancelled_operation(self):
        token = CancellationToken()
        token.request()
        with self.assertRaises(OperationCancelled):
            with cancellation_scope(token):
                self.fail("must not run")
        self.assertIsNone(current_token())


class CancellationPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_shared_panel_can_cancel_hidden_work_without_marking_it_finished(self):
        owner = tk.Toplevel(self.root)
        owner.withdraw()
        activity = BackgroundActivity(owner, "Webの現在値を確認")
        token = CancellationToken()
        activity.start("読取り中", cancellation=token)
        local_panel = ActivityPanel(owner, activity)
        shared_panel = ActivityPanel(self.root)
        shared_panel.cancel_button.invoke()
        local_panel._render()
        self.assertTrue(token.requested)
        self.assertIn(activity, running_activities(self.root))
        self.assertEqual(local_panel.status_label.cget("text"), "中断待ち：Webの現在値を確認")
        self.assertTrue(local_panel.cancel_button.instate(["disabled"]))
        activity.update("中断前に送られた遅延通知")
        self.assertIn("中断を要求", activity.message)
        activity.finish("中断しました", cancelled=True)
        shared_panel._render()
        self.assertFalse(activity.failed)
        self.assertEqual(running_activities(self.root), ())
        self.assertEqual(shared_panel.status_label.cget("text"), "中断済み：Webの現在値を確認")

    def test_restart_uses_fresh_token_and_saving_disables_cancel(self):
        activity = BackgroundActivity(self.root, "取込")
        old = CancellationToken()
        activity.start("取得", cancellation=old)
        activity.request_cancel()
        activity.finish("中断", cancelled=True)
        new = CancellationToken()
        activity.start("再取得", cancellation=new)
        panel = ActivityPanel(self.root, activity)
        self.assertFalse(activity.cancelled)
        self.assertTrue(panel.cancel_button.instate(["!disabled"]))
        new.begin_commit()
        panel._render()
        self.assertTrue(panel.cancel_button.instate(["disabled"]))
        self.assertEqual(panel.cancel_button.cget("text"), "保存中")
        activity.request_cancel()
        self.assertFalse(new.requested)

    def test_selected_job_only_is_cancelled(self):
        first, second = BackgroundActivity(self.root, "A"), BackgroundActivity(self.root, "B")
        first.start("取得", cancellation=CancellationToken())
        second.start("取得", cancellation=CancellationToken())
        panel = ActivityPanel(self.root)
        panel.selector.current(panel._choices.index(second))
        panel._select_activity()
        panel.cancel_button.invoke()
        self.assertFalse(first.cancel_requested)
        self.assertTrue(second.cancel_requested)


if __name__ == "__main__":
    unittest.main()
