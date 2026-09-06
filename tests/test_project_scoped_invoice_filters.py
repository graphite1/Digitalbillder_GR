from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.ui.invoice_list_window import InvoiceListWindow
from invoice_manager.utils.date_utils import format_billing_month, format_invoice_date
from tests.test_repository_behaviors import make_row


class InvoiceFilterFixture:
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        data_dir = Path(folder.name) / "synthetic-data"
        for name, value in (("DATA_DIR", data_dir), ("DB_PATH", data_dir / "app.db")):
            patcher = patch.object(db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.initialize_database()
        batch_id = repositories.create_import_batch("2026-09", Path("synthetic.csv"),
                                                     Path("synthetic.zip"), "csv", "zip", "")
        invoices = (
            ("A1", "PA", "取引先A", "2026-08-01", "2026-08"),
            ("A2", "PA", "共通取引先", "2026-09-05", "2026-09"),
            ("A3", "PA", "取引先A", "2026-08-03", ""),
            ("A4", "PA", "取引先A", "2026-08-01", "2026-08"),
            ("B1", "PB", "取引先B", "2026-10-01", "2026-10"),
            ("B2", "PB", "共通取引先", "2026-09-05", "2026-09"),
            ("H1", "PH", "非表示工事だけの取引先", "2026-12-01", "2026-12"),
        )
        for external_id, project, vendor, date, month in invoices:
            repositories.insert_invoice(make_row(external_id, project_code=project,
                                                  vendor_name=vendor, invoice_date=date), month, batch_id)
        self.projects = {row["project_code"]: int(row["id"]) for row in repositories.list_projects()}
        repositories.set_project_active(self.projects["PH"], False)
        self.projects["PE"] = repositories.get_or_create_project("PE", "請求なし工事")
        repositories.get_or_create_vendor("請求なしのマスタ取引先")

    def candidate_values(self, **kwargs):
        return (
            {row["vendor_name"] for row in repositories.list_vendors(**kwargs)},
            set(repositories.list_billing_months(include_blank=True, **kwargs)),
            set(repositories.list_invoice_dates(**kwargs)),
        )


class ProjectScopedFilterRepositoryTests(InvoiceFilterFixture, unittest.TestCase):
    def test_project_candidates_come_only_from_its_invoices_and_are_distinct(self):
        self.assertEqual(self.candidate_values(project_id=self.projects["PA"], active_projects_only=True), (
            {"取引先A", "共通取引先"},
            {"2026-08", "2026-09", ""},
            {"2026-08-01", "2026-08-03", "2026-09-05"},
        ))
        self.assertEqual(len(repositories.list_vendors(project_id=self.projects["PA"])), 2)
        self.assertEqual(len(repositories.list_invoice_dates(project_id=self.projects["PA"])), 3)
        self.assertEqual(repositories.list_billing_months(project_id=self.projects["PA"]),
                         ["2026-09", "2026-08"])

    def test_all_projects_excludes_hidden_and_unused_master_candidates(self):
        self.assertEqual(self.candidate_values(active_projects_only=True), (
            {"取引先A", "取引先B", "共通取引先"},
            {"2026-08", "2026-09", "2026-10", ""},
            {"2026-08-01", "2026-08-03", "2026-09-05", "2026-10-01"},
        ))
        visible_rows = repositories.list_invoices({"active_projects_only": "1"})
        vendors, months, dates = self.candidate_values(active_projects_only=True)
        self.assertEqual(vendors, {row["vendor_name"] for row in visible_rows})
        self.assertEqual(months, {row["billing_month"] for row in visible_rows})
        self.assertEqual(dates, {row["invoice_date"] for row in visible_rows})

    def test_empty_hidden_and_missing_projects_do_not_fall_back_to_global_candidates(self):
        for project_id in (self.projects["PE"], self.projects["PH"], -1):
            with self.subTest(project_id=project_id):
                self.assertEqual(self.candidate_values(project_id=project_id, active_projects_only=True),
                                 (set(), set(), set()))

    def test_unscoped_defaults_remain_compatible_for_other_windows(self):
        vendors, months, dates = self.candidate_values()
        self.assertIn("請求なしのマスタ取引先", vendors)
        self.assertIn("非表示工事だけの取引先", vendors)
        self.assertIn("2026-12", months)
        self.assertIn("2026-12-01", dates)
        self.assertEqual(self.candidate_values(project_id=self.projects["PH"]), (
            {"非表示工事だけの取引先"}, {"2026-12"}, {"2026-12-01"},
        ))


class ProjectScopedFilterTkTests(InvoiceFilterFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def make_window(self, project="PA"):
        repositories.set_app_setting("selected_project_id", str(self.projects[project]))
        window = InvoiceListWindow(self.root)
        window.withdraw()
        return window

    def select_project(self, window, project):
        project_id = self.projects[project] if project is not None else None
        label = next(label for label, value in window.project_options.items() if value == project_id)
        window.selected_project_var.set(label)
        window.on_project_filter_selected()

    def select_filters(self, window, vendor, month, date_from, date_to):
        window.selected_vendor_var.set(vendor)
        window.selected_month_var.set(format_billing_month(month))
        window.selected_date_from_var.set(format_invoice_date(date_from))
        window.selected_date_to_var.set(format_invoice_date(date_to))

    def filter_selection(self, window):
        return tuple(variable.get() for variable in (
            window.selected_vendor_var, window.selected_month_var,
            window.selected_date_from_var, window.selected_date_to_var,
        ))

    def test_startup_restores_project_before_building_candidates(self):
        window = self.make_window()
        self.assertEqual(set(window.vendor_options), {"すべて", "取引先A", "共通取引先"})
        self.assertEqual(set(window.month_options), {"すべて", "未設定", "2026年8月", "2026年9月"})
        self.assertEqual(set(window.invoice_date_options.values()),
                         {None, "2026-08-01", "2026-08-03", "2026-09-05"})
        self.assertEqual(set(window.vendor_combo.cget("values")), set(window.vendor_options))
        self.assertEqual(set(window.date_from_combo.cget("values")), set(window.invoice_date_options))
        self.assertEqual(set(window.date_to_combo.cget("values")), set(window.invoice_date_options))

    def test_switch_project_resets_invalid_filters_without_changing_invoice_data_or_display_settings(self):
        window = self.make_window()
        self.select_filters(window, "取引先A", "2026-08", "2026-08-01", "2026-08-03")
        window.amount_display_var.set("税込")
        window.on_amount_display_selected()
        window.selected_sort_var.set("金額（高い順）")
        with db.get_connection() as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM invoices ORDER BY id")]
        self.select_project(window, "PB")
        self.assertEqual(self.filter_selection(window), ("すべて",) * 4)
        self.assertEqual(set(window.vendor_options), {"すべて", "取引先B", "共通取引先"})
        self.assertEqual(set(window.invoice_date_options.values()), {None, "2026-09-05", "2026-10-01"})
        self.assertEqual(len(window.tree.get_children()), 2)
        self.assertEqual(window.amount_display_var.get(), "税込")
        self.assertEqual(window.selected_sort_var.get(), "金額（高い順）")
        with db.get_connection() as conn:
            after = [tuple(row) for row in conn.execute("SELECT * FROM invoices ORDER BY id")]
        self.assertEqual(before, after)

    def test_reload_same_project_and_switch_to_shared_candidates_preserve_valid_selection(self):
        window = self.make_window()
        self.select_filters(window, "共通取引先", "2026-09", "2026-09-05", "2026-09-05")
        selected = self.filter_selection(window)
        window.reload_filter_options()
        self.assertEqual(self.filter_selection(window), selected)
        self.select_project(window, "PB")
        self.assertEqual(self.filter_selection(window), selected)
        self.assertEqual(len(window.tree.get_children()), 1)

    def test_all_and_empty_projects_rebuild_each_combo(self):
        window = self.make_window()
        self.select_project(window, None)
        self.assertEqual(set(window.vendor_options), {"すべて", "取引先A", "取引先B", "共通取引先"})
        self.assertNotIn("2026年12月", window.month_combo.cget("values"))
        self.select_filters(window, "取引先A", "2026-08", "2026-08-01", "2026-08-03")
        self.select_project(window, "PE")
        self.assertEqual(self.filter_selection(window), ("すべて",) * 4)
        for combo in (window.vendor_combo, window.month_combo, window.date_from_combo, window.date_to_combo):
            self.assertEqual(combo.cget("values"), ("すべて",))
        self.assertEqual(window.tree.get_children(), ())

    def test_hiding_selected_project_rebuilds_global_candidates_before_refresh(self):
        window = self.make_window()
        self.select_filters(window, "取引先A", "2026-08", "2026-08-01", "2026-08-03")
        repositories.set_project_active(self.projects["PA"], False)
        window.reload_filter_options()
        self.assertEqual(window.selected_project_var.get(), "すべて")
        self.assertEqual(self.filter_selection(window), ("すべて",) * 4)
        self.assertEqual(set(window.vendor_options), {"すべて", "取引先B", "共通取引先"})
        self.assertEqual(len(window.tree.get_children()), 2)


if __name__ == "__main__":
    unittest.main()
