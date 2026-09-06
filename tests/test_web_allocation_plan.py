from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.models import InvoiceCsvRow
from invoice_manager.services.work_type_resolution import CanonicalWorkType, load_work_type_choices
from invoice_manager.services.web_allocation_plan import (
    AllocationLine,
    build_allocation_plan,
    compare_allocations,
)


EXTERNAL_ID = "123e4567-e89b-12d3-a456-426614174000"


def make_row(total_amount: int) -> InvoiceCsvRow:
    return InvoiceCsvRow(
        row_number=2,
        external_id=EXTERNAL_ID,
        project_name="工事A",
        project_code="P001",
        vendor_name="取引先A",
        last_name="",
        first_name="",
        email="",
        phone="",
        invoice_date="2026-08-20",
        total_amount=total_amount,
        raw_data={},
    )


class WebAllocationPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog_patch = patch(
            "invoice_manager.services.web_allocation_plan.load_work_type_choices",
            return_value=tuple(CanonicalWorkType(f"D{code}", f"実績名称{code}") for code in ("301", "302", "303")),
        )
        self.catalog = catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        db.DATA_DIR = Path(self.temp_dir.name) / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()

        batch = repositories.create_import_batch(
            "2026-08", Path("source.csv"), Path("source.zip"), "csv", "zip", ""
        )
        self.invoice_id = repositories.insert_invoice(make_row(0), "2026-08", batch)
        detail = repositories.get_invoice_detail(self.invoice_id)
        self.project_id = int(detail["project_id"])
        repositories.ensure_work_type_codes_for_project(self.project_id)
        # Retain coverage for allocations made with the pre-D numeric template.
        for code in ("301", "302", "303"):
            repositories.save_work_type_code(self.project_id, code, f"旧名称{code}")
        self.code_ids = {
            row["code"]: int(row["id"])
            for row in repositories.list_work_type_codes(self.project_id, active_only=True)
        }

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def add_allocation(self, amount: int, rate: str, code: str = "301", sort_order: int = 1) -> int:
        return repositories.save_invoice_allocation(
            self.invoice_id,
            self.code_ids[code],
            amount,
            sort_order=sort_order,
            tax_rate=rate,
        )

    def set_invoice_total(self, amount: int) -> None:
        with db.get_connection() as conn:
            conn.execute("UPDATE invoices SET total_amount = ? WHERE id = ?", (amount, self.invoice_id))

    def test_plan_calculates_10_percent_8_percent_and_exempt_lines(self) -> None:
        self.add_allocation(100, "10", "301", 1)
        self.add_allocation(100, "8", "302", 2)
        self.add_allocation(100, "exempt", "303", 3)
        self.set_invoice_total(318)

        plan = build_allocation_plan(self.invoice_id)

        self.assertEqual([line.tax_rate for line in plan.lines], ["10", "8", "exempt"])
        self.assertEqual([line.tax_amount for line in plan.lines], [10, 8, 0])
        self.assertEqual([line.amount_included for line in plan.lines], [110, 108, 100])
        self.assertEqual(plan.total_included, 318)
        self.assertEqual(plan.errors, ())
        self.assertEqual([line.code for line in plan.lines], ["D301", "D302", "D303"])
        self.assertEqual(plan.lines[0].name, "実績名称301")

    def test_ambiguous_codes_block_plan_without_changing_allocations(self) -> None:
        self.add_allocation(100, "10")
        self.set_invoice_total(110)
        before = [dict(row) for row in repositories.list_invoice_allocations(self.invoice_id)]
        self.catalog.return_value = (CanonicalWorkType("D301", "土木"), CanonicalWorkType("B301", "建築"))
        plan = build_allocation_plan(self.invoice_id)
        self.assertTrue(plan.errors)
        after = [dict(row) for row in repositories.list_invoice_allocations(self.invoice_id)]
        self.assertEqual(before, after)

    def test_basic_d_rule_without_history_is_valid_and_keeps_old_allocation(self) -> None:
        self.catalog.side_effect = load_work_type_choices
        self.add_allocation(100, "10")
        self.set_invoice_total(110)
        before = [dict(row) for row in repositories.list_invoice_allocations(self.invoice_id)]
        plan = build_allocation_plan(self.invoice_id)
        self.assertEqual(plan.errors, ())
        self.assertEqual(plan.lines[0].code, "D301")
        self.assertEqual(plan.lines[0].name, "保険料")
        self.assertEqual([dict(row) for row in repositories.list_invoice_allocations(self.invoice_id)], before)

    def test_unknown_custom_prefix_without_history_is_not_guessed(self) -> None:
        self.catalog.side_effect = load_work_type_choices
        code_id = repositories.save_work_type_code(self.project_id, "Z999", "独自工種")
        repositories.save_invoice_allocation(self.invoice_id, code_id, 100)
        self.set_invoice_total(110)
        self.assertIn("確認できません", " ".join(build_allocation_plan(self.invoice_id).errors))

    def test_basic_rule_rejects_disabled_numeric_or_formal_alias(self) -> None:
        self.catalog.side_effect = load_work_type_choices
        self.add_allocation(100, "10")
        self.set_invoice_total(110)
        with db.get_connection() as connection:
            connection.execute("UPDATE work_type_codes SET is_active = 0 WHERE project_id = ? AND code = 'D301'", (self.project_id,))
        self.assertIn("無効化", " ".join(build_allocation_plan(self.invoice_id).errors))

    def test_mixed_tax_compares_gross_total_to_invoice_original(self) -> None:
        self.add_allocation(100, "10", "301", 1)
        self.add_allocation(100, "8", "302", 2)
        self.set_invoice_total(218)

        plan = build_allocation_plan(self.invoice_id)

        self.assertEqual(sum(line.amount_excluded for line in plan.lines), 200)
        self.assertEqual(plan.total_included, 218)
        self.assertNotIn("原本額が一致", " ".join(plan.errors))

    def test_fingerprint_changes_when_an_allocation_changes(self) -> None:
        allocation_id = self.add_allocation(100, "10")
        self.set_invoice_total(110)
        before = build_allocation_plan(self.invoice_id)

        repositories.save_invoice_allocation(
            self.invoice_id,
            self.code_ids["301"],
            100,
            allocation_id=allocation_id,
            tax_rate="8",
        )
        after = build_allocation_plan(self.invoice_id)

        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.lines[0].tax_rate, "10")
        self.assertEqual(after.lines[0].tax_rate, "8")

    def test_fingerprint_changes_when_validation_errors_change(self) -> None:
        self.add_allocation(100, "10")
        self.set_invoice_total(110)
        before = build_allocation_plan(self.invoice_id)

        with db.get_connection() as conn:
            conn.execute(
                "UPDATE work_type_codes SET is_active = 0 WHERE id = ?",
                (self.code_ids["301"],),
            )
        after = build_allocation_plan(self.invoice_id)

        self.assertNotEqual(before.errors, after.errors)
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.lines[0].amount_excluded, after.lines[0].amount_excluded)

    def test_compare_allocations_keeps_row_order_and_reports_each_field(self) -> None:
        first = AllocationLine("301", "保険料", 100, "10", 10, 110)
        second = AllocationLine("302", "給与", 200, "8", 16, 216)

        differences = compare_allocations((first, second), (second, first))

        self.assertEqual(differences[0].row_number, 1)
        self.assertEqual(differences[0].field, "工種コード")
        self.assertEqual(differences[0].local, "301")
        self.assertEqual(differences[0].web, "302")
        self.assertEqual(differences[1].row_number, 1)
        self.assertEqual(differences[1].field, "税抜金額")
        self.assertEqual(differences[-1].row_number, 2)
        self.assertEqual(len(differences), 10)

        added = compare_allocations((first,), (first, second))
        self.assertEqual(added[-1].row_number, 2)
        self.assertEqual(added[-1].field, "振分行")
        self.assertEqual(added[-1].local, "なし")
        self.assertEqual(added[-1].web, "302")

    def test_plan_reports_inactive_code_and_zero_amount(self) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE work_type_codes SET is_active = 0 WHERE project_id = ? AND code = ?",
                (self.project_id, "301"),
            )
        self.add_allocation(0, "10", "301")
        self.set_invoice_total(0)

        plan = build_allocation_plan(self.invoice_id)

        joined = " ".join(plan.errors)
        self.assertIn("有効でない工種コード", joined)
        self.assertIn("0円以下", joined)

    def test_plan_reports_missing_rows(self) -> None:
        plan = build_allocation_plan(self.invoice_id)

        self.assertEqual(plan.lines, ())
        self.assertIn("工種振分を入力", " ".join(plan.errors))

    def test_plan_rejects_invalid_external_id_and_saved_tax_mismatch(self) -> None:
        self.add_allocation(100, "10")
        self.set_invoice_total(110)
        with db.get_connection() as conn:
            conn.execute("UPDATE invoices SET external_id = ? WHERE id = ?", ("not-a-uuid", self.invoice_id))
            conn.execute(
                "UPDATE invoice_allocations SET amount = ? WHERE invoice_id = ?",
                (111, self.invoice_id),
            )

        plan = build_allocation_plan(self.invoice_id)

        self.assertIn("請求書ID", " ".join(plan.errors))
        self.assertIn("税込金額と税率計算", " ".join(plan.errors))
        with self.assertRaises(ValueError):
            UUID(plan.external_id)


if __name__ == "__main__":
    unittest.main()
