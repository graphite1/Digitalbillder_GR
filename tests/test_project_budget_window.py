from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tkinter as tk

from invoice_manager import db, repositories
from invoice_manager.services.project_budget import (
    ExtractedBudgetCandidate,
    SourcePreview,
    get_project_budget,
    list_source_proposals,
)
from invoice_manager.ui.project_budget_window import ProjectBudgetWindow


class ProjectBudgetWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        self.root_path = Path(self.temp_dir.name)
        db.DATA_DIR = self.root_path / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        self.project_id = repositories.get_or_create_project("P-BUDGET-UI", "架空工事")
        self.pdf_path = self._make_pdf()
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.window = ProjectBudgetWindow(self.tk_root, self.project_id)
        self.window.withdraw()

    def tearDown(self) -> None:
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        finally:
            self.tk_root.destroy()
            db.DATA_DIR = self.original_data_dir
            db.DB_PATH = self.original_db_path
            self.temp_dir.cleanup()

    def _make_pdf(self) -> Path:
        import pymupdf

        path = self.root_path / "budget-source.pdf"
        document = pymupdf.open()
        document.new_page()
        document.new_page()
        document.save(path)
        document.close()
        return path

    @staticmethod
    def candidate(
        code: str,
        name: str = "架空科目",
        budget: int | None = 1200,
        scheduled: int | None = 1100,
        location: str = "表1 行2",
    ) -> ExtractedBudgetCandidate:
        return ExtractedBudgetCandidate(
            page_number=2,
            work_type_code=code,
            work_type_name=name,
            budget_net=budget,
            scheduled_net=scheduled,
            source_location=location,
        )

    def show_candidates(self, *candidates: ExtractedBudgetCandidate) -> None:
        preview = SourcePreview(
            self.pdf_path,
            "pdf",
            2,
            "コード\t科目\t実行予算\t予定金額",
            tuple(candidates),
            (),
        )
        self.window.source_preview = preview
        self.window.source_path = self.pdf_path
        self.window._show_candidates(preview)

    def test_bulk_add_is_local_until_save_and_keeps_manual_values_on_readd(self) -> None:
        first = self.candidate("NEW-A", budget=1200, scheduled=1100, location="表1 行2")
        second = self.candidate("NEW-B", budget=300, scheduled=None, location="表1 行3")
        third = self.candidate("NEW-C", budget=500, scheduled=450, location="表1 行4")
        self.show_candidates(first, second, third)

        with patch.object(self.window, "_refresh_forecast"):
            self.window._add_all_candidates()
        self.assertIsNone(get_project_budget(self.project_id))
        self.assertEqual({row["work_type_code"] for row in self.window.row_values.values()}, {"NEW-A", "NEW-B", "NEW-C"})

        first_item = next(item for item, row in self.window.row_values.items() if row["work_type_code"] == "NEW-A")
        self.window.row_values[first_item]["budget_net"] = 9999
        self.window._render_row(first_item)

        self.window._add_all_candidates()
        first_row = self.window.row_values[first_item]
        self.assertEqual(first_row["budget_net"], 9999)
        self.assertEqual(len(self.window.row_values), 3)
        self.assertIsNone(first_row["remaining_net"])
        self.assertIsNone(first_row["actual_work_type_code"])

    def test_selected_bulk_add_then_inclusion_and_confirmed_save_persists_all_rows(self) -> None:
        candidates = (
            self.candidate("SEL-A", budget=1000, scheduled=900, location="表1 行2"),
            self.candidate("SEL-B", budget=2000, scheduled=None, location="表1 行3"),
            self.candidate("SEL-C", budget=3000, scheduled=2800, location="表1 行4"),
        )
        self.show_candidates(*candidates)
        candidate_items = self.window.candidate_tree.get_children()
        self.window.candidate_tree.selection_set(candidate_items[:2])
        self.window._add_selected_candidates()
        self.assertEqual(len(self.window.row_values), 2)

        self.window._add_all_candidates()
        self.window._select_all_rows()
        self.window._set_selected_inclusion(True)
        with (
            patch.object(self.window, "_refresh_forecast"),
            patch("invoice_manager.ui.project_budget_window.messagebox.askyesno", return_value=True) as ask,
            patch("invoice_manager.ui.project_budget_window.messagebox.showinfo"),
            patch("invoice_manager.ui.project_budget_window.messagebox.showwarning"),
            patch("invoice_manager.ui.project_budget_window.messagebox.showerror"),
        ):
            self.window._save()

        ask.assert_called_once()
        saved = get_project_budget(self.project_id)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual([row.work_type_code for row in saved.rows], ["SEL-A", "SEL-B", "SEL-C"])
        self.assertEqual([row.budget_net for row in saved.rows], [1000, 2000, 3000])
        self.assertTrue(all(row.include_in_total for row in saved.rows))
        self.assertTrue(all(row.remaining_net is None for row in saved.rows))
        self.assertTrue(all(row.actual_work_type_code is None for row in saved.rows))
        self.assertTrue(all(row.source_candidate_json for row in saved.rows))
        proposals = list_source_proposals(self.project_id)
        self.assertEqual(len(proposals), 1)
        self.assertEqual([item["work_type_code"] for item in proposals[0]["candidates"]], ["SEL-A", "SEL-B", "SEL-C"])

    def test_invalid_candidate_rejects_entire_bulk_add_without_partial_rows(self) -> None:
        valid = self.candidate("VALID", location="表1 行2")
        invalid = self.candidate("INVALID", name="", location="表1 行3")
        self.show_candidates(valid, invalid)

        with (
            patch("invoice_manager.ui.project_budget_window.messagebox.showwarning") as warning,
            patch("invoice_manager.ui.project_budget_window.messagebox.showinfo"),
        ):
            self.window._add_all_candidates()

        warning.assert_called_once()
        self.assertEqual(self.window.row_values, {})
        self.assertIsNone(get_project_budget(self.project_id))

    def test_cancelled_save_confirmation_does_not_write_database(self) -> None:
        self.show_candidates(self.candidate("CANCEL", budget=700, scheduled=600))
        self.window._add_all_candidates()
        self.window._select_all_rows()
        self.window._set_selected_inclusion(True)

        with (
            patch("invoice_manager.ui.project_budget_window.messagebox.askyesno", return_value=False) as ask,
            patch("invoice_manager.ui.project_budget_window.messagebox.showinfo"),
            patch("invoice_manager.ui.project_budget_window.messagebox.showwarning"),
            patch("invoice_manager.ui.project_budget_window.messagebox.showerror"),
        ):
            self.window._save()

        ask.assert_called_once()
        self.assertIsNone(get_project_budget(self.project_id))


if __name__ == "__main__":
    unittest.main()
