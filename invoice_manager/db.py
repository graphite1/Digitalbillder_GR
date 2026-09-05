from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
_configured_data_dir = os.environ.get("DIGITALBUILDER_DATA_DIR", "").strip()
DATA_DIR = (
    Path(_configured_data_dir).expanduser().resolve()
    if _configured_data_dir
    else BASE_DIR / "data"
)
DB_PATH = DATA_DIR / "app.db"
_transaction_connection: ContextVar[sqlite3.Connection | None] = ContextVar("transaction_connection", default=None)


class BorrowedConnection:
    """Repository context managers must not commit an enclosing transaction."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False

    def __getattr__(self, name):
        return getattr(self.connection, name)


@contextmanager
def atomic_transaction():
    if _transaction_connection.get() is not None:
        raise RuntimeError("Nested atomic transactions are not supported")
    with get_connection() as connection:
        token = _transaction_connection.set(connection)
        try:
            yield
        finally:
            _transaction_connection.reset(token)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    billing_month TEXT NOT NULL,
    csv_file_name TEXT,
    zip_file_name TEXT,
    csv_hash TEXT,
    zip_hash TEXT,
    imported_at TEXT NOT NULL,
    memo TEXT,
    registered_count INTEGER NOT NULL DEFAULT 0,
    pdf_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    completed_at TEXT,
    failure_message TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    project_name TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_code)
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(vendor_name)
);

CREATE TABLE IF NOT EXISTS vendor_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    last_name TEXT,
    first_name TEXT,
    email TEXT,
    phone TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(vendor_id) REFERENCES vendors(id)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_batch_id INTEGER,
    external_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    contact_id INTEGER,
    invoice_date TEXT NOT NULL,
    billing_month TEXT NOT NULL,
    billing_month_manual_override INTEGER NOT NULL DEFAULT 0,
    total_amount INTEGER NOT NULL,
    total_amount_excluded INTEGER,
    local_memo TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(vendor_id) REFERENCES vendors(id),
    FOREIGN KEY(contact_id) REFERENCES vendor_contacts(id),
    UNIQUE(external_id)
);

CREATE TABLE IF NOT EXISTS invoice_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    original_file_name TEXT NOT NULL,
    stored_file_path TEXT NOT NULL,
    file_type TEXT,
    file_hash TEXT NOT NULL,
    file_size INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
    UNIQUE(invoice_id, file_hash)
);

CREATE TABLE IF NOT EXISTS work_type_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    UNIQUE(project_id, code)
);

CREATE TABLE IF NOT EXISTS invoice_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    work_type_code_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    amount_excluded INTEGER,
    tax_rate TEXT NOT NULL DEFAULT '10' CHECK(tax_rate IN ('10', '8', 'exempt')),
    memo TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
    FOREIGN KEY(work_type_code_id) REFERENCES work_type_codes(id)
);

CREATE TABLE IF NOT EXISTS pdf_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_file_id INTEGER NOT NULL,
    invoice_id INTEGER NOT NULL,
    allocation_id INTEGER,
    page_number INTEGER NOT NULL,
    x_ratio REAL NOT NULL,
    y_ratio REAL NOT NULL,
    x_pt REAL,
    y_pt REAL,
    page_width_pt REAL,
    page_height_pt REAL,
    mark_type TEXT NOT NULL,
    label TEXT NOT NULL,
    memo TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(invoice_file_id) REFERENCES invoice_files(id),
    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
    FOREIGN KEY(allocation_id) REFERENCES invoice_allocations(id)
);

CREATE TABLE IF NOT EXISTS vendor_work_type_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(vendor_id) REFERENCES vendors(id),
    UNIQUE(vendor_id, code)
);

CREATE TABLE IF NOT EXISTS import_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_batch_id INTEGER,
    row_number INTEGER,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    raw_data TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT,
    target_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_allocation_guard (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    state TEXT NOT NULL CHECK(state IN ('unverified', 'ready', 'frozen')),
    contract_version TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    active = _transaction_connection.get()
    if active is not None:
        return BorrowedConnection(active)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_import_batches_table(conn)
        _migrate_invoices_table(conn)
        _migrate_invoice_files_table(conn)
        _migrate_pdf_marks_table(conn)
        _migrate_projects_table(conn)
        _migrate_invoice_billing_month_override(conn)
        _migrate_tax_excluded_amounts(conn)
        _migrate_allocation_tax_rate(conn)
        conn.execute("DROP TABLE IF EXISTS budget_categories")


def _migrate_import_batches_table(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(import_batches)").fetchall()]
    migrations = {
        "registered_count": "ALTER TABLE import_batches ADD COLUMN registered_count INTEGER NOT NULL DEFAULT 0",
        "pdf_count": "ALTER TABLE import_batches ADD COLUMN pdf_count INTEGER NOT NULL DEFAULT 0",
        "error_count": "ALTER TABLE import_batches ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0",
        "status": "ALTER TABLE import_batches ADD COLUMN status TEXT NOT NULL DEFAULT 'running'",
        "completed_at": "ALTER TABLE import_batches ADD COLUMN completed_at TEXT",
        "failure_message": "ALTER TABLE import_batches ADD COLUMN failure_message TEXT",
    }
    added = False
    for name, sql in migrations.items():
        if name not in columns:
            conn.execute(sql)
            added = True
    if not added:
        return

    conn.execute(
        """
        UPDATE import_batches
        SET
            registered_count = (
                SELECT COUNT(*) FROM invoices WHERE invoices.import_batch_id = import_batches.id
            ),
            pdf_count = (
                SELECT COUNT(*)
                FROM invoice_files
                JOIN invoices ON invoices.id = invoice_files.invoice_id
                WHERE invoices.import_batch_id = import_batches.id
            ),
            error_count = (
                SELECT COUNT(*) FROM import_errors WHERE import_errors.import_batch_id = import_batches.id
            ),
            status = 'completed',
            completed_at = COALESCE(completed_at, imported_at)
        """
    )


def _migrate_invoices_table(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()]
    forbidden_columns = {"status", "budget_category_id", "memo"}
    if "local_memo" in columns and not (forbidden_columns & set(columns)):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE invoices RENAME TO invoices_old")
    conn.execute(
        """
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id INTEGER,
            external_id TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            vendor_id INTEGER NOT NULL,
            contact_id INTEGER,
            invoice_date TEXT NOT NULL,
            billing_month TEXT NOT NULL,
            billing_month_manual_override INTEGER NOT NULL DEFAULT 0,
            total_amount INTEGER NOT NULL,
            local_memo TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(vendor_id) REFERENCES vendors(id),
            FOREIGN KEY(contact_id) REFERENCES vendor_contacts(id),
            UNIQUE(external_id)
        )
        """
    )
    memo_source = "memo" if "memo" in columns else "NULL"
    manual_override_source = "billing_month_manual_override" if "billing_month_manual_override" in columns else "0"
    conn.execute(
        f"""
        INSERT INTO invoices (
            id, import_batch_id, external_id, project_id, vendor_id, contact_id,
            invoice_date, billing_month, billing_month_manual_override,
            total_amount, local_memo, created_at, updated_at
        )
        SELECT
            id, import_batch_id, external_id, project_id, vendor_id, contact_id,
            invoice_date, billing_month, {manual_override_source},
            total_amount, {memo_source}, created_at, updated_at
        FROM invoices_old
        """
    )
    conn.execute("DROP TABLE invoices_old")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_invoice_files_table(conn: sqlite3.Connection) -> None:
    fk_tables = [row["table"] for row in conn.execute("PRAGMA foreign_key_list(invoice_files)").fetchall()]
    if fk_tables == ["invoices"]:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE invoice_files RENAME TO invoice_files_old")
    conn.execute(
        """
        CREATE TABLE invoice_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            original_file_name TEXT NOT NULL,
            stored_file_path TEXT NOT NULL,
            file_type TEXT,
            file_hash TEXT NOT NULL,
            file_size INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id),
            UNIQUE(invoice_id, file_hash)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO invoice_files (
            id, invoice_id, original_file_name, stored_file_path,
            file_type, file_hash, file_size, created_at
        )
        SELECT
            id, invoice_id, original_file_name, stored_file_path,
            file_type, file_hash, file_size, created_at
        FROM invoice_files_old
        """
    )
    conn.execute("DROP TABLE invoice_files_old")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_pdf_marks_table(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(pdf_marks)").fetchall()]
    add_columns = []
    if "x_pt" not in columns:
        add_columns.append("ALTER TABLE pdf_marks ADD COLUMN x_pt REAL")
    if "y_pt" not in columns:
        add_columns.append("ALTER TABLE pdf_marks ADD COLUMN y_pt REAL")
    if "page_width_pt" not in columns:
        add_columns.append("ALTER TABLE pdf_marks ADD COLUMN page_width_pt REAL")
    if "page_height_pt" not in columns:
        add_columns.append("ALTER TABLE pdf_marks ADD COLUMN page_height_pt REAL")
    for sql in add_columns:
        conn.execute(sql)


def _migrate_projects_table(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()]
    if "is_visible" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN is_visible INTEGER NOT NULL DEFAULT 1")


def _migrate_invoice_billing_month_override(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()]
    if "billing_month_manual_override" in columns:
        return

    conn.execute(
        "ALTER TABLE invoices ADD COLUMN billing_month_manual_override INTEGER NOT NULL DEFAULT 0"
    )
    from invoice_manager.utils.date_utils import billing_month_from_invoice_date

    updates = []
    for row in conn.execute("SELECT id, invoice_date, billing_month FROM invoices").fetchall():
        try:
            automatic_month = billing_month_from_invoice_date(row["invoice_date"])
            is_manual = int((row["billing_month"] or "").strip() != automatic_month)
        except Exception:
            is_manual = 1
        updates.append((is_manual, int(row["id"])))
    if updates:
        conn.executemany(
            "UPDATE invoices SET billing_month_manual_override = ? WHERE id = ?",
            updates,
        )


def _migrate_allocation_tax_rate(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(invoice_allocations)")}
    if "tax_rate" not in columns:
        conn.execute("ALTER TABLE invoice_allocations ADD COLUMN tax_rate TEXT NOT NULL DEFAULT '10' CHECK(tax_rate IN ('10', '8', 'exempt'))")


def _migrate_tax_excluded_amounts(conn: sqlite3.Connection) -> None:
    invoice_columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()]
    if "total_amount_excluded" not in invoice_columns:
        conn.execute("ALTER TABLE invoices ADD COLUMN total_amount_excluded INTEGER")
    conn.execute(
        """
        UPDATE invoices
        SET total_amount_excluded = CASE
            WHEN total_amount < 0 THEN -((ABS(total_amount) * 10) / 11)
            ELSE (total_amount * 10) / 11
        END
        WHERE total_amount_excluded IS NULL
        """
    )

    allocation_columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoice_allocations)").fetchall()]
    if "amount_excluded" not in allocation_columns:
        conn.execute("ALTER TABLE invoice_allocations ADD COLUMN amount_excluded INTEGER")
    conn.execute(
        """
        UPDATE invoice_allocations
        SET amount_excluded = CASE
            WHEN amount < 0 THEN -((ABS(amount) * 10) / 11)
            ELSE (amount * 10) / 11
        END
        WHERE amount_excluded IS NULL
        """
    )
