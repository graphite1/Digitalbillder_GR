from __future__ import annotations

from pathlib import PurePosixPath
from zipfile import ZipFile

from invoice_manager.models import ZipIndex, ZipPdfItem


def read_zip_index(path) -> ZipIndex:
    index = ZipIndex()
    with ZipFile(path) as zip_file:
        for info in zip_file.infolist():
            name = info.filename
            clean = name.rstrip("/")
            if not clean:
                continue
            parts = PurePosixPath(clean).parts
            if info.is_dir():
                if len(parts) >= 2:
                    index.id_folders.add(parts[-1])
                continue
            if len(parts) < 3:
                index.warnings.append(f"想定外のzip内ファイルをスキップ: {name}")
                continue
            external_id = parts[-2]
            index.id_folders.add(external_id)
            file_name = parts[-1]
            if not file_name.lower().endswith(".pdf"):
                index.warnings.append(f"PDF以外のファイルをスキップ: {name}")
                continue
            item = ZipPdfItem(
                external_id=external_id,
                zip_name=name,
                original_file_name=file_name,
                file_type="PDF",
                file_size=info.file_size,
            )
            index.pdf_by_id.setdefault(external_id, []).append(item)
    return index
