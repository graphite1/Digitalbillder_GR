from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from invoice_manager import db, repositories
from invoice_manager.models import InvoiceCsvRow


class CustomWorkTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        db.DATA_DIR = Path(self.temp_dir.name) / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        self.project_id = repositories.get_or_create_project("P001", "第一工事")
        self.other_project_id = repositories.get_or_create_project("P002", "第二工事")
        self.vendor_id = repositories.get_or_create_vendor("取引先A")

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def make_invoice(self, external_id: str, project_code: str = "P001") -> int:
        batch = repositories.create_import_batch(
            "2026-08", Path("source.csv"), Path("source.zip"), "csv", "zip", ""
        )
        row = InvoiceCsvRow(
            row_number=2,
            external_id=external_id,
            project_name="第一工事" if project_code == "P001" else "第二工事",
            project_code=project_code,
            vendor_name="取引先A",
            last_name="",
            first_name="",
            email="",
            phone="",
            invoice_date="2026-08-20",
            total_amount=110,
            raw_data={},
        )
        return repositories.insert_invoice(row, "2026-08", batch)

    def test_custom_code_is_saved_and_listed_for_its_project(self) -> None:
        custom_id = repositories.save_work_type_code(
            self.project_id, "WebD607", "Web土工", sort_order=99
        )

        rows = repositories.list_work_type_codes(self.project_id, active_only=True)
        row = next(row for row in rows if int(row["id"]) == custom_id)
        self.assertEqual((row["code"], row["name"], row["sort_order"]), ("WebD607", "Web土工", 99))

    def test_same_code_is_allowed_in_another_project_but_not_duplicate_in_one(self) -> None:
        first_id = repositories.save_work_type_code(self.project_id, "607", "数値607")
        second_id = repositories.save_work_type_code(self.other_project_id, "607", "別工事607")
        self.assertNotEqual(first_id, second_id)

        with self.assertRaisesRegex(ValueError, "同じ工事"):
            repositories.save_work_type_code(self.project_id, "607", "重複")

    def test_empty_and_long_codes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            repositories.save_work_type_code(self.project_id, "  ", "名称")
        with self.assertRaises(ValueError):
            repositories.save_work_type_code(self.project_id, "x" * 65, "名称")

    def test_catalog_is_initial_seed_and_does_not_overwrite_manual_name_or_order(self) -> None:
        manual_id = repositories.save_work_type_code(
            self.project_id, "301", "手動名称", sort_order=777
        )

        inserted = repositories.ensure_work_type_codes_for_project(self.project_id)
        self.assertGreater(inserted, 0)
        repositories.ensure_work_type_codes_for_project(self.project_id)

        row = next(row for row in repositories.list_work_type_codes(self.project_id) if int(row["id"]) == manual_id)
        self.assertEqual((row["code"], row["name"], row["sort_order"]), ("301", "手動名称", 777))
        self.assertNotIn("D301", {row["code"] for row in repositories.list_work_type_codes(self.project_id)})

    def test_d_template_preserves_disabled_legacy_and_existing_aliases(self) -> None:
        legacy_id = repositories.save_work_type_code(self.project_id, "301", "旧無効名称", is_active=0)
        numeric_id = repositories.save_work_type_code(self.project_id, "302", "数字名")
        formal_id = repositories.save_work_type_code(self.project_id, "D302", "D手動名", sort_order=333)
        before = {row["id"]: dict(row) for row in repositories.list_work_type_codes(self.project_id)}
        repositories.ensure_work_type_codes_for_project(self.project_id)
        self.assertEqual(repositories.ensure_work_type_codes_for_project(self.project_id), 0)
        after = {row["id"]: dict(row) for row in repositories.list_work_type_codes(self.project_id)}
        for identifier in (legacy_id, numeric_id, formal_id):
            self.assertEqual(after[identifier], before[identifier])
        self.assertNotIn("D301", {row["code"] for row in after.values()})

    def test_renaming_referenced_code_keeps_allocation_reference_intact(self) -> None:
        custom_id = repositories.save_work_type_code(self.project_id, "A-01", "旧名称")
        invoice_id = self.make_invoice("invoice-custom")
        allocation_id = repositories.save_invoice_allocation(invoice_id, custom_id, 100)

        renamed_id = repositories.save_work_type_code(
            self.project_id,
            "B-01",
            "新名称",
            work_type_code_id=custom_id,
        )
        allocation = repositories.list_invoice_allocations(invoice_id)[0]
        self.assertEqual(renamed_id, custom_id)
        self.assertEqual(int(allocation["id"]), allocation_id)
        self.assertEqual((allocation["code"], allocation["name"]), ("B-01", "新名称"))

    def test_allocation_rejects_work_type_from_another_project(self) -> None:
        local_id = repositories.save_work_type_code(self.project_id, "LOCAL", "自工事")
        foreign_id = repositories.save_work_type_code(self.other_project_id, "FOREIGN", "他工事")
        invoice_id = self.make_invoice("invoice-project-boundary")

        with self.assertRaisesRegex(ValueError, "工種コードと請求書の工事"):
            repositories.save_invoice_allocation(invoice_id, foreign_id, 100)

        allocation_id = repositories.save_invoice_allocation(invoice_id, local_id, 100)
        with self.assertRaisesRegex(ValueError, "工種コードと請求書の工事"):
            repositories.save_invoice_allocation(
                invoice_id, foreign_id, 100, allocation_id=allocation_id
            )

    def test_custom_code_is_available_to_recent_suggestions_and_not_rewritten(self) -> None:
        custom_id = repositories.save_work_type_code(self.project_id, "607", "PDF数値コード")
        older_invoice_id = self.make_invoice("invoice-old")
        repositories.save_invoice_allocation(older_invoice_id, custom_id, 100)
        newer_invoice_id = self.make_invoice("invoice-new")

        recent = repositories.list_recent_work_type_codes_for_project_vendor(
            self.project_id, self.vendor_id, newer_invoice_id
        )

        self.assertEqual(recent, ["607"])
        self.assertEqual(repositories.list_invoice_allocations(older_invoice_id)[0]["code"], "607")

    def test_vendor_candidates_keep_custom_and_manual_codes(self) -> None:
        saved = repositories.save_vendor_work_type_candidates(
            self.vendor_id, ["301", "WebD607", "manual-77", "WebD607", ""]
        )
        rows = repositories.list_vendor_work_type_candidates(self.vendor_id)

        self.assertEqual(saved, 3)
        self.assertEqual([row["code"] for row in rows], ["301", "WebD607", "manual-77"])
        self.assertEqual(rows[1]["name"], "WebD607")
        self.assertEqual(rows[2]["name"], "manual-77")

        with self.assertRaises(ValueError):
            repositories.save_vendor_work_type_candidates(self.vendor_id, ["x" * 65])


if __name__ == "__main__":
    unittest.main()
