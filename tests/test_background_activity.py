import tkinter as tk
import unittest
from unittest.mock import patch

from invoice_manager.ui.background_activity import (
    ActivityPanel,
    BackgroundActivity,
    _registry,
    has_running_descendants,
    running_activities,
)


class BackgroundActivityTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_start_update_finish_and_restart(self):
        activity = BackgroundActivity(self.root, "実績取得")
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=100):
            activity.start("ログイン中")
        activity.update("2件目を確認中")
        self.assertEqual(running_activities(self.root), (activity,))
        self.assertEqual(activity.message, "2件目を確認中")
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=105):
            activity.finish("接続に失敗しました", failed=True)
        self.assertEqual(activity.elapsed_seconds, 5)
        self.assertTrue(activity.failed)
        self.assertFalse(activity.running)
        self.assertEqual(running_activities(self.root), ())
        activity.update("遅延した通知")
        self.assertEqual(activity.message, "接続に失敗しました")
        activity.start("再取得中")
        self.assertFalse(activity.failed)
        self.assertIsNone(activity.finished_at)
        self.assertEqual(len(_registry(self.root)), 1)

    def test_root_registry_is_separate(self):
        other_root = tk.Tk()
        other_root.withdraw()
        try:
            activity = BackgroundActivity(self.root, "検索")
            activity.start("検索中")
            self.assertEqual(running_activities(other_root), ())
            self.assertEqual(running_activities(self.root), (activity,))
        finally:
            other_root.destroy()

    def test_running_descendants_include_self_but_not_sibling(self):
        parent = tk.Frame(self.root)
        child = tk.Frame(parent)
        sibling = tk.Frame(self.root)
        activity = BackgroundActivity(child, "検索")
        activity.start("検索中")
        self.assertTrue(has_running_descendants(self.root))
        self.assertTrue(has_running_descendants(parent))
        self.assertTrue(has_running_descendants(child))
        self.assertFalse(has_running_descendants(sibling))
        activity.finish("完了")
        self.assertFalse(has_running_descendants(parent))

    def test_recent_completion_limit_retains_all_running_work(self):
        ongoing = BackgroundActivity(self.root, "継続中")
        ongoing.start("継続中")
        for index in range(30):
            activity = BackgroundActivity(self.root, str(index))
            activity.start("検索中")
            activity.finish("確認しました")
        self.assertEqual(len(_registry(self.root)), 21)
        self.assertEqual(running_activities(self.root), (ongoing,))
        self.assertEqual(_registry(self.root)[1].title, "10")

    def test_elapsed_time_refreshes_without_progress_messages(self):
        activity = BackgroundActivity(self.root, "検索")
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=100):
            activity.start("応答待ち")
            panel = ActivityPanel(self.root, activity)
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=165):
            self.root.after(550, self.root.quit)
            self.root.mainloop()
        self.assertEqual(panel.elapsed_label.cget("text"), "経過 01:05")
        self.assertEqual(panel.message_label.cget("text"), "応答待ち")
        self.assertIsNone(self.root.grab_current())
        panel.destroy()

    def test_background_status_does_not_block_other_callbacks(self):
        activity = BackgroundActivity(self.root, "取得")
        activity.start("取得中")
        panel = ActivityPanel(self.root, activity)
        callbacks = []
        self.root.after(5, lambda: callbacks.append("別画面の操作"))
        self.root.after(20, self.root.quit)
        self.root.mainloop()
        self.assertEqual(callbacks, ["別画面の操作"])
        self.assertIsNone(self.root.grab_current())
        panel.destroy()

    def test_shared_panel_selects_running_owner_and_reopens_withdrawn_window(self):
        first = tk.Toplevel(self.root)
        second = tk.Toplevel(self.root)
        first.withdraw()
        second.withdraw()
        one = BackgroundActivity(first, "検索")
        two = BackgroundActivity(second, "実績取得")
        one.start("検索中")
        two.start("実績確認中")
        panel = ActivityPanel(self.root)
        self.assertEqual(len(panel.selector.cget("values")), 2)
        panel.selector.current(1)
        panel._select_activity()
        self.assertEqual(panel.message_label.cget("text"), "実績確認中")
        with patch.object(second, "lift"), patch.object(second, "focus_set"):
            panel.open_button.invoke()
        self.assertEqual(second.state(), "normal")
        self.assertEqual(first.state(), "withdrawn")
        self.assertIsNone(self.root.grab_current())
        panel.destroy()

    def test_failure_is_explicit_and_completion_time_stops(self):
        activity = BackgroundActivity(self.root, "取得")
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=100):
            activity.start("取得中")
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=103):
            activity.finish("通信に失敗しました", failed=True)
        with patch("invoice_manager.ui.background_activity.time.monotonic", return_value=900):
            panel = ActivityPanel(self.root)
        self.assertIn("失敗", panel.status_label.cget("text"))
        self.assertEqual(str(panel.status_label.cget("foreground")), "#b00020")
        self.assertEqual(panel.elapsed_label.cget("text"), "経過 00:03")
        self.assertFalse(panel._animating)
        panel.destroy()

    def test_destroy_cancels_refresh_and_animation_callbacks(self):
        activity = BackgroundActivity(self.root, "取得")
        activity.start("取得中")
        baseline = set(self.root.tk.call("after", "info"))
        panel = ActivityPanel(self.root, activity)
        self.assertIsNotNone(panel._after_id)
        panel.destroy()
        self.assertIsNone(panel._after_id)
        self.assertEqual(set(self.root.tk.call("after", "info")), baseline)
        self.root.update()

    def test_closed_owner_disables_navigation(self):
        owner = tk.Toplevel(self.root)
        owner.withdraw()
        activity = BackgroundActivity(owner, "取得")
        activity.start("取得中")
        activity.finish("確認しました")
        owner.destroy()
        panel = ActivityPanel(self.root)
        self.assertEqual(str(panel.open_button.cget("state")), "disabled")
        panel.open_button.invoke()
        panel.destroy()

    def test_parent_destruction_also_cancels_panel_timer(self):
        owner = tk.Toplevel(self.root)
        owner.withdraw()
        activity = BackgroundActivity(owner, "取得")
        activity.start("取得中")
        baseline = set(self.root.tk.call("after", "info"))
        panel = ActivityPanel(owner, activity)
        owner.destroy()
        self.assertTrue(panel._closed)
        self.assertIsNone(panel._after_id)
        self.assertEqual(set(self.root.tk.call("after", "info")), baseline)
        self.root.update()

    def test_navigation_restores_hidden_transient_ancestors_but_not_root(self):
        hub = tk.Toplevel(self.root)
        detail = tk.Toplevel(hub)
        detail.transient(hub)
        preview = tk.Toplevel(detail)
        preview.transient(detail)
        owner = tk.Frame(preview)
        owner.pack()
        self.root.update()
        preview.withdraw()
        detail.withdraw()
        hub.withdraw()
        self.root.update()
        activity = BackgroundActivity(owner, "Web取得")
        activity.start("確認中")
        panel = ActivityPanel(self.root)
        order = []
        with (
            patch.object(hub, "deiconify", side_effect=lambda: (order.append("hub"), hub.wm_deiconify())),
            patch.object(detail, "deiconify", side_effect=lambda: (order.append("detail"), detail.wm_deiconify())),
            patch.object(preview, "deiconify", side_effect=lambda: (order.append("preview"), preview.wm_deiconify())),
            patch.object(preview, "lift"),
            patch.object(preview, "focus_set"),
        ):
            panel.open_button.invoke()
        self.root.update()
        self.assertEqual(order, ["hub", "detail", "preview"])
        for window in (hub, detail, preview):
            self.assertEqual(window.state(), "normal")
            self.assertTrue(window.winfo_viewable())
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertIsNone(self.root.grab_current())
        panel.destroy()

    def test_restarting_completed_activity_does_not_duplicate_history(self):
        activities = []
        for index in range(20):
            activity = BackgroundActivity(self.root, str(index))
            activity.start("開始")
            activity.finish("完了")
            activities.append(activity)
        restart = activities[0]
        restart.start("再取得中")
        self.assertEqual(len(_registry(self.root)), 20)
        self.assertEqual(running_activities(self.root), (restart,))
        for index in range(5):
            other = BackgroundActivity(self.root, f"追加 {index}")
            other.start("開始")
            other.finish("完了")
        self.assertEqual(len(_registry(self.root)), 21)
        restart.finish("再取得完了")
        self.assertEqual(len(_registry(self.root)), 20)
        self.assertEqual(_registry(self.root).count(restart), 1)
        self.assertEqual(_registry(self.root)[-1], restart)

    def test_destroyed_running_owner_does_not_break_shared_panel(self):
        owner = tk.Toplevel(self.root)
        owner.withdraw()
        activity = BackgroundActivity(owner, "取得")
        activity.start("取得中")
        panel = ActivityPanel(self.root)
        owner.destroy()
        panel._render()
        self.assertEqual(str(panel.open_button.cget("state")), "disabled")
        panel._show_owner()
        activity.finish("画面を閉じた後に処理が終了しました")
        panel._render()
        self.assertIn("完了", panel.status_label.cget("text"))
        self.assertEqual(running_activities(self.root), ())
        panel.destroy()


if __name__ == "__main__":
    unittest.main()
