"""Tax-excluded budget consumption from common confirmed actuals."""

from __future__ import annotations

from dataclasses import dataclass

from invoice_manager.services.actual_ledger import build_project_actual_ledger
from invoice_manager.services.project_budget import get_project_budget
from invoice_manager.services.work_type_resolution import normalize_budget_work_type_code


@dataclass(frozen=True, slots=True)
class BudgetConsumptionRow:
    work_type_code: str
    official_work_type_code: str | None
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
    """Summarize confirmed actuals; archived records take priority by invoice ID."""
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
    ledger = build_project_actual_ledger(project_id, billing_month=month)
    for invoice in ledger.unconfirmed_local_invoices:
        unconfirmed.append(UnconfirmedBudgetInvoice(
            invoice.invoice_id, invoice.external_id, invoice.billing_month,
            invoice.invoice_net, invoice.allocation_net, invoice.reason,
        ))
        for code in invoice.allocation_codes:
            budget_row = by_code.get(normalize_budget_work_type_code(code))
            if budget_row is not None:
                unconfirmed_counts[budget_row.work_type_code] += 1
    for allocation in ledger.allocations:
        budget_row = by_code.get(normalize_budget_work_type_code(allocation.work_type_code))
        if budget_row is not None:
            totals[budget_row.work_type_code] += allocation.net_amount
    rows = tuple(
        BudgetConsumptionRow(
            row.work_type_code, row.actual_work_type_code, row.work_type_name, row.budget_net,
            totals[row.work_type_code], row.budget_net - totals[row.work_type_code],
            None if row.budget_net == 0 else totals[row.work_type_code] / row.budget_net,
            unconfirmed_counts[row.work_type_code],
        )
        for row in budget.rows
    )
    return BudgetConsumptionSummary(rows, tuple(unconfirmed))
