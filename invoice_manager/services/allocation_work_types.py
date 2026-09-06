"""Save shorthand using actual codes or the named local D-code rule."""
from invoice_manager import db, repositories
from invoice_manager.services.work_type_resolution import load_work_type_choices, resolve_from_catalog, WorkTypeResolutionError


def save_resolved_allocation(invoice_id: int, work_type_input: str, amount: int | None,
                             memo: str = "", sort_order: int = 0,
                             allocation_id: int | None = None, tax_rate: str | None = None) -> int:
    with db.atomic_transaction():
        invoice = repositories.get_invoice_detail(invoice_id)
        if invoice is None:
            raise ValueError("請求データが見つかりません。")
        project_id = int(invoice["project_id"])
        catalog = load_work_type_choices(project_id)
        canonical = resolve_from_catalog(work_type_input, catalog)
        rows = repositories.list_work_type_codes(project_id)
        existing = next((row for row in rows if row["code"] == canonical.code), None)
        # A disabled shorthand must not silently become a new active master row.
        for row in rows:
            if row["is_active"]:
                continue
            try:
                disabled = resolve_from_catalog(row["code"], catalog).code
            except WorkTypeResolutionError:
                disabled = row["code"]
            if disabled == canonical.code:
                raise ValueError("この工事で無効化された工種コードです。工種コードマスタを確認してください。")
        if existing is None:
            code_id = repositories.save_work_type_code(project_id, canonical.code, canonical.name)
        else:
            code_id = int(existing["id"])
        return repositories.save_invoice_allocation(
            invoice_id, code_id, amount, memo, sort_order, allocation_id, tax_rate
        )
