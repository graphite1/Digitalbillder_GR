from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImportErrorItem:
    row_number: int | None
    error_type: str
    message: str
    raw_data: str = ""


@dataclass(frozen=True)
class InvoiceCsvRow:
    row_number: int
    external_id: str
    project_name: str
    project_code: str
    vendor_name: str
    last_name: str
    first_name: str
    email: str
    phone: str
    invoice_date: str
    total_amount: int
    raw_data: dict[str, str]


@dataclass(frozen=True)
class ZipPdfItem:
    external_id: str
    zip_name: str
    original_file_name: str
    file_type: str
    file_size: int


@dataclass
class ZipIndex:
    id_folders: set[str] = field(default_factory=set)
    pdf_by_id: dict[str, list[ZipPdfItem]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DuplicateSummary:
    new_ids: set[str] = field(default_factory=set)
    existing_skip_ids: set[str] = field(default_factory=set)
    update_candidate_ids: set[str] = field(default_factory=set)
    duplicate_candidate_ids: set[str] = field(default_factory=set)


@dataclass
class PreviewResult:
    csv_count: int
    zip_id_count: int
    matched_count: int
    csv_only_count: int
    zip_only_count: int
    new_count: int
    existing_skip_count: int
    update_candidate_count: int
    duplicate_candidate_count: int
    error_count: int
    total_amount: int
    pdf_file_count: int
    project_totals: dict[str, int]
    vendor_totals: dict[str, int]
    detected_billing_months: list[str] = field(default_factory=list)
    csv_rows: list[InvoiceCsvRow] = field(default_factory=list)
    zip_index: ZipIndex = field(default_factory=ZipIndex)
    duplicate_summary: DuplicateSummary = field(default_factory=DuplicateSummary)
    warnings: list[str] = field(default_factory=list)
    errors: list[ImportErrorItem] = field(default_factory=list)


@dataclass(frozen=True)
class ImportResult:
    preview: PreviewResult
    import_batch_id: int
    inserted_count: int
    file_count: int
