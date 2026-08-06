from __future__ import annotations


def parse_amount(value: str) -> int:
    text = (value or "").strip().replace(",", "")
    if not text:
        raise ValueError("請求金額(税込)が空です")
    return int(text)


def format_amount(value) -> str:
    if value in (None, ""):
        return ""
    return f"{int(value):,}"


def tax_excluded_amount(value) -> int:
    amount = int(value)
    sign = -1 if amount < 0 else 1
    return sign * (abs(amount) * 10 // 11)
