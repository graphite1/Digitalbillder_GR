from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.services import test_tools_access as access


class TestToolsAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(db, "DATA_DIR", self.root)
        self.path_patch = patch.object(db, "DB_PATH", self.root / "synthetic.db")
        self.data_patch.start()
        self.path_patch.start()
        db.initialize_database()
        self.allowed_patch = patch.object(access, "_ALLOWED_ACCOUNT_SHA256",
                                          hashlib.sha256(b"test-admin@example.test").hexdigest())
        self.allowed_patch.start()

    def tearDown(self) -> None:
        self.allowed_patch.stop()
        self.path_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def test_unregistered_or_regular_account_is_denied(self) -> None:
        self.assertFalse(access.can_use_test_tools())
        for value in ("", " ", "regular@example.test", "test-admin@example.test.invalid"):
            repositories.set_app_setting("digital_billder_sync_account", value)
            self.assertFalse(access.can_use_test_tools())
            with self.assertRaisesRegex(PermissionError, "管理者アカウントの登録が必要"):
                access.require_test_tools_access()

    def test_registered_administrator_normalizes_whitespace_and_case(self) -> None:
        repositories.set_app_setting("digital_billder_sync_account", "  TEST-ADMIN@Example.Test \t")
        self.assertTrue(access.can_use_test_tools())
        access.require_test_tools_access()

    def test_account_is_checked_again_after_ui_precheck(self) -> None:
        repositories.set_app_setting("digital_billder_sync_account", "test-admin@example.test")
        self.assertTrue(access.can_use_test_tools())
        repositories.set_app_setting("digital_billder_sync_account", "regular@example.test")
        with self.assertRaises(PermissionError):
            access.require_test_tools_access()
        with self.assertRaises(PermissionError):
            repositories.reset_invoice_billing_months_to_auto([])
        with self.assertRaises(PermissionError):
            repositories.recalculate_invoice_billing_months(99999)

    def test_read_failure_fails_closed(self) -> None:
        with patch.object(repositories, "get_app_setting", side_effect=sqlite3.OperationalError("unreadable")):
            self.assertFalse(access.can_use_test_tools())
            with self.assertRaisesRegex(PermissionError, "管理者アカウントの登録が必要"):
                access.require_test_tools_access()

    def test_backend_direct_calls_reject_before_database_mutation_or_backup(self) -> None:
        with db.get_connection() as connection:
            before = tuple(connection.iterdump())
        with patch.object(repositories, "create_database_backup") as backup:
            with self.assertRaises(PermissionError):
                repositories.reset_invoice_billing_months_to_auto([1])
            with self.assertRaises(PermissionError):
                repositories.recalculate_invoice_billing_months(1)
            backup.assert_not_called()
        with db.get_connection() as connection:
            self.assertEqual(tuple(connection.iterdump()), before)

    def test_regular_account_keeps_normal_manual_billing_month_change(self) -> None:
        repositories.set_app_setting("digital_billder_sync_account", "regular@example.test")
        project = repositories.get_or_create_project("P001", "架空工事")
        vendor = repositories.get_or_create_vendor("架空取引先")
        with db.get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO invoices (external_id, project_id, vendor_id, invoice_date,
                    billing_month, billing_month_manual_override, total_amount, total_amount_excluded,
                    created_at, updated_at) VALUES ('synthetic-normal', ?, ?, '2026-08-10',
                    '2026-09', 0, 110, 100, '2026-08-10', '2026-08-10')""", (project, vendor))
            invoice_id = int(cursor.lastrowid)
        self.assertEqual(repositories.update_invoice_billing_month([invoice_id], "2026-08"), 1)
        with db.get_connection() as connection:
            row = connection.execute("SELECT billing_month, billing_month_manual_override FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        self.assertEqual((row["billing_month"], row["billing_month_manual_override"]), ("2026-08", 1))


if __name__ == "__main__":
    unittest.main()
