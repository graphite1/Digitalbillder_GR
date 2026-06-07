from __future__ import annotations

import re
from pathlib import Path

from invoice_manager.db import DATA_DIR


_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_path_part(value: str, default: str = "file") -> str:
    text = _UNSAFE_NAME_CHARS.sub("_", (value or "").strip())
    text = text.strip(" .")
    if text in ("", ".", ".."):
        return default
    return text[:120]


def is_safe_path_part(value: str) -> bool:
    return sanitize_path_part(value) == value and value not in ("", ".", "..")


def validate_original_pdf_path(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    originals_dir = (DATA_DIR / "originals").resolve()
    if resolved.suffix.lower() != ".pdf":
        raise ValueError("PDFファイルではありません。")
    if not resolved.is_relative_to(originals_dir):
        raise ValueError("原本PDF保存フォルダ外のファイルは開けません。")
    if not resolved.exists():
        raise ValueError("PDFファイルが見つかりません。")
    return resolved


def safe_excel_value(value):
    if not isinstance(value, str):
        return value
    if value.startswith(_EXCEL_FORMULA_PREFIXES):
        return f"'{value}"
    return value
