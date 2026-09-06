"""Explicit, snapshot-checked one-yen tax adjustments; net amounts never change."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from invoice_manager import db, repositories
from invoice_manager.utils.money_utils import TAX_RATE_LABELS, tax_included_amount


@dataclass(frozen=True)
class RoundingAdjustmentPreview:
    invoice_id: int
    allocation_id: int
    fingerprint: str
    difference: int
    net_amount: int
    tax_rate: str
    tax_before: int
    tax_after: int
    gross_before: int
    gross_after: int
    adjustment_before: int
    adjustment_after: int
    code: str
    name: str


def _preview(connection, invoice_id: int, allocation_id: int) -> RoundingAdjustmentPreview:
    if not allocation_id:
        raise ValueError("税額を調整する振分行を選択してください。")
    repositories._require_invoice_ids_visible(connection, [invoice_id])
    invoice = connection.execute("SELECT * FROM invoices WHERE id = ?", (int(invoice_id),)).fetchone()
    if invoice["total_amount_excluded"] is None:
        raise ValueError("請求書の保存済み税抜金額が不明なため、税額端数を調整できません。")
    rows = connection.execute(
        """SELECT a.*, w.code, w.name, w.is_active, w.project_id AS work_type_project_id
           FROM invoice_allocations AS a LEFT JOIN work_type_codes AS w ON w.id = a.work_type_code_id
           WHERE a.invoice_id = ? ORDER BY a.id""", (int(invoice_id),),
    ).fetchall()
    selected = next((row for row in rows if int(row["id"]) == int(allocation_id)), None)
    if selected is None:
        raise ValueError("選択した振分行がこの請求書に見つかりません。")
    for row in rows:
        if not row["is_active"] or row["work_type_project_id"] != invoice["project_id"]:
            raise ValueError("無効な工種または別工事の振分があるため、税額端数を調整できません。")
        rate, net, adjustment = row["tax_rate"], row["amount_excluded"], row["tax_rounding_adjustment"]
        if rate not in TAX_RATE_LABELS or net is None or int(net) <= 0:
            raise ValueError("税率または税抜金額が不正な振分があります。")
        if type(adjustment) is not int or adjustment not in (-1, 0, 1) or (rate == "exempt" and adjustment):
            raise ValueError("保存済みの税額端数調整が不正です。")
        if tax_included_amount(int(net), rate) + adjustment != int(row["amount"]):
            raise ValueError("保存済みの税込金額と税率計算が一致しない振分があります。")
    if sum(int(row["amount_excluded"]) for row in rows) != int(invoice["total_amount_excluded"]):
        raise ValueError("振分の税抜合計と請求書の保存済み税抜金額が一致していません。")
    difference = int(invoice["total_amount"]) - sum(int(row["amount"]) for row in rows)
    if difference not in (-1, 1):
        raise ValueError("税込合計の差が1円の場合だけ税額端数を調整できます。")
    if selected["tax_rate"] not in ("10", "8"):
        raise ValueError("非課税の振分行は税額端数を調整できません。")
    adjustment_before = int(selected["tax_rounding_adjustment"])
    adjustment_after = adjustment_before + difference
    if adjustment_after not in (-1, 0, 1):
        raise ValueError("1行の税額端数調整は合計で±1円までです。")
    net, gross = int(selected["amount_excluded"]), int(selected["amount"])
    if gross + difference < net:
        raise ValueError("調整後の消費税額をマイナスにはできません。")
    payload = {"invoice": dict(invoice), "allocations": [dict(row) for row in rows]}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return RoundingAdjustmentPreview(
        int(invoice_id), int(allocation_id), fingerprint, difference, net, selected["tax_rate"],
        gross - net, gross + difference - net, gross, gross + difference,
        adjustment_before, adjustment_after, str(selected["code"]), str(selected["name"]),
    )


def preview_rounding_adjustment(invoice_id: int, allocation_id: int) -> RoundingAdjustmentPreview:
    with db.get_connection() as connection:
        return _preview(connection, invoice_id, allocation_id)


def apply_rounding_adjustment(invoice_id: int, allocation_id: int,
                              expected_preview: RoundingAdjustmentPreview) -> RoundingAdjustmentPreview:
    if not isinstance(expected_preview, RoundingAdjustmentPreview):
        raise ValueError("税額端数調整の確認内容を取得し直してください。")
    with db.atomic_transaction():
        with db.get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _preview(connection, invoice_id, allocation_id)
            if current != expected_preview:
                raise ValueError("確認後に請求書または振分が変更されました。税額端数調整を確認し直してください。")
            connection.execute(
                """UPDATE invoice_allocations SET amount = ?, tax_rounding_adjustment = ?, updated_at = ?
                   WHERE id = ? AND invoice_id = ?""",
                (current.gross_after, current.adjustment_after, repositories.now_text(), int(allocation_id), int(invoice_id)),
            )
            repositories.add_audit_log(
                "振分税額端数調整", "invoice_allocations", int(allocation_id),
                f"請求ID:{invoice_id} 税抜:{current.net_amount} 税率:{current.tax_rate} "
                f"税額:{current.tax_before}→{current.tax_after} 税込:{current.gross_before}→{current.gross_after} "
                f"調整:{current.adjustment_before}→{current.adjustment_after} 差:{current.difference:+d}円",
            )
    return current
