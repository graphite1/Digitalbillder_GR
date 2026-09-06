from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_manager import db
from invoice_manager.services.work_type_resolution import (
    CanonicalWorkType,
    WorkTypeResolutionError,
    load_confirmed_work_types,
    load_work_type_choices,
    resolve_from_catalog,
    resolve_work_type_code,
)


class WorkTypeResolutionTests(unittest.TestCase):
    def test_three_digits_resolve_to_confirmed_prefix_and_keep_leading_zero(self) -> None:
        catalog = (CanonicalWorkType("D301", "保険料"), CanonicalWorkType("B001", "仮設"))
        self.assertEqual(resolve_from_catalog("301", catalog), catalog[0])
        self.assertEqual(resolve_from_catalog(" ００１ ", catalog), catalog[1])

    def test_exact_official_codes_and_custom_codes_keep_original_text(self) -> None:
        catalog = (CanonicalWorkType("D301", " 名称 "), CanonicalWorkType("custom-long/77", "独自"))
        self.assertEqual(resolve_from_catalog(" D301 ", catalog), catalog[0])
        self.assertEqual(resolve_from_catalog("custom-long/77", catalog), catalog[1])
        with self.assertRaises(WorkTypeResolutionError):
            resolve_from_catalog("d301", catalog)
        with self.assertRaises(WorkTypeResolutionError):
            resolve_from_catalog("Ｄ３０１", catalog)

    def test_multiple_prefixes_do_not_guess(self) -> None:
        catalog = (CanonicalWorkType("D301", "土木"), CanonicalWorkType("B301", "建築"))
        with self.assertRaisesRegex(WorkTypeResolutionError, "複数"):
            resolve_from_catalog("301", catalog)
        self.assertEqual(resolve_from_catalog("B301", catalog), catalog[1])

    def test_legacy_numeric_actual_is_valid_only_if_unique(self) -> None:
        legacy = CanonicalWorkType("301", "旧工種")
        self.assertEqual(resolve_from_catalog("301", (legacy,)), legacy)
        self.assertEqual(resolve_from_catalog("３０１", (legacy,)), legacy)
        with self.assertRaisesRegex(WorkTypeResolutionError, "複数"):
            resolve_from_catalog("301", (legacy, CanonicalWorkType("D301", "新工種")))

    def test_only_letters_followed_by_three_digits_can_be_abbreviated(self) -> None:
        catalog = tuple(CanonicalWorkType(code, code) for code in ("D1301", "D-301", "工301", "D301X"))
        with self.assertRaisesRegex(WorkTypeResolutionError, "確認できません"):
            resolve_from_catalog("301", catalog)
        self.assertEqual(resolve_from_catalog("D1301", catalog), catalog[0])

    def test_unknown_and_missing_codes_are_errors(self) -> None:
        with self.assertRaisesRegex(WorkTypeResolutionError, "入力"):
            resolve_from_catalog(" ", ())
        with self.assertRaisesRegex(WorkTypeResolutionError, "確認できません"):
            resolve_from_catalog("999", (CanonicalWorkType("D301", "保険料"),))
        with self.assertRaisesRegex(WorkTypeResolutionError, "確認できません"):
            resolve_from_catalog("D301", ())


class ConfirmedWorkTypeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetic.db"
        self.db_path_patch = patch.object(db, "DB_PATH", self.path)
        self.data_dir_patch = patch.object(db, "DATA_DIR", Path(self.temp.name))
        self.db_path_patch.start()
        self.data_dir_patch.start()

    def tearDown(self) -> None:
        self.data_dir_patch.stop()
        self.db_path_patch.stop()
        self.temp.cleanup()

    def initialize_fixture(self) -> None:
        with sqlite3.connect(self.path, factory=db.ClosingConnection) as connection:
            connection.executescript("""
                CREATE TABLE projects (id INTEGER PRIMARY KEY, project_code TEXT);
                CREATE TABLE historical_archived_invoices (
                    id INTEGER PRIMARY KEY, project_code TEXT, invoice_date TEXT,
                    source TEXT, status TEXT, is_active INTEGER);
                CREATE TABLE historical_archived_allocations (
                    historical_invoice_id INTEGER, line_number INTEGER,
                    work_type_code TEXT, work_type_name TEXT);
                INSERT INTO projects VALUES (1, 'P1'), (2, 'P2'), (3, 'P3');
            """)

    def add_history(self, identifier: int, project: str, date: str, code: str, name: str,
                    *, active: int = 1, status: str = "archived", source: str = "digital_billder") -> None:
        with sqlite3.connect(self.path, factory=db.ClosingConnection) as connection:
            connection.execute("INSERT INTO historical_archived_invoices VALUES (?, ?, ?, ?, ?, ?)",
                               (identifier, project, date, source, status, active))
            connection.execute("INSERT INTO historical_archived_allocations VALUES (?, 1, ?, ?)",
                               (identifier, code, name))

    def test_missing_database_does_not_create_it(self) -> None:
        self.assertEqual(load_confirmed_work_types(1), ())
        self.assertEqual(load_work_type_choices(1), ())
        self.assertFalse(self.path.exists())

    def test_missing_history_tables_do_not_change_schema(self) -> None:
        with sqlite3.connect(self.path, factory=db.ClosingConnection) as connection:
            connection.execute("CREATE TABLE projects (id INTEGER, project_code TEXT)")
        before = self.path.read_bytes()
        self.assertEqual(load_confirmed_work_types(1), ())
        self.assertEqual(self.path.read_bytes(), before)

    def test_catalog_is_project_scoped_and_uses_latest_active_archived_name(self) -> None:
        self.initialize_fixture()
        self.add_history(1, "P1", "2026-08-10", "D301", "新名称")
        self.add_history(2, "P1", "2026-07-10", "D301", "旧名称")
        self.add_history(3, "P1", "2026-09-10", "D301", "無効", active=0)
        self.add_history(4, "P1", "2026-09-10", "D301", "未保管", status="draft")
        self.add_history(5, "P1", "2026-09-10", "D301", "別ソース", source="other")
        self.add_history(6, "P2", "2026-09-10", "B301", "別工事")
        self.add_history(7, "P1", "2026-08-10", "D001", " 名称原文 ")
        before = self.path.read_bytes()
        self.assertEqual(load_confirmed_work_types(1), (
            CanonicalWorkType("D001", " 名称原文 "), CanonicalWorkType("D301", "新名称")))
        self.assertEqual(resolve_work_type_code(1, "301"), CanonicalWorkType("D301", "新名称"))
        self.assertEqual(resolve_work_type_code(2, "301"), CanonicalWorkType("B301", "別工事"))
        self.assertEqual(load_confirmed_work_types(3), ())
        self.assertEqual(load_confirmed_work_types(999), ())
        with self.assertRaises(WorkTypeResolutionError):
            resolve_work_type_code(3, "301")
        self.assertEqual(self.path.read_bytes(), before)

    def add_masters(self, rows: tuple[tuple[int, str, str], ...]) -> None:
        with sqlite3.connect(self.path, factory=db.ClosingConnection) as connection:
            connection.execute("CREATE TABLE work_type_codes (project_id INTEGER, code TEXT, name TEXT)")
            connection.executemany("INSERT INTO work_type_codes VALUES (?, ?, ?)", rows)

    def test_named_numeric_or_d_master_uses_basic_rule_without_history(self) -> None:
        self.initialize_fixture()
        self.add_masters(((1, "301", "保険料"), (1, "D001", "仮設"), (2, "302", "別工事")))
        before = self.path.read_bytes()
        self.assertEqual(resolve_work_type_code(1, "301"), CanonicalWorkType("D301", "保険料", False))
        self.assertEqual(resolve_work_type_code(1, "００１"), CanonicalWorkType("D001", "仮設", False))
        for code in ("302", "999"):
            with self.assertRaisesRegex(WorkTypeResolutionError, "工種コードマスタ"):
                resolve_work_type_code(1, code)
        self.assertEqual(self.path.read_bytes(), before)

    def test_actual_prefix_wins_and_ambiguity_is_not_hidden_by_basic_rule(self) -> None:
        self.initialize_fixture()
        self.add_masters(((1, "301", "旧保険料"), (1, "D301", "基本保険料"), (1, "302", "給与")))
        self.add_history(1, "P1", "2026-08-20", "B301", "確認保険料")
        self.add_history(2, "P1", "2026-08-20", "D302", "土木給与")
        self.add_history(3, "P1", "2026-08-20", "B302", "建築給与")
        self.assertEqual(resolve_work_type_code(1, "301"), CanonicalWorkType("B301", "確認保険料"))
        self.assertNotIn("D301", {item.code for item in load_work_type_choices(1)})
        with self.assertRaisesRegex(WorkTypeResolutionError, "複数"):
            resolve_work_type_code(1, "302")

    def test_existing_d_master_name_wins_locally_without_changing_either_row(self) -> None:
        self.initialize_fixture()
        self.add_masters(((1, "301", "数字名称"), (1, "D301", "D手入力名称"), (1, "999", " ")))
        before = self.path.read_bytes()
        self.assertEqual(resolve_work_type_code(1, "301"), CanonicalWorkType("D301", "D手入力名称", False))
        self.assertEqual(load_work_type_choices(1), (CanonicalWorkType("D301", "D手入力名称", False),))
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
