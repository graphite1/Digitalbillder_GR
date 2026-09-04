from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.models import ImportErrorItem, InvoiceCsvRow
from invoice_manager.services import duplicate_checker
from invoice_manager.work_type_catalog import WORK_TYPE_CODE_CATALOG


def make_row(
    external_id: str,
    *,
    project_code: str = "P001",
    vendor_name: str = "取引先A",
    invoice_date: str = "2026-08-20",
    total_amount: int = 110_000,
) -> InvoiceCsvRow:
    return InvoiceCsvRow(
        row_number=2,
        external_id=external_id,
        project_name="工事A",
        project_code=project_code,
        vendor_name=vendor_name,
        last_name="",
        first_name="",
        email="",
        phone="",
        invoice_date=invoice_date,
        total_amount=total_amount,
        raw_data={},
    )


class RepositoryBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        db.DATA_DIR = Path(self.temp_dir.name) / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_duplicate_check_reuses_one_database_connection(self) -> None:
        batch_id = repositories.create_import_batch(
            "2026-09",
            Path("source.csv"),
            Path("source.zip"),
            "csv-hash",
            "zip-hash",
            "",
        )
        repositories.insert_invoice(make_row("EXISTING"), "2026-09", batch_id)

        rows = [
            make_row("EXISTING"),
            make_row("EXISTING", total_amount=220_000),
            make_row("DUPLICATE"),
            make_row("NEW", project_code="P002"),
        ]
        with patch.object(repositories, "get_connection", wraps=db.get_connection) as get_connection:
            summary = duplicate_checker.check_duplicates(rows)

        get_connection.assert_called_once_with()
        self.assertEqual(summary.existing_skip_ids, {"EXISTING"})
        self.assertEqual(summary.update_candidate_ids, {"EXISTING"})
        self.assertEqual(summary.duplicate_candidate_ids, {"DUPLICATE"})
        self.assertEqual(summary.new_ids, {"NEW"})

    def test_work_type_catalog_does_not_rewrite_unchanged_rows(self) -> None:
        project_id = repositories.get_or_create_project("P001", "工事A")
        timestamps = ["2026-09-04 10:00:00", "2026-09-04 11:00:00"]
        with patch.object(repositories, "now_text", side_effect=timestamps):
            inserted_first = repositories.ensure_work_type_codes_for_project(project_id)
            inserted_second = repositories.ensure_work_type_codes_for_project(project_id)

        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT updated_at FROM work_type_codes WHERE project_id = ?",
                (project_id,),
            ).fetchall()

        self.assertEqual(inserted_first, len(WORK_TYPE_CODE_CATALOG))
        self.assertEqual(inserted_second, 0)
        self.assertEqual({row["updated_at"] for row in rows}, {timestamps[0]})

    def test_import_history_keeps_completion_snapshot(self) -> None:
        batch_id = repositories.create_import_batch(
            "2026-09",
            Path("source.csv"),
            Path("source.zip"),
            "csv-hash",
            "zip-hash",
            "定期取込",
        )
        repositories.save_import_errors(
            batch_id,
            [ImportErrorItem(3, "CSV", "必須項目がありません", "raw")],
        )
        repositories.finalize_import_batch(batch_id, 4, 3, 1, "completed")

        history = repositories.list_import_batches()
        errors = repositories.list_import_errors(batch_id)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["registered_count"], 4)
        self.assertEqual(history[0]["pdf_count"], 3)
        self.assertEqual(history[0]["error_count"], 1)
        self.assertEqual(history[0]["status"], "completed")
        self.assertTrue(history[0]["completed_at"])
        self.assertEqual(history[0]["memo"], "定期取込")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["message"], "必須項目がありません")

    def test_project_visibility_rows_include_invoice_count_and_latest_date(self) -> None:
        batch_id = repositories.create_import_batch(
            "2026-09",
            Path("source.csv"),
            Path("source.zip"),
            "csv-hash",
            "zip-hash",
            "",
        )
        repositories.insert_invoice(make_row("INV-1", invoice_date="2026-08-09"), "2026-08", batch_id)
        repositories.insert_invoice(make_row("INV-2", invoice_date="2026-08-20"), "2026-09", batch_id)
        empty_project_id = repositories.get_or_create_project("P002", "工事B")
        repositories.set_project_active(empty_project_id, False)

        rows = {
            row["project_code"]: row
            for row in repositories.list_projects_for_visibility()
        }

        self.assertEqual(rows["P001"]["invoice_count"], 2)
        self.assertEqual(rows["P001"]["last_invoice_date"], "2026-08-20")
        self.assertEqual(rows["P002"]["invoice_count"], 0)
        self.assertEqual(rows["P002"]["last_invoice_date"], "")
        self.assertEqual(rows["P002"]["is_active"], 0)


if __name__ == "__main__":
    unittest.main()
