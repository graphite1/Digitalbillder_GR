from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from invoice_manager.db import DATA_DIR, DB_PATH, initialize_database
from invoice_manager.utils.money_utils import format_amount
from invoice_manager.version import APP_VERSION


UPDATE_HEALTH_FILE_ENV = "DIGITALBUILDER_UPDATE_HEALTH_FILE"
UPDATE_HEALTH_NONCE_ENV = "DIGITALBUILDER_UPDATE_HEALTH_NONCE"
DATA_DIR_ENV = "DIGITALBUILDER_DATA_DIR"
INSTALL_ROOT_ENV = "DIGITALBUILDER_INSTALL_ROOT"
HEALTH_REQUIRED_TABLES = frozenset(
    {"projects", "vendors", "invoices", "invoice_files", "invoice_allocations", "app_settings"}
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="請求書管理アプリ")
    parser.add_argument("--init-db", action="store_true", help="SQLite DBを初期化します")
    parser.add_argument(
        "--update-health-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--new-invoices", action="store_true", help="Digital Billder新着取込画面を開きます")
    parser.add_argument("--preview", action="store_true", help="CSV + zip取込プレビューを表示します")
    parser.add_argument("--import", dest="do_import", action="store_true", help="CSV + zipをDBへ取り込みます")
    parser.add_argument("--export", action="store_true", help="月別請求一覧をExcel出力します")
    parser.add_argument("--csv", dest="csv_path", help="請求一覧CSVパス")
    parser.add_argument("--zip", dest="zip_path", help="請求書PDF zipパス")
    parser.add_argument("--month", default="", help="請求月")
    parser.add_argument("--memo", default="", help="取込メモ")
    return parser


def _validate_update_health() -> tuple[Path, str]:
    """Validate a staged release against the existing data without writing the database."""
    health_file_text = os.environ.get(UPDATE_HEALTH_FILE_ENV, "").strip()
    nonce = os.environ.get(UPDATE_HEALTH_NONCE_ENV, "").strip()
    configured_data_text = os.environ.get(DATA_DIR_ENV, "").strip()
    install_root_text = os.environ.get(INSTALL_ROOT_ENV, "").strip()
    if not health_file_text or not nonce or not configured_data_text or not install_root_text:
        raise RuntimeError("更新確認用の起動環境が不足しています。")

    configured_data = Path(configured_data_text).expanduser().resolve()
    if configured_data != DATA_DIR.resolve() or not configured_data.is_dir():
        raise RuntimeError("更新後のデータ保存先を確認できません。")
    if DB_PATH.resolve() != configured_data / "app.db" or not DB_PATH.is_file():
        raise RuntimeError("更新後に既存データベースを参照できません。")

    database_uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("データベースの整合性を確認できません。")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not HEALTH_REQUIRED_TABLES <= tables:
            raise RuntimeError("更新後に必要なデータベース構成を確認できません。")

    # Import the normal entry modules and construct the hidden management window.
    from invoice_manager import repositories  # noqa: F401
    from invoice_manager.ui.main_window import MainWindow

    import tkinter as tk

    root = None
    try:
        try:
            from tkinterdnd2 import TkinterDnD

            root = TkinterDnD.Tk()
        except Exception:
            root = tk.Tk()
        root.withdraw()
        MainWindow(root)
        root.update_idletasks()
    finally:
        if root is not None:
            root.destroy()

    install_root = Path(install_root_text).expanduser().resolve()
    health_file = Path(health_file_text).expanduser().resolve()
    if not install_root.is_dir() or health_file.parent != install_root / ".updates" / "health":
        raise RuntimeError("更新確認結果の保存先を確認できません。")
    if not health_file.parent.is_dir():
        raise RuntimeError("更新確認結果の保存先を確認できません。")
    return health_file, nonce


def _write_update_health_marker(health_file: Path, nonce: str) -> None:
    marker = {"schema": 1, "nonce": nonce, "version": APP_VERSION}
    temporary = health_file.with_name(f".{health_file.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(marker, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, health_file)
    finally:
        temporary.unlink(missing_ok=True)


def run_update_health_check() -> None:
    health_file, nonce = _validate_update_health()
    _write_update_health_marker(health_file, nonce)


def _require(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"{name} を指定してください")
    return value


def print_preview(preview) -> None:
    print(f"CSV件数: {preview.csv_count}")
    print(f"zip内IDフォルダ数: {preview.zip_id_count}")
    print(f"CSVとzipの一致件数: {preview.matched_count}")
    print(f"CSVのみ存在するID数: {preview.csv_only_count}")
    print(f"zipのみ存在するID数: {preview.zip_only_count}")
    print(f"新規登録件数: {preview.new_count}")
    print(f"既存スキップ件数: {preview.existing_skip_count}")
    print(f"更新候補件数: {preview.update_candidate_count}")
    print(f"重複候補件数: {preview.duplicate_candidate_count}")
    print(f"アーカイブ工事スキップ件数: {preview.archived_skip_count}")
    print(f"エラー件数: {preview.error_count}")
    print(f"請求金額合計(税抜): {format_amount(preview.total_amount)}")
    print(f"PDFファイル総数: {preview.pdf_file_count}")
    print("工事別合計(税抜):")
    for name, amount in preview.project_totals.items():
        print(f"  {name}: {format_amount(amount)}")
    print("取引先別合計(税抜):")
    for name, amount in preview.vendor_totals.items():
        print(f"  {name}: {format_amount(amount)}")
    if preview.warnings:
        print("警告:")
        for warning in preview.warnings:
            print(f"  {warning}")
    if preview.errors:
        print("エラー:")
        for error in preview.errors:
            row = f"{error.row_number}: " if error.row_number else ""
            print(f"  {row}{error.error_type} - {error.message}")


def main() -> int | None:
    parser = build_parser()
    args = parser.parse_args()

    if args.update_health_check:
        run_update_health_check()
        return

    if args.new_invoices:
        import tkinter as tk
        from invoice_manager.ui.digital_billder_sync_window import DigitalBillderSyncWindow

        initialize_database()
        root = tk.Tk()
        root.withdraw()
        DigitalBillderSyncWindow(root, on_close=root.destroy)
        root.mainloop()
        return

    if args.init_db:
        initialize_database()
        print(f"DBを初期化しました: {DB_PATH}")
        return

    if args.preview:
        from invoice_manager.services.import_service import preview_import

        csv_path = Path(_require(args.csv_path, "--csv"))
        zip_path = Path(_require(args.zip_path, "--zip"))
        month = args.month
        initialize_database()
        print_preview(preview_import(csv_path, zip_path, month))
        return

    if args.do_import:
        from invoice_manager.services.import_service import execute_import

        csv_path = Path(_require(args.csv_path, "--csv"))
        zip_path = Path(_require(args.zip_path, "--zip"))
        month = args.month
        initialize_database()
        result = execute_import(csv_path, zip_path, month, args.memo)
        print_preview(result.preview)
        print(f"登録件数: {result.inserted_count}")
        print(f"添付ファイル登録件数: {result.file_count}")
        print(f"取込バッチID: {result.import_batch_id}")
        return

    if args.export:
        from invoice_manager.services.export_excel import export_monthly_invoice_list

        month = _require(args.month, "--month")
        initialize_database()
        output_path = export_monthly_invoice_list(month)
        print(f"Excel出力しました: {output_path}")
        return

    initialize_database()
    from invoice_manager.ui.main_window import run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
