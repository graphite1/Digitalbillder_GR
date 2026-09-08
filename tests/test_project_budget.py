from __future__ import annotations

import tempfile
import hashlib
import io
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.services.project_budget import (
    BudgetRowInput,
    ExtractedBudgetCandidate,
    SourcePreview,
    build_project_forecast,
    get_project_budget,
    list_source_proposals,
    prepare_budget_rows_from_candidates,
    preview_source_document,
    preview_source_document_isolated,
    resolve_budget_source,
    save_project_budget,
    suggest_budget_work_type_mappings,
    _source_digest,
    _candidates_from_table,
)
from invoice_manager.services.work_type_resolution import CanonicalWorkType
from invoice_manager.services.operation_cancellation import (
    CancellationToken, OperationCancelled, cancellation_scope,
)


class ProjectBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        self.root = Path(self.temp_dir.name)
        db.DATA_DIR = self.root / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        with db.get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO projects (project_code, project_name, created_at, updated_at)
                VALUES ('P-BUDGET', '架空工事', '2026-01-01', '2026-01-01')
                """
            )
            self.project_id = int(cursor.lastrowid)

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def row(self, code: str = "NEW-A", **changes) -> BudgetRowInput:
        values = {
            "work_type_code": code,
            "work_type_name": "架空科目",
            "budget_net": 1200,
            "remaining_net": None,
            "scheduled_net": 1100,
            "include_in_total": True,
            "actual_work_type_code": None,
        }
        values.update(changes)
        return BudgetRowInput(**values)

    def make_table_pdf(self) -> Path:
        import pymupdf

        path = self.root / "架空予算.pdf"
        document = pymupdf.open()
        document.new_page()
        page = document.new_page(width=595, height=842)
        x_positions = (60, 150, 330, 430, 530)
        y_positions = (100, 130, 160, 190, 220)
        for x in x_positions:
            page.draw_line((x, y_positions[0]), (x, y_positions[-1]))
        for y in y_positions:
            page.draw_line((x_positions[0], y), (x_positions[-1], y))
        rows = (
            ("Code", "Item", "Budget", "Scheduled"),
            ("NEW-A", "Fictional item", "1,200", "1,100"),
            ("NEW-B", "Fictional item 2", "300", "250"),
            ("NEW-A", "Repeated location", "200", "150"),
        )
        for row_index, row in enumerate(rows):
            y = y_positions[row_index] + 20
            for column_index, text in enumerate(row):
                page.insert_text((x_positions[column_index] + 4, y), text, fontsize=9)
        document.save(path)
        document.close()
        return path

    @staticmethod
    def candidate(
        code: str = "NEW-A",
        *,
        name: str = "架空科目",
        budget_net: int | None = 1200,
        scheduled_net: int | None = 1100,
        location: str = "表1 行2",
    ) -> ExtractedBudgetCandidate:
        return ExtractedBudgetCandidate(
            page_number=2,
            work_type_code=code,
            work_type_name=name,
            budget_net=budget_net,
            scheduled_net=scheduled_net,
            source_location=location,
        )

    def test_prepare_all_candidates_as_safe_unconfirmed_rows(self) -> None:
        first = self.candidate()
        second = self.candidate(
            "NEW-B", name="", budget_net=300, scheduled_net=None, location="表1 行3"
        )

        rows, skipped = prepare_budget_rows_from_candidates(
            [first, second], fallback_names={"NEW-B": "補完科目"}
        )

        self.assertEqual([row.work_type_code for row in rows], ["NEW-A", "NEW-B"])
        self.assertEqual(rows[1].work_type_name, "補完科目")
        self.assertIsNone(rows[1].scheduled_net)
        self.assertTrue(all(row.remaining_net is None for row in rows))
        self.assertTrue(all(row.actual_work_type_code is None for row in rows))
        self.assertTrue(all(not row.include_in_total for row in rows))
        self.assertIs(rows[0].source_candidate, first)
        self.assertIs(rows[1].source_candidate, second)
        self.assertEqual(skipped, ())

    def test_prepare_skips_existing_codes_without_overwriting_manual_rows(self) -> None:
        candidates = [self.candidate("NEW-A"), self.candidate("NEW-B", location="表1 行3")]

        first_rows, first_skipped = prepare_budget_rows_from_candidates(candidates)
        second_rows, second_skipped = prepare_budget_rows_from_candidates(
            candidates, existing_codes=(row.work_type_code for row in first_rows)
        )

        self.assertEqual(len(first_rows), 2)
        self.assertEqual(first_skipped, ())
        self.assertEqual(second_rows, ())
        self.assertEqual(second_skipped, ("NEW-A", "NEW-B"))

    def test_prepare_rejects_duplicate_or_invalid_candidates_without_partial_result(self) -> None:
        valid = self.candidate("VALID")
        invalid_cases = (
            ([valid, self.candidate("VALID", location="表2 行8")], "重複"),
            ([valid, self.candidate("", location="表2 行9")], "工種コード"),
            ([valid, self.candidate("NO-NAME", name="", location="表2 行10")], "工種名"),
            ([valid, self.candidate("NO-BUDGET", budget_net=None, location="表2 行11")], "実行予算"),
            ([valid, self.candidate("NEGATIVE", budget_net=-1, location="表2 行12")], "0以上"),
        )

        for candidates, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    prepare_budget_rows_from_candidates(candidates)

    def test_pdf_numeric_code_uses_registered_d_mapping_without_changing_source(self) -> None:
        repositories.save_work_type_code(self.project_id, "513", "マスタ科目")
        candidate = self.candidate("513", name="")
        rows, skipped = prepare_budget_rows_from_candidates([candidate], project_id=self.project_id)
        self.assertEqual(skipped, ())
        self.assertEqual(rows[0].work_type_code, "513")
        self.assertEqual(rows[0].work_type_name, "マスタ科目")
        self.assertEqual(rows[0].actual_work_type_code, "D513")
        self.assertIs(rows[0].source_candidate, candidate)
        self.assertEqual(candidate.work_type_name, "")
        self.assertFalse(rows[0].include_in_total)
        self.assertIsNone(get_project_budget(self.project_id))

    def test_pdf_blank_budget_cell_becomes_zero_budget_candidate(self) -> None:
        candidates, inferred = _candidates_from_table(
            [
                ["工種コード", "科目", "実行予算", "予定金額"],
                ["513", "舗装工", "", ""],
            ],
            page_number=2,
            table_number=1,
        )

        self.assertFalse(inferred)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].work_type_code, "513")
        self.assertEqual(candidates[0].budget_net, 0)
        self.assertIsNone(candidates[0].scheduled_net)

    def test_budget_numeric_code_keeps_d_rule_when_history_has_other_prefix(self) -> None:
        repositories.save_work_type_code(self.project_id, "513", "基本科目")
        with patch("invoice_manager.services.work_type_resolution.load_confirmed_work_types",
                   return_value=(CanonicalWorkType("X513", "別体系の実績"),)):
            rows, _ = prepare_budget_rows_from_candidates(
                [self.candidate("５１３", name="原本の舗装工")], project_id=self.project_id
            )
        self.assertEqual(rows[0].work_type_code, "５１３")
        self.assertEqual(rows[0].actual_work_type_code, "D513")
        self.assertEqual(rows[0].work_type_name, "原本の舗装工")

    def test_unknown_ambiguous_and_competing_budget_codes_remain_unmapped(self) -> None:
        catalog = (CanonicalWorkType("D513", "明細"), CanonicalWorkType("D301", "土木"),
                   CanonicalWorkType("X301", "別体系"))
        with patch("invoice_manager.services.project_budget.load_work_type_choices", return_value=catalog):
            rows, issues = suggest_budget_work_type_mappings(
                self.project_id, [self.row(code) for code in ("999", "301", "513", "D513")]
            )
        self.assertEqual([row.actual_work_type_code for row in rows], ["D999", "D301", None, None])
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("重複" in issue for issue in issues))

    def test_budget_source_d_and_three_digits_are_duplicate_matching_codes(self) -> None:
        with self.assertRaisesRegex(ValueError, "重複"):
            prepare_budget_rows_from_candidates([
                self.candidate("513", location="表1 行2"),
                self.candidate("D513", location="表1 行3"),
            ])
        rows, skipped = prepare_budget_rows_from_candidates(
            [self.candidate("D513")], existing_codes=("５１３",)
        )
        self.assertEqual(rows, ())
        self.assertEqual(skipped, ("D513",))

        with self.assertRaisesRegex(ValueError, "重複"):
            save_project_budget(
                self.project_id,
                [self.row("513"), self.row("D513")],
                confirmed=True,
            )

    def test_budget_mapping_preserves_explicit_choice_and_reserves_it(self) -> None:
        repositories.save_work_type_code(self.project_id, "513", "明細")
        manual = self.row("MANUAL", actual_work_type_code="D513", budget_net=9876)
        proposed, issues = suggest_budget_work_type_mappings(
            self.project_id, [manual, self.row("513")]
        )
        self.assertEqual(proposed[0], manual)
        self.assertIsNone(proposed[1].actual_work_type_code)
        self.assertEqual(len(issues), 1)
        rows, _ = prepare_budget_rows_from_candidates(
            [self.candidate("513")], project_id=self.project_id, existing_actual_codes=("D513",)
        )
        self.assertIsNone(rows[0].actual_work_type_code)

    def test_confirmed_mapping_updates_forecast_but_preserves_saved_source_and_row_id(self) -> None:
        repositories.save_work_type_code(self.project_id, "513", "明細")
        source = self.make_table_pdf()
        original_bytes = source.read_bytes()
        first = save_project_budget(
            self.project_id, [self.row("513", source_candidate=self.candidate("513"), remaining_net=50)],
            source_path=source, confirmed=True,
        )
        old = first.rows[0]
        rows, issues = suggest_budget_work_type_mappings(self.project_id, [
            self.row(old.work_type_code, row_id=old.id, remaining_net=old.remaining_net)
        ])
        self.assertFalse(issues)
        self.assertIsNone(get_project_budget(self.project_id).rows[0].actual_work_type_code)
        saved = save_project_budget(self.project_id, rows, confirmed=True)
        self.assertEqual(saved.rows[0].id, old.id)
        self.assertEqual(saved.rows[0].source_candidate_json, old.source_candidate_json)
        self.assertEqual(saved.rows[0].work_type_code, "513")
        self.assertEqual(source.read_bytes(), original_bytes)
        with (
            patch("invoice_manager.services.historical_costs.list_actual_costs", return_value=[
                SimpleNamespace(work_type_code="D513", work_type_name="明細", net_amount=100)]),
            patch("invoice_manager.services.historical_costs.get_historical_sync_status",
                  return_value=SimpleNamespace(last_successful_refresh="2026-09-07")),
        ):
            forecast = build_project_forecast(self.project_id)
        self.assertEqual(len(forecast), 1)
        self.assertEqual(forecast[0].projected_final_net, 150)

    def test_isolated_pdf_parser_preserves_original_and_reports_real_stages(self) -> None:
        source = self.make_table_pdf()
        before = source.read_bytes()
        updates = []
        preview = preview_source_document_isolated(source, progress=updates.append)
        self.assertEqual(preview.path, source)
        self.assertTrue(preview.candidates)
        self.assertEqual(preview.source_sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(updates[0].startswith("1/4"))
        self.assertTrue(any(text.startswith("3/4") for text in updates))
        self.assertTrue(updates[-1].startswith("4/4"))
        self.assertIsNone(get_project_budget(self.project_id))

    def test_source_hash_is_chunked_and_observes_cancellation_between_reads(self) -> None:
        token = CancellationToken()
        sizes = []

        class Stream(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                token.request()
                return super().read(size)

        with patch.object(Path, "open", return_value=Stream(b"x" * (3 * 1024 * 1024))), cancellation_scope(token):
            with self.assertRaises(OperationCancelled):
                _source_digest(self.root / "synthetic.pdf")
        self.assertEqual(sizes, [1024 * 1024])

    def test_cancelling_isolated_parser_stops_owned_process_without_saving(self) -> None:
        source = self.make_table_pdf()
        token = CancellationToken()
        processes = []
        popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch("invoice_manager.services.project_budget.subprocess.Popen", side_effect=launch), cancellation_scope(token):
            with self.assertRaises(OperationCancelled):
                preview_source_document_isolated(source, progress=lambda _text: token.request())
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertIsNone(get_project_budget(self.project_id))

    def test_changed_original_after_preview_is_not_saved(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        source.write_bytes(source.read_bytes() + b"\n%changed\n")
        with self.assertRaisesRegex(ValueError, "原本が変更"):
            save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        self.assertIsNone(get_project_budget(self.project_id))

    def test_source_copy_verifies_owned_temporary_bytes_before_publishing(self) -> None:
        source = self.make_table_pdf()
        before = source.read_bytes()
        preview = preview_source_document(source)
        copied = []
        copy2 = shutil.copy2

        def copy_to_temporary(src, destination):
            copied.append(Path(destination))
            self.assertTrue(Path(destination).name.startswith(".budget-copy-"))
            self.assertEqual(Path(destination).parent, db.DATA_DIR / "budgets" / f"project-{self.project_id}")
            return copy2(src, destination)

        with patch("invoice_manager.services.project_budget.shutil.copy2", side_effect=copy_to_temporary):
            saved = save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        stored = resolve_budget_source(saved.source_stored_path)
        self.assertEqual(stored.read_bytes(), before)
        self.assertEqual(saved.source_sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(len(copied), 1)
        self.assertFalse(copied[0].exists())
        self.assertEqual(list(stored.parent.iterdir()), [stored])

    def test_changed_copy_bytes_are_rejected_without_changing_database_or_original(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        original = source.read_bytes()
        database = db.DB_PATH.read_bytes()
        copy2 = shutil.copy2

        def changed_copy(src, destination):
            copy2(src, destination)
            with Path(destination).open("ab") as stream:
                stream.write(b"\n%changed during copy\n")

        with patch("invoice_manager.services.project_budget.shutil.copy2", side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, "コピー中に予算原本が変更"):
                save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(db.DB_PATH.read_bytes(), database)
        self.assertFalse(list((db.DATA_DIR / "budgets").rglob("*.*")))

    def test_corrupted_stored_original_is_not_reused_or_overwritten(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        first = save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        destination = resolve_budget_source(first.source_stored_path)
        destination.write_bytes(b"corrupted stored source")
        corrupted = destination.read_bytes()
        original, database = source.read_bytes(), db.DB_PATH.read_bytes()
        with patch("invoice_manager.services.project_budget.shutil.copy2") as copy:
            with self.assertRaisesRegex(ValueError, "保存済み予算原本の内容が一致"):
                save_project_budget(self.project_id, [self.row(budget_net=2000)], source_preview=preview, confirmed=True)
        copy.assert_not_called()
        self.assertEqual(destination.read_bytes(), corrupted)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(db.DB_PATH.read_bytes(), database)

    def test_cancel_after_copy_removes_temporary_and_preserves_database(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        original, database = source.read_bytes(), db.DB_PATH.read_bytes()
        token = CancellationToken()
        copy2 = shutil.copy2

        def copy_then_cancel(src, destination):
            copy2(src, destination)
            token.request()

        with patch("invoice_manager.services.project_budget.shutil.copy2", side_effect=copy_then_cancel), cancellation_scope(token):
            with self.assertRaises(OperationCancelled):
                save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(db.DB_PATH.read_bytes(), database)
        self.assertFalse(list((db.DATA_DIR / "budgets").rglob("*.*")))

    @unittest.skipUnless(os.name == "nt", "Windows non-overwriting rename")
    def test_destination_created_during_publish_is_not_overwritten(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        original, database = source.read_bytes(), db.DB_PATH.read_bytes()
        rename = os.rename
        destinations = []

        def race(src, destination):
            destinations.append(Path(destination))
            Path(destination).write_bytes(b"another saved original")
            return rename(src, destination)

        with patch("invoice_manager.services.project_budget.os.rename", side_effect=race):
            with self.assertRaisesRegex(ValueError, "保存済み予算原本の内容が一致"):
                save_project_budget(self.project_id, [self.row()], source_preview=preview, confirmed=True)
        self.assertEqual(destinations[0].read_bytes(), b"another saved original")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(db.DB_PATH.read_bytes(), database)
        self.assertFalse(list(destinations[0].parent.glob(".budget-copy-*")))

    def test_isolated_parser_error_is_reported_and_can_be_retried(self) -> None:
        source = self.make_table_pdf()
        with self.assertRaisesRegex(ValueError, "ページ"):
            preview_source_document_isolated(source, page_number=99)
        self.assertEqual(preview_source_document_isolated(source).page_count, 2)

    def test_new_source_does_not_reassign_saved_rows_original_proposal(self) -> None:
        source = self.make_table_pdf()
        first_candidate = self.candidate("FIRST")
        first_preview = SourcePreview(source, "pdf", 2, "", (first_candidate,), ())
        first = save_project_budget(
            self.project_id, [self.row("FIRST", source_candidate=first_candidate)],
            source_preview=first_preview, confirmed=True,
        )
        second_source = self.root / "second.pdf"
        second_source.write_bytes(source.read_bytes() + b"\n%second\n")
        second_candidate = self.candidate("SECOND")
        second_preview = SourcePreview(second_source, "pdf", 2, "", (second_candidate,), ())
        saved = save_project_budget(self.project_id, [
            self.row("FIRST", row_id=first.rows[0].id, source_candidate=first_candidate),
            self.row("SECOND", source_candidate=second_candidate),
        ], source_preview=second_preview, confirmed=True)
        self.assertEqual(saved.rows[0].source_proposal_id, first.rows[0].source_proposal_id)
        self.assertEqual(saved.rows[0].source_candidate_json, first.rows[0].source_candidate_json)
        self.assertNotEqual(saved.rows[0].source_proposal_id, saved.rows[1].source_proposal_id)

    def test_unsaved_candidate_from_different_source_rolls_back_entire_budget(self) -> None:
        source = self.make_table_pdf()
        candidate = self.candidate("ACTUAL")
        preview = SourcePreview(source, "pdf", 2, "", (candidate,), ())
        with self.assertRaisesRegex(ValueError, "別の予算原本"):
            save_project_budget(self.project_id, [
                self.row("ACTUAL", source_candidate=candidate),
                self.row("OTHER", source_candidate=self.candidate("OTHER")),
            ], source_preview=preview, confirmed=True)
        self.assertIsNone(get_project_budget(self.project_id))
        self.assertFalse(list_source_proposals(self.project_id))

    def test_save_requires_explicit_confirmation_and_rejects_duplicate_codes(self) -> None:
        with self.assertRaisesRegex(ValueError, "確認"):
            save_project_budget(self.project_id, [self.row()])
        with self.assertRaisesRegex(ValueError, "重複"):
            save_project_budget(self.project_id, [self.row(), self.row()], confirmed=True)
        with self.assertRaisesRegex(ValueError, "集計対象"):
            save_project_budget(
                self.project_id, [self.row(include_in_total=False)], confirmed=True
            )
        self.assertIsNone(get_project_budget(self.project_id))

    def test_save_replaces_rows_without_replacing_budget_and_keeps_unknown_remaining(self) -> None:
        first = save_project_budget(self.project_id, [self.row()], confirmed=True)
        second = save_project_budget(
            self.project_id,
            [self.row("OTHER/2", budget_net=2500, scheduled_net=None)],
            confirmed=True,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual([row.work_type_code for row in second.rows], ["OTHER/2"])
        self.assertIsNone(second.rows[0].remaining_net)
        self.assertEqual(second.total_budget_net, 2500)

    def test_pdf_candidates_are_review_only_and_original_proposal_stays_immutable(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source, page_number=2)

        self.assertEqual(preview.page_count, 2)
        candidate = next(item for item in preview.candidates if item.work_type_code == "NEW-A")
        self.assertEqual(candidate.budget_net, 1200)
        self.assertEqual(candidate.scheduled_net, 1100)
        self.assertTrue(candidate.requires_review)
        self.assertEqual(
            len([item for item in preview.candidates if item.work_type_code == "NEW-A"]), 2
        )
        self.assertIn("統合していません", " ".join(preview.warnings))
        self.assertIsNone(get_project_budget(self.project_id))

        saved = save_project_budget(
            self.project_id,
            [self.row(source_candidate=candidate)],
            source_preview=preview,
            confirmed=True,
        )
        original_json = saved.rows[0].source_candidate_json
        original_proposal_id = saved.rows[0].source_proposal_id
        updated = save_project_budget(
            self.project_id,
            [self.row(budget_net=1300, scheduled_net=1000)],
            confirmed=True,
        )

        self.assertEqual(updated.rows[0].budget_net, 1300)
        self.assertEqual(updated.rows[0].source_candidate_json, original_json)
        self.assertEqual(updated.rows[0].source_proposal_id, original_proposal_id)
        self.assertEqual(updated.rows[0].source_candidate["budget_net"], 1200)
        self.assertEqual(updated.rows[0].edit_version, 2)
        proposals = list_source_proposals(self.project_id)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["candidates"][0]["work_type_code"], "NEW-A")

    def test_source_copy_is_bounded_to_data_directory_and_duplicate_content_is_reused(self) -> None:
        source = self.make_table_pdf()
        first = save_project_budget(
            self.project_id, [self.row()], source_path=source, confirmed=True
        )
        second = save_project_budget(
            self.project_id, [self.row(budget_net=1400)], source_path=source, confirmed=True
        )

        self.assertEqual(first.source_stored_path, second.source_stored_path)
        stored = resolve_budget_source(second.source_stored_path)
        self.assertTrue(stored.is_file())
        self.assertTrue(stored.is_relative_to(db.DATA_DIR.resolve()))
        with self.assertRaisesRegex(ValueError, "データ領域外"):
            resolve_budget_source("../outside.pdf")
        with self.assertRaisesRegex(ValueError, "不正"):
            resolve_budget_source(str(source.resolve()))

    def test_forecast_uses_only_explicit_web_mapping_and_preserves_unknown(self) -> None:
        save_project_budget(
            self.project_id,
            [self.row("607", actual_work_type_code="D607", remaining_net=None)],
            confirmed=True,
        )
        actuals = [
            SimpleNamespace(work_type_code="D607", work_type_name="対応済実績", net_amount=100),
            SimpleNamespace(work_type_code="607", work_type_name="未対応実績", net_amount=999),
        ]
        with (
            patch("invoice_manager.services.historical_costs.list_actual_costs", return_value=actuals) as actual_api,
            patch(
                "invoice_manager.services.historical_costs.get_historical_sync_status",
                return_value=SimpleNamespace(last_successful_refresh="2026-01-02T00:00:00+00:00"),
            ),
        ):
            forecast = build_project_forecast(self.project_id)

        budget_row = next(row for row in forecast if row.work_type_code == "607" and row.include_in_total)
        self.assertEqual(budget_row.actual_net, 100)
        self.assertIsNone(budget_row.projected_final_net)
        unmatched = next(row for row in forecast if not row.include_in_total)
        self.assertEqual(unmatched.actual_net, 999)
        self.assertTrue(unmatched.is_unmapped_actual)
        actual_api.assert_called_once_with(self.project_id)

        save_project_budget(
            self.project_id,
            [self.row("607", actual_work_type_code="D607", remaining_net=50)],
            confirmed=True,
        )
        with (
            patch("invoice_manager.services.historical_costs.list_actual_costs", return_value=actuals),
            patch(
                "invoice_manager.services.historical_costs.get_historical_sync_status",
                return_value=SimpleNamespace(last_successful_refresh="2026-01-02T00:00:00+00:00"),
            ),
        ):
            updated = build_project_forecast(self.project_id)
        self.assertEqual(updated[0].projected_final_net, 150)
        self.assertEqual(updated[0].variance_net, 1050)

    def test_forecast_does_not_treat_never_synced_actuals_as_zero(self) -> None:
        save_project_budget(
            self.project_id,
            [self.row("A", actual_work_type_code="WEB-A", remaining_net=50)],
            confirmed=True,
        )
        with (
            patch("invoice_manager.services.historical_costs.list_actual_costs") as actual_api,
            patch(
                "invoice_manager.services.historical_costs.get_historical_sync_status",
                return_value=SimpleNamespace(last_successful_refresh=None),
            ),
        ):
            forecast = build_project_forecast(self.project_id)

        self.assertIsNone(forecast[0].actual_net)
        self.assertIsNone(forecast[0].projected_final_net)
        actual_api.assert_not_called()

    def test_code_edit_keeps_row_identity_source_and_edit_version(self) -> None:
        source = self.make_table_pdf()
        preview = preview_source_document(source)
        candidate = next(item for item in preview.candidates if item.work_type_code == "NEW-A")
        first = save_project_budget(
            self.project_id,
            [self.row("NEW-A", source_candidate=candidate)],
            source_preview=preview,
            confirmed=True,
        )
        first_row = first.rows[0]

        updated = save_project_budget(
            self.project_id,
            [self.row("REVISED-CODE", row_id=first_row.id)],
            confirmed=True,
        )

        self.assertEqual(updated.rows[0].id, first_row.id)
        self.assertEqual(updated.rows[0].work_type_code, "REVISED-CODE")
        self.assertEqual(updated.rows[0].source_candidate_json, first_row.source_candidate_json)
        self.assertEqual(updated.rows[0].source_proposal_id, first_row.source_proposal_id)
        self.assertEqual(updated.rows[0].edit_version, 2)


if __name__ == "__main__":
    unittest.main()
