from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from invoice_manager import db
from invoice_manager.services import historical_costs as history


def allocation(
    code: str,
    net: int,
    *,
    name: str | None = None,
    tax_rate: str | int = "10",
    tax: int | None = None,
) -> history.ArchivedAllocationSnapshot:
    resolved_tax = net // 10 if tax is None else tax
    if str(tax_rate) == "exempt":
        resolved_tax = 0
    return history.ArchivedAllocationSnapshot(
        work_type_code=code,
        work_type_name=name or f"工種{code}",
        net_amount=net,
        tax_rate=tax_rate,
        tax_amount=resolved_tax,
        gross_amount=net + resolved_tax,
    )


def snapshot(
    external_id: str,
    *lines: history.ArchivedAllocationSnapshot,
    project_code: str = "P001",
    project_name: str = "第一工事",
    vendor_name: str = "山田建設",
    invoice_date: str = "2026-08-31",
) -> history.ArchivedInvoiceSnapshot:
    return history.ArchivedInvoiceSnapshot(
        external_id=external_id,
        project_code=project_code,
        project_name=project_name,
        vendor_name=vendor_name,
        invoice_date=invoice_date,
        gross_invoice_total=sum(line.gross_amount for line in lines),
        status="archived",
        allocations=list(lines),
    )


class HistoricalCostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir, self.original_db_path = db.DATA_DIR, db.DB_PATH
        db.DATA_DIR = Path(self.temp.name)
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()

    def tearDown(self) -> None:
        db.DATA_DIR, db.DB_PATH = self.original_data_dir, self.original_db_path
        self.temp.cleanup()

    def test_snapshot_normalizes_and_rejects_unverified_values(self) -> None:
        line = history.ArchivedAllocationSnapshot(" 01 ", " 仮設 ", 100, 10, 10, 110)
        item = history.ArchivedInvoiceSnapshot(
            " web-1 ", " P1 ", " 工事 ", " 取引先 ", "2026-08-01", 110, "ARCHIVED", [line]
        )
        self.assertEqual((line.work_type_code, line.work_type_name, line.tax_rate), ("01", "仮設", "10"))
        self.assertEqual((item.external_id, item.status, item.allocations), ("web-1", "archived", (line,)))

        with self.assertRaisesRegex(ValueError, "保管済み"):
            history.ArchivedInvoiceSnapshot("x", "P", "工事", "取引先", "2026-08-01", 0, "paid", [line])
        with self.assertRaisesRegex(ValueError, "振分行"):
            history.ArchivedInvoiceSnapshot("x", "P", "工事", "取引先", "2026-08-01", 0, "archived", [])
        with self.assertRaisesRegex(ValueError, "税率"):
            history.ArchivedAllocationSnapshot("01", "仮設", 100, "5", 5, 105)
        with self.assertRaisesRegex(ValueError, "一致"):
            history.ArchivedAllocationSnapshot("01", "仮設", 100, "10", 10, 109)
        with self.assertRaisesRegex(ValueError, "非課税"):
            history.ArchivedAllocationSnapshot("01", "仮設", 100, "exempt", 1, 101)

    def test_upsert_is_idempotent_replaces_snapshot_and_keeps_regular_invoice(self) -> None:
        project_id, vendor_id = self._insert_local_project_vendor()
        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO invoices (
                    external_id, project_id, vendor_id, invoice_date, billing_month,
                    total_amount, total_amount_excluded, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("same-id", project_id, vendor_id, "2026-08-31", "2026-08", 110, 100, "now", "now"),
            )

        first = history.upsert_archived_invoice(snapshot("same-id", allocation("01", 100)))
        second = history.upsert_archived_invoice(
            snapshot("same-id", allocation("02", 200), project_name="第一工事（更新）")
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.invoice_id, second.invoice_id)
        with db.get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM historical_archived_invoices").fetchone()[0], 1)
            row = connection.execute(
                """
                SELECT i.project_name, a.work_type_code, a.net_amount
                FROM historical_archived_invoices AS i
                JOIN historical_archived_allocations AS a ON a.historical_invoice_id = i.id
                """
            ).fetchone()
        self.assertEqual(tuple(row), ("第一工事（更新）", "02", 200))

    def test_upsert_rolls_back_header_and_lines_together(self) -> None:
        history.upsert_archived_invoice(snapshot("atomic", allocation("01", 100)))
        with db.get_connection() as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_failed_history_line
                BEFORE INSERT ON historical_archived_allocations
                WHEN NEW.work_type_code = 'FAIL'
                BEGIN
                    SELECT RAISE(ABORT, 'forced history line failure');
                END
                """
            )

        with self.assertRaisesRegex(Exception, "forced history line failure"):
            history.upsert_archived_invoice(
                snapshot("atomic", allocation("FAIL", 500), project_name="更新されない工事")
            )
        with db.get_connection() as connection:
            row = connection.execute(
                """
                SELECT i.project_name, a.work_type_code, a.net_amount
                FROM historical_archived_invoices AS i
                JOIN historical_archived_allocations AS a ON a.historical_invoice_id = i.id
                WHERE i.external_id = 'atomic'
                """
            ).fetchone()
        self.assertEqual(tuple(row), ("第一工事", "01", 100))

    def test_complete_scan_deactivates_missing_history_without_deleting_it(self) -> None:
        first = snapshot("first", allocation("01", 100))
        second = snapshot("second", allocation("02", 200))
        initial = history.replace_active_archived_snapshots([first, second])
        refreshed = history.replace_active_archived_snapshots([second])

        self.assertEqual((initial.active_invoice_count, initial.inserted_invoice_count), (2, 2))
        self.assertEqual((refreshed.active_invoice_count, refreshed.deactivated_invoice_count), (1, 1))
        self.assertEqual([row.work_type_code for row in history.list_actual_costs(project_code="P001")], ["02"])
        self.assertEqual(
            [row.work_type_code for row in history.list_historical_work_type_suggestions("山田建設")],
            ["02"],
        )
        status = history.get_historical_sync_status()
        self.assertEqual(status.active_invoice_count, 1)
        self.assertIsNotNone(status.last_successful_refresh)
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT external_id, is_active FROM historical_archived_invoices ORDER BY external_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("first", 0), ("second", 1)])

    def test_active_cache_restores_complete_verified_snapshots_in_line_order(self) -> None:
        expected = snapshot(
            "cached",
            allocation("任意-A", 1_000, name="架空10%", tax_rate="10", tax=100),
            allocation("任意-A", 2_000, name="架空8%", tax_rate="8", tax=160),
            allocation("X/非課税", 300, name="架空非課税", tax_rate="exempt", tax=0),
            project_code="PROJECT-FREE",
            project_name="コード体系可変工事",
            vendor_name="架空取引先",
            invoice_date="2026-09-05",
        )
        history.replace_active_archived_snapshots([expected])

        cached = history.load_active_archived_snapshots()

        self.assertEqual(cached, {"cached": expected})
        self.assertEqual(
            [(line.work_type_code, line.tax_rate, line.tax_amount) for line in cached["cached"].allocations],
            [("任意-A", "10", 100), ("任意-A", "8", 160), ("X/非課税", "exempt", 0)],
        )

    def test_active_cache_excludes_inactive_and_remains_idempotent(self) -> None:
        first = snapshot("first", allocation("01", 100))
        second = snapshot("second", allocation("02", 200))
        history.replace_active_archived_snapshots([first, second])
        history.replace_active_archived_snapshots([second])

        first_load = history.load_active_archived_snapshots()
        second_load = history.load_active_archived_snapshots()
        self.assertEqual(first_load, second_load)
        self.assertEqual(first_load, {"second": second})

        # A formerly inactive invoice is deliberately absent from the incremental cache.
        # Once fetched and upserted again it becomes active without duplicating its lines.
        history.upsert_archived_invoice(first)
        history.upsert_archived_invoice(first)
        reactivated = history.load_active_archived_snapshots()
        self.assertEqual(reactivated, {"first": first, "second": second})
        self.assertEqual(len(reactivated["first"].allocations), 1)

    def test_invalid_complete_scan_keeps_last_successful_generation(self) -> None:
        history.replace_active_archived_snapshots([snapshot("safe", allocation("01", 100))])
        before = history.get_historical_sync_status()
        with self.assertRaisesRegex(ValueError, "重複"):
            history.replace_active_archived_snapshots(
                [snapshot("duplicate", allocation("02", 200)), snapshot("duplicate", allocation("03", 300))]
            )
        after = history.get_historical_sync_status()
        self.assertEqual(after, before)
        self.assertEqual([row.work_type_code for row in history.list_actual_costs(project_code="P001")], ["01"])

    def test_failed_complete_scan_rolls_back_all_snapshots_and_active_flags(self) -> None:
        history.replace_active_archived_snapshots([snapshot("safe", allocation("01", 100))])
        before = history.get_historical_sync_status()
        with db.get_connection() as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_scan_line
                BEFORE INSERT ON historical_archived_allocations
                WHEN NEW.work_type_code = 'FAIL'
                BEGIN
                    SELECT RAISE(ABORT, 'forced scan failure');
                END
                """
            )
        with self.assertRaisesRegex(Exception, "forced scan failure"):
            history.replace_active_archived_snapshots(
                [snapshot("new-good", allocation("02", 200)), snapshot("new-bad", allocation("FAIL", 300))]
            )
        self.assertEqual(history.get_historical_sync_status(), before)
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT external_id, is_active FROM historical_archived_invoices ORDER BY external_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("safe", 1)])

    def test_suggestions_count_invoices_aggregate_repeated_lines_and_filter_project(self) -> None:
        history.upsert_archived_invoice(
            snapshot("one", allocation("01", 100), allocation("01", 200), allocation("02", 900))
        )
        history.upsert_archived_invoice(
            snapshot("two", allocation("01", 50), allocation("03", 800), invoice_date="2026-09-01")
        )
        history.upsert_archived_invoice(
            snapshot(
                "other-project",
                allocation("03", 2_000),
                project_code="P002",
                project_name="第二工事",
            )
        )
        history.upsert_archived_invoice(
            snapshot("other-vendor", allocation("99", 9_999), vendor_name="別会社")
        )

        suggestions = history.list_historical_work_type_suggestions("山田建設", project_code="P001")
        self.assertEqual([row.work_type_code for row in suggestions], ["01", "02", "03"])
        self.assertEqual(
            (
                suggestions[0].invoice_count,
                suggestions[0].allocation_line_count,
                suggestions[0].net_amount,
            ),
            (2, 3, 350),
        )
        # 02 and 03 have equal frequency; their spend decides the order.
        self.assertGreater(suggestions[1].net_amount, suggestions[2].net_amount)
        all_projects = history.list_historical_work_type_suggestions("山田建設")
        self.assertEqual([row.work_type_code for row in all_projects], ["03", "01", "02"])

    def test_history_does_not_modify_manual_vendor_candidates(self) -> None:
        _, vendor_id = self._insert_local_project_vendor()
        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO vendor_work_type_candidates
                    (vendor_id, code, sort_order, created_at, updated_at)
                VALUES (?, '77', 1, 'now', 'now')
                """,
                (vendor_id,),
            )
        history.upsert_archived_invoice(snapshot("learn", allocation("01", 100)))
        history.list_historical_work_type_suggestions("山田建設")
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT code, sort_order FROM vendor_work_type_candidates WHERE vendor_id = ?",
                (vendor_id,),
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("77", 1)])

    def test_actual_reporting_aggregates_by_project_vendor_work_type(self) -> None:
        history.upsert_archived_invoice(
            snapshot("one", allocation("01", 100), allocation("01", 200))
        )
        history.upsert_archived_invoice(snapshot("two", allocation("01", 50)))
        history.upsert_archived_invoice(snapshot("three", allocation("02", 800), vendor_name="別会社"))

        rows = history.list_actual_costs(project_code="P001", vendor_name="山田建設")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.source, history.ACTUAL_SOURCE)
        self.assertEqual(
            (row.work_type_code, row.invoice_count, row.allocation_line_count, row.net_amount, row.gross_amount),
            ("01", 2, 3, 350, 385),
        )

    def test_actual_reporting_accepts_local_project_id_without_planned_double_count(self) -> None:
        project_id, vendor_id = self._insert_local_project_vendor()
        self._insert_local_allocation(project_id, vendor_id, external_id="local-only", net=900)
        history.upsert_archived_invoice(snapshot("web", allocation("01", 100)))

        actual_rows = history.list_actual_costs(project_id)
        self.assertEqual([(row.source, row.net_amount) for row in actual_rows], [(history.ACTUAL_SOURCE, 100)])
        combined = history.list_costs(project_code="P001", include_planned=True)
        self.assertEqual({row.source for row in combined}, {history.ACTUAL_SOURCE, history.PLANNED_SOURCE})
        self.assertEqual(sum(row.net_amount for row in combined if row.source == history.PLANNED_SOURCE), 900)

    def test_empty_history_is_represented_as_missing_rows(self) -> None:
        self.assertFalse(history.has_historical_costs())
        self.assertEqual(history.list_actual_costs(project_code="P001"), [])
        options = history.list_historical_cost_filter_options()
        self.assertEqual((options.projects, options.vendors, options.work_types), ((), (), ()))

    def _insert_local_project_vendor(self) -> tuple[int, int]:
        with db.get_connection() as connection:
            project_id = connection.execute(
                """
                INSERT INTO projects (project_code, project_name, created_at, updated_at)
                VALUES ('P001', '第一工事', 'now', 'now')
                """
            ).lastrowid
            vendor_id = connection.execute(
                """
                INSERT INTO vendors (vendor_name, created_at, updated_at)
                VALUES ('山田建設', 'now', 'now')
                """
            ).lastrowid
        return int(project_id), int(vendor_id)

    def _insert_local_allocation(self, project_id: int, vendor_id: int, external_id: str, net: int) -> None:
        with db.get_connection() as connection:
            invoice_id = connection.execute(
                """
                INSERT INTO invoices (
                    external_id, project_id, vendor_id, invoice_date, billing_month,
                    total_amount, total_amount_excluded, created_at, updated_at
                ) VALUES (?, ?, ?, '2026-08-31', '2026-08', ?, ?, 'now', 'now')
                """,
                (external_id, project_id, vendor_id, net * 11 // 10, net),
            ).lastrowid
            work_type_id = connection.execute(
                """
                INSERT INTO work_type_codes (
                    project_id, code, name, sort_order, is_active, created_at, updated_at
                ) VALUES (?, '01', '工種01', 1, 1, 'now', 'now')
                """,
                (project_id,),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO invoice_allocations (
                    invoice_id, work_type_code_id, amount, amount_excluded, tax_rate,
                    sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '10', 1, 'now', 'now')
                """,
                (invoice_id, work_type_id, net * 11 // 10, net),
            )


if __name__ == "__main__":
    unittest.main()
