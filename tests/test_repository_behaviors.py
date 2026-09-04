from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.models import InvoiceCsvRow
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


if __name__ == "__main__":
    unittest.main()
