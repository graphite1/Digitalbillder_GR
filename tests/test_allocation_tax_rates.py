from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from invoice_manager import db, repositories
from invoice_manager.models import InvoiceCsvRow
from invoice_manager.utils.money_utils import (
    tax_excluded_amount,
    tax_included_amount,
    tax_rate_percent,
)


class AllocationTaxRateTests(unittest.TestCase):
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

    def test_tax_utilities_support_10_8_exempt_and_reject_unknown_rates(self) -> None:
        self.assertEqual(tax_rate_percent("10"), 10)
        self.assertEqual(tax_rate_percent("8"), 8)
        self.assertEqual(tax_rate_percent("exempt"), 0)
        self.assertEqual(tax_included_amount(100, "10"), 110)
        self.assertEqual(tax_included_amount(100, "8"), 108)
        self.assertEqual(tax_included_amount(100, "exempt"), 100)
        self.assertEqual(tax_excluded_amount(108, "8"), 100)
        with self.assertRaises(ValueError):
            tax_rate_percent("5")

    def test_legacy_allocation_migration_preserves_gross_amount(self) -> None:
        db.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("DROP TABLE invoice_allocations")
            conn.execute(
                """
                CREATE TABLE invoice_allocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER NOT NULL,
                    work_type_code_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    memo TEXT,
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO invoice_allocations
                    (invoice_id, work_type_code_id, amount, memo, sort_order, created_at, updated_at)
                VALUES (1, 1, 110, '', 1, '2026-01-01', '2026-01-01')
                """
            )

        db.initialize_database()

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT amount, amount_excluded, tax_rate FROM invoice_allocations WHERE id = 1"
            ).fetchone()
        self.assertEqual(row["amount"], 110)
        self.assertEqual(row["amount_excluded"], 100)
        self.assertEqual(row["tax_rate"], "10")

    def test_rate_edits_recalculate_and_omitted_rate_preserves_existing_rate(self) -> None:
        batch = repositories.create_import_batch(
            "2026-08", Path("source.csv"), Path("source.zip"), "csv", "zip", ""
        )
        row = InvoiceCsvRow(
            row_number=2,
            external_id="123e4567-e89b-12d3-a456-426614174000",
            project_name="工事A",
            project_code="P001",
            vendor_name="取引先A",
            last_name="",
            first_name="",
            email="",
            phone="",
            invoice_date="2026-08-20",
            total_amount=216,
            raw_data={},
        )
        invoice_id = repositories.insert_invoice(row, "2026-08", batch)
        project_id = int(repositories.get_invoice_detail(invoice_id)["project_id"])
        repositories.ensure_work_type_codes_for_project(project_id)
        code_id = int(repositories.list_work_type_codes(project_id, active_only=True)[0]["id"])

        allocation_id = repositories.save_invoice_allocation(
            invoice_id, code_id, 100, tax_rate="8"
        )
        with db.get_connection() as conn:
            created = conn.execute(
                "SELECT amount, amount_excluded, tax_rate FROM invoice_allocations WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        self.assertEqual((created["amount"], created["amount_excluded"], created["tax_rate"]), (108, 100, "8"))

        repositories.save_invoice_allocation(
            invoice_id, code_id, 200, allocation_id=allocation_id
        )
        with db.get_connection() as conn:
            omitted = conn.execute(
                "SELECT amount, amount_excluded, tax_rate FROM invoice_allocations WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        self.assertEqual((omitted["amount"], omitted["amount_excluded"], omitted["tax_rate"]), (216, 200, "8"))

        repositories.save_invoice_allocation(
            invoice_id, code_id, 200, allocation_id=allocation_id, tax_rate="10"
        )
        with db.get_connection() as conn:
            edited = conn.execute(
                "SELECT amount, amount_excluded, tax_rate FROM invoice_allocations WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        self.assertEqual((edited["amount"], edited["amount_excluded"], edited["tax_rate"]), (220, 200, "10"))

    def test_new_allocation_without_rate_defaults_to_ten_percent(self) -> None:
        batch = repositories.create_import_batch(
            "2026-08", Path("source.csv"), Path("source.zip"), "csv", "zip", ""
        )
        row = InvoiceCsvRow(
            row_number=2,
            external_id="123e4567-e89b-12d3-a456-426614174001",
            project_name="工事A",
            project_code="P001",
            vendor_name="取引先A",
            last_name="",
            first_name="",
            email="",
            phone="",
            invoice_date="2026-08-20",
            total_amount=110,
            raw_data={},
        )
        invoice_id = repositories.insert_invoice(row, "2026-08", batch)
        project_id = int(repositories.get_invoice_detail(invoice_id)["project_id"])
        repositories.ensure_work_type_codes_for_project(project_id)
        code_id = int(repositories.list_work_type_codes(project_id, active_only=True)[0]["id"])

        allocation_id = repositories.save_invoice_allocation(invoice_id, code_id, 100)

        with db.get_connection() as conn:
            saved = conn.execute(
                "SELECT amount, tax_rate FROM invoice_allocations WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        self.assertEqual(saved["amount"], 110)
        self.assertEqual(saved["tax_rate"], "10")


if __name__ == "__main__":
    unittest.main()
