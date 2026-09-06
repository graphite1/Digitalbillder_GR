from __future__ import annotations

import sqlite3
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories


class BillingMonthResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(db, "DATA_DIR", self.root)
        self.path_patch = patch.object(db, "DB_PATH", self.root / "synthetic.db")
        self.data_patch.start()
        self.path_patch.start()
        db.initialize_database()
        permission_patch = patch(
            "invoice_manager.services.test_tools_access._ALLOWED_ACCOUNT_SHA256",
            hashlib.sha256(b"test-admin@example.test").hexdigest(),
        )
        permission_patch.start()
        self.addCleanup(permission_patch.stop)
        repositories.set_app_setting("digital_billder_sync_account", "test-admin@example.test")
        self.project_id = repositories.get_or_create_project("P001", "架空工事")
        self.vendor_id = repositories.get_or_create_vendor("架空取引先")

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def make_invoice(self, date: str, month: str = "2026-01", *, manual: int = 1,
                     project_id: int | None = None) -> int:
        with db.get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO invoices (
                    external_id, project_id, vendor_id, invoice_date, billing_month,
                    billing_month_manual_override, total_amount, total_amount_excluded,
                    local_memo, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 110, 100, '保持するメモ', '2026-01-01', '2026-01-01')""",
                (f"synthetic-{date}-{month}-{project_id}", project_id or self.project_id,
                 self.vendor_id, date, month, manual),
            )
            return int(cursor.lastrowid)

    def invoice(self, invoice_id: int) -> dict:
        with db.get_connection() as connection:
            return dict(connection.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone())

    def database_state(self) -> tuple[str, ...]:
        with db.get_connection() as connection:
            return tuple(connection.iterdump())

    def test_resets_only_requested_ids_and_preserves_unrelated_data(self) -> None:
        before_cutoff = self.make_invoice("2026-08-09")
        after_cutoff = self.make_invoice("2026-08-10")
        next_year = self.make_invoice("2026-12-31")
        outside = self.make_invoice("2026-08-20")
        before = {identifier: self.invoice(identifier) for identifier in (before_cutoff, after_cutoff, next_year, outside)}
        count = repositories.reset_invoice_billing_months_to_auto([before_cutoff, after_cutoff, next_year, before_cutoff])
        self.assertEqual(count, 3)
        for identifier, month in ((before_cutoff, "2026-08"), (after_cutoff, "2026-09"), (next_year, "2027-01")):
            row = self.invoice(identifier)
            self.assertEqual((row["billing_month"], row["billing_month_manual_override"]), (month, 0))
            for key in before[identifier]:
                if key not in ("billing_month", "billing_month_manual_override", "updated_at"):
                    self.assertEqual(row[key], before[identifier][key])
        self.assertEqual(self.invoice(outside), before[outside])
        backups = list((self.root / "backups").glob("*before_billing_month_auto_reset.db"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0], factory=db.ClosingConnection) as connection:
            original = connection.execute("SELECT billing_month, billing_month_manual_override FROM invoices WHERE id = ?",
                                          (before_cutoff,)).fetchone()
        self.assertEqual(original, ("2026-01", 1))
        with db.get_connection() as connection:
            audit = connection.execute("SELECT * FROM audit_logs WHERE action = '請求月自動判定へリセット'").fetchall()
        self.assertEqual(len(audit), 1)
        self.assertIn("3件", audit[0]["detail"])
        self.assertIn(f"{before_cutoff},{after_cutoff},{next_year}", audit[0]["detail"])

    def test_clears_manual_flag_even_when_month_already_matches(self) -> None:
        identifier = self.make_invoice("2026-08-09", "2026-08")
        self.assertEqual(repositories.reset_invoice_billing_months_to_auto([identifier]), 1)
        self.assertEqual(self.invoice(identifier)["billing_month_manual_override"], 0)

    def test_corrects_automatic_row_with_stale_month(self) -> None:
        identifier = self.make_invoice("2026/08/10", "2026-08", manual=0)
        self.assertEqual(repositories.reset_invoice_billing_months_to_auto([identifier]), 1)
        self.assertEqual(self.invoice(identifier)["billing_month"], "2026-09")

    def test_empty_input_and_already_automatic_state_are_noops(self) -> None:
        identifier = self.make_invoice("2026-08-09", "2026-08", manual=0)
        before = self.database_state()
        self.assertEqual(repositories.reset_invoice_billing_months_to_auto([]), 0)
        self.assertEqual(repositories.reset_invoice_billing_months_to_auto([identifier]), 0)
        self.assertEqual(self.database_state(), before)
        self.assertFalse((self.root / "backups").exists())

    def test_missing_id_rejects_the_whole_selection(self) -> None:
        identifier = self.make_invoice("2026-08-09")
        before = self.database_state()
        with self.assertRaisesRegex(ValueError, "見つかりません"):
            repositories.reset_invoice_billing_months_to_auto([identifier, 99999])
        self.assertEqual(self.database_state(), before)

    def test_hidden_project_rejects_the_whole_selection(self) -> None:
        visible = self.make_invoice("2026-08-09")
        hidden_project = repositories.get_or_create_project("P002", "非表示工事")
        hidden = self.make_invoice("2026-08-10", project_id=hidden_project)
        with db.get_connection() as connection:
            connection.execute("UPDATE projects SET is_visible = 0 WHERE id = ?", (hidden_project,))
        before = self.database_state()
        with self.assertRaisesRegex(ValueError, "アーカイブ"):
            repositories.reset_invoice_billing_months_to_auto([visible, hidden])
        self.assertEqual(self.database_state(), before)

    def test_invalid_or_blank_date_rejects_all_rows_before_update(self) -> None:
        valid = self.make_invoice("2026-08-09")
        for date in ("2026-02-30", "", "not-a-date"):
            invalid = self.make_invoice(date)
            before = self.database_state()
            with self.subTest(date=date), self.assertRaisesRegex(ValueError, "請求日が未設定または不正"):
                repositories.reset_invoice_billing_months_to_auto([valid, invalid])
            self.assertEqual(self.database_state(), before)
        self.assertFalse((self.root / "backups").exists())

    def test_audit_failure_rolls_back_all_changes(self) -> None:
        first = self.make_invoice("2026-08-09")
        second = self.make_invoice("2026-08-10")
        before = self.database_state()
        with patch.object(repositories, "add_audit_log", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                repositories.reset_invoice_billing_months_to_auto([first, second])
        self.assertEqual(self.database_state(), before)

    def test_backup_failure_prevents_changes(self) -> None:
        identifier = self.make_invoice("2026-08-09")
        before = self.database_state()
        with patch.object(repositories, "create_database_backup", side_effect=OSError("backup failed")):
            with self.assertRaisesRegex(OSError, "backup failed"):
                repositories.reset_invoice_billing_months_to_auto([identifier])
        self.assertEqual(self.database_state(), before)


if __name__ == "__main__":
    unittest.main()
