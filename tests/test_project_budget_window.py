from __future__ import annotations

import tempfile
import threading
import time
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

    def pump_until(self, condition, timeout=5) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            self.tk_root.update()
            if time.monotonic() >= deadline:
                self.fail("Background preview did not finish in time")
            time.sleep(0.01)
        self.tk_root.update()

    def result_preview(self, code="READ"):
        return SourcePreview(self.pdf_path, "pdf", 2, "", (self.candidate(code),), ())

    def test_source_worker_keeps_ui_responsive_and_reports_only_completed_result(self) -> None:
        entered, release = threading.Event(), threading.Event()
        gui_thread = threading.get_ident()
        result = self.result_preview()
        calls = []

        def parse(path, *, page_number, progress):
            calls.append(threading.get_ident())
            progress("3/4 罫線表 1/2 を読み取っています")
            entered.set()
            release.wait(3)
            return result

        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated", side_effect=parse):
            try:
                self.assertTrue(self.window._start_source_preview(self.pdf_path))
                self.pump_until(entered.is_set)
                self.pump_until(lambda: "1/2" in self.window.source_activity.message)
                self.assertNotEqual(calls[0], gui_thread)
                heartbeat = []
                self.tk_root.after(0, lambda: heartbeat.append(True))
                self.pump_until(lambda: bool(heartbeat))
                self.assertTrue(self.window.source_busy)
                self.assertEqual(str(self.window.save_button["state"]), "disabled")
                self.assertFalse(self.window._start_source_preview(self.pdf_path))
                self.assertIsNone(self.window.source_preview)
            finally:
                release.set()
                self.pump_until(lambda: not self.window.source_busy)
        self.assertIs(self.window.source_preview, result)
        self.assertFalse(self.window.source_activity.failed)
        self.assertEqual(str(self.window.save_button["state"]), "normal")
        self.assertIsNone(get_project_budget(self.project_id))

    def test_cancel_after_result_is_queued_discards_it_and_retry_uses_new_token(self) -> None:
        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated",
                   return_value=self.result_preview()):
            self.window._start_source_preview(self.pdf_path)
            self.window._source_thread.join(timeout=2)
            first_token = self.window._source_token
            self.window.source_activity.request_cancel()
            self.pump_until(lambda: not self.window.source_busy)
            self.assertTrue(self.window.source_activity.cancelled)
            self.assertIsNone(self.window.source_preview)
            self.window._start_source_preview(self.pdf_path)
            self.pump_until(lambda: not self.window.source_busy)
        self.assertIsNot(first_token, self.window._source_token)
        self.assertIsNotNone(self.window.source_preview)

    def test_project_switch_discards_old_result_and_late_events_do_not_finish_new_job(self) -> None:
        entered, release = threading.Event(), threading.Event()
        other = repositories.get_or_create_project("OTHER-BUDGET", "別工事")
        self.window.project_options["別工事"] = other

        def parse(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            return self.result_preview()

        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated", side_effect=parse):
            try:
                self.window._start_source_preview(self.pdf_path)
                old_generation = self.window._source_job_generation
                self.pump_until(entered.is_set)
                self.window.project_var.set("別工事")
                self.window._project_changed()
                self.assertTrue(self.window._source_token.requested)
            finally:
                release.set()
                self.pump_until(lambda: not self.window.source_busy)
            self.assertIsNone(self.window.source_preview)
            self.assertFalse(self.window.candidate_values)
            release.clear()
            self.window._start_source_preview(self.pdf_path)
            self.window._source_events.put((old_generation, self.project_id, "done", self.result_preview("OLD")))
            try:
                self.pump_until(self.window._source_events.empty)
                self.assertTrue(self.window.source_busy)
                self.assertIsNone(self.window.source_preview)
            finally:
                release.set()
                self.pump_until(lambda: not self.window.source_busy)

    def test_close_requests_cancel_and_waits_for_worker_before_destroy(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def parse(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            return self.result_preview()

        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated", side_effect=parse):
            try:
                self.window._start_source_preview(self.pdf_path)
                self.pump_until(entered.is_set)
                self.window.close()
                self.assertTrue(self.window.winfo_exists())
                self.assertTrue(self.window.source_busy)
                self.assertTrue(self.window.source_activity.running)
                self.assertTrue(self.window._source_token.requested)
            finally:
                release.set()
                self.pump_until(lambda: not self.window.source_busy)
        self.assertFalse(self.window.winfo_exists())
        self.assertTrue(self.window.source_activity.cancelled)

    def test_parser_failure_keeps_previous_preview_and_restores_controls(self) -> None:
        self.show_candidates(self.candidate("PREVIOUS"))
        old = self.window.source_preview
        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated",
                   side_effect=ValueError("PDFを読み取れません")):
            self.window._start_source_preview(self.pdf_path)
            self.pump_until(lambda: not self.window.source_busy)
        self.assertIs(self.window.source_preview, old)
        self.assertTrue(self.window.source_activity.failed)
        self.assertIn("PDFを読み取れません", self.window.batch_status_var.get())
        self.assertEqual(str(self.window.source_button["state"]), "normal")

    def test_candidate_render_failure_keeps_old_source_and_old_candidates_together(self) -> None:
        old = self.result_preview("OLD")
        self.window._apply_source_preview(old)
        previous_items = self.window.candidate_tree.get_children()
        previous_values = dict(self.window.candidate_values)
        new = SourcePreview(self.root_path / "new.pdf", "pdf", 2, "",
                            (self.candidate("NEW-1"), self.candidate("NEW-2")), ())
        insert = self.window.candidate_tree.insert
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("render failed")
            return insert(*args, **kwargs)

        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated", return_value=new), \
                patch.object(self.window.candidate_tree, "insert", side_effect=fail_second):
            self.window._start_source_preview(new.path)
            self.pump_until(lambda: not self.window.source_busy)
        self.assertIs(self.window.source_preview, old)
        self.assertEqual(self.window.source_path, old.path)
        self.assertEqual(self.window.source_var.get(), old.path.name)
        self.assertEqual(self.window.candidate_tree.get_children(), previous_items)
        self.assertEqual(self.window.candidate_values, previous_values)
        self.assertTrue(self.window.source_activity.failed)

    def test_direct_window_destruction_cancels_worker_without_tk_calls_from_thread(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def parse(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            raise RuntimeError("Stopped while owner was destroyed")

        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated", side_effect=parse):
            try:
                self.window._start_source_preview(self.pdf_path)
                self.pump_until(entered.is_set)
                self.window.destroy()
                self.assertTrue(self.window._source_token.requested)
            finally:
                release.set()
                self.pump_until(lambda: not self.window.source_busy)
        self.assertTrue(self.window.source_activity.cancelled)
        self.assertFalse(self.window.source_activity.failed)
        self.assertIsNone(self.window._source_poll_id)

    def test_thread_start_failure_does_not_leave_busy_state(self) -> None:
        with patch("invoice_manager.ui.project_budget_window.threading.Thread.start", side_effect=RuntimeError("cannot start")):
            self.assertFalse(self.window._start_source_preview(self.pdf_path))
        self.assertFalse(self.window.source_busy)
        self.assertFalse(self.window.source_activity.running)
        self.assertTrue(self.window.source_activity.failed)
        self.assertEqual(str(self.window.save_button["state"]), "normal")
        self.assertIsNone(self.window._source_poll_id)

    def test_unpersisted_pdf_rows_prevent_mixing_another_source(self) -> None:
        self.show_candidates(self.candidate("UNSAVED"))
        self.window._add_all_candidates()
        with patch("invoice_manager.ui.project_budget_window.preview_source_document_isolated") as parse:
            self.assertFalse(self.window._start_source_preview(self.pdf_path))
        parse.assert_not_called()
        self.assertEqual(next(iter(self.window.row_values.values()))["work_type_code"], "UNSAVED")
        self.assertIn("未保存", self.window.batch_status_var.get())

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

    def test_numeric_pdf_candidate_proposes_web_code_in_bulk_and_individual_editor(self) -> None:
        repositories.save_work_type_code(self.project_id, "513", "確認科目")
        self.window._project_changed()
        self.assertIn("D513｜確認科目", self.window.actual_combo.cget("values"))
        candidate = self.candidate("513", name="")
        self.show_candidates(candidate)
        self.window.candidate_tree.selection_set(self.window.candidate_tree.get_children())
        self.window._candidate_to_form()
        self.assertEqual(self.window.code_var.get(), "513")
        self.assertEqual(self.window.name_var.get(), "確認科目")
        self.assertEqual(self.window.actual_code_var.get(), "D513")
        self.window._add_all_candidates()
        row = next(iter(self.window.row_values.values()))
        self.assertEqual(row["work_type_code"], "513")
        self.assertEqual(row["actual_work_type_code"], "D513")
        self.assertIs(row["source_candidate"], candidate)
        self.assertIsNone(get_project_budget(self.project_id))

    def test_mapping_existing_editor_rows_is_unsaved_and_keeps_manual_mapping(self) -> None:
        self.show_candidates(self.candidate("513"), self.candidate("514", location="表1 行3"))
        self.window._add_all_candidates()
        rows = list(self.window.row_values.values())
        rows[1]["actual_work_type_code"] = "CUSTOM"
        rows[1]["budget_net"] = 5555
        self.window.row_tree.selection_set(self.window.row_tree.get_children()[0])
        self.window._edit_current_row()
        self.window.budget_var.set("7777")
        repositories.save_work_type_code(self.project_id, "513", "確認科目")
        self.window._match_actual_codes()
        self.assertEqual(rows[0]["actual_work_type_code"], "D513")
        self.assertEqual(rows[1]["actual_work_type_code"], "CUSTOM")
        self.assertEqual(rows[1]["budget_net"], 5555)
        self.assertEqual(self.window.actual_code_var.get(), "D513")
        self.assertEqual(self.window.budget_var.get(), "7777")
        self.assertIn("未保存", self.window.batch_status_var.get())
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
