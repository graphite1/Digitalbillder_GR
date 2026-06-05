from __future__ import annotations

from invoice_manager.models import DuplicateSummary, InvoiceCsvRow
from invoice_manager.repositories import find_duplicate_candidate, find_invoice_by_external_id


def check_duplicates(rows: list[InvoiceCsvRow]) -> DuplicateSummary:
    summary = DuplicateSummary()
    for row in rows:
        existing = find_invoice_by_external_id(row.external_id)
        if existing:
            same = (
                existing["invoice_date"] == row.invoice_date
                and int(existing["total_amount"]) == row.total_amount
                and existing["project_code"] == row.project_code
                and existing["vendor_name"] == row.vendor_name
            )
            if same:
                summary.existing_skip_ids.add(row.external_id)
            else:
                summary.update_candidate_ids.add(row.external_id)
            continue
        if find_duplicate_candidate(row):
            summary.duplicate_candidate_ids.add(row.external_id)
        else:
            summary.new_ids.add(row.external_id)
    return summary
