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


TAX_RATE_LABELS = {"10": "10%", "8": "8%", "exempt": "非課税"}


def tax_rate_percent(tax_rate: str) -> int:
    if tax_rate not in TAX_RATE_LABELS:
        raise ValueError("税率は10%・8%・非課税から選択してください。")
    return 0 if tax_rate == "exempt" else int(tax_rate)


def tax_excluded_amount(value, tax_rate: str = "10") -> int:
    amount = int(value)
    sign = -1 if amount < 0 else 1
    rate = tax_rate_percent(tax_rate)
    return sign * (abs(amount) * 100 // (100 + rate))


def tax_included_amount(value, tax_rate: str = "10") -> int:
    amount = int(value)
    sign = -1 if amount < 0 else 1
    rate = tax_rate_percent(tax_rate)
    return amount + sign * (abs(amount) * rate // 100)
