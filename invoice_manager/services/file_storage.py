from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from invoice_manager.db import DATA_DIR
from invoice_manager.models import ZipPdfItem
from invoice_manager.services.zip_reader import MAX_PDF_SIZE_BYTES
from invoice_manager.utils.file_safety import sanitize_path_part
from invoice_manager.utils.file_hash import sha256_bytes


def store_pdf_from_zip(zip_path: Path, item: ZipPdfItem, billing_month: str) -> tuple[Path, str, int]:
    if item.file_size > MAX_PDF_SIZE_BYTES:
        raise ValueError("PDFが大きすぎます。1ファイル100MB以下にしてください。")
    external_id = sanitize_path_part(item.external_id, "unknown_id")
    file_name = sanitize_path_part(item.original_file_name, "document.pdf")
    if not file_name.lower().endswith(".pdf"):
        file_name = f"{Path(file_name).stem or 'document'}.pdf"
    if billing_month.strip():
        year, month = billing_month.split("-", 1)
        target_dir = DATA_DIR / "originals" / year / month / external_id
    else:
        target_dir = DATA_DIR / "originals" / "unset" / external_id
    target_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as zip_file:
        data = zip_file.read(item.zip_name)
    file_hash = sha256_bytes(data)
    target_path = _unique_path(target_dir / file_name, file_hash)
    if not target_path.exists():
        target_path.write_bytes(data)
    return target_path.resolve(), file_hash, len(data)


def _unique_path(path: Path, file_hash: str) -> Path:
    if not path.exists():
        return path
    if sha256_bytes(path.read_bytes()) == file_hash:
        return path
    return path.with_name(f"{path.stem}_{file_hash[:8]}{path.suffix}")
