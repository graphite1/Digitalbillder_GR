from __future__ import annotations

import argparse
from pathlib import Path

from invoice_manager.db import DB_PATH, initialize_database
from invoice_manager.services.export_excel import export_monthly_invoice_list
from invoice_manager.services.import_service import execute_import, preview_import
from invoice_manager.ui.main_window import run_app
from invoice_manager.utils.money_utils import format_amount


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="請求書管理アプリ")
    parser.add_argument("--init-db", action="store_true", help="SQLite DBを初期化します")
    parser.add_argument("--preview", action="store_true", help="CSV + zip取込プレビューを表示します")
    parser.add_argument("--import", dest="do_import", action="store_true", help="CSV + zipをDBへ取り込みます")
    parser.add_argument("--export", action="store_true", help="月別請求一覧をExcel出力します")
    parser.add_argument("--csv", dest="csv_path", help="請求一覧CSVパス")
    parser.add_argument("--zip", dest="zip_path", help="請求書PDF zipパス")
    parser.add_argument("--month", default="", help="請求月")
    parser.add_argument("--memo", default="", help="取込メモ")
    return parser


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
    print(f"エラー件数: {preview.error_count}")
    print(f"請求金額合計: {format_amount(preview.total_amount)}")
    print(f"PDFファイル総数: {preview.pdf_file_count}")
    print("工事別合計:")
    for name, amount in preview.project_totals.items():
        print(f"  {name}: {format_amount(amount)}")
    print("取引先別合計:")
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.init_db:
        initialize_database()
        print(f"DBを初期化しました: {DB_PATH}")
        return

    if args.preview:
        csv_path = Path(_require(args.csv_path, "--csv"))
        zip_path = Path(_require(args.zip_path, "--zip"))
        month = args.month
        initialize_database()
        print_preview(preview_import(csv_path, zip_path, month))
        return

    if args.do_import:
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
        month = _require(args.month, "--month")
        initialize_database()
        output_path = export_monthly_invoice_list(month)
        print(f"Excel出力しました: {output_path}")
        return

    initialize_database()
    run_app()


if __name__ == "__main__":
    main()
