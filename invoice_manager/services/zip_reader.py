from __future__ import annotations

from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from invoice_manager.models import ZipIndex, ZipPdfItem
from invoice_manager.utils.file_safety import is_safe_path_part


MAX_ZIP_SIZE_BYTES = 500 * 1024 * 1024
MAX_PDF_SIZE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_PDF_SIZE_BYTES = 1000 * 1024 * 1024
MAX_PDF_COUNT = 1000


def read_zip_index(path) -> ZipIndex:
    path = Path(path)
    index = ZipIndex()
    if path.stat().st_size > MAX_ZIP_SIZE_BYTES:
        raise ValueError("zipファイルが大きすぎます。500MB以下のzipを指定してください。")
    total_pdf_size = 0
    pdf_count = 0
    with ZipFile(path) as zip_file:
        for info in zip_file.infolist():
            name = info.filename
            clean = name.rstrip("/")
            if not clean:
                continue
            parts = PurePosixPath(clean).parts
            if info.is_dir():
                if len(parts) >= 2 and is_safe_path_part(parts[-1]):
                    index.id_folders.add(parts[-1])
                continue
            if len(parts) < 3:
                index.warnings.append(f"想定外のzip内ファイルをスキップ: {name}")
                continue
            external_id = parts[-2]
            if not is_safe_path_part(external_id):
                index.warnings.append(f"安全でないIDフォルダをスキップ: {name}")
                continue
            index.id_folders.add(external_id)
            file_name = parts[-1]
            if not is_safe_path_part(file_name):
                index.warnings.append(f"安全でないファイル名をスキップ: {name}")
                continue
            if not file_name.lower().endswith(".pdf"):
                index.warnings.append(f"PDF以外のファイルをスキップ: {name}")
                continue
            if info.file_size > MAX_PDF_SIZE_BYTES:
                raise ValueError("zip内のPDFが大きすぎます。1ファイル100MB以下にしてください。")
            pdf_count += 1
            total_pdf_size += info.file_size
            if pdf_count > MAX_PDF_COUNT:
                raise ValueError("zip内のPDF数が多すぎます。1000件以下にしてください。")
            if total_pdf_size > MAX_TOTAL_PDF_SIZE_BYTES:
                raise ValueError("zip内PDFの合計サイズが大きすぎます。合計1GB以下にしてください。")
            item = ZipPdfItem(
                external_id=external_id,
                zip_name=name,
                original_file_name=file_name,
                file_type="PDF",
                file_size=info.file_size,
            )
            index.pdf_by_id.setdefault(external_id, []).append(item)
    return index
