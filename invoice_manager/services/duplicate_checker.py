from __future__ import annotations

from invoice_manager.models import DuplicateSummary, InvoiceCsvRow
from invoice_manager.repositories import list_invoice_duplicate_references


def check_duplicates(rows: list[InvoiceCsvRow]) -> DuplicateSummary:
    summary = DuplicateSummary()
    references = list_invoice_duplicate_references()
    by_external_id = {reference["external_id"]: reference for reference in references}
    existing_signatures = {
        (
            reference["project_code"],
            reference["vendor_name"],
            reference["invoice_date"],
            int(reference["total_amount"]),
        )
        for reference in references
    }
    batch_signature_counts: dict[tuple[str, str, str, int], int] = {}
    for row in rows:
        signature = (row.project_code, row.vendor_name, row.invoice_date, row.total_amount)
        batch_signature_counts[signature] = batch_signature_counts.get(signature, 0) + 1
    duplicate_batch_signatures = {
        signature for signature, count in batch_signature_counts.items() if count > 1
    }
    for row in rows:
        existing = by_external_id.get(row.external_id)
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
        signature = (row.project_code, row.vendor_name, row.invoice_date, row.total_amount)
        if signature in existing_signatures or signature in duplicate_batch_signatures:
            summary.duplicate_candidate_ids.add(row.external_id)
        else:
            summary.new_ids.add(row.external_id)
    return summary
