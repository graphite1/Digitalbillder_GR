from __future__ import annotations


def parse_amount(value: str) -> int:
    text = (value or "").strip().replace(",", "")
    if not text:
        raise ValueError("請求金額(税込)が空です")
    return int(text)
