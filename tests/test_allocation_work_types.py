from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from invoice_manager import db, repositories
from invoice_manager.models import InvoiceCsvRow
from invoice_manager.services import historical_costs as history
from invoice_manager.services.allocation_work_types import save_resolved_allocation
from invoice_manager.services.work_type_resolution import WorkTypeResolutionError
from invoice_manager.ui.project_budget_window import ProjectBudgetWindow


class ResolvedAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.data_patch = patch.object(db, "DATA_DIR", directory)
        self.path_patch = patch.object(db, "DB_PATH", directory / "synthetic.db")
        self.data_patch.start()
        self.path_patch.start()
        db.initialize_database()
        history.initialize_historical_costs()
        self.project_id = repositories.get_or_create_project("P001", "架空第一工事")
        self.other_project_id = repositories.get_or_create_project("P002", "架空第二工事")
        self.invoice_id = self.make_invoice("invoice-1")

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def make_invoice(self, external_id: str, project_code: str = "P001") -> int:
        batch = repositories.create_import_batch("2026-08", Path("synthetic.csv"), Path("synthetic.zip"),
                                                 external_id, external_id, "")
        row = InvoiceCsvRow(
            row_number=2, external_id=external_id,
            project_name="架空第一工事" if project_code == "P001" else "架空第二工事",
            project_code=project_code, vendor_name="架空取引先", last_name="", first_name="",
            email="", phone="", invoice_date="2026-08-20", total_amount=110, raw_data={},
        )
        return repositories.insert_invoice(row, "2026-08", batch)

    def add_history(self, code: str, *, project_code: str = "P001", name: str = "実績工種") -> None:
        history.upsert_archived_invoice(history.ArchivedInvoiceSnapshot(
            external_id=f"history-{project_code}-{code}", project_code=project_code,
            project_name="架空工事", vendor_name="架空取引先", invoice_date="2026-08-19",
            gross_invoice_total=110, status="archived",
            allocations=(history.ArchivedAllocationSnapshot(code, name, 100, "10", 10, 110),),
        ))

    def database_state(self) -> tuple[str, ...]:
        with db.get_connection() as connection:
            return tuple(connection.iterdump())

    def test_three_digits_save_under_official_master_and_reuse_it(self) -> None:
        self.add_history("D301", name="正式保険料")
        first = save_resolved_allocation(self.invoice_id, "301", 100, tax_rate="10")
        second = save_resolved_allocation(self.invoice_id, "３０１", 200, tax_rate="8")
        rows = repositories.list_invoice_allocations(self.invoice_id)
        self.assertEqual({int(row["id"]) for row in rows}, {first, second})
        self.assertEqual({row["code"] for row in rows}, {"D301"})
        self.assertEqual(len({row["work_type_code_id"] for row in rows}), 1)
        master = [row for row in repositories.list_work_type_codes(self.project_id) if row["code"] == "D301"]
        self.assertEqual(len(master), 1)
        self.assertEqual(master[0]["name"], "正式保険料")
        self.assertEqual([(row["amount_excluded"], row["tax_rate"], row["amount"]) for row in rows],
                         [(100, "10", 110), (200, "8", 216)])

    def test_basic_d_code_without_history_saves_and_reuses_template_master(self) -> None:
        repositories.ensure_work_type_codes_for_project(self.project_id)
        masters = repositories.list_work_type_codes(self.project_id)
        self.assertTrue(all(row["code"].startswith("D") for row in masters))
        code_id = next(int(row["id"]) for row in masters if row["code"] == "D301")
        save_resolved_allocation(self.invoice_id, "301", 100, tax_rate="8")
        save_resolved_allocation(self.invoice_id, "D301", 200, tax_rate="10")
        rows = repositories.list_invoice_allocations(self.invoice_id)
        self.assertEqual({row["work_type_code_id"] for row in rows}, {code_id})
        self.assertEqual([row["code"] for row in rows], ["D301", "D301"])
        self.assertEqual(len(repositories.list_work_type_codes(self.project_id)), len(masters))

    def test_basic_rule_keeps_legacy_numeric_row_and_rolls_back_failed_creation(self) -> None:
        numeric_id = repositories.save_work_type_code(self.project_id, "301", "旧名称", sort_order=77)
        before = self.database_state()
        with self.assertRaises(ValueError):
            save_resolved_allocation(self.invoice_id, "301", 100, tax_rate="5")
        self.assertEqual(self.database_state(), before)
        save_resolved_allocation(self.invoice_id, "301", 100)
        legacy = next(row for row in repositories.list_work_type_codes(self.project_id) if row["id"] == numeric_id)
        self.assertEqual((legacy["code"], legacy["name"], legacy["sort_order"]), ("301", "旧名称", 77))
        self.assertEqual(repositories.list_invoice_allocations(self.invoice_id)[0]["code"], "D301")

    def test_basic_rule_rejects_disabled_d_and_numeric_masters(self) -> None:
        for code in ("301", "D302"):
            repositories.save_work_type_code(self.project_id, code, "無効工種", is_active=0)
        before = self.database_state()
        for value in ("301", "３０１", "D301", "302", "D302"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "無効化"):
                save_resolved_allocation(self.invoice_id, value, 100)
            self.assertEqual(self.database_state(), before)

    def test_existing_numeric_master_and_allocation_are_not_migrated(self) -> None:
        self.add_history("D301")
        numeric_id = repositories.save_work_type_code(self.project_id, "301", "手入力名称", sort_order=77)
        previous_id = repositories.save_invoice_allocation(self.invoice_id, numeric_id, 120, "旧メモ", 4,
                                                            tax_rate="8")
        previous = dict(repositories.list_invoice_allocations(self.invoice_id)[0])
        original_master = dict(next(row for row in repositories.list_work_type_codes(self.project_id)
                                    if row["id"] == numeric_id))
        new_id = save_resolved_allocation(self.invoice_id, "301", 50, tax_rate="exempt")
        rows = {int(row["id"]): dict(row) for row in repositories.list_invoice_allocations(self.invoice_id)}
        self.assertEqual(rows[previous_id], previous)
        self.assertEqual(rows[new_id]["code"], "D301")
        self.assertNotEqual(rows[new_id]["work_type_code_id"], numeric_id)
        self.assertEqual(dict(next(row for row in repositories.list_work_type_codes(self.project_id)
                                   if row["id"] == numeric_id)), original_master)

    def test_reusing_existing_official_master_preserves_manual_name_and_order(self) -> None:
        self.add_history("D301", name="履歴名称")
        master_id = repositories.save_work_type_code(self.project_id, "D301", "手入力名称", sort_order=88)
        before = dict(next(row for row in repositories.list_work_type_codes(self.project_id) if row["id"] == master_id))
        save_resolved_allocation(self.invoice_id, "301", 100)
        self.assertEqual(dict(next(row for row in repositories.list_work_type_codes(self.project_id)
                                   if row["id"] == master_id)), before)

    def test_unknown_and_ambiguous_codes_make_no_database_changes(self) -> None:
        self.add_history("D301")
        self.add_history("B301")
        for value in ("999", "301"):
            with self.subTest(value=value):
                before = self.database_state()
                with self.assertRaises(WorkTypeResolutionError):
                    save_resolved_allocation(self.invoice_id, value, 100)
                self.assertEqual(self.database_state(), before)

    def test_other_project_history_is_not_used_to_resolve_allocation(self) -> None:
        self.add_history("D301", project_code="P002")
        before = self.database_state()
        with self.assertRaises(WorkTypeResolutionError):
            save_resolved_allocation(self.invoice_id, "301", 100)
        self.assertEqual(self.database_state(), before)

    def test_disabled_official_master_rejects_save_without_changes(self) -> None:
        self.add_history("D301")
        repositories.save_work_type_code(self.project_id, "D301", "無効工種", is_active=0)
        before = self.database_state()
        with self.assertRaisesRegex(ValueError, "無効化"):
            save_resolved_allocation(self.invoice_id, "301", 100)
        self.assertEqual(self.database_state(), before)

    def test_disabled_numeric_master_cannot_be_bypassed_with_fullwidth_input(self) -> None:
        self.add_history("D301")
        repositories.save_work_type_code(self.project_id, "301", "無効略記", is_active=0)
        for value in ("301", "３０１"):
            with self.subTest(value=value):
                before = self.database_state()
                with self.assertRaisesRegex(ValueError, "無効化"):
                    save_resolved_allocation(self.invoice_id, value, 100)
                self.assertEqual(self.database_state(), before)

    def test_invalid_tax_rate_rolls_back_new_master_and_audit_log(self) -> None:
        self.add_history("D301")
        before = self.database_state()
        with self.assertRaises(ValueError):
            save_resolved_allocation(self.invoice_id, "301", 100, tax_rate="5")
        self.assertEqual(self.database_state(), before)

    def test_allocation_from_another_invoice_rolls_back_new_master(self) -> None:
        self.add_history("D301")
        numeric_id = repositories.save_work_type_code(self.project_id, "301", "数値工種")
        other_invoice = self.make_invoice("invoice-2")
        other_allocation = repositories.save_invoice_allocation(other_invoice, numeric_id, 100)
        before = self.database_state()
        with self.assertRaisesRegex(ValueError, "一致しません"):
            save_resolved_allocation(self.invoice_id, "301", 100, allocation_id=other_allocation)
        self.assertEqual(self.database_state(), before)

    def test_missing_invoice_does_not_create_anything(self) -> None:
        self.add_history("D301")
        before = self.database_state()
        with self.assertRaisesRegex(ValueError, "見つかりません"):
            save_resolved_allocation(99999, "301", 100)
        self.assertEqual(self.database_state(), before)

    def test_budget_actual_code_resolves_shorthand_and_preserves_empty(self) -> None:
        self.add_history("D301")
        self.add_history("B401", project_code="P002")
        for value, expected in (("301", "D301"), ("３０１", "D301"), ("D301｜実績工種", "D301"), (" ", None)):
            with self.subTest(value=value):
                stub = SimpleNamespace(_project_id=lambda: self.project_id, code_name_options={},
                                       actual_code_var=SimpleNamespace(get=lambda: value))
                self.assertEqual(ProjectBudgetWindow._actual_code(stub), expected)
        for value in ("401", "B401", "999"):
            with self.subTest(value=value):
                stub = SimpleNamespace(_project_id=lambda: self.project_id, code_name_options={},
                                       actual_code_var=SimpleNamespace(get=lambda: value))
                with self.assertRaises(WorkTypeResolutionError):
                    ProjectBudgetWindow._actual_code(stub)


if __name__ == "__main__":
    unittest.main()
