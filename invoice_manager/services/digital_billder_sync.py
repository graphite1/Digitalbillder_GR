"""Keep first-seen candidates separate from imported / intentionally excluded IDs."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from invoice_manager import db
from invoice_manager.models import InvoiceCsvRow
from invoice_manager.services.csv_reader import REQUIRED_COLUMNS, read_invoice_csv
from invoice_manager.services.digital_billder_download import download_csv, export_session
from invoice_manager.services.selected_invoice_download import download_selected_zip
from invoice_manager.services.import_service import execute_import, preview_import
from invoice_manager.services.operation_cancellation import check_cancelled, begin_commit

SYNC_LOCK = Lock()


def initialize_sync() -> None:
    with db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS digital_billder_seen (
                external_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('pending', 'excluded', 'imported')),
                row_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                is_available INTEGER NOT NULL DEFAULT 1
            )
        """)


def validate_rows(path: Path) -> list[InvoiceCsvRow]:
    rows, errors, _ = read_invoice_csv(path)
    if errors:
        raise ValueError(f"CSVに読み取りエラーが{len(errors)}件あります。未確認状態は変更していません。")
    ids = [row.external_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("CSV内で請求書IDが重複しています。取込を中止しました。")
    return rows


def remember_candidates(rows: list[InvoiceCsvRow]) -> None:
    begin_commit()
    initialize_sync()
    now = datetime.now().isoformat(timespec="seconds")
    with db.get_connection() as conn:
        # Existing local invoices (including older manual imports) are already known.
        existing = {r[0] for r in conn.execute("SELECT external_id FROM invoices")}
        conn.execute("UPDATE digital_billder_seen SET is_available=0")
        for row in rows:
            state = "imported" if row.external_id in existing else "pending"
            conn.execute("""
                INSERT INTO digital_billder_seen
                    (external_id, state, row_json, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    row_json=excluded.row_json, updated_at=excluded.updated_at, is_available=1,
                    state=CASE WHEN excluded.state='imported' THEN 'imported'
                               ELSE digital_billder_seen.state END
            """, (row.external_id, state, json.dumps(asdict(row), ensure_ascii=False), now, now))


def list_candidates(state: str = "pending") -> list[InvoiceCsvRow]:
    initialize_sync()
    with db.get_connection() as conn:
        rows = conn.execute("""
            SELECT row_json FROM digital_billder_seen
            WHERE state=? AND (is_available=1 OR state='excluded')
                AND external_id NOT IN (SELECT external_id FROM invoices)
            ORDER BY first_seen_at, external_id
        """, (state,)).fetchall()
    return [InvoiceCsvRow(**json.loads(row[0])) for row in rows]


def set_excluded(ids: set[str], excluded: bool, reason: str = "誤請求・取込対象外") -> None:
    initialize_sync()
    with db.get_connection() as conn:
        conn.executemany("""
            UPDATE digital_billder_seen SET state=?, reason=?, updated_at=?
            WHERE external_id=? AND state=?
        """, [(
            "excluded" if excluded else "pending", reason if excluded else "",
            datetime.now().isoformat(timespec="seconds"), invoice_id,
            "pending" if excluded else "excluded",
        ) for invoice_id in ids])


def write_selected_csv(path: Path, rows: list[InvoiceCsvRow]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(row.raw_data for row in rows)


def fetch_candidates(progress=lambda text: None) -> int:
    check_cancelled()
    if not SYNC_LOCK.acquire(blocking=False):
        raise ValueError("別の自動取得を実行中です。")
    try:
        db.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="billder-", dir=db.DATA_DIR) as folder:
            with export_session(progress) as page:
                progress("CSVを取得しています…")
                path = download_csv(page, Path(folder) / "invoices.csv")
                check_cancelled()
                rows = validate_rows(path) if path else []
            begin_commit()
            remember_candidates(rows)
            return len(list_candidates())
    finally:
        SYNC_LOCK.release()


def import_selected(ids: set[str], progress=lambda text: None):
    check_cancelled()
    if not ids:
        raise ValueError("取り込む請求を選択してください。")
    if not SYNC_LOCK.acquire(blocking=False):
        raise ValueError("別の自動取得を実行中です。")
    try:
        with TemporaryDirectory(prefix="billder-", dir=db.DATA_DIR) as folder:
            folder = Path(folder)
            with export_session(progress) as page:
                progress("選択した請求が現在も有効か確認しています…")
                csv_path = download_csv(page, folder / "current.csv")
                check_cancelled()
                current = validate_rows(csv_path) if csv_path else []
                current_by_id = {row.external_id: row for row in current}
                pending_by_id = {row.external_id: row for row in list_candidates()}
                if not ids <= current_by_id.keys() or not ids <= pending_by_id.keys():
                    raise ValueError("選択した請求に破棄済み・対象外・処理済みが含まれます。新着確認をやり直してください。")
                for invoice_id in ids:
                    check_cancelled()
                    if current_by_id[invoice_id].raw_data != pending_by_id[invoice_id].raw_data:
                        raise ValueError("確認後に請求内容が変わりました。新着確認をやり直してください。")
                progress("選択した未取込請求のPDFを取得しています…")
                zip_path = download_selected_zip(page, folder / "invoices.zip", current, ids, progress)
                check_cancelled()
            selected_csv = folder / "selected.csv"
            write_selected_csv(selected_csv, [current_by_id[i] for i in sorted(ids)])
            preview = preview_import(selected_csv, zip_path, "")
            check_cancelled()
            missing_pdf = ids - set(preview.zip_index.pdf_by_id)
            if preview.errors or missing_pdf:
                raise ValueError("選択した請求のCSV・PDFが揃っていません。未確認のまま残します。")
            if set(preview.zip_index.pdf_by_id) != ids:
                raise ValueError("選択していない請求のPDFが含まれています。取込は行いません。")
            if preview.new_count != len(ids):
                raise ValueError("選択した請求に重複候補・アーカイブ工事があります。手動取込で内容を確認してください。")
            progress("選択した請求を台帳に登録しています…")
            begin_commit()
            with db.atomic_transaction():
                result = execute_import(selected_csv, zip_path, "", "Digital Billder新着取込", prepared_preview=preview)
                with db.get_connection() as conn:
                    conn.executemany("UPDATE digital_billder_seen SET state='imported' WHERE external_id=?", [(i,) for i in ids])
            return result
    finally:
        SYNC_LOCK.release()
