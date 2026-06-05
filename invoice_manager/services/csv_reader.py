from __future__ import annotations

import csv
from pathlib import Path

from invoice_manager.models import ImportErrorItem, InvoiceCsvRow
from invoice_manager.utils.date_utils import parse_invoice_date
from invoice_manager.utils.money_utils import parse_amount


REQUIRED_COLUMNS = [
    "ID",
    "工事名",
    "工事コード",
    "取引先名",
    "姓",
    "名",
    "メールアドレス",
    "電話番号",
    "請求日",
    "請求金額(税込)",
]

ENCODINGS = ["utf-8-sig", "cp932", "shift_jis"]


def read_invoice_csv(path: Path) -> tuple[list[InvoiceCsvRow], list[ImportErrorItem], str | None]:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            return _read_with_encoding(path, encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    return (
        [],
        [
            ImportErrorItem(
                None,
                "CSV文字コード",
                f"CSVを読み込めませんでした: {last_error}",
            )
        ],
        None,
    )


def _read_with_encoding(path: Path, encoding: str) -> tuple[list[InvoiceCsvRow], list[ImportErrorItem], str]:
    rows: list[InvoiceCsvRow] = []
    errors: list[ImportErrorItem] = []
    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            errors.append(
                ImportErrorItem(
                    None,
                    "必須列不足",
                    "不足列: " + ", ".join(missing),
                )
            )
            return rows, errors, encoding
        for row_number, raw in enumerate(reader, start=2):
            try:
                rows.append(_parse_row(row_number, raw))
            except Exception as exc:
                errors.append(
                    ImportErrorItem(
                        row_number,
                        "CSV行エラー",
                        str(exc),
                        repr(raw),
                    )
                )
    return rows, errors, encoding


def _parse_row(row_number: int, raw: dict[str, str]) -> InvoiceCsvRow:
    values = {key: (raw.get(key) or "").strip() for key in REQUIRED_COLUMNS}
    for key in ("ID", "工事名", "工事コード", "取引先名", "請求日", "請求金額(税込)"):
        if not values[key]:
            raise ValueError(f"{key} が空です")
    return InvoiceCsvRow(
        row_number=row_number,
        external_id=values["ID"],
        project_name=values["工事名"],
        project_code=values["工事コード"],
        vendor_name=values["取引先名"],
        last_name=values["姓"],
        first_name=values["名"],
        email=values["メールアドレス"],
        phone=values["電話番号"],
        invoice_date=parse_invoice_date(values["請求日"]),
        total_amount=parse_amount(values["請求金額(税込)"]),
        raw_data=values,
    )
