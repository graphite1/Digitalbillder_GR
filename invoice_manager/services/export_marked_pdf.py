from __future__ import annotations

from pathlib import Path

try:
    import fitz
except Exception:
    fitz = None

from invoice_manager.db import DATA_DIR
from invoice_manager.repositories import add_audit_log, list_pdf_marks
from invoice_manager.utils.file_safety import sanitize_path_part, validate_original_pdf_path


_MARK_PALETTE = (
    "#1d4ed8",
    "#047857",
    "#b45309",
    "#be123c",
    "#6d28d9",
    "#0f766e",
    "#92400e",
    "#b91c1c",
)


def export_marked_pdf(invoice_id: int, invoice_file_id: int, source_pdf_path: str | Path, output_path: Path | None = None) -> Path:
    if fitz is None:
        raise ValueError("PDF出力には PyMuPDF が必要です。")
    source_path = validate_original_pdf_path(source_pdf_path)
    path = output_path or _output_path(source_path)
    page_marks = _group_marks_by_page(list_pdf_marks(invoice_id, invoice_file_id))

    with fitz.open(source_path) as document:
        for page_index in range(document.page_count):
            marks = page_marks.get(page_index + 1, [])
            if not marks:
                continue
            page = document.load_page(page_index)
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            for mark in marks:
                _draw_mark(page, mark, page_width, page_height)
        document.save(path)

    add_audit_log("確認用PDF出力", "invoice_files", int(invoice_file_id), str(path))
    return path.resolve()


def _output_path(source_path: Path) -> Path:
    output_dir = DATA_DIR / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{sanitize_path_part(source_path.stem, 'invoice')}_確認用.pdf"
    return output_dir / file_name


def _group_marks_by_page(rows: list) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["page_number"]), []).append(dict(row))
    return grouped


def _draw_mark(page, row: dict, page_width: float, page_height: float) -> None:
    text = _get_display_text(row)
    fill_color = _hex_to_rgb(_get_fill_color(str(row["work_type_code"] or "")))
    x = float(row["x_ratio"]) * page_width
    y = float(row["y_ratio"]) * page_height
    half_width = max(28.0, len(text) * 7.0 + 12.0)
    half_height = 12.0
    rect = fitz.Rect(x - half_width, y - half_height, x + half_width, y + half_height)
    page.draw_rect(rect, color=(1, 1, 1), fill=fill_color, width=1.2)
    text_rect = fitz.Rect(rect.x0 + 4, rect.y0 + 2, rect.x1 - 4, rect.y1 - 2)
    page.insert_textbox(text_rect, text, fontsize=9, fontname="helv", color=(1, 1, 1), align=1)


def _get_display_text(row: dict) -> str:
    work_type_code = str(row["work_type_code"] or "").strip()
    return work_type_code or str(row["label"])


def _get_fill_color(work_type_code: str) -> str:
    if not work_type_code:
        return "#475569"
    index = sum(ord(char) for char in work_type_code) % len(_MARK_PALETTE)
    return _MARK_PALETTE[index]


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    return tuple(int(text[index:index + 2], 16) / 255 for index in (0, 2, 4))
