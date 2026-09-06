from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from invoice_manager import db, repositories
from invoice_manager.services.allocation_rounding import preview_rounding_adjustment, apply_rounding_adjustment
from invoice_manager.services.web_allocation_plan import build_allocation_plan


class AllocationRoundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.data_patch = patch.object(db, "DATA_DIR", directory)
        self.path_patch = patch.object(db, "DB_PATH", directory / "synthetic.db")
        self.data_patch.start()
        self.path_patch.start()
        db.initialize_database()
        self.project_id = repositories.get_or_create_project("P001", "架空工事")
        self.vendor_id = repositories.get_or_create_vendor("架空取引先")
        self.code_id = repositories.save_work_type_code(self.project_id, "D301", "保険料")
        self.invoice_id = self.make_invoice(22956, 25252)
        self.allocation_id = repositories.save_invoice_allocation(self.invoice_id, self.code_id, 22956, "保持メモ", 7)

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def make_invoice(self, net: int | None, gross: int) -> int:
        with db.get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO invoices (external_id, project_id, vendor_id, invoice_date,
                     billing_month, total_amount, total_amount_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, '2026-08-09', '2026-08', ?, ?, '2026-08-09', '2026-08-09')""",
                (str(uuid4()), self.project_id, self.vendor_id, gross, net),
            )
            return int(cursor.lastrowid)

    def state(self) -> tuple[str, ...]:
        with db.get_connection() as connection:
            return tuple(connection.iterdump())

    def allocation(self, allocation_id: int | None = None) -> dict:
        with db.get_connection() as connection:
            return dict(connection.execute("SELECT * FROM invoice_allocations WHERE id = ?",
                                           (allocation_id or self.allocation_id,)).fetchone())

    def preview(self):
        return preview_rounding_adjustment(self.invoice_id, self.allocation_id)

    def apply(self):
        preview = self.preview()
        return apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)

    def test_positive_one_yen_preserves_net_rate_code_and_records_adjustment(self) -> None:
        before = self.allocation()
        preview = self.preview()
        self.assertEqual((preview.difference, preview.net_amount, preview.tax_before, preview.tax_after),
                         (1, 22956, 2295, 2296))
        self.assertEqual((preview.gross_before, preview.gross_after, preview.code), (25251, 25252, "D301"))
        apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)
        after = self.allocation()
        self.assertEqual(after["amount"], 25252)
        self.assertEqual(after["tax_rounding_adjustment"], 1)
        for key in before:
            if key not in ("amount", "tax_rounding_adjustment", "updated_at"):
                self.assertEqual(after[key], before[key])
        self.assertEqual(build_allocation_plan(self.invoice_id).errors, ())
        with db.get_connection() as connection:
            audit = connection.execute("SELECT detail FROM audit_logs WHERE action = '振分税額端数調整'").fetchone()
        self.assertIn("税額:2295→2296", audit["detail"])
        self.assertIn("税込:25251→25252", audit["detail"])

    def test_negative_one_yen_is_allowed(self) -> None:
        invoice = self.make_invoice(100, 109)
        line = repositories.save_invoice_allocation(invoice, self.code_id, 100)
        preview = preview_rounding_adjustment(invoice, line)
        self.assertEqual((preview.difference, preview.tax_after, preview.adjustment_after), (-1, 9, -1))
        apply_rounding_adjustment(invoice, line, preview)
        self.assertEqual(build_allocation_plan(invoice).errors, ())

    def test_mixed_tax_adjusts_selected_eight_percent_line_only(self) -> None:
        invoice = self.make_invoice(200, 219)
        first = repositories.save_invoice_allocation(invoice, self.code_id, 100, tax_rate="10")
        selected = repositories.save_invoice_allocation(invoice, self.code_id, 100, tax_rate="8")
        first_before = self.allocation(first)
        preview = preview_rounding_adjustment(invoice, selected)
        self.assertEqual((preview.tax_rate, preview.tax_before, preview.tax_after), ("8", 8, 9))
        apply_rounding_adjustment(invoice, selected, preview)
        self.assertEqual(self.allocation(first), first_before)
        self.assertEqual(build_allocation_plan(invoice).errors, ())

    def test_zero_or_two_yen_difference_is_rejected(self) -> None:
        for gross in (25251, 25253, 25249):
            with db.get_connection() as connection:
                connection.execute("UPDATE invoices SET total_amount = ? WHERE id = ?", (gross, self.invoice_id))
            before = self.state()
            with self.subTest(gross=gross), self.assertRaisesRegex(ValueError, "差が1円"):
                self.preview()
            self.assertEqual(self.state(), before)

    def test_unknown_or_mismatched_invoice_net_is_rejected(self) -> None:
        for net in (None, 22955):
            with db.get_connection() as connection:
                connection.execute("UPDATE invoices SET total_amount_excluded = ? WHERE id = ?", (net, self.invoice_id))
            with self.subTest(net=net), self.assertRaisesRegex(ValueError, "税抜"):
                self.preview()

    def test_exempt_line_is_rejected(self) -> None:
        invoice = self.make_invoice(100, 101)
        line = repositories.save_invoice_allocation(invoice, self.code_id, 100, tax_rate="exempt")
        with self.assertRaisesRegex(ValueError, "非課税"):
            preview_rounding_adjustment(invoice, line)

    def test_missing_selection_and_another_invoice_line_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "選択"):
            preview_rounding_adjustment(self.invoice_id, 0)
        other_invoice = self.make_invoice(100, 111)
        other_line = repositories.save_invoice_allocation(other_invoice, self.code_id, 100)
        with self.assertRaisesRegex(ValueError, "見つかりません"):
            preview_rounding_adjustment(self.invoice_id, other_line)

    def test_hidden_invoice_and_disabled_master_are_rejected(self) -> None:
        with db.get_connection() as connection:
            connection.execute("UPDATE projects SET is_visible = 0 WHERE id = ?", (self.project_id,))
        with self.assertRaisesRegex(ValueError, "アーカイブ"):
            self.preview()
        with db.get_connection() as connection:
            connection.execute("UPDATE projects SET is_visible = 1 WHERE id = ?", (self.project_id,))
            connection.execute("UPDATE work_type_codes SET is_active = 0 WHERE id = ?", (self.code_id,))
        with self.assertRaisesRegex(ValueError, "無効"):
            self.preview()

    def test_double_apply_is_rejected_without_further_changes(self) -> None:
        preview = self.preview()
        apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)
        before = self.state()
        with self.assertRaises(ValueError):
            apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)
        self.assertEqual(self.state(), before)

    def test_another_line_change_invalidates_preview_even_if_totals_are_unchanged(self) -> None:
        invoice = self.make_invoice(200, 221)
        first = repositories.save_invoice_allocation(invoice, self.code_id, 100)
        second = repositories.save_invoice_allocation(invoice, self.code_id, 100)
        preview = preview_rounding_adjustment(invoice, first)
        repositories.save_invoice_allocation(invoice, self.code_id, 100, "別のメモ", allocation_id=second)
        before = self.state()
        with self.assertRaisesRegex(ValueError, "確認後"):
            apply_rounding_adjustment(invoice, first, preview)
        self.assertEqual(self.state(), before)

    def test_invoice_change_invalidates_preview(self) -> None:
        preview = self.preview()
        repositories.update_invoice_memo(self.invoice_id, "確認後のメモ")
        with self.assertRaisesRegex(ValueError, "確認後"):
            apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)

    def test_audit_failure_rolls_back_adjustment(self) -> None:
        preview = self.preview()
        before = self.state()
        with patch.object(repositories, "add_audit_log", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                apply_rounding_adjustment(self.invoice_id, self.allocation_id, preview)
        self.assertEqual(self.state(), before)

    def test_memo_edit_preserves_adjustment_and_net_edit_clears_it(self) -> None:
        self.apply()
        repositories.save_invoice_allocation(self.invoice_id, self.code_id, 22956, "新メモ", allocation_id=self.allocation_id)
        row = self.allocation()
        self.assertEqual((row["amount"], row["tax_rounding_adjustment"]), (25252, 1))
        repositories.save_invoice_allocation(self.invoice_id, self.code_id, 100, allocation_id=self.allocation_id)
        row = self.allocation()
        self.assertEqual((row["amount"], row["tax_rounding_adjustment"]), (110, 0))

    def test_tax_rate_edit_clears_adjustment(self) -> None:
        self.apply()
        repositories.save_invoice_allocation(self.invoice_id, self.code_id, 22956, allocation_id=self.allocation_id, tax_rate="8")
        row = self.allocation()
        self.assertEqual((row["amount"], row["tax_rounding_adjustment"]), (24792, 0))

    def test_cumulative_adjustment_cannot_exceed_one_yen(self) -> None:
        self.apply()
        with db.get_connection() as connection:
            connection.execute("UPDATE invoices SET total_amount = 25253 WHERE id = ?", (self.invoice_id,))
        with self.assertRaisesRegex(ValueError, "合計で±1円"):
            self.preview()

    def test_unrecorded_gross_mismatch_remains_invalid_in_web_plan(self) -> None:
        with db.get_connection() as connection:
            connection.execute("UPDATE invoice_allocations SET amount = 25252 WHERE id = ?", (self.allocation_id,))
        self.assertIn("税率計算に差", " ".join(build_allocation_plan(self.invoice_id).errors))

    def test_invalid_recorded_adjustment_and_exempt_adjustment_are_not_accepted(self) -> None:
        with db.get_connection() as connection:
            connection.execute("UPDATE invoice_allocations SET amount = 25253, tax_rounding_adjustment = 2 WHERE id = ?", (self.allocation_id,))
        self.assertIn("端数調整が不正", " ".join(build_allocation_plan(self.invoice_id).errors))
        with self.assertRaisesRegex(ValueError, "端数調整が不正"):
            self.preview()
        invoice = self.make_invoice(100, 101)
        line = repositories.save_invoice_allocation(invoice, self.code_id, 100, tax_rate="exempt")
        with db.get_connection() as connection:
            connection.execute("UPDATE invoice_allocations SET amount = 101, tax_rounding_adjustment = 1 WHERE id = ?", (line,))
        self.assertIn("端数調整が不正", " ".join(build_allocation_plan(invoice).errors))

    def test_negative_tax_after_adjustment_is_rejected(self) -> None:
        invoice = self.make_invoice(1, 0)
        line = repositories.save_invoice_allocation(invoice, self.code_id, 1)
        with self.assertRaisesRegex(ValueError, "マイナス"):
            preview_rounding_adjustment(invoice, line)
        with db.get_connection() as connection:
            connection.execute("UPDATE invoice_allocations SET amount = 0, tax_rounding_adjustment = -1 WHERE id = ?", (line,))
        self.assertIn("端数調整が不正", " ".join(build_allocation_plan(invoice).errors))

    def test_migration_keeps_legacy_amounts_and_is_idempotent(self) -> None:
        with sqlite3.connect(":memory:", factory=db.ClosingConnection) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE invoice_allocations (id INTEGER, amount INTEGER, amount_excluded INTEGER, tax_rate TEXT)")
            connection.execute("INSERT INTO invoice_allocations VALUES (1, 25252, 22956, '10')")
            db._migrate_allocation_rounding_adjustment(connection)
            db._migrate_allocation_rounding_adjustment(connection)
            row = connection.execute("SELECT * FROM invoice_allocations").fetchone()
            self.assertEqual(dict(row), {"id": 1, "amount": 25252, "amount_excluded": 22956,
                                         "tax_rate": "10", "tax_rounding_adjustment": 0})


if __name__ == "__main__":
    unittest.main()
