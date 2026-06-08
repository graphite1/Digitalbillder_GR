from __future__ import annotations

from datetime import datetime


def parse_invoice_date(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("請求日の日付形式が不正です")


def format_invoice_date(value: str) -> str:
    date_text = parse_invoice_date(value)
    date = datetime.strptime(date_text, "%Y-%m-%d").date()
    return f"{date.month}月{date.day}日"


def billing_month_from_invoice_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    invoice_date = datetime.strptime(parse_invoice_date(text), "%Y-%m-%d").date()
    year = invoice_date.year
    month = invoice_date.month
    if invoice_date.day >= 10:
        month += 1
        if month > 12:
            year += 1
            month = 1
    return f"{year:04d}-{month:02d}"


def validate_billing_month(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%Y-%m", "%Y/%m", "%Y年%m月", "%Y年%-m月"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    if "年" in text and text.endswith("月"):
        try:
            year, month = text[:-1].split("年", 1)
            return f"{int(year):04d}-{int(month):02d}"
        except ValueError:
            pass
    raise ValueError("請求月を選択してください")


def format_billing_month(value: str) -> str:
    if not (value or "").strip():
        return "未設定"
    normalized = validate_billing_month(value)
    year, month = normalized.split("-", 1)
    return f"{int(year)}年{int(month)}月"


def add_months(value: str, offset: int) -> str:
    normalized = validate_billing_month(value)
    year, month = [int(part) for part in normalized.split("-", 1)]
    total = year * 12 + month - 1 + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def billing_month_candidates(center: str | None = None, before: int = 3, after: int = 6) -> list[str]:
    base = validate_billing_month(center or datetime.now().strftime("%Y-%m"))
    return [add_months(base, offset) for offset in range(-before, after + 1)]
