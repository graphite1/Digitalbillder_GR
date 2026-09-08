from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from invoice_manager import db
from invoice_manager.services.actual_ledger import (
    list_actual_billing_months,
    list_monthly_actual_work_type_summary,
)
from invoice_manager.services.budget_consumption import build_budget_consumption
from invoice_manager.services.historical_costs import (
    ArchivedAllocationSnapshot,
    ArchivedInvoiceSnapshot,
    replace_active_archived_snapshots,
)
from invoice_manager.services.project_budget import BudgetRowInput, save_project_budget


class BudgetConsumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir, self.original_db_path = db.DATA_DIR, db.DB_PATH
        db.DATA_DIR = Path(self.temp_dir.name) / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        with db.get_connection() as connection:
            self.project_id = int(connection.execute(
                "INSERT INTO projects (project_code, project_name, created_at, updated_at) VALUES ('P-BUDGET', '試験工事', '2026', '2026')"
            ).lastrowid)
            self.vendor_id = int(connection.execute(
                "INSERT INTO vendors (vendor_name, created_at, updated_at) VALUES ('試験会社', '2026', '2026')"
            ).lastrowid)
            self.code_301 = int(connection.execute(
                "INSERT INTO work_type_codes (project_id, code, name, created_at, updated_at) VALUES (?, 'D301', '仮設工', '2026', '2026')",
                (self.project_id,),
            ).lastrowid)
            self.code_513 = int(connection.execute(
                "INSERT INTO work_type_codes (project_id, code, name, created_at, updated_at) VALUES (?, 'D513', '舗装工', '2026', '2026')",
                (self.project_id,),
            ).lastrowid)
        save_project_budget(self.project_id, [
            BudgetRowInput('301', '仮設工', 1_000, include_in_total=True, actual_work_type_code='D301'),
            BudgetRowInput('513', '舗装工', 0, include_in_total=True, actual_work_type_code='D513'),
        ], confirmed=True)

    def tearDown(self) -> None:
        db.DATA_DIR, db.DB_PATH = self.original_data_dir, self.original_db_path
        self.temp_dir.cleanup()

    def invoice(self, external_id: str, net: int, month: str = '2026-09') -> int:
        with db.get_connection() as connection:
            return int(connection.execute(
                """INSERT INTO invoices (external_id, project_id, vendor_id, invoice_date, billing_month,
                   total_amount, total_amount_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, '2026-09-10', ?, ?, ?, '2026', '2026')""",
                (external_id, self.project_id, self.vendor_id, month, net, net),
            ).lastrowid)

    def allocation(self, invoice_id: int, code_id: int, net: int) -> None:
        with db.get_connection() as connection:
            connection.execute(
                """INSERT INTO invoice_allocations (invoice_id, work_type_code_id, amount, amount_excluded,
                   tax_rate, created_at, updated_at) VALUES (?, ?, ?, ?, '10', '2026', '2026')""",
                (invoice_id, code_id, net, net),
            )

    def test_counts_only_reconciled_local_allocations_and_keeps_zero_budget_rows(self) -> None:
        confirmed = self.invoice('confirmed', 300)
        self.allocation(confirmed, self.code_301, 300)
        partial = self.invoice('partial', 200)
        self.allocation(partial, self.code_301, 100)
        missing = self.invoice('missing', 100, '2026-10')

        summary = build_budget_consumption(self.project_id)
        september = build_budget_consumption(self.project_id, billing_month='2026-09')

        self.assertEqual([(row.work_type_code, row.official_work_type_code, row.actual_net, row.remaining_net) for row in summary.rows],
                         [('301', 'D301', 300, 700), ('513', 'D513', 0, 0)])
        self.assertEqual([(row.external_id, row.reason) for row in summary.unconfirmed_invoices], [
            ('partial', '振分税抜合計が請求書税抜額と一致しません'),
            ('missing', '工種振分が未入力です'),
        ])
        self.assertEqual(len(september.unconfirmed_invoices), 1)
        self.assertEqual(september.rows[0].unconfirmed_invoice_count, 1)

    def test_archived_actuals_are_counted_and_take_priority_over_same_local_invoice_id(self) -> None:
        duplicate = self.invoice('same-id', 300)
        self.allocation(duplicate, self.code_301, 300)
        local = self.invoice('local-only', 200)
        self.allocation(local, self.code_301, 200)
        replace_active_archived_snapshots([
            ArchivedInvoiceSnapshot(
                'same-id', 'P-BUDGET', '試験工事', '試験会社', '2026-09-10', 550,
                'archived', (ArchivedAllocationSnapshot('D301', '仮設工', 500, '10', 50, 550),),
            ),
            ArchivedInvoiceSnapshot(
                'archive-only', 'P-BUDGET', '試験工事', '試験会社', '2026-09-09', 110,
                'archived', (ArchivedAllocationSnapshot('D301', '仮設工', 100, '10', 10, 110),),
            ),
        ], project_code='P-BUDGET')

        september = build_budget_consumption(self.project_id, billing_month='2026-09')
        october = build_budget_consumption(self.project_id, billing_month='2026-10')

        self.assertEqual(september.rows[0].actual_net, 300)  # local-only 200 + archive-only 100
        self.assertEqual(october.rows[0].actual_net, 500)  # archived same-id; local 300 is excluded
        self.assertEqual(october.unconfirmed_invoices, ())

    def test_monthly_actual_summary_uses_the_same_archive_priority(self) -> None:
        local = self.invoice('same-id', 200)
        self.allocation(local, self.code_301, 200)
        replace_active_archived_snapshots([
            ArchivedInvoiceSnapshot(
                'same-id', 'P-BUDGET', '試験工事', '試験会社', '2026-09-10', 330,
                'archived', (ArchivedAllocationSnapshot('D301', '仮設工', 300, '10', 30, 330),),
            ),
        ], project_code='P-BUDGET')

        self.assertEqual(list_actual_billing_months(self.project_id), ('2026-10',))
        rows = list_monthly_actual_work_type_summary(self.project_id, '2026-10')
        self.assertEqual(
            [(row.work_type_code, row.invoice_count, row.net_amount) for row in rows],
            [('D301', 1, 300)],
        )
