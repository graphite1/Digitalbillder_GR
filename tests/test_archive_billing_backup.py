from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from invoice_manager import db, repositories


class ArchiveBillingBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        db.DATA_DIR = self.root / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        self._ensure_manual_override_column()

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def _ensure_manual_override_column(self) -> None:
        with db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()}
            if "billing_month_manual_override" not in columns:
                conn.execute(
                    "ALTER TABLE invoices ADD COLUMN billing_month_manual_override INTEGER NOT NULL DEFAULT 0"
                )

    def _create_project(self, project_code: str, project_name: str, *, is_visible: int = 1) -> int:
        timestamp = "2026-09-04 12:00:00"
        with db.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO projects (project_code, project_name, is_visible, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_code, project_name, is_visible, timestamp, timestamp),
            )
            return int(cur.lastrowid)

    def _create_vendor(self, vendor_name: str = "取引先A") -> int:
        timestamp = "2026-09-04 12:00:00"
        with db.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO vendors (vendor_name, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (vendor_name, timestamp, timestamp),
            )
            return int(cur.lastrowid)

    def _create_invoice(
        self,
        *,
        project_id: int,
        vendor_id: int,
        external_id: str,
        invoice_date: str,
        billing_month: str,
        manual_override: int = 0,
    ) -> int:
        timestamp = "2026-09-04 12:00:00"
        with db.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO invoices (
                    import_batch_id,
                    external_id,
                    project_id,
                    vendor_id,
                    contact_id,
                    invoice_date,
                    billing_month,
                    billing_month_manual_override,
                    total_amount,
                    total_amount_excluded,
                    local_memo,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    external_id,
                    project_id,
                    vendor_id,
                    None,
                    invoice_date,
                    billing_month,
                    manual_override,
                    110_000,
                    100_000,
                    "",
                    timestamp,
                    timestamp,
                ),
            )
            return int(cur.lastrowid)

    def _fetch_invoice(self, invoice_id: int) -> sqlite3.Row:
        with db.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, billing_month, billing_month_manual_override
                FROM invoices
                WHERE id = ?
                """,
                (invoice_id,),
            ).fetchone()

    def test_update_invoice_billing_month_sets_manual_override_when_manual_value_differs(self) -> None:
        project_id = self._create_project("P001", "工事A")
        vendor_id = self._create_vendor()
        invoice_id = self._create_invoice(
            project_id=project_id,
            vendor_id=vendor_id,
            external_id="INV-001",
            invoice_date="2026-08-20",
            billing_month="2026-09",
            manual_override=0,
        )

        updated_count = repositories.update_invoice_billing_month([invoice_id], "2026-08")

        invoice = self._fetch_invoice(invoice_id)
        self.assertEqual(updated_count, 1)
        self.assertEqual(invoice["billing_month"], "2026-08")
        self.assertEqual(invoice["billing_month_manual_override"], 1)

    def test_update_invoice_billing_month_clears_manual_override_when_value_matches_auto(self) -> None:
        project_id = self._create_project("P001", "工事A")
        vendor_id = self._create_vendor()
        invoice_id = self._create_invoice(
            project_id=project_id,
            vendor_id=vendor_id,
            external_id="INV-002",
            invoice_date="2026-08-20",
            billing_month="2026-08",
            manual_override=1,
        )

        updated_count = repositories.update_invoice_billing_month([invoice_id], "2026-09")

        invoice = self._fetch_invoice(invoice_id)
        self.assertEqual(updated_count, 1)
        self.assertEqual(invoice["billing_month"], "2026-09")
        self.assertEqual(invoice["billing_month_manual_override"], 0)

    def test_recalculate_invoice_billing_months_only_updates_visible_projects_without_manual_override(self) -> None:
        visible_project_id = self._create_project("P001", "工事A", is_visible=1)
        hidden_project_id = self._create_project("P002", "工事B", is_visible=0)
        vendor_id = self._create_vendor()
        visible_auto_id = self._create_invoice(
            project_id=visible_project_id,
            vendor_id=vendor_id,
            external_id="INV-101",
            invoice_date="2026-08-09",
            billing_month="2026-09",
            manual_override=0,
        )
        visible_manual_id = self._create_invoice(
            project_id=visible_project_id,
            vendor_id=vendor_id,
            external_id="INV-102",
            invoice_date="2026-08-20",
            billing_month="2026-09",
            manual_override=1,
        )
        hidden_auto_id = self._create_invoice(
            project_id=hidden_project_id,
            vendor_id=vendor_id,
            external_id="INV-103",
            invoice_date="2026-08-20",
            billing_month="2026-08",
            manual_override=0,
        )

        updated_count = repositories.recalculate_invoice_billing_months(visible_project_id)

        visible_auto = self._fetch_invoice(visible_auto_id)
        visible_manual = self._fetch_invoice(visible_manual_id)
        hidden_auto = self._fetch_invoice(hidden_auto_id)
        self.assertEqual(updated_count, 1)
        self.assertEqual(visible_auto["billing_month"], "2026-08")
        self.assertEqual(visible_auto["billing_month_manual_override"], 0)
        self.assertEqual(visible_manual["billing_month"], "2026-09")
        self.assertEqual(visible_manual["billing_month_manual_override"], 1)
        self.assertEqual(hidden_auto["billing_month"], "2026-08")
        self.assertEqual(hidden_auto["billing_month_manual_override"], 0)

    def test_get_or_create_project_does_not_unhide_hidden_project(self) -> None:
        with db.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO projects (project_code, project_name, is_visible, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                ("P001", "工事A", "2026-09-04 12:00:00", "2026-09-04 12:00:00"),
            )
            project_id = int(cur.lastrowid)

        returned_id = repositories.get_or_create_project("P001", "工事A")

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, is_visible FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()

        self.assertEqual(returned_id, project_id)
        self.assertEqual(row["id"], project_id)
        self.assertEqual(row["is_visible"], 0)

    def test_archived_project_invoice_cannot_be_changed(self) -> None:
        project_id = self._create_project("P001", "工事A", is_visible=0)
        vendor_id = self._create_vendor()
        invoice_id = self._create_invoice(
            project_id=project_id,
            vendor_id=vendor_id,
            external_id="ARCHIVED",
            invoice_date="2026-08-20",
            billing_month="2026-09",
        )

        with self.assertRaisesRegex(ValueError, "アーカイブ中"):
            repositories.update_invoice_billing_month([invoice_id], "2026-08")
        with self.assertRaisesRegex(ValueError, "アーカイブ中"):
            repositories.update_invoice_memo(invoice_id, "変更しない")

        invoice = self._fetch_invoice(invoice_id)
        self.assertEqual(invoice["billing_month"], "2026-09")

    def test_create_database_backup_creates_sqlite_copy(self) -> None:
        backup_dir = self.root / "backups"
        source_path = self.root / "source.db"
        with closing(sqlite3.connect(source_path)) as conn:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute("INSERT INTO sample (name) VALUES (?)", ("alpha",))
            conn.commit()

        db_backup = importlib.import_module("invoice_manager.services.db_backup")
        backup_path = db_backup.create_database_backup(
            "archive-check",
            source_path=source_path,
            backup_dir=backup_dir,
        )

        self.assertIsInstance(backup_path, Path)
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.parent, backup_dir)
        with closing(sqlite3.connect(backup_path)) as conn:
            row = conn.execute("SELECT name FROM sample").fetchone()
        self.assertEqual(row[0], "alpha")
        self.assertTrue(source_path.exists())

    def test_migration_marks_existing_exception_as_manual_override(self) -> None:
        project_id = self._create_project("P001", "工事A")
        vendor_id = self._create_vendor()
        automatic_id = self._create_invoice(
            project_id=project_id,
            vendor_id=vendor_id,
            external_id="AUTO",
            invoice_date="2026-08-20",
            billing_month="2026-09",
        )
        exception_id = self._create_invoice(
            project_id=project_id,
            vendor_id=vendor_id,
            external_id="MANUAL",
            invoice_date="2026-08-20",
            billing_month="2026-08",
        )
        with db.get_connection() as conn:
            conn.execute("ALTER TABLE invoices DROP COLUMN billing_month_manual_override")

        db.initialize_database()

        automatic = self._fetch_invoice(automatic_id)
        exception = self._fetch_invoice(exception_id)
        self.assertEqual(automatic["billing_month_manual_override"], 0)
        self.assertEqual(exception["billing_month_manual_override"], 1)


if __name__ == "__main__":
    unittest.main()
