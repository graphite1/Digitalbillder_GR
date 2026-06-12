from __future__ import annotations

from datetime import datetime
from pathlib import Path

from invoice_manager.db import get_connection
from invoice_manager.models import ImportErrorItem, InvoiceCsvRow
from invoice_manager.utils.date_utils import billing_month_from_invoice_date, parse_invoice_date, validate_billing_month
from invoice_manager.work_type_catalog import WORK_TYPE_CODE_CATALOG, WORK_TYPE_CODE_NAMES


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_audit_log(action: str, target_table: str | None = None, target_id: int | None = None, detail: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (target_table, target_id, action, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (target_table, target_id, action, detail, now_text()),
        )


def get_app_setting(key: str) -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def set_app_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now_text()),
        )


def create_import_batch(
    billing_month: str,
    csv_path: Path,
    zip_path: Path,
    csv_hash: str,
    zip_hash: str,
    memo: str,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO import_batches
                (billing_month, csv_file_name, zip_file_name, csv_hash, zip_hash, imported_at, memo)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (billing_month, csv_path.name, zip_path.name, csv_hash, zip_hash, now_text(), memo),
        )
        return int(cur.lastrowid)


def save_import_errors(import_batch_id: int, errors: list[ImportErrorItem]) -> None:
    if not errors:
        return
    created_at = now_text()
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO import_errors
                (import_batch_id, row_number, error_type, message, raw_data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    import_batch_id,
                    error.row_number,
                    error.error_type,
                    error.message,
                    error.raw_data,
                    created_at,
                )
                for error in errors
            ],
        )


def get_or_create_project(project_code: str, project_name: str) -> int:
    timestamp = now_text()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE project_code = ?", (project_code,)).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO projects (project_code, project_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_code, project_name, timestamp, timestamp),
        )
        return int(cur.lastrowid)


def list_projects() -> list:
    with get_connection() as conn:
        return list(
            conn.execute(
                """
                SELECT id, project_code, project_name
                FROM projects
                ORDER BY project_code ASC, project_name ASC
                """
            ).fetchall()
        )


def list_billing_months(include_blank: bool = False) -> list[str]:
    blank_condition = "" if include_blank else "WHERE COALESCE(billing_month, '') <> ''"
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT billing_month FROM invoices
            {blank_condition}
            ORDER BY billing_month DESC
            """
        ).fetchall()
    return [row["billing_month"] for row in rows]


def list_invoice_dates() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT invoice_date
            FROM invoices
            ORDER BY invoice_date ASC
            """
        ).fetchall()
    return [row["invoice_date"] for row in rows]


def get_or_create_vendor(vendor_name: str) -> int:
    timestamp = now_text()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM vendors WHERE vendor_name = ?", (vendor_name,)).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO vendors (vendor_name, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (vendor_name, timestamp, timestamp),
        )
        return int(cur.lastrowid)


def list_vendors() -> list:
    with get_connection() as conn:
        return list(
            conn.execute(
                """
                SELECT id, vendor_name
                FROM vendors
                ORDER BY vendor_name ASC
                """
            ).fetchall()
        )


def get_or_create_contact(row: InvoiceCsvRow, vendor_id: int) -> int | None:
    if not any((row.last_name, row.first_name, row.email, row.phone)):
        return None
    timestamp = now_text()
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM vendor_contacts
            WHERE vendor_id = ?
              AND COALESCE(last_name, '') = ?
              AND COALESCE(first_name, '') = ?
              AND COALESCE(email, '') = ?
              AND COALESCE(phone, '') = ?
            """,
            (vendor_id, row.last_name, row.first_name, row.email, row.phone),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO vendor_contacts
                (vendor_id, last_name, first_name, email, phone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vendor_id,
                row.last_name,
                row.first_name,
                row.email,
                row.phone,
                timestamp,
                timestamp,
            ),
        )
        return int(cur.lastrowid)


def find_invoice_by_external_id(external_id: str):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                invoices.*,
                projects.project_code,
                projects.project_name,
                vendors.vendor_name
            FROM invoices
            JOIN projects ON projects.id = invoices.project_id
            JOIN vendors ON vendors.id = invoices.vendor_id
            WHERE invoices.external_id = ?
            """,
            (external_id,),
        ).fetchone()


def find_duplicate_candidate(row: InvoiceCsvRow):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT invoices.id
            FROM invoices
            JOIN projects ON projects.id = invoices.project_id
            JOIN vendors ON vendors.id = invoices.vendor_id
            WHERE invoices.external_id <> ?
              AND projects.project_code = ?
              AND vendors.vendor_name = ?
              AND invoices.invoice_date = ?
              AND invoices.total_amount = ?
            LIMIT 1
            """,
            (
                row.external_id,
                row.project_code,
                row.vendor_name,
                row.invoice_date,
                row.total_amount,
            ),
        ).fetchone()


def insert_invoice(row: InvoiceCsvRow, billing_month: str, import_batch_id: int) -> int:
    project_id = get_or_create_project(row.project_code, row.project_name)
    vendor_id = get_or_create_vendor(row.vendor_name)
    contact_id = get_or_create_contact(row, vendor_id)
    timestamp = now_text()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO invoices
                (
                    import_batch_id, external_id, project_id, vendor_id, contact_id,
                    invoice_date, billing_month, total_amount, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_batch_id,
                row.external_id,
                project_id,
                vendor_id,
                contact_id,
                row.invoice_date,
                billing_month,
                row.total_amount,
                timestamp,
                timestamp,
            ),
        )
        return int(cur.lastrowid)


def insert_invoice_file(
    invoice_id: int,
    original_file_name: str,
    stored_file_path: Path,
    file_type: str,
    file_hash: str,
    file_size: int,
) -> bool:
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO invoice_files
                    (
                        invoice_id, original_file_name, stored_file_path, file_type,
                        file_hash, file_size, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    original_file_name,
                    str(stored_file_path),
                    file_type,
                    file_hash,
                    file_size,
                    now_text(),
                ),
            )
            return True
        except Exception:
            return False


def list_invoices(filters: dict[str, str] | None = None) -> list:
    filters = filters or {}
    where = []
    params: list[str | int] = []
    like_mapping = {
        "project_name": "projects.project_name",
        "vendor_name": "vendors.vendor_name",
    }
    exact_mapping = {
        "billing_month": "invoices.billing_month",
        "project_code": "projects.project_code",
        "invoice_date": "invoices.invoice_date",
    }
    project_id = _filter_text(filters.get("project_id"))
    if project_id and project_id.lower() != "all":
        where.append("invoices.project_id = ?")
        params.append(int(project_id))
    vendor_id = _filter_text(filters.get("vendor_id"))
    if vendor_id and vendor_id.lower() != "all":
        where.append("invoices.vendor_id = ?")
        params.append(int(vendor_id))
    if _filter_text(filters.get("billing_month_blank")):
        where.append("COALESCE(invoices.billing_month, '') = ''")
    for key, column in like_mapping.items():
        value = _filter_text(filters.get(key))
        if value:
            where.append(f"{column} LIKE ?")
            params.append(f"%{value}%")
    for key, column in exact_mapping.items():
        value = _filter_text(filters.get(key))
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    invoice_date_from = _filter_text(filters.get("invoice_date_from"))
    if invoice_date_from:
        where.append("invoices.invoice_date >= ?")
        params.append(parse_invoice_date(invoice_date_from))
    invoice_date_to = _filter_text(filters.get("invoice_date_to"))
    if invoice_date_to:
        where.append("invoices.invoice_date <= ?")
        params.append(parse_invoice_date(invoice_date_to))
    amount_min = _filter_text(filters.get("amount_min"))
    if amount_min:
        where.append("invoices.total_amount >= ?")
        params.append(int(amount_min.replace(",", "")))
    amount_max = _filter_text(filters.get("amount_max"))
    if amount_max:
        where.append("invoices.total_amount <= ?")
        params.append(int(amount_max.replace(",", "")))

    sql = """
        SELECT
            invoices.id,
            invoices.external_id,
            invoices.billing_month,
            projects.project_code,
            projects.project_name,
            vendors.vendor_name,
            COALESCE(vendor_contacts.last_name, '') || COALESCE(vendor_contacts.first_name, '') AS contact_name,
            COALESCE(vendor_contacts.email, '') AS email,
            COALESCE(vendor_contacts.phone, '') AS phone,
            invoices.invoice_date,
            invoices.total_amount,
            COALESCE(invoices.local_memo, '') AS local_memo,
            COUNT(invoice_files.id) AS file_count
        FROM invoices
        JOIN projects ON projects.id = invoices.project_id
        JOIN vendors ON vendors.id = invoices.vendor_id
        LEFT JOIN vendor_contacts ON vendor_contacts.id = invoices.contact_id
        LEFT JOIN invoice_files ON invoice_files.invoice_id = invoices.id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    order_mapping = {
        "invoice_date_desc": "invoices.invoice_date DESC, invoices.id DESC",
        "invoice_date_asc": "invoices.invoice_date ASC, invoices.id ASC",
        "billing_month_desc": "invoices.billing_month DESC, invoices.invoice_date DESC, invoices.id DESC",
        "project_code_asc": "projects.project_code ASC, invoices.invoice_date DESC, invoices.id DESC",
        "vendor_name_asc": "vendors.vendor_name ASC, invoices.invoice_date DESC, invoices.id DESC",
        "amount_desc": "invoices.total_amount DESC, invoices.invoice_date DESC, invoices.id DESC",
        "amount_asc": "invoices.total_amount ASC, invoices.invoice_date DESC, invoices.id DESC",
    }
    sort_key = _filter_text(filters.get("sort"))
    order_by = order_mapping.get(sort_key, order_mapping["invoice_date_desc"])
    sql += f"""
        GROUP BY invoices.id
        ORDER BY {order_by}
    """
    with get_connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def _filter_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def list_invoice_files(invoice_id: int) -> list:
    with get_connection() as conn:
        return list(
            conn.execute(
                """
                SELECT id, original_file_name, stored_file_path, file_type, file_size
                FROM invoice_files
                WHERE invoice_id = ?
                ORDER BY id
                """,
                (invoice_id,),
            ).fetchall()
        )


def update_invoice_memo(invoice_id: int, memo: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE invoices SET local_memo = ?, updated_at = ? WHERE id = ?",
            (memo, now_text(), invoice_id),
        )
    add_audit_log("メモ変更", "invoices", invoice_id, memo)


def update_invoice_billing_month(invoice_ids: list[int], billing_month: str) -> int:
    billing_month = validate_billing_month(billing_month)
    ids = [int(invoice_id) for invoice_id in invoice_ids]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    params = [billing_month, now_text(), *ids]
    with get_connection() as conn:
        cur = conn.execute(
            f"""
            UPDATE invoices
            SET billing_month = ?, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            params,
        )
    add_audit_log("請求月変更", "invoices", None, f"{billing_month}: {len(ids)}件")
    return int(cur.rowcount)


def delete_invoices(invoice_ids: list[int]) -> tuple[int, list[str]]:
    ids = [int(invoice_id) for invoice_id in invoice_ids]
    if not ids:
        return 0, []
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        file_rows = conn.execute(
            f"""
            SELECT stored_file_path
            FROM invoice_files
            WHERE invoice_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        conn.execute(f"DELETE FROM pdf_marks WHERE invoice_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM invoice_allocations WHERE invoice_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM invoice_files WHERE invoice_id IN ({placeholders})", ids)
        cur = conn.execute(f"DELETE FROM invoices WHERE id IN ({placeholders})", ids)
    deleted_paths = [_delete_invoice_file(Path(row["stored_file_path"])) for row in file_rows]
    failed_paths = [str(path) for path in deleted_paths if path is not None]
    add_audit_log("請求削除", "invoices", None, f"{len(ids)}件")
    return int(cur.rowcount), failed_paths


def recalculate_invoice_billing_months() -> int:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, invoice_date FROM invoices").fetchall()
        updates = [
            (billing_month_from_invoice_date(row["invoice_date"]), now_text(), int(row["id"]))
            for row in rows
            if (row["invoice_date"] or "").strip()
        ]
        if not updates:
            return 0
        conn.executemany(
            """
            UPDATE invoices
            SET billing_month = ?, updated_at = ?
            WHERE id = ?
            """,
            updates,
        )
    add_audit_log("請求月一括再計算", "invoices", None, f"{len(updates)}件")
    return len(updates)


def _delete_invoice_file(path: Path) -> Path | None:
    try:
        if path.exists():
            path.unlink()
            _remove_empty_parent_dirs(path.parent)
        return None
    except Exception:
        return path


def _remove_empty_parent_dirs(path: Path) -> None:
    current = path
    originals_dir = (Path(__file__).resolve().parent.parent / "data" / "originals").resolve()
    while current.exists() and current != originals_dir:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def list_work_type_codes(project_id: int | None = None, active_only: bool = False) -> list:
    where = []
    params: list[int | str] = []
    if project_id:
        where.append("project_id = ?")
        params.append(int(project_id))
    if active_only:
        where.append("is_active = 1")
    catalog_codes = [code for code, _name in WORK_TYPE_CODE_CATALOG]
    placeholders = ",".join("?" for _code in catalog_codes)
    where.append(f"work_type_codes.code IN ({placeholders})")
    params.extend(catalog_codes)
    sql = """
        SELECT work_type_codes.*, projects.project_code, projects.project_name
        FROM work_type_codes
        LEFT JOIN projects ON projects.id = work_type_codes.project_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sort_order ASC, code ASC"
    with get_connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def ensure_work_type_codes_for_project(project_id: int) -> int:
    timestamp = now_text()
    with get_connection() as conn:
        conn.executemany(
            """
            UPDATE work_type_codes
            SET name = ?, sort_order = ?, updated_at = ?
            WHERE project_id = ? AND code = ?
            """,
            [
                (name, index, timestamp, int(project_id), code)
                for index, (code, name) in enumerate(WORK_TYPE_CODE_CATALOG, start=1)
            ],
        )
        cur = conn.executemany(
            """
            INSERT OR IGNORE INTO work_type_codes
                (project_id, code, name, sort_order, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            [
                (int(project_id), code, name, index, timestamp, timestamp)
                for index, (code, name) in enumerate(WORK_TYPE_CODE_CATALOG, start=1)
            ],
        )
    return int(cur.rowcount)


def save_work_type_code(
    project_id: int,
    code: str,
    name: str,
    sort_order: int = 0,
    is_active: int = 1,
    work_type_code_id: int | None = None,
) -> int:
    code = code.strip()
    name = WORK_TYPE_CODE_NAMES.get(code, "")
    if not name:
        raise ValueError("工種コードは指定の一覧から選択してください。")
    timestamp = now_text()
    with get_connection() as conn:
        if work_type_code_id:
            conn.execute(
                """
                UPDATE work_type_codes
                SET project_id = ?, code = ?, name = ?, sort_order = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (project_id, code, name, sort_order, is_active, timestamp, work_type_code_id),
            )
            saved_id = int(work_type_code_id)
            action = "工種コード更新"
        else:
            cur = conn.execute(
                """
                INSERT INTO work_type_codes
                    (project_id, code, name, sort_order, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, code, name, sort_order, is_active, timestamp, timestamp),
            )
            saved_id = int(cur.lastrowid)
            action = "工種コード登録"
    add_audit_log(action, "work_type_codes", saved_id, f"{code} {name}")
    return saved_id


def list_vendor_work_type_candidates(vendor_id: int) -> list[dict[str, str | int]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT code, sort_order
            FROM vendor_work_type_candidates
            WHERE vendor_id = ?
            ORDER BY sort_order ASC, code ASC
            """,
            (int(vendor_id),),
        ).fetchall()
    return [
        {"code": row["code"], "name": WORK_TYPE_CODE_NAMES[row["code"]], "sort_order": row["sort_order"]}
        for row in rows
        if row["code"] in WORK_TYPE_CODE_NAMES
    ]


def save_vendor_work_type_candidates(vendor_id: int, codes: list[str]) -> int:
    timestamp = now_text()
    valid_codes = [code for code in codes if code in WORK_TYPE_CODE_NAMES]
    with get_connection() as conn:
        conn.execute("DELETE FROM vendor_work_type_candidates WHERE vendor_id = ?", (int(vendor_id),))
        conn.executemany(
            """
            INSERT INTO vendor_work_type_candidates
                (vendor_id, code, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (int(vendor_id), code, index, timestamp, timestamp)
                for index, code in enumerate(valid_codes, start=1)
            ],
        )
    add_audit_log("取引先工種候補更新", "vendors", int(vendor_id), f"{len(valid_codes)}件")
    return len(valid_codes)


def list_recent_work_type_codes_for_project_vendor(project_id: int, vendor_id: int, exclude_invoice_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                work_type_codes.code,
                MAX(invoices.invoice_date) AS latest_invoice_date,
                MIN(invoice_allocations.sort_order) AS first_sort_order,
                MAX(invoice_allocations.id) AS latest_allocation_id
            FROM invoice_allocations
            JOIN invoices ON invoices.id = invoice_allocations.invoice_id
            JOIN work_type_codes ON work_type_codes.id = invoice_allocations.work_type_code_id
            WHERE invoices.project_id = ?
              AND invoices.vendor_id = ?
              AND invoices.id <> ?
            GROUP BY work_type_codes.code
            ORDER BY latest_invoice_date DESC, first_sort_order ASC, latest_allocation_id DESC
            """,
            (int(project_id), int(vendor_id), int(exclude_invoice_id)),
        ).fetchall()
    return [row["code"] for row in rows if row["code"] in WORK_TYPE_CODE_NAMES]


def list_invoice_allocations(invoice_id: int) -> list:
    with get_connection() as conn:
        return list(
            conn.execute(
                """
                SELECT
                    invoice_allocations.*,
                    work_type_codes.code,
                    work_type_codes.name
                FROM invoice_allocations
                JOIN work_type_codes ON work_type_codes.id = invoice_allocations.work_type_code_id
                WHERE invoice_allocations.invoice_id = ?
                ORDER BY invoice_allocations.sort_order ASC, invoice_allocations.id ASC
                """,
                (invoice_id,),
            ).fetchall()
        )


def save_invoice_allocation(
    invoice_id: int,
    work_type_code_id: int,
    amount: int | None,
    memo: str = "",
    sort_order: int = 0,
    allocation_id: int | None = None,
) -> int:
    normalized_amount = 0 if amount is None else int(amount)
    timestamp = now_text()
    with get_connection() as conn:
        if allocation_id:
            conn.execute(
                """
                UPDATE invoice_allocations
                SET work_type_code_id = ?, amount = ?, memo = ?, sort_order = ?, updated_at = ?
                WHERE id = ?
                """,
                (work_type_code_id, normalized_amount, memo, sort_order, timestamp, allocation_id),
            )
            saved_id = int(allocation_id)
            action = "振分更新"
        else:
            cur = conn.execute(
                """
                INSERT INTO invoice_allocations
                    (invoice_id, work_type_code_id, amount, memo, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, work_type_code_id, normalized_amount, memo, sort_order, timestamp, timestamp),
            )
            saved_id = int(cur.lastrowid)
            action = "振分登録"
    add_audit_log(action, "invoice_allocations", saved_id, str(normalized_amount))
    return saved_id


def delete_invoice_allocation(allocation_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM pdf_marks WHERE allocation_id = ?", (allocation_id,))
        conn.execute("DELETE FROM invoice_allocations WHERE id = ?", (allocation_id,))
    add_audit_log("振分削除", "invoice_allocations", allocation_id, "")


def get_invoice_allocation_total(invoice_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM invoice_allocations WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
    return int(row["total"])


def get_or_create_pdf_mark_label(invoice_id: int, allocation_id: int) -> str:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT label
            FROM pdf_marks
            WHERE invoice_id = ? AND allocation_id = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (int(invoice_id), int(allocation_id)),
        ).fetchone()
        if existing:
            return str(existing["label"])
        rows = conn.execute(
            """
            SELECT DISTINCT label
            FROM pdf_marks
            WHERE invoice_id = ?
            ORDER BY id ASC
            """,
            (int(invoice_id),),
        ).fetchall()
    used_numbers = [int(row["label"]) for row in rows if str(row["label"]).isdigit()]
    return str(max(used_numbers, default=0) + 1)


def list_pdf_marks(invoice_id: int, invoice_file_id: int | None = None, page_number: int | None = None) -> list:
    where = ["pdf_marks.invoice_id = ?"]
    params: list[int] = [int(invoice_id)]
    if invoice_file_id is not None:
        where.append("pdf_marks.invoice_file_id = ?")
        params.append(int(invoice_file_id))
    if page_number is not None:
        where.append("pdf_marks.page_number = ?")
        params.append(int(page_number))
    sql = f"""
        SELECT
            pdf_marks.*,
            COALESCE(work_type_codes.code, '') AS work_type_code,
            COALESCE(work_type_codes.name, '') AS work_type_name
        FROM pdf_marks
        LEFT JOIN invoice_allocations ON invoice_allocations.id = pdf_marks.allocation_id
        LEFT JOIN work_type_codes ON work_type_codes.id = invoice_allocations.work_type_code_id
        WHERE {' AND '.join(where)}
        ORDER BY pdf_marks.page_number ASC, pdf_marks.id ASC
    """
    with get_connection() as conn:
        return list(conn.execute(sql, params).fetchall())


def create_pdf_mark(
    invoice_file_id: int,
    invoice_id: int,
    allocation_id: int,
    page_number: int,
    x_ratio: float,
    y_ratio: float,
    mark_type: str,
    label: str,
    memo: str = "",
) -> int:
    if not 0 <= x_ratio <= 1 or not 0 <= y_ratio <= 1:
        raise ValueError("PDFマーク位置がページ範囲外です。")
    timestamp = now_text()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO pdf_marks
                (
                    invoice_file_id, invoice_id, allocation_id, page_number,
                    x_ratio, y_ratio, mark_type, label, memo, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(invoice_file_id),
                int(invoice_id),
                int(allocation_id),
                int(page_number),
                float(x_ratio),
                float(y_ratio),
                mark_type,
                label,
                memo,
                timestamp,
                timestamp,
            ),
        )
        saved_id = int(cur.lastrowid)
    add_audit_log("PDFマーク登録", "pdf_marks", saved_id, label)
    return saved_id


def delete_pdf_mark(mark_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM pdf_marks WHERE id = ?", (int(mark_id),))
    add_audit_log("PDFマーク削除", "pdf_marks", int(mark_id), "")


def update_pdf_mark_position(mark_id: int, x_ratio: float, y_ratio: float) -> None:
    if not 0 <= x_ratio <= 1 or not 0 <= y_ratio <= 1:
        raise ValueError("PDFマーク位置がページ範囲外です。")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE pdf_marks
            SET x_ratio = ?, y_ratio = ?, updated_at = ?
            WHERE id = ?
            """,
            (float(x_ratio), float(y_ratio), now_text(), int(mark_id)),
        )
    add_audit_log("PDFマーク位置更新", "pdf_marks", int(mark_id), f"{x_ratio:.4f},{y_ratio:.4f}")


def list_work_type_summary(kind: str) -> list:
    labels = {
        "project": "projects.project_code || '｜' || projects.project_name",
        "vendor": "vendors.vendor_name",
        "month": "invoices.billing_month",
    }
    group_expr = labels[kind]
    with get_connection() as conn:
        return list(
            conn.execute(
                f"""
                SELECT
                    {group_expr} AS label,
                    work_type_codes.code AS work_type_code,
                    work_type_codes.name AS work_type_name,
                    COUNT(DISTINCT invoices.id) AS count,
                    SUM(invoice_allocations.amount) AS total
                FROM invoice_allocations
                JOIN invoices ON invoices.id = invoice_allocations.invoice_id
                JOIN projects ON projects.id = invoices.project_id
                JOIN vendors ON vendors.id = invoices.vendor_id
                JOIN work_type_codes ON work_type_codes.id = invoice_allocations.work_type_code_id
                GROUP BY label, work_type_codes.id
                ORDER BY label ASC, work_type_codes.sort_order ASC, work_type_codes.code ASC
                """
            ).fetchall()
        )


def list_invoice_allocation_export_rows() -> list:
    with get_connection() as conn:
        return list(
            conn.execute(
                """
                SELECT
                    invoices.external_id,
                    invoices.billing_month,
                    projects.project_code,
                    projects.project_name,
                    vendors.vendor_name,
                    invoices.invoice_date,
                    invoices.total_amount,
                    work_type_codes.code AS work_type_code,
                    work_type_codes.name AS work_type_name,
                    invoice_allocations.amount,
                    COALESCE(invoice_allocations.memo, '') AS allocation_memo
                FROM invoice_allocations
                JOIN invoices ON invoices.id = invoice_allocations.invoice_id
                JOIN projects ON projects.id = invoices.project_id
                JOIN vendors ON vendors.id = invoices.vendor_id
                JOIN work_type_codes ON work_type_codes.id = invoice_allocations.work_type_code_id
                ORDER BY invoices.billing_month DESC, invoices.invoice_date DESC, invoices.id DESC
                """
            ).fetchall()
        )


def get_invoice_detail(invoice_id: int):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                invoices.*,
                projects.project_code,
                projects.project_name,
                vendors.vendor_name,
                vendor_contacts.last_name,
                vendor_contacts.first_name,
                vendor_contacts.email,
                vendor_contacts.phone
            FROM invoices
            JOIN projects ON projects.id = invoices.project_id
            JOIN vendors ON vendors.id = invoices.vendor_id
            LEFT JOIN vendor_contacts ON vendor_contacts.id = invoices.contact_id
            WHERE invoices.id = ?
            """,
            (invoice_id,),
        ).fetchone()


def get_summary() -> dict[str, list]:
    with get_connection() as conn:
        return {
            "月別合計": list(
                conn.execute(
                    """
                    SELECT billing_month AS label, COUNT(*) AS count, SUM(total_amount) AS total
                    FROM invoices
                    GROUP BY billing_month
                    ORDER BY billing_month DESC
                    """
                )
            ),
            "工事別合計": list(
                conn.execute(
                    """
                    SELECT projects.project_name AS label, COUNT(*) AS count, SUM(invoices.total_amount) AS total
                    FROM invoices
                    JOIN projects ON projects.id = invoices.project_id
                    GROUP BY projects.id
                    ORDER BY total DESC
                    """
                )
            ),
            "取引先別合計": list(
                conn.execute(
                    """
                    SELECT vendors.vendor_name AS label, COUNT(*) AS count, SUM(invoices.total_amount) AS total
                    FROM invoices
                    JOIN vendors ON vendors.id = invoices.vendor_id
                    GROUP BY vendors.id
                    ORDER BY total DESC
                    """
                )
            ),
            "工事別・取引先別合計": list(
                conn.execute(
                    """
                    SELECT
                        projects.project_name || ' / ' || vendors.vendor_name AS label,
                        COUNT(*) AS count,
                        SUM(invoices.total_amount) AS total
                    FROM invoices
                    JOIN projects ON projects.id = invoices.project_id
                    JOIN vendors ON vendors.id = invoices.vendor_id
                    GROUP BY projects.id, vendors.id
                    ORDER BY total DESC
                    """
                )
            ),
            "請求月別件数": list(
                conn.execute(
                    """
                    SELECT billing_month AS label, COUNT(*) AS count, 0 AS total
                    FROM invoices
                    GROUP BY billing_month
                    ORDER BY billing_month DESC
                    """
                )
            ),
            "添付PDF件数": list(
                conn.execute(
                    """
                    SELECT invoices.billing_month AS label, COUNT(invoice_files.id) AS count, 0 AS total
                    FROM invoices
                    LEFT JOIN invoice_files ON invoice_files.invoice_id = invoices.id
                    GROUP BY invoices.billing_month
                    ORDER BY invoices.billing_month DESC
                    """
                )
            ),
            "工事別・工種コード別集計": list_work_type_summary("project"),
            "取引先別・工種コード別集計": list_work_type_summary("vendor"),
            "月別・工種コード別集計": list_work_type_summary("month"),
        }
