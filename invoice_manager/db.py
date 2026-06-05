from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"


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
    memo TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    project_name TEXT NOT NULL,
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
    total_amount INTEGER NOT NULL,
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
    memo TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
    FOREIGN KEY(work_type_code_id) REFERENCES work_type_codes(id)
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
"""


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_invoices_table(conn)
        _migrate_invoice_files_table(conn)
        conn.execute("DROP TABLE IF EXISTS budget_categories")


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
    conn.execute(
        f"""
        INSERT INTO invoices (
            id, import_batch_id, external_id, project_id, vendor_id, contact_id,
            invoice_date, billing_month, total_amount, local_memo, created_at, updated_at
        )
        SELECT
            id, import_batch_id, external_id, project_id, vendor_id, contact_id,
            invoice_date, billing_month, total_amount, {memo_source}, created_at, updated_at
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
