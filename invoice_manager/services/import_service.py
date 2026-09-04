from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from invoice_manager.models import ImportResult, PreviewResult
from invoice_manager.repositories import (
    add_audit_log,
    create_import_batch,
    insert_invoice,
    insert_invoice_file,
    list_hidden_project_codes,
    save_import_errors,
)
from invoice_manager.services.csv_reader import read_invoice_csv
from invoice_manager.services.db_backup import create_database_backup
from invoice_manager.services.duplicate_checker import check_duplicates
from invoice_manager.services.file_storage import store_pdf_from_zip
from invoice_manager.services.zip_reader import read_zip_index
from invoice_manager.utils.date_utils import billing_month_from_invoice_date
from invoice_manager.utils.file_hash import sha256_file
from invoice_manager.utils.money_utils import tax_excluded_amount


def preview_import(csv_path: Path, zip_path: Path, billing_month: str) -> PreviewResult:
    rows, errors, _encoding = read_invoice_csv(csv_path)
    zip_index = read_zip_index(zip_path)
    duplicate_summary = check_duplicates(rows)
    detected_billing_months = _detect_billing_months(rows)
    hidden_project_codes = list_hidden_project_codes()
    archived_skip_ids = {
        row.external_id
        for row in rows
        if row.project_code in hidden_project_codes and row.external_id in duplicate_summary.new_ids
    }
    duplicate_summary.new_ids.difference_update(archived_skip_ids)

    csv_ids = {row.external_id for row in rows}
    zip_ids = set(zip_index.id_folders)
    matched_ids = csv_ids & zip_ids
    csv_only_ids = csv_ids - zip_ids
    zip_only_ids = zip_ids - csv_ids

    project_totals: dict[str, int] = {}
    vendor_totals: dict[str, int] = {}
    total_amount = 0
    for row in rows:
        if row.external_id in archived_skip_ids:
            continue
        amount_excluded = tax_excluded_amount(row.total_amount)
        total_amount += amount_excluded
        project_totals[row.project_name] = project_totals.get(row.project_name, 0) + amount_excluded
        vendor_totals[row.vendor_name] = vendor_totals.get(row.vendor_name, 0) + amount_excluded
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
    for external_id in sorted(archived_skip_ids):
        warnings.append(f"アーカイブ工事のため取込対象外です: {external_id}")
    if len(detected_billing_months) > 1:
        warnings.append("請求月が複数含まれています。請求日から行単位で自動判定して登録します。")

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
        archived_skip_count=len(archived_skip_ids),
        detected_billing_months=detected_billing_months,
        csv_rows=rows,
        zip_index=zip_index,
        duplicate_summary=duplicate_summary,
        warnings=warnings,
        errors=errors,
        source_signature=_source_signature(csv_path, zip_path),
    )


def execute_import(
    csv_path: Path,
    zip_path: Path,
    billing_month: str,
    memo: str = "",
    prepared_preview: PreviewResult | None = None,
) -> ImportResult:
    if prepared_preview is not None and prepared_preview.source_signature == _source_signature(csv_path, zip_path):
        preview = prepared_preview
    else:
        preview = preview_import(csv_path, zip_path, billing_month)
    create_database_backup("before_import")
    batch_billing_month = preview.detected_billing_months[0] if len(preview.detected_billing_months) == 1 else ""
    import_batch_id = create_import_batch(
        billing_month=batch_billing_month,
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
    hidden_project_codes = list_hidden_project_codes()
    importable_ids = {
        row.external_id
        for row in preview.csv_rows
        if row.external_id in preview.duplicate_summary.new_ids
        and row.project_code not in hidden_project_codes
    }
    with ZipFile(zip_path) as zip_file:
        for row in preview.csv_rows:
            if row.external_id not in importable_ids:
                continue
            row_billing_month = billing_month_from_invoice_date(row.invoice_date)
            invoice_id = insert_invoice(row, row_billing_month, import_batch_id)
            inserted_count += 1
            for item in preview.zip_index.pdf_by_id.get(row.external_id, []):
                stored_path, file_hash, file_size = store_pdf_from_zip(
                    zip_path,
                    item,
                    row_billing_month,
                    zip_file=zip_file,
                )
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


def _detect_billing_months(rows) -> list[str]:
    months = {billing_month_from_invoice_date(row.invoice_date) for row in rows if (row.invoice_date or "").strip()}
    return sorted(month for month in months if month)


def _source_signature(csv_path: Path, zip_path: Path) -> tuple[tuple[str, int, int], tuple[str, int, int]]:
    return (_file_signature(csv_path), _file_signature(zip_path))


def _file_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns
