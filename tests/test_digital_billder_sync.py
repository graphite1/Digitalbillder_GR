from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from invoice_manager import db, repositories
from invoice_manager.services import digital_billder_sync as sync, file_storage, import_service
from invoice_manager.services.csv_reader import REQUIRED_COLUMNS, _parse_row


def make_row(external_id, amount=11000):
    raw = dict.fromkeys(REQUIRED_COLUMNS, "")
    raw.update({"ID": external_id, "工事名": "テスト工事", "工事コード": "TEST",
                "取引先名": "テスト取引先", "請求日": "2026-08-31", "請求金額(税込)": str(amount)})
    return _parse_row(2, raw)


class DigitalBillderSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.original_dir, self.original_db = db.DATA_DIR, db.DB_PATH
        db.DATA_DIR, db.DB_PATH = self.root, self.root / "app.db"
        self.storage_patch = patch.object(file_storage, "DATA_DIR", self.root)
        self.storage_patch.start()
        db.initialize_database()
        sync.initialize_sync()
        self.rows = [make_row("one"), make_row("two", 22000)]

    def tearDown(self):
        self.storage_patch.stop()
        db.DATA_DIR, db.DB_PATH = self.original_dir, self.original_db
        self.temp.cleanup()

    def candidate_ids(self, state="pending"):
        return {r.external_id for r in sync.list_candidates(state)}

    def fake_csv(self, page, path):
        sync.write_selected_csv(path, self.rows)
        return path

    def fake_zip(self, page, path):
        with ZipFile(path, "w") as archive:
            for row in self.rows:
                archive.writestr(f"invoices/{row.external_id}/invoice.pdf", b"%PDF-1.4\n%%EOF\n")
        return path

    def run_import(self, ids):
        with (
            patch.object(sync, "export_session", return_value=nullcontext(object())),
            patch.object(sync, "download_csv", side_effect=self.fake_csv),
            patch.object(sync, "download_zip", side_effect=self.fake_zip),
        ):
            return sync.import_selected(ids)

    def test_exclusion_persists_after_refresh_and_can_be_restored(self):
        sync.remember_candidates(self.rows)
        sync.set_excluded({"one"}, True)
        sync.remember_candidates(self.rows)
        self.assertEqual(self.candidate_ids(), {"two"})
        self.assertEqual(self.candidate_ids("excluded"), {"one"})
        sync.set_excluded({"one"}, False)
        self.assertEqual(self.candidate_ids(), {"one", "two"})

    def test_unavailable_invoice_is_hidden_without_losing_exclusion(self):
        sync.remember_candidates(self.rows)
        sync.set_excluded({"one"}, True)
        sync.remember_candidates([])
        self.assertEqual(self.candidate_ids(), set())
        sync.remember_candidates(self.rows)
        self.assertEqual(self.candidate_ids(), {"two"})

    def test_only_selected_invoice_and_its_pdf_are_imported(self):
        sync.remember_candidates(self.rows)
        result = self.run_import({"one"})
        self.assertEqual((result.inserted_count, result.file_count), (1, 1))
        self.assertEqual(self.candidate_ids(), {"two"})
        with db.get_connection() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT external_id FROM invoices")], ["one"])
        self.assertEqual(len(list((self.root / "originals").rglob("*.pdf"))), 1)
        sync.remember_candidates(self.rows)
        self.assertEqual(self.candidate_ids(), {"two"})

    def test_previous_manual_import_is_known(self):
        batch = repositories.create_import_batch("2026-08", Path("x.csv"), Path("x.zip"), "", "", "")
        repositories.insert_invoice(self.rows[0], "2026-08", batch)
        sync.remember_candidates(self.rows)
        self.assertEqual(self.candidate_ids(), {"two"})

    def test_mid_import_error_rolls_back_invoice_and_keeps_candidate(self):
        sync.remember_candidates(self.rows)
        with patch.object(import_service, "insert_invoice_file", side_effect=RuntimeError("disk failure")):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                self.run_import({"one"})
        with db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)
        self.assertEqual(self.candidate_ids(), {"one", "two"})
        result = self.run_import({"one"})
        self.assertEqual(result.inserted_count, 1)

    def test_missing_pdf_keeps_candidate_and_database_unchanged(self):
        sync.remember_candidates(self.rows)
        original = self.fake_zip

        def empty_zip(page, path):
            with ZipFile(path, "w"):
                pass
            return path

        self.fake_zip = empty_zip
        with self.assertRaisesRegex(ValueError, "揃っていません"):
            self.run_import({"one"})
        self.assertEqual(self.candidate_ids(), {"one", "two"})
        self.fake_zip = original

    def test_changed_or_discarded_invoice_is_not_imported(self):
        sync.remember_candidates(self.rows)
        self.rows = [make_row("one", 99999)]
        with self.assertRaisesRegex(ValueError, "変わりました"):
            self.run_import({"one"})
        with self.assertRaisesRegex(ValueError, "破棄済み"):
            self.run_import({"two"})

    def test_invalid_csv_does_not_mark_invoices_as_seen(self):
        sync.remember_candidates([self.rows[0]])
        def bad_csv(page, path):
            path.write_text("invalid\n", encoding="utf-8")
            return path

        with (
            patch.object(sync, "export_session", return_value=nullcontext(object())),
            patch.object(sync, "download_csv", side_effect=bad_csv),
            self.assertRaises(ValueError),
        ):
            sync.fetch_candidates()
        self.assertEqual(self.candidate_ids(), {"one"})

    def test_excluded_invoice_cannot_be_imported_from_stale_selection(self):
        sync.remember_candidates(self.rows)
        sync.set_excluded({"one"}, True)
        with self.assertRaisesRegex(ValueError, "処理済み"):
            self.run_import({"one"})
        self.assertEqual(self.candidate_ids("excluded"), {"one"})

    def test_concurrent_sync_is_rejected(self):
        sync.SYNC_LOCK.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "実行中"):
                sync.fetch_candidates()
        finally:
            sync.SYNC_LOCK.release()

    def test_atomic_transaction_is_not_committed_by_inner_repository_context(self):
        with self.assertRaises(RuntimeError):
            with db.atomic_transaction():
                repositories.set_app_setting("transaction-test", "must roll back")
                raise RuntimeError("test")
        self.assertEqual(repositories.get_app_setting("transaction-test"), "")
        repositories.set_app_setting("transaction-test", "normal commit")
        self.assertEqual(repositories.get_app_setting("transaction-test"), "normal commit")
