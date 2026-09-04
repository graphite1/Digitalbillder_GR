from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from invoice_manager.ui import invoice_list_window


class FakeStringVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class ProjectFilterPersistenceTests(unittest.TestCase):
    def make_window(self, selected: str = "すべて") -> SimpleNamespace:
        return SimpleNamespace(
            project_options={"すべて": None, "P001｜工事A": 7},
            selected_project_var=FakeStringVar(selected),
        )

    def test_restore_project_selection_by_id(self) -> None:
        window = self.make_window()
        with (
            patch.object(invoice_list_window, "get_app_setting", return_value="7"),
            patch.object(invoice_list_window, "set_app_setting") as set_app_setting,
        ):
            invoice_list_window.InvoiceListWindow.restore_project_selection(window)

        self.assertEqual(window.selected_project_var.get(), "P001｜工事A")
        set_app_setting.assert_not_called()

    def test_restore_missing_project_falls_back_to_all(self) -> None:
        window = self.make_window("P001｜工事A")
        with (
            patch.object(invoice_list_window, "get_app_setting", return_value="999"),
            patch.object(invoice_list_window, "set_app_setting") as set_app_setting,
        ):
            invoice_list_window.InvoiceListWindow.restore_project_selection(window)

        self.assertEqual(window.selected_project_var.get(), "すべて")
        set_app_setting.assert_called_once_with(
            invoice_list_window.PROJECT_SELECTION_SETTING_KEY,
            "",
        )

    def test_save_project_selection_uses_project_id(self) -> None:
        window = self.make_window("P001｜工事A")
        with patch.object(invoice_list_window, "set_app_setting") as set_app_setting:
            invoice_list_window.InvoiceListWindow.save_project_selection(window)

        set_app_setting.assert_called_once_with(
            invoice_list_window.PROJECT_SELECTION_SETTING_KEY,
            "7",
        )

    def test_reload_filter_options_restores_saved_project(self) -> None:
        window = self.make_window()
        window.project_combo = Mock()
        window.selected_vendor_var = FakeStringVar("すべて")
        window.vendor_options = {"すべて": None}
        window.vendor_combo = Mock()
        window.selected_month_var = FakeStringVar("すべて")
        window.month_options = {"すべて": None}
        window.month_combo = Mock()
        window.selected_date_from_var = FakeStringVar("すべて")
        window.selected_date_to_var = FakeStringVar("すべて")
        window.invoice_date_options = {"すべて": None}
        window.date_from_combo = Mock()
        window.date_to_combo = Mock()
        window.load_project_options = Mock()
        window.load_vendor_options = Mock()
        window.load_month_options = Mock()
        window.load_invoice_date_options = Mock()
        window.restore_project_selection = lambda: (
            invoice_list_window.InvoiceListWindow.restore_project_selection(window)
        )
        window.refresh = Mock()

        with patch.object(invoice_list_window, "get_app_setting", return_value="7"):
            invoice_list_window.InvoiceListWindow.reload_filter_options(window)

        self.assertEqual(window.selected_project_var.get(), "P001｜工事A")
        window.refresh.assert_called_once_with()

    def test_close_window_saves_before_closing(self) -> None:
        events: list[str] = []
        window = SimpleNamespace(
            save_project_selection=Mock(side_effect=lambda: events.append("save")),
            on_close=Mock(side_effect=lambda: events.append("close")),
            destroy=Mock(),
        )

        invoice_list_window.InvoiceListWindow.close_window(window)

        self.assertEqual(events, ["save", "close"])
        window.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
