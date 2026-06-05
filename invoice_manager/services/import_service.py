from __future__ import annotations

from pathlib import Path

from invoice_manager.models import ImportResult, PreviewResult
from invoice_manager.repositories import (
    add_audit_log,
    create_import_batch,
    insert_invoice,
    insert_invoice_file,
    save_import_errors,
)
from invoice_manager.services.csv_reader import read_invoice_csv
from invoice_manager.services.duplicate_checker import check_duplicates
from invoice_manager.services.file_storage import store_pdf_from_zip
from invoice_manager.services.zip_reader import read_zip_index
from invoice_manager.utils.date_utils import validate_billing_month
from invoice_manager.utils.file_hash import sha256_file


def preview_import(csv_path: Path, zip_path: Path, billing_month: str) -> PreviewResult:
    billing_month = _normalize_billing_month(billing_month)
    rows, errors, _encoding = read_invoice_csv(csv_path)
    zip_index = read_zip_index(zip_path)
    duplicate_summary = check_duplicates(rows)

    csv_ids = {row.external_id for row in rows}
    zip_ids = set(zip_index.id_folders)
    matched_ids = csv_ids & zip_ids
    csv_only_ids = csv_ids - zip_ids
    zip_only_ids = zip_ids - csv_ids

    project_totals: dict[str, int] = {}
    vendor_totals: dict[str, int] = {}
    total_amount = 0
    for row in rows:
        total_amount += row.total_amount
        project_totals[row.project_name] = project_totals.get(row.project_name, 0) + row.total_amount
        vendor_totals[row.vendor_name] = vendor_totals.get(row.vendor_name, 0) + row.total_amount
    pdf_file_count = sum(len(items) for items in zip_index.pdf_by_id.values())

    warnings = list(zip_index.warnings)
    for external_id in sorted(csv_only_ids):
        warnings.append(f"CSVにはあるがzipにないID: {external_id}")
    for external_id in sorted(zip_only_ids):
        warnings.append(f"zipにはあるがCSVにないID: {external_id}")
    for external_id in sorted(duplicate_summary.update_candidate_ids):
        warnings.append(f"更新候補のため自動上書きしません: {external_id}")
    for external_id in sorted(duplicate_summary.duplicate_candidate_ids):
        warnings.append(f"重複候補のため自動登録しません: {external_id}")

    return PreviewResult(
        csv_count=len(rows),
        zip_id_count=len(zip_ids),
        matched_count=len(matched_ids),
        csv_only_count=len(csv_only_ids),
        zip_only_count=len(zip_only_ids),
        new_count=len(duplicate_summary.new_ids),
        existing_skip_count=len(duplicate_summary.existing_skip_ids),
        update_candidate_count=len(duplicate_summary.update_candidate_ids),
        duplicate_candidate_count=len(duplicate_summary.duplicate_candidate_ids),
        error_count=len(errors),
        total_amount=total_amount,
        pdf_file_count=pdf_file_count,
        project_totals=project_totals,
        vendor_totals=vendor_totals,
        csv_rows=rows,
        zip_index=zip_index,
        duplicate_summary=duplicate_summary,
        warnings=warnings,
        errors=errors,
    )


def execute_import(csv_path: Path, zip_path: Path, billing_month: str, memo: str = "") -> ImportResult:
    billing_month = _normalize_billing_month(billing_month)
    preview = preview_import(csv_path, zip_path, billing_month)
    import_batch_id = create_import_batch(
        billing_month=billing_month,
        csv_path=csv_path,
        zip_path=zip_path,
        csv_hash=sha256_file(csv_path),
        zip_hash=sha256_file(zip_path),
        memo=memo,
    )
    save_import_errors(import_batch_id, preview.errors)
    add_audit_log("CSV取込", "import_batches", import_batch_id, f"{csv_path.name}: {preview.csv_count}件")
    add_audit_log("zip取込", "import_batches", import_batch_id, f"{zip_path.name}: PDF {preview.pdf_file_count}件")
    for external_id in sorted(preview.duplicate_summary.existing_skip_ids):
        add_audit_log("重複スキップ", "import_batches", import_batch_id, external_id)
    for external_id in sorted(preview.duplicate_summary.update_candidate_ids):
        add_audit_log("更新候補検出", "import_batches", import_batch_id, external_id)

    inserted_count = 0
    file_count = 0
    importable_ids = preview.duplicate_summary.new_ids
    for row in preview.csv_rows:
        if row.external_id not in importable_ids:
            continue
        invoice_id = insert_invoice(row, billing_month, import_batch_id)
        inserted_count += 1
        for item in preview.zip_index.pdf_by_id.get(row.external_id, []):
            stored_path, file_hash, file_size = store_pdf_from_zip(zip_path, item, billing_month)
            inserted = insert_invoice_file(
                invoice_id=invoice_id,
                original_file_name=item.original_file_name,
                stored_file_path=stored_path,
                file_type=item.file_type,
                file_hash=file_hash,
                file_size=file_size,
            )
            if inserted:
                file_count += 1
                add_audit_log("PDF保存", "invoices", invoice_id, str(stored_path))

    return ImportResult(
        preview=preview,
        import_batch_id=import_batch_id,
        inserted_count=inserted_count,
        file_count=file_count,
    )


def _normalize_billing_month(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return validate_billing_month(text)
