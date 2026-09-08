from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.models import ImportErrorItem, InvoiceCsvRow
from invoice_manager.services import duplicate_checker
from invoice_manager.work_type_catalog import WORK_TYPE_CODE_CATALOG, WORK_TYPE_CODE_NAMES, WORK_TYPE_CODE_ORDERS


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

    def test_duplicate_check_marks_same_batch_different_ids_regardless_of_order(self) -> None:
        first = make_row("BATCH-A")
        second = make_row("BATCH-B")

        forward = duplicate_checker.check_duplicates([first, second])
        reverse = duplicate_checker.check_duplicates([second, first])

        self.assertEqual(forward.duplicate_candidate_ids, {"BATCH-A", "BATCH-B"})
        self.assertEqual(forward.new_ids, set())
        self.assertEqual(reverse.duplicate_candidate_ids, {"BATCH-A", "BATCH-B"})
        self.assertEqual(reverse.new_ids, set())

    def test_duplicate_check_distinguishes_project_vendor_date_and_amount(self) -> None:
        rows = [
            make_row("BASE"),
            make_row("OTHER-PROJECT", project_code="P002"),
            make_row("OTHER-VENDOR", vendor_name="取引先B"),
            make_row("OTHER-DATE", invoice_date="2026-08-21"),
            make_row("OTHER-AMOUNT", total_amount=110_001),
        ]

        summary = duplicate_checker.check_duplicates(rows)

        self.assertEqual(summary.duplicate_candidate_ids, set())
        self.assertEqual(summary.new_ids, {row.external_id for row in rows})

    def test_update_candidate_takes_precedence_over_batch_duplicate(self) -> None:
        batch_id = repositories.create_import_batch(
            "2026-09",
            Path("source.csv"),
            Path("source.zip"),
            "csv-hash",
            "zip-hash",
            "",
        )
        repositories.insert_invoice(make_row("EXISTING", total_amount=110_000), "2026-09", batch_id)

        summary = duplicate_checker.check_duplicates([
            make_row("EXISTING", total_amount=220_000),
            make_row("NEW-SAME-SIGNATURE", total_amount=220_000),
        ])

        self.assertEqual(summary.update_candidate_ids, {"EXISTING"})
        self.assertEqual(summary.duplicate_candidate_ids, {"NEW-SAME-SIGNATURE"})
        self.assertEqual(summary.new_ids, set())

    def test_work_type_catalog_does_not_rewrite_unchanged_rows(self) -> None:
        project_id = repositories.get_or_create_project("P001", "工事A")
        timestamps = ["2026-09-04 10:00:00", "2026-09-04 11:00:00"]
        with patch.object(repositories, "now_text", side_effect=timestamps):
            inserted_first = repositories.ensure_work_type_codes_for_project(project_id)
            inserted_second = repositories.ensure_work_type_codes_for_project(project_id)

        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT code, updated_at FROM work_type_codes WHERE project_id = ?",
                (project_id,),
            ).fetchall()

        self.assertEqual(inserted_first, len(WORK_TYPE_CODE_CATALOG))
        self.assertEqual(inserted_second, 0)
        self.assertEqual({row["updated_at"] for row in rows}, {timestamps[0]})
        self.assertEqual({row["code"] for row in rows}, {code for code, _name in WORK_TYPE_CODE_CATALOG})
        self.assertTrue(all(row["code"].startswith("D") for row in rows))
        self.assertEqual(WORK_TYPE_CODE_NAMES["301"], WORK_TYPE_CODE_NAMES["D301"])
        self.assertEqual(WORK_TYPE_CODE_ORDERS["301"], WORK_TYPE_CODE_ORDERS["D301"])

    def test_invoice_list_includes_work_type_codes_and_allocated_amounts(self) -> None:
        batch_id = repositories.create_import_batch(
            "2026-09", Path("source.csv"), Path("source.zip"), "csv-hash", "zip-hash", ""
        )
        invoice_id = repositories.insert_invoice(make_row("ALLOCATED"), "2026-09", batch_id)
        project_id = repositories.get_or_create_project("P001", "工事A")
        first_code = repositories.save_work_type_code(project_id, "D301", "保険料")
        second_code = repositories.save_work_type_code(project_id, "D513", "仮設費")
        repositories.save_invoice_allocation(invoice_id, first_code, 110, sort_order=2)
        repositories.save_invoice_allocation(invoice_id, second_code, 220, sort_order=1)

        row = next(row for row in repositories.list_invoices() if row["id"] == invoice_id)

        self.assertEqual(
            row["allocation_summary"],
            "D513\x1f242\x1f220\x1eD301\x1f121\x1f110",
        )

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
