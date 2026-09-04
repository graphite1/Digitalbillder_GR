from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from invoice_manager.models import DuplicateSummary, InvoiceCsvRow, PreviewResult, ZipIndex, ZipPdfItem
from invoice_manager.services import import_service


def empty_preview(source_signature) -> PreviewResult:
    return PreviewResult(
        csv_count=0,
        zip_id_count=0,
        matched_count=0,
        csv_only_count=0,
        zip_only_count=0,
        new_count=0,
        existing_skip_count=0,
        update_candidate_count=0,
        duplicate_candidate_count=0,
        error_count=0,
        total_amount=0,
        pdf_file_count=0,
        project_totals={},
        vendor_totals={},
        source_signature=source_signature,
    )


class ImportOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.csv_path = self.root / "source.csv"
        self.zip_path = self.root / "source.zip"
        self.csv_path.write_text("header\n", encoding="utf-8")
        with ZipFile(self.zip_path, "w"):
            pass

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def execute_with_repository_mocks(self, preview: PreviewResult):
        with (
            patch.object(import_service, "preview_import", side_effect=AssertionError("preview was repeated")) as preview_import,
            patch.object(import_service, "create_import_batch", return_value=1),
            patch.object(import_service, "save_import_errors"),
            patch.object(import_service, "add_audit_log"),
            patch.object(import_service, "create_database_backup"),
            patch.object(import_service, "list_hidden_project_codes", return_value=set()),
        ):
            result = import_service.execute_import(
                self.csv_path,
                self.zip_path,
                "",
                prepared_preview=preview,
            )
        return result, preview_import

    def test_execute_import_reuses_matching_preview(self) -> None:
        preview = empty_preview(import_service._source_signature(self.csv_path, self.zip_path))

        result, preview_import = self.execute_with_repository_mocks(preview)

        preview_import.assert_not_called()
        self.assertIs(result.preview, preview)

    def test_execute_import_rebuilds_preview_after_source_change(self) -> None:
        stale_preview = empty_preview(import_service._source_signature(self.csv_path, self.zip_path))
        self.csv_path.write_text("header\nchanged\n", encoding="utf-8")
        fresh_preview = empty_preview(import_service._source_signature(self.csv_path, self.zip_path))

        with (
            patch.object(import_service, "preview_import", return_value=fresh_preview) as preview_import,
            patch.object(import_service, "create_import_batch", return_value=1),
            patch.object(import_service, "save_import_errors"),
            patch.object(import_service, "add_audit_log"),
            patch.object(import_service, "create_database_backup"),
            patch.object(import_service, "list_hidden_project_codes", return_value=set()),
        ):
            result = import_service.execute_import(
                self.csv_path,
                self.zip_path,
                "",
                prepared_preview=stale_preview,
            )

        preview_import.assert_called_once_with(self.csv_path, self.zip_path, "")
        self.assertIs(result.preview, fresh_preview)

    def test_execute_import_passes_one_open_zip_to_all_pdf_stores(self) -> None:
        row = InvoiceCsvRow(
            row_number=2,
            external_id="NEW",
            project_name="工事A",
            project_code="P001",
            vendor_name="取引先A",
            last_name="",
            first_name="",
            email="",
            phone="",
            invoice_date="2026-08-20",
            total_amount=110_000,
            raw_data={},
        )
        items = [
            ZipPdfItem("NEW", "NEW/a.pdf", "a.pdf", "invoice", 1),
            ZipPdfItem("NEW", "NEW/b.pdf", "b.pdf", "invoice", 1),
        ]
        preview = empty_preview(import_service._source_signature(self.csv_path, self.zip_path))
        preview.csv_rows = [row]
        preview.zip_index = ZipIndex(id_folders={"NEW"}, pdf_by_id={"NEW": items})
        preview.duplicate_summary = DuplicateSummary(new_ids={"NEW"})

        with (
            patch.object(import_service, "preview_import", side_effect=AssertionError("preview was repeated")),
            patch.object(import_service, "create_import_batch", return_value=1),
            patch.object(import_service, "save_import_errors"),
            patch.object(import_service, "add_audit_log"),
            patch.object(import_service, "create_database_backup"),
            patch.object(import_service, "list_hidden_project_codes", return_value=set()),
            patch.object(import_service, "insert_invoice", return_value=10),
            patch.object(import_service, "insert_invoice_file", return_value=True),
            patch.object(
                import_service,
                "store_pdf_from_zip",
                side_effect=[
                    (self.root / "a.pdf", "hash-a", 1),
                    (self.root / "b.pdf", "hash-b", 1),
                ],
            ) as store_pdf,
        ):
            result = import_service.execute_import(
                self.csv_path,
                self.zip_path,
                "",
                prepared_preview=preview,
            )

        zip_handles = [call.kwargs["zip_file"] for call in store_pdf.call_args_list]
        self.assertEqual(result.file_count, 2)
        self.assertIs(zip_handles[0], zip_handles[1])
        self.assertIsNone(zip_handles[0].fp)

    def test_preview_excludes_new_invoice_for_archived_project(self) -> None:
        row = InvoiceCsvRow(
            row_number=2,
            external_id="ARCHIVED",
            project_name="完了工事",
            project_code="P999",
            vendor_name="取引先A",
            last_name="",
            first_name="",
            email="",
            phone="",
            invoice_date="2026-08-20",
            total_amount=110_000,
            raw_data={},
        )
        zip_index = ZipIndex(id_folders={"ARCHIVED"})
        with (
            patch.object(import_service, "read_invoice_csv", return_value=([row], [], "utf-8")),
            patch.object(import_service, "read_zip_index", return_value=zip_index),
            patch.object(
                import_service,
                "check_duplicates",
                return_value=DuplicateSummary(new_ids={"ARCHIVED"}),
            ),
            patch.object(import_service, "list_hidden_project_codes", return_value={"P999"}),
        ):
            preview = import_service.preview_import(self.csv_path, self.zip_path, "")

        self.assertEqual(preview.new_count, 0)
        self.assertEqual(preview.archived_skip_count, 1)
        self.assertEqual(preview.total_amount, 0)
        self.assertTrue(any("アーカイブ工事" in warning for warning in preview.warnings))


if __name__ == "__main__":
    unittest.main()
