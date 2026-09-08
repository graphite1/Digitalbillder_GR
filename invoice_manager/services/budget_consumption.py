"""Tax-excluded budget consumption from confirmed local invoice allocations."""

from __future__ import annotations

from dataclasses import dataclass

from invoice_manager import db
from invoice_manager.services.project_budget import get_project_budget
from invoice_manager.services.work_type_resolution import normalize_budget_work_type_code


@dataclass(frozen=True, slots=True)
class BudgetConsumptionRow:
    work_type_code: str
    work_type_name: str
    budget_net: int
    actual_net: int
    remaining_net: int
    utilization_rate: float | None
    unconfirmed_invoice_count: int


@dataclass(frozen=True, slots=True)
class UnconfirmedBudgetInvoice:
    invoice_id: int
    external_id: str
    billing_month: str
    invoice_net: int | None
    allocation_net: int
    reason: str


@dataclass(frozen=True, slots=True)
class BudgetConsumptionSummary:
    rows: tuple[BudgetConsumptionRow, ...]
    unconfirmed_invoices: tuple[UnconfirmedBudgetInvoice, ...]


def build_budget_consumption(
    project_id: int, *, billing_month: str | None = None,
) -> BudgetConsumptionSummary:
    """Summarize only invoices whose saved allocation total matches their net total.

    Archived Web history is intentionally absent from this calculation.  A local
    allocation is counted only after every allocation maps to a registered budget
    work type and the allocation tax-excluded total equals the invoice tax-excluded
    total.
    """
    budget = get_project_budget(project_id)
    if budget is None:
        return BudgetConsumptionSummary((), ())
    month = None if billing_month is None else str(billing_month).strip()
    if month == "":
        raise ValueError("請求月が空です。")
    by_code = {
        normalize_budget_work_type_code(row.actual_work_type_code): row
        for row in budget.rows
        if row.actual_work_type_code
    }
    totals = {row.work_type_code: 0 for row in budget.rows}
    unconfirmed_counts = {row.work_type_code: 0 for row in budget.rows}
    unconfirmed: list[UnconfirmedBudgetInvoice] = []
    with db.get_connection() as connection:
        invoices = connection.execute(
            """
            SELECT id, external_id, billing_month, total_amount_excluded
            FROM invoices
            WHERE project_id = ? AND (? IS NULL OR billing_month = ?)
            ORDER BY billing_month, invoice_date, id
            """,
            (project_id, month, month),
        ).fetchall()
        for invoice in invoices:
            allocations = connection.execute(
                """
                SELECT w.code, a.amount_excluded
                FROM invoice_allocations AS a
                JOIN work_type_codes AS w ON w.id = a.work_type_code_id
                WHERE a.invoice_id = ?
                ORDER BY a.sort_order, a.id
                """,
                (int(invoice["id"]),),
            ).fetchall()
            allocation_net = sum(int(row["amount_excluded"] or 0) for row in allocations)
            reason = ""
            if invoice["total_amount_excluded"] is None:
                reason = "請求書の税抜額が未入力です"
            elif not allocations:
                reason = "工種振分が未入力です"
            elif any(row["amount_excluded"] is None for row in allocations):
                reason = "振分の税抜額が未入力です"
            elif allocation_net != int(invoice["total_amount_excluded"]):
                reason = "振分税抜合計が請求書税抜額と一致しません"
            else:
                unknown = [
                    str(row["code"]) for row in allocations
                    if normalize_budget_work_type_code(str(row["code"])) not in by_code
                ]
                if unknown:
                    reason = "予算原本の公式工種コード未登録です: " + ", ".join(dict.fromkeys(unknown))
            if reason:
                unconfirmed.append(UnconfirmedBudgetInvoice(
                    int(invoice["id"]), str(invoice["external_id"]), str(invoice["billing_month"]),
                    None if invoice["total_amount_excluded"] is None else int(invoice["total_amount_excluded"]),
                    allocation_net, reason,
                ))
                for row in allocations:
                    budget_row = by_code.get(normalize_budget_work_type_code(str(row["code"])))
                    if budget_row is not None:
                        unconfirmed_counts[budget_row.work_type_code] += 1
                continue
            for row in allocations:
                budget_row = by_code[normalize_budget_work_type_code(str(row["code"]))]
                totals[budget_row.work_type_code] += int(row["amount_excluded"])
    rows = tuple(
        BudgetConsumptionRow(
            row.work_type_code, row.work_type_name, row.budget_net,
            totals[row.work_type_code], row.budget_net - totals[row.work_type_code],
            None if row.budget_net == 0 else totals[row.work_type_code] / row.budget_net,
            unconfirmed_counts[row.work_type_code],
        )
        for row in budget.rows
    )
    return BudgetConsumptionSummary(rows, tuple(unconfirmed))
