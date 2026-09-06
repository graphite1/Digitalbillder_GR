from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Literal

from invoice_manager import db


ACTUAL_SOURCE = "web_archived_actual"
PLANNED_SOURCE = "local_allocation_planned"
SUPPORTED_TAX_RATES = frozenset({"10", "8", "exempt"})


def _required_text(value: object, label: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{label}は必須です。")
    return text


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}は整数で指定してください。")
    return value


def _optional_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class ArchivedAllocationSnapshot:
    work_type_code: str
    work_type_name: str
    net_amount: int
    tax_rate: str | int
    tax_amount: int
    gross_amount: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_type_code", _required_text(self.work_type_code, "工種コード"))
        object.__setattr__(self, "work_type_name", _required_text(self.work_type_name, "工種名"))
        object.__setattr__(self, "net_amount", _integer(self.net_amount, "税抜金額"))
        object.__setattr__(self, "tax_amount", _integer(self.tax_amount, "税額"))
        object.__setattr__(self, "gross_amount", _integer(self.gross_amount, "税込金額"))
        normalized_rate = str(self.tax_rate).strip().lower()
        if normalized_rate not in SUPPORTED_TAX_RATES:
            raise ValueError("税率は 10、8、exempt のいずれかで指定してください。")
        object.__setattr__(self, "tax_rate", normalized_rate)
        if self.gross_amount != self.net_amount + self.tax_amount:
            raise ValueError("振分行の税込金額が税抜金額と税額の合計に一致しません。")
        if normalized_rate == "exempt" and self.tax_amount != 0:
            raise ValueError("非課税行の税額は0円で指定してください。")


@dataclass(frozen=True, slots=True)
class ArchivedInvoiceSnapshot:
    external_id: str
    project_code: str
    project_name: str
    vendor_name: str
    invoice_date: str | date
    gross_invoice_total: int
    status: str
    allocations: tuple[ArchivedAllocationSnapshot, ...] | list[ArchivedAllocationSnapshot]

    def __post_init__(self) -> None:
        object.__setattr__(self, "external_id", _required_text(self.external_id, "請求書ID"))
        object.__setattr__(self, "project_code", _required_text(self.project_code, "工事コード"))
        object.__setattr__(self, "project_name", _required_text(self.project_name, "工事名"))
        object.__setattr__(self, "vendor_name", _required_text(self.vendor_name, "取引先名"))
        invoice_date = self.invoice_date.isoformat() if isinstance(self.invoice_date, date) else str(self.invoice_date).strip()
        try:
            normalized_date = date.fromisoformat(invoice_date).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("請求日は YYYY-MM-DD 形式で指定してください。") from exc
        object.__setattr__(self, "invoice_date", normalized_date)
        object.__setattr__(self, "gross_invoice_total", _integer(self.gross_invoice_total, "請求金額(税込)"))
        normalized_status = _required_text(self.status, "保管状態").lower()
        if normalized_status != "archived":
            raise ValueError("保管済み（archived）の請求書だけを履歴へ登録できます。")
        object.__setattr__(self, "status", normalized_status)
        allocations = tuple(self.allocations)
        if not allocations:
            raise ValueError("査定入力の振分行がありません。")
        if any(not isinstance(item, ArchivedAllocationSnapshot) for item in allocations):
            raise TypeError("allocationsにはArchivedAllocationSnapshotを指定してください。")
        object.__setattr__(self, "allocations", allocations)


@dataclass(frozen=True, slots=True)
class HistoricalUpsertResult:
    invoice_id: int
    created: bool
    allocation_count: int


@dataclass(frozen=True, slots=True)
class HistoricalReconcileResult:
    active_invoice_count: int
    inserted_invoice_count: int
    updated_invoice_count: int
    deactivated_invoice_count: int
    refreshed_at: str


@dataclass(frozen=True, slots=True)
class HistoricalSyncStatus:
    last_successful_refresh: str | None
    active_invoice_count: int


@dataclass(frozen=True, slots=True)
class HistoricalWorkTypeSuggestion:
    work_type_code: str
    work_type_name: str
    invoice_count: int
    allocation_line_count: int
    net_amount: int
    gross_amount: int


@dataclass(frozen=True, slots=True)
class CostSummaryRow:
    source: Literal["web_archived_actual", "local_allocation_planned"]
    project_code: str
    project_name: str
    vendor_name: str
    work_type_code: str
    work_type_name: str
    invoice_count: int
    allocation_line_count: int
    net_amount: int
    gross_amount: int


@dataclass(frozen=True, slots=True)
class HistoricalCostFilterOptions:
    projects: tuple[tuple[str, str], ...]
    vendors: tuple[str, ...]
    work_types: tuple[tuple[str, str], ...]


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS historical_archived_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL DEFAULT 'digital_billder',
        external_id TEXT NOT NULL,
        project_code TEXT NOT NULL,
        project_name TEXT NOT NULL,
        vendor_name TEXT NOT NULL,
        invoice_date TEXT NOT NULL,
        gross_invoice_total INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status = 'archived'),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
        unavailable_at TEXT,
        fetched_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_archived_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        historical_invoice_id INTEGER NOT NULL,
        line_number INTEGER NOT NULL,
        work_type_code TEXT NOT NULL,
        work_type_name TEXT NOT NULL,
        net_amount INTEGER NOT NULL,
        tax_rate TEXT NOT NULL CHECK(tax_rate IN ('10', '8', 'exempt')),
        tax_amount INTEGER NOT NULL,
        gross_amount INTEGER NOT NULL,
        FOREIGN KEY(historical_invoice_id) REFERENCES historical_archived_invoices(id) ON DELETE CASCADE,
        UNIQUE(historical_invoice_id, line_number)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_historical_invoice_vendor_project ON historical_archived_invoices(vendor_name, project_code)",
    "CREATE INDEX IF NOT EXISTS idx_historical_allocation_code ON historical_archived_allocations(work_type_code)",
    """
    CREATE TABLE IF NOT EXISTS historical_archive_sync_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        last_successful_refresh TEXT,
        active_invoice_count INTEGER NOT NULL DEFAULT 0
    )
    """,
)


def _ensure_schema(connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(historical_archived_invoices)").fetchall()
    }
    if "is_active" not in columns:
        connection.execute(
            "ALTER TABLE historical_archived_invoices ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    if "unavailable_at" not in columns:
        connection.execute("ALTER TABLE historical_archived_invoices ADD COLUMN unavailable_at TEXT")


def initialize_historical_costs() -> None:
    """Create the history tables without changing the application's existing tables."""
    with db.get_connection() as connection:
        _ensure_schema(connection)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_archived_invoice(snapshot: ArchivedInvoiceSnapshot) -> HistoricalUpsertResult:
    """Atomically insert or replace one verified Digital Billder archive snapshot."""
    if not isinstance(snapshot, ArchivedInvoiceSnapshot):
        raise TypeError("snapshotにはArchivedInvoiceSnapshotを指定してください。")
    timestamp = _utc_now_text()
    with db.get_connection() as connection:
        _ensure_schema(connection)
        connection.execute("SAVEPOINT historical_invoice_upsert")
        try:
            result = _upsert_with_connection(connection, snapshot, timestamp)
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT historical_invoice_upsert")
            connection.execute("RELEASE SAVEPOINT historical_invoice_upsert")
            raise
        else:
            connection.execute("RELEASE SAVEPOINT historical_invoice_upsert")
    return result


def _upsert_with_connection(connection, snapshot: ArchivedInvoiceSnapshot, timestamp: str) -> HistoricalUpsertResult:
    existing = connection.execute(
        "SELECT id FROM historical_archived_invoices WHERE source = 'digital_billder' AND external_id = ?",
        (snapshot.external_id,),
    ).fetchone()
    created = existing is None
    if created:
        cursor = connection.execute(
            """
            INSERT INTO historical_archived_invoices (
                source, external_id, project_code, project_name, vendor_name,
                invoice_date, gross_invoice_total, status, is_active, unavailable_at,
                fetched_at, updated_at
            ) VALUES ('digital_billder', ?, ?, ?, ?, ?, ?, 'archived', 1, NULL, ?, ?)
            """,
            (
                snapshot.external_id,
                snapshot.project_code,
                snapshot.project_name,
                snapshot.vendor_name,
                snapshot.invoice_date,
                snapshot.gross_invoice_total,
                timestamp,
                timestamp,
            ),
        )
        invoice_id = int(cursor.lastrowid)
    else:
        invoice_id = int(existing["id"])
        connection.execute(
            """
            UPDATE historical_archived_invoices
            SET project_code = ?, project_name = ?, vendor_name = ?, invoice_date = ?,
                gross_invoice_total = ?, status = 'archived', is_active = 1,
                unavailable_at = NULL, fetched_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                snapshot.project_code,
                snapshot.project_name,
                snapshot.vendor_name,
                snapshot.invoice_date,
                snapshot.gross_invoice_total,
                timestamp,
                timestamp,
                invoice_id,
            ),
        )
        connection.execute(
            "DELETE FROM historical_archived_allocations WHERE historical_invoice_id = ?",
            (invoice_id,),
        )
    connection.executemany(
        """
        INSERT INTO historical_archived_allocations (
            historical_invoice_id, line_number, work_type_code, work_type_name,
            net_amount, tax_rate, tax_amount, gross_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                invoice_id,
                line_number,
                line.work_type_code,
                line.work_type_name,
                line.net_amount,
                line.tax_rate,
                line.tax_amount,
                line.gross_amount,
            )
            for line_number, line in enumerate(snapshot.allocations, start=1)
        ],
    )
    return HistoricalUpsertResult(invoice_id, created, len(snapshot.allocations))


def upsert_archived_invoices(snapshots: Iterable[ArchivedInvoiceSnapshot]) -> list[HistoricalUpsertResult]:
    """Convenience wrapper for readers that yield one verified snapshot at a time."""
    return [upsert_archived_invoice(snapshot) for snapshot in snapshots]


def replace_active_archived_snapshots(
    snapshots: Iterable[ArchivedInvoiceSnapshot],
) -> HistoricalReconcileResult:
    """Atomically publish one complete archive scan and deactivate missing prior records.

    The iterable is fully materialized and verified before the transaction begins. Duplicate
    external IDs are rejected because a complete scan must have one authoritative snapshot per
    invoice. Records absent from a successful scan remain stored for audit but stop contributing
    to actuals and suggestions.
    """
    verified = tuple(snapshots)
    if any(not isinstance(item, ArchivedInvoiceSnapshot) for item in verified):
        raise TypeError("snapshotsにはArchivedInvoiceSnapshotを指定してください。")
    external_ids = [item.external_id for item in verified]
    if len(external_ids) != len(set(external_ids)):
        raise ValueError("完全走査の結果に同じ請求書IDが重複しています。")
    timestamp = _utc_now_text()
    with db.get_connection() as connection:
        _ensure_schema(connection)
        connection.execute("SAVEPOINT historical_archive_reconcile")
        try:
            results = [_upsert_with_connection(connection, item, timestamp) for item in verified]
            if external_ids:
                placeholders = ", ".join("?" for _ in external_ids)
                cursor = connection.execute(
                    f"""
                    UPDATE historical_archived_invoices
                    SET is_active = 0, unavailable_at = ?, updated_at = ?
                    WHERE source = 'digital_billder' AND is_active = 1
                      AND external_id NOT IN ({placeholders})
                    """,
                    (timestamp, timestamp, *external_ids),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE historical_archived_invoices
                    SET is_active = 0, unavailable_at = ?, updated_at = ?
                    WHERE source = 'digital_billder' AND is_active = 1
                    """,
                    (timestamp, timestamp),
                )
            connection.execute(
                """
                INSERT INTO historical_archive_sync_state (id, last_successful_refresh, active_invoice_count)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_successful_refresh = excluded.last_successful_refresh,
                    active_invoice_count = excluded.active_invoice_count
                """,
                (timestamp, len(verified)),
            )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT historical_archive_reconcile")
            connection.execute("RELEASE SAVEPOINT historical_archive_reconcile")
            raise
        else:
            connection.execute("RELEASE SAVEPOINT historical_archive_reconcile")
    return HistoricalReconcileResult(
        active_invoice_count=len(verified),
        inserted_invoice_count=sum(1 for result in results if result.created),
        updated_invoice_count=sum(1 for result in results if not result.created),
        deactivated_invoice_count=int(cursor.rowcount),
        refreshed_at=timestamp,
    )


def get_historical_sync_status() -> HistoricalSyncStatus:
    with db.get_connection() as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT last_successful_refresh, active_invoice_count FROM historical_archive_sync_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return HistoricalSyncStatus(None, 0)
    return HistoricalSyncStatus(
        last_successful_refresh=str(row["last_successful_refresh"]) if row["last_successful_refresh"] else None,
        active_invoice_count=int(row["active_invoice_count"]),
    )


def load_active_archived_snapshots() -> dict[str, ArchivedInvoiceSnapshot]:
    """Load the active Web cache as verified snapshots keyed by external invoice ID.

    This is a single-query, read-only cache API for incremental archive scans.  Inactive
    records are deliberately excluded so a re-archived invoice is fetched from the Web
    again. Allocation order and the Web-provided tax components are restored unchanged.
    """
    with db.get_connection() as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT
                i.id AS historical_invoice_id,
                i.external_id,
                i.project_code,
                i.project_name,
                i.vendor_name,
                i.invoice_date,
                i.gross_invoice_total,
                a.id AS allocation_id,
                a.line_number,
                a.work_type_code,
                a.work_type_name,
                a.net_amount,
                a.tax_rate,
                a.tax_amount,
                a.gross_amount
            FROM historical_archived_invoices AS i
            LEFT JOIN historical_archived_allocations AS a
              ON a.historical_invoice_id = i.id
            WHERE i.source = 'digital_billder'
              AND i.status = 'archived'
              AND i.is_active = 1
            ORDER BY i.external_id, a.line_number, a.id
            """
        ).fetchall()

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        external_id = str(row["external_id"])
        item = grouped.setdefault(
            external_id,
            {
                "project_code": str(row["project_code"]),
                "project_name": str(row["project_name"]),
                "vendor_name": str(row["vendor_name"]),
                "invoice_date": str(row["invoice_date"]),
                "gross_invoice_total": int(row["gross_invoice_total"]),
                "allocations": [],
            },
        )
        if row["allocation_id"] is None:
            raise RuntimeError(f"有効な保管済み履歴に査定行がありません: {external_id}")
        item["allocations"].append(
            ArchivedAllocationSnapshot(
                work_type_code=str(row["work_type_code"]),
                work_type_name=str(row["work_type_name"]),
                net_amount=int(row["net_amount"]),
                tax_rate=str(row["tax_rate"]),
                tax_amount=int(row["tax_amount"]),
                gross_amount=int(row["gross_amount"]),
            )
        )

    return {
        external_id: ArchivedInvoiceSnapshot(
            external_id=external_id,
            project_code=str(item["project_code"]),
            project_name=str(item["project_name"]),
            vendor_name=str(item["vendor_name"]),
            invoice_date=str(item["invoice_date"]),
            gross_invoice_total=int(item["gross_invoice_total"]),
            status="archived",
            allocations=tuple(item["allocations"]),
        )
        for external_id, item in grouped.items()
    }


def has_historical_costs() -> bool:
    with db.get_connection() as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT 1 FROM historical_archived_invoices WHERE status = 'archived' AND is_active = 1 LIMIT 1"
        ).fetchone()
    return row is not None


def list_historical_work_type_suggestions(
    vendor_name: str,
    project_code: str | None = None,
    limit: int | None = None,
) -> list[HistoricalWorkTypeSuggestion]:
    """Rank learned codes by invoice frequency, then tax-excluded historical spend."""
    normalized_vendor = _required_text(vendor_name, "取引先名")
    normalized_project = _optional_filter(project_code)
    if limit is not None and limit < 0:
        raise ValueError("limitは0以上で指定してください。")
    where = ["i.status = 'archived'", "i.is_active = 1", "i.vendor_name = ?"]
    params: list[object] = [normalized_vendor]
    if normalized_project is not None:
        where.append("i.project_code = ?")
        params.append(normalized_project)
    with db.get_connection() as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""
            SELECT
                a.work_type_code,
                a.work_type_name,
                i.id AS invoice_id,
                i.invoice_date,
                i.id AS history_order,
                a.line_number,
                a.net_amount,
                a.gross_amount
            FROM historical_archived_allocations AS a
            JOIN historical_archived_invoices AS i ON i.id = a.historical_invoice_id
            WHERE {' AND '.join(where)}
            ORDER BY i.invoice_date DESC, i.id DESC, a.line_number DESC
            """,
            params,
        ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        code = str(row["work_type_code"])
        item = grouped.setdefault(
            code,
            {
                "name": str(row["work_type_name"]),
                "invoice_ids": set(),
                "line_count": 0,
                "net": 0,
                "gross": 0,
            },
        )
        item["invoice_ids"].add(int(row["invoice_id"]))
        item["line_count"] += 1
        item["net"] += int(row["net_amount"])
        item["gross"] += int(row["gross_amount"])
    suggestions = [
        HistoricalWorkTypeSuggestion(
            work_type_code=code,
            work_type_name=str(item["name"]),
            invoice_count=len(item["invoice_ids"]),
            allocation_line_count=int(item["line_count"]),
            net_amount=int(item["net"]),
            gross_amount=int(item["gross"]),
        )
        for code, item in grouped.items()
    ]
    suggestions.sort(key=lambda item: (-item.invoice_count, -item.net_amount, item.work_type_code))
    return suggestions if limit is None else suggestions[:limit]


def _cost_filters(
    project_code: str | None,
    vendor_name: str | None,
    work_type_code: str | None,
    project_column: str,
    vendor_column: str,
    work_type_column: str,
) -> tuple[list[str], list[object]]:
    where: list[str] = []
    params: list[object] = []
    for value, column in (
        (_optional_filter(project_code), project_column),
        (_optional_filter(vendor_name), vendor_column),
        (_optional_filter(work_type_code), work_type_column),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    return where, params


def _project_code_from_local_id(project_id: int) -> str | None:
    if isinstance(project_id, bool) or not isinstance(project_id, int):
        raise ValueError("project_idは整数で指定してください。")
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT project_code FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    return None if row is None else str(row["project_code"])


def list_actual_costs(
    project_id: int | None = None,
    *,
    project_code: str | None = None,
    vendor_name: str | None = None,
    work_type_code: str | None = None,
) -> list[CostSummaryRow]:
    """Return actual costs sourced only from archived Digital Billder appraisal data."""
    if project_id is not None and _optional_filter(project_code) is not None:
        raise ValueError("project_idとproject_codeは同時に指定できません。")
    if project_id is not None:
        project_code = _project_code_from_local_id(project_id)
        if project_code is None:
            return []
    where, params = _cost_filters(
        project_code, vendor_name, work_type_code,
        "i.project_code", "i.vendor_name", "a.work_type_code",
    )
    where.extend(("i.status = 'archived'", "i.is_active = 1"))
    with db.get_connection() as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""
            SELECT
                i.project_code, i.project_name, i.vendor_name,
                a.work_type_code, a.work_type_name,
                COUNT(DISTINCT i.id) AS invoice_count,
                COUNT(a.id) AS allocation_line_count,
                SUM(a.net_amount) AS net_amount,
                SUM(a.gross_amount) AS gross_amount
            FROM historical_archived_allocations AS a
            JOIN historical_archived_invoices AS i ON i.id = a.historical_invoice_id
            WHERE {' AND '.join(where)}
            GROUP BY i.project_code, i.project_name, i.vendor_name, a.work_type_code, a.work_type_name
            ORDER BY i.project_code, i.vendor_name, a.work_type_code, a.work_type_name
            """,
            params,
        ).fetchall()
    return [_cost_row(ACTUAL_SOURCE, row) for row in rows]


def list_planned_costs(
    project_code: str | None = None,
    vendor_name: str | None = None,
    work_type_code: str | None = None,
) -> list[CostSummaryRow]:
    """Return local allocations as planned values, clearly separated from Web actuals."""
    where, params = _cost_filters(
        project_code, vendor_name, work_type_code,
        "p.project_code", "v.vendor_name", "w.code",
    )
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.project_code, p.project_name, v.vendor_name,
                w.code AS work_type_code, w.name AS work_type_name,
                COUNT(DISTINCT i.id) AS invoice_count,
                COUNT(a.id) AS allocation_line_count,
                SUM(COALESCE(a.amount_excluded, 0)) AS net_amount,
                SUM(a.amount) AS gross_amount
            FROM invoice_allocations AS a
            JOIN invoices AS i ON i.id = a.invoice_id
            JOIN projects AS p ON p.id = i.project_id
            JOIN vendors AS v ON v.id = i.vendor_id
            JOIN work_type_codes AS w ON w.id = a.work_type_code_id
            {('WHERE ' + ' AND '.join(where)) if where else ''}
            GROUP BY p.project_code, p.project_name, v.vendor_name, w.code, w.name
            ORDER BY p.project_code, v.vendor_name, w.code, w.name
            """,
            params,
        ).fetchall()
    return [_cost_row(PLANNED_SOURCE, row) for row in rows]


def list_costs(
    project_code: str | None = None,
    vendor_name: str | None = None,
    work_type_code: str | None = None,
    include_planned: bool = False,
) -> list[CostSummaryRow]:
    rows = list_actual_costs(
        project_code=project_code,
        vendor_name=vendor_name,
        work_type_code=work_type_code,
    )
    if include_planned:
        rows.extend(list_planned_costs(project_code, vendor_name, work_type_code))
    return rows


def _cost_row(source: Literal["web_archived_actual", "local_allocation_planned"], row) -> CostSummaryRow:
    return CostSummaryRow(
        source=source,
        project_code=str(row["project_code"]),
        project_name=str(row["project_name"]),
        vendor_name=str(row["vendor_name"]),
        work_type_code=str(row["work_type_code"]),
        work_type_name=str(row["work_type_name"]),
        invoice_count=int(row["invoice_count"]),
        allocation_line_count=int(row["allocation_line_count"]),
        net_amount=int(row["net_amount"] or 0),
        gross_amount=int(row["gross_amount"] or 0),
    )


def list_historical_cost_filter_options() -> HistoricalCostFilterOptions:
    with db.get_connection() as connection:
        _ensure_schema(connection)
        projects = connection.execute(
            """
            SELECT DISTINCT project_code, project_name
            FROM historical_archived_invoices
            WHERE status = 'archived'
              AND is_active = 1
            ORDER BY project_code, project_name
            """
        ).fetchall()
        vendors = connection.execute(
            """
            SELECT DISTINCT vendor_name
            FROM historical_archived_invoices
            WHERE status = 'archived'
              AND is_active = 1
            ORDER BY vendor_name
            """
        ).fetchall()
        work_types = connection.execute(
            """
            SELECT DISTINCT a.work_type_code, a.work_type_name
            FROM historical_archived_allocations AS a
            JOIN historical_archived_invoices AS i ON i.id = a.historical_invoice_id
            WHERE i.status = 'archived'
              AND i.is_active = 1
            ORDER BY a.work_type_code, a.work_type_name
            """
        ).fetchall()
    return HistoricalCostFilterOptions(
        projects=tuple((str(row["project_code"]), str(row["project_name"])) for row in projects),
        vendors=tuple(str(row["vendor_name"]) for row in vendors),
        work_types=tuple((str(row["work_type_code"]), str(row["work_type_name"])) for row in work_types),
    )
