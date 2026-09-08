"""Common, tax-excluded actuals used by budget and monthly summaries.

Archived Digital Billder records are the authoritative source for an invoice ID.
Local allocations supplement only invoice IDs that do not exist in the active
archive cache, so the same invoice can never be counted twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from invoice_manager import db
from invoice_manager.services.historical_costs import load_active_archived_snapshots
from invoice_manager.utils.date_utils import billing_month_from_invoice_date


@dataclass(frozen=True, slots=True)
class ActualAllocation:
    external_id: str
    billing_month: str
    work_type_code: str
    work_type_name: str
    net_amount: int
    gross_amount: int
    source: str


@dataclass(frozen=True, slots=True)
class UnconfirmedLocalInvoice:
    invoice_id: int
    external_id: str
    billing_month: str
    invoice_net: int | None
    allocation_net: int
    allocation_codes: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ActualLedger:
    allocations: tuple[ActualAllocation, ...]
    unconfirmed_local_invoices: tuple[UnconfirmedLocalInvoice, ...]


@dataclass(frozen=True, slots=True)
class MonthlyActualWorkTypeSummaryRow:
    work_type_code: str
    work_type_name: str
    invoice_count: int
    allocation_line_count: int
    net_amount: int
    gross_amount: int


def build_project_actual_ledger(project_id: int, *, billing_month: str | None = None) -> ActualLedger:
    """Return confirmed project actuals, preferring active archived invoices by ID."""
    if isinstance(project_id, bool) or not isinstance(project_id, int):
        raise ValueError("project_idは整数で指定してください。")
    month = None if billing_month is None else str(billing_month).strip()
    if month == "":
        raise ValueError("請求月が空です。")
    with db.get_connection() as connection:
        project = connection.execute(
            "SELECT project_code FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if project is None:
        return ActualLedger((), ())
    project_code = str(project["project_code"])
    archived = load_active_archived_snapshots(project_code)
    allocations: list[ActualAllocation] = []
    for snapshot in archived.values():
        snapshot_month = billing_month_from_invoice_date(str(snapshot.invoice_date))
        if month is not None and snapshot_month != month:
            continue
        allocations.extend(
            ActualAllocation(
                snapshot.external_id, snapshot_month, line.work_type_code,
                line.work_type_name, line.net_amount, line.gross_amount,
                "archived",
            )
            for line in snapshot.allocations
        )

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
        unconfirmed: list[UnconfirmedLocalInvoice] = []
        for invoice in invoices:
            external_id = str(invoice["external_id"])
            if external_id in archived:
                continue
            rows = connection.execute(
                """
                SELECT w.code, w.name, a.amount, a.amount_excluded
                FROM invoice_allocations AS a
                JOIN work_type_codes AS w ON w.id = a.work_type_code_id
                WHERE a.invoice_id = ?
                ORDER BY a.sort_order, a.id
                """,
                (int(invoice["id"]),),
            ).fetchall()
            allocation_net = sum(int(row["amount_excluded"] or 0) for row in rows)
            reason = ""
            if invoice["total_amount_excluded"] is None:
                reason = "請求書の税抜額が未入力です"
            elif not rows:
                reason = "工種振分が未入力です"
            elif any(row["amount_excluded"] is None for row in rows):
                reason = "振分の税抜額が未入力です"
            elif allocation_net != int(invoice["total_amount_excluded"]):
                reason = "振分税抜合計が請求書税抜額と一致しません"
            if reason:
                unconfirmed.append(UnconfirmedLocalInvoice(
                    int(invoice["id"]), external_id, str(invoice["billing_month"]),
                    None if invoice["total_amount_excluded"] is None else int(invoice["total_amount_excluded"]),
                    allocation_net, tuple(str(row["code"]) for row in rows), reason,
                ))
                continue
            allocations.extend(
                ActualAllocation(
                    external_id, str(invoice["billing_month"]), str(row["code"]),
                    str(row["name"]), int(row["amount_excluded"]), int(row["amount"] or 0),
                    "local",
                )
                for row in rows
            )
    return ActualLedger(tuple(allocations), tuple(unconfirmed))


def list_actual_billing_months(project_id: int) -> tuple[str, ...]:
    """List billing months represented by the common actual ledger."""
    return tuple(sorted({item.billing_month for item in build_project_actual_ledger(project_id).allocations}, reverse=True))


def list_monthly_actual_work_type_summary(
    project_id: int, billing_month: str,
) -> tuple[MonthlyActualWorkTypeSummaryRow, ...]:
    """Aggregate one project's actuals for one billing month."""
    month = str(billing_month).strip()
    if len(month) != 7 or month[4] != "-" or not month.replace("-", "").isdigit():
        raise ValueError("請求月はYYYY-MM形式で指定してください。")
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for item in build_project_actual_ledger(project_id, billing_month=month).allocations:
        values = grouped.setdefault((item.work_type_code, item.work_type_name), {
            "invoice_ids": set(), "line_count": 0, "net": 0, "gross": 0,
        })
        values["invoice_ids"].add(item.external_id)
        values["line_count"] += 1
        values["net"] += item.net_amount
        values["gross"] += item.gross_amount
    return tuple(
        MonthlyActualWorkTypeSummaryRow(
            code, name, len(values["invoice_ids"]), int(values["line_count"]),
            int(values["net"]), int(values["gross"]),
        )
        for (code, name), values in sorted(grouped.items())
    )
