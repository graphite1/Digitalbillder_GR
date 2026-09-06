"""Build and compare transfer plans locally. No network access or Web updates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from uuid import UUID

from invoice_manager.repositories import get_invoice_detail, list_invoice_allocations, list_work_type_codes
from invoice_manager.utils.money_utils import TAX_RATE_LABELS, tax_included_amount
from invoice_manager.services.work_type_resolution import load_confirmed_work_types, resolve_from_catalog, WorkTypeResolutionError


@dataclass(frozen=True)
class AllocationLine:
    code: str
    name: str
    amount_excluded: int
    tax_rate: str
    tax_amount: int
    amount_included: int


@dataclass(frozen=True)
class AllocationPlan:
    external_id: str
    project_code: str
    vendor_name: str
    invoice_date: str
    invoice_amount: int
    lines: tuple[AllocationLine, ...]
    errors: tuple[str, ...]

    @property
    def total_included(self) -> int:
        return sum(line.amount_included for line in self.lines)

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AllocationDifference:
    row_number: int
    field: str
    local: str
    web: str


def build_allocation_plan(invoice_id: int) -> AllocationPlan:
    invoice = get_invoice_detail(invoice_id)
    if invoice is None:
        raise ValueError("請求データが見つかりません。")
    errors = []
    external_id = invoice["external_id"]
    try:
        UUID(external_id)
    except (ValueError, TypeError, AttributeError):
        errors.append("Webの請求書IDとして確認できない形式です。")
    masters = list_work_type_codes(int(invoice["project_id"]))
    active_codes = {int(row["id"]) for row in masters if row["is_active"]}
    canonical_codes = load_confirmed_work_types(int(invoice["project_id"]))
    disabled_codes = set()
    for master in masters:
        if not master["is_active"]:
            try:
                disabled_codes.add(resolve_from_catalog(master["code"], canonical_codes).code)
            except WorkTypeResolutionError:
                disabled_codes.add(master["code"])
    lines = []
    for row in list_invoice_allocations(invoice_id):
        rate = row["tax_rate"]
        if rate not in TAX_RATE_LABELS:
            errors.append("未対応の税率があります。")
        amount = int(row["amount_excluded"] or 0)
        included = int(row["amount"])
        if not row["code"] or int(row["work_type_code_id"]) not in active_codes:
            errors.append("この工事で有効でない工種コードがあります。")
        if amount <= 0:
            errors.append("税抜金額が0円以下の振分行があります。")
        if rate in TAX_RATE_LABELS and tax_included_amount(amount, rate) != included:
            errors.append("保存済みの税込金額と税率計算に差があります。端数の扱いを確認してください。")
        code, name = row["code"], row["name"]
        try:
            canonical = resolve_from_catalog(code, canonical_codes)
            code, name = canonical.code, canonical.name
            if code in disabled_codes:
                errors.append("この工事で無効化された工種コードがあります。")
        except WorkTypeResolutionError as exc:
            errors.append(str(exc))
        lines.append(AllocationLine(code, name, amount, rate, included - amount, included))
    if not lines:
        errors.append("工種振分を入力してください。")
    invoice_amount = int(invoice["total_amount"])
    if lines and sum(line.amount_included for line in lines) != invoice_amount:
        errors.append("振分の税込合計と請求書の税込原本額が一致していません。")
    return AllocationPlan(
        external_id, invoice["project_code"], invoice["vendor_name"], invoice["invoice_date"],
        invoice_amount, tuple(lines), tuple(dict.fromkeys(errors)),
    )


def compare_allocations(local: tuple[AllocationLine, ...], web: tuple[AllocationLine, ...]) -> tuple[AllocationDifference, ...]:
    """Keep row order and multiplicity: differing existing rows never imply overwrite."""
    differences = []
    fields = {"code": "工種コード", "amount_excluded": "税抜金額", "tax_rate": "税率",
              "tax_amount": "消費税額", "amount_included": "税込金額"}
    for index in range(max(len(local), len(web))):
        left = local[index] if index < len(local) else None
        right = web[index] if index < len(web) else None
        if left is None or right is None:
            differences.append(AllocationDifference(index + 1, "振分行", left.code if left else "なし", right.code if right else "なし"))
            continue
        for field, label in fields.items():
            local_value, web_value = getattr(left, field), getattr(right, field)
            if local_value != web_value:
                differences.append(AllocationDifference(index + 1, label, str(local_value), str(web_value)))
    return tuple(differences)
