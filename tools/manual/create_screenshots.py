from __future__ import annotations

import hashlib
import shutil
import sys
import time
import tkinter as tk
from pathlib import Path

import fitz
from PIL import ImageGrab


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
WORK_DIR = REPO_ROOT / "build" / "manual_work"
DATA_DIR = WORK_DIR / "data"
SCREENSHOT_DIR = WORK_DIR / "screenshots"


def prepare_demo_data() -> tuple[int, list[int]]:
    import invoice_manager.db as db

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    db.DATA_DIR = DATA_DIR
    db.DB_PATH = DATA_DIR / "app.db"
    db.initialize_database()

    from invoice_manager.models import InvoiceCsvRow
    from invoice_manager.repositories import (
        create_import_batch,
        create_pdf_mark,
        ensure_work_type_codes_for_project,
        get_connection,
        insert_invoice,
        insert_invoice_file,
        list_projects,
        list_work_type_codes,
        save_invoice_allocation,
        set_app_setting,
    )

    set_app_setting("amount_display_mode", "税抜")
    batch_id = create_import_batch(
        "2026-08",
        Path("請求一覧_サンプル.csv"),
        Path("請求書PDF_サンプル.zip"),
        "demo-csv-hash",
        "demo-zip-hash",
        "操作マニュアル用サンプル",
    )
    samples = [
        ("INV-DEMO-001", "GR中央道路改良工事", "GR-2601", "サンプル建設株式会社", "山田", "太郎", "2026-07-25", 1_210_000, "確認済み"),
        ("INV-DEMO-002", "GR中央道路改良工事", "GR-2601", "テスト資材株式会社", "佐藤", "花子", "2026-07-28", 550_000, ""),
        ("INV-DEMO-003", "GR河川護岸整備工事", "GR-2602", "デモ土木株式会社", "鈴木", "一郎", "2026-08-02", 330_000, "振分待ち"),
        ("INV-DEMO-004", "GR橋梁補修工事", "GR-2603", "サンプル運輸株式会社", "高橋", "美咲", "2026-08-05", 1_760_000, "PDF確認"),
        ("INV-DEMO-005", "GR中央道路改良工事", "GR-2601", "テスト設備株式会社", "伊藤", "健", "2026-08-06", 99_999, ""),
    ]
    invoice_ids: list[int] = []
    for index, (external_id, project_name, project_code, vendor_name, last_name, first_name, invoice_date, amount, memo) in enumerate(samples, start=1):
        row = InvoiceCsvRow(
            row_number=index,
            external_id=external_id,
            project_name=project_name,
            project_code=project_code,
            vendor_name=vendor_name,
            last_name=last_name,
            first_name=first_name,
            email=f"demo{index}@example.jp",
            phone=f"000-0000-000{index}",
            invoice_date=invoice_date,
            total_amount=amount,
            raw_data={},
        )
        invoice_id = insert_invoice(row, "2026-08", batch_id)
        invoice_ids.append(invoice_id)
        with get_connection() as conn:
            conn.execute("UPDATE invoices SET local_memo = ? WHERE id = ?", (memo, invoice_id))

    pdf_dir = DATA_DIR / "originals" / "2026" / "08" / "INV-DEMO-001"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / "請求書_サンプル.pdf"
    create_sample_pdf(pdf_path)
    pdf_bytes = pdf_path.read_bytes()
    insert_invoice_file(
        invoice_ids[0],
        pdf_path.name,
        pdf_path,
        "pdf",
        hashlib.sha256(pdf_bytes).hexdigest(),
        len(pdf_bytes),
    )

    project_id = int(next(row["id"] for row in list_projects() if row["project_code"] == "GR-2601"))
    ensure_work_type_codes_for_project(project_id)
    code_ids = {row["code"]: int(row["id"]) for row in list_work_type_codes(project_id, active_only=True)}
    allocation_1 = save_invoice_allocation(invoice_ids[0], code_ids["521"], 770_000, "土工・掘削", 1)
    allocation_2 = save_invoice_allocation(invoice_ids[0], code_ids["524"], 440_000, "仮設材", 2)
    with get_connection() as conn:
        invoice_file_id = int(conn.execute("SELECT id FROM invoice_files WHERE invoice_id = ?", (invoice_ids[0],)).fetchone()["id"])
    create_pdf_mark(invoice_file_id, invoice_ids[0], allocation_1, 1, 0.76, 0.37, 452, 312, 595, 842, "badge", "521")
    create_pdf_mark(invoice_file_id, invoice_ids[0], allocation_2, 1, 0.76, 0.43, 452, 362, 595, 842, "badge", "524")
    return invoice_ids[0], invoice_ids


def create_sample_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(42, 42, 553, 800), color=(0.65, 0.68, 0.72), width=1)
    page.insert_text((60, 90), "SAMPLE INVOICE", fontsize=22, fontname="helv", color=(0.08, 0.24, 0.39))
    page.insert_text((60, 125), "Invoice No. INV-DEMO-001", fontsize=11, fontname="helv")
    page.insert_text((60, 145), "Invoice date: 2026-07-25", fontsize=11, fontname="helv")
    page.insert_text((60, 185), "Project: GR-2601 / GR Central Road Improvement", fontsize=11, fontname="helv")
    page.insert_text((60, 215), "Bill to: GR Sample Construction", fontsize=11, fontname="helv")
    page.draw_line(fitz.Point(60, 250), fitz.Point(535, 250), color=(0.2, 0.25, 0.3), width=1)
    headers = [(60, "Description"), (355, "Qty"), (425, "Amount")]
    for x, text in headers:
        page.insert_text((x, 274), text, fontsize=10, fontname="helv")
    lines = [
        (310, "Earthwork and excavation", "1", "770,000"),
        (360, "Temporary works", "1", "440,000"),
    ]
    for y, desc, qty, amount in lines:
        page.insert_text((60, y), desc, fontsize=11, fontname="helv")
        page.insert_text((370, y), qty, fontsize=11, fontname="helv")
        page.insert_text((445, y), amount, fontsize=11, fontname="helv")
        page.draw_line(fitz.Point(60, y + 12), fitz.Point(535, y + 12), color=(0.82, 0.84, 0.86), width=0.6)
    page.insert_text((355, 455), "Subtotal", fontsize=11, fontname="helv")
    page.insert_text((445, 455), "1,100,000", fontsize=11, fontname="helv")
    page.insert_text((355, 480), "Tax 10%", fontsize=11, fontname="helv")
    page.insert_text((445, 480), "110,000", fontsize=11, fontname="helv")
    page.insert_text((355, 515), "TOTAL", fontsize=13, fontname="helv")
    page.insert_text((445, 515), "1,210,000", fontsize=13, fontname="helv")
    page.insert_text((60, 720), "This PDF contains fictional data for the operation manual.", fontsize=9, fontname="helv", color=(0.4, 0.4, 0.4))
    doc.save(path)
    doc.close()


def capture_window(window: tk.Misc, output_name: str, wait_seconds: float = 0.7) -> None:
    window.update_idletasks()
    window.update()
    window.lift()
    try:
        window.attributes("-topmost", True)
    except tk.TclError:
        pass
    time.sleep(wait_seconds)
    window.update_idletasks()
    x = window.winfo_rootx()
    y = window.winfo_rooty()
    width = window.winfo_width()
    height = window.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
    image.save(SCREENSHOT_DIR / output_name)


def screenshot_invoice_list(mode: str, output_name: str) -> None:
    from invoice_manager.ui.invoice_list_window import InvoiceListWindow

    root = tk.Tk()
    root.withdraw()
    window = InvoiceListWindow(root, open_hub=lambda _parent: None)
    window.geometry("1320x700+140+90")
    window.amount_display_var.set(mode)
    window.on_amount_display_selected()
    first = next(iter(window.tree.get_children()), None)
    if first:
        window.tree.selection_set(first)
        window.tree.focus(first)
        window.on_select()
    capture_window(window, output_name)
    root.destroy()


def screenshot_detail(invoice_id: int, invoice_ids: list[int]) -> None:
    from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow

    root = tk.Tk()
    root.withdraw()
    window = InvoiceDetailWindow(root, invoice_id, invoice_ids=invoice_ids, amount_display_mode="税抜")
    window.geometry("1500x900+110+65")
    window.update_idletasks()
    if window.pdf_path:
        window.render_pdf_page()
    capture_window(window, "03_invoice_detail.png", wait_seconds=1.2)
    root.destroy()


def screenshot_import() -> None:
    from invoice_manager.ui.import_window import ImportWindow

    root = tk.Tk()
    root.withdraw()
    window = ImportWindow(root)
    window.geometry("760x620+330+150")
    window.csv_var.set(r"C:\取込データ\請求一覧_202608.csv")
    window.zip_var.set(r"C:\取込データ\請求書PDF_202608.zip")
    window.month_summary_var.set("自動判定: 2026年8月")
    window.memo_var.set("2026年8月請求分")
    window.drop_label.configure(text="ここにCSVまたはzipをドラッグ＆ドロップ")
    preview_rows = [
        ("CSV件数", "5"),
        ("zip内IDフォルダ数", "5"),
        ("CSVとzipの一致件数", "5"),
        ("CSVのみ存在するID数", "0"),
        ("zipのみ存在するID数", "0"),
        ("新規登録件数", "5"),
        ("既存スキップ件数", "0"),
        ("重複候補件数", "0"),
        ("エラー件数", "0"),
        ("請求金額合計", "3,949,999"),
        ("PDFファイル総数", "5"),
        ("請求月(自動判定)", "2026年8月"),
    ]
    for row in preview_rows:
        window.tree.insert("", tk.END, values=row)
    window.message.insert(tk.END, "プレビュー結果を確認してから［取込実行］を押します。\n")
    capture_window(window, "01_csv_zip_import.png")
    root.destroy()


def screenshot_management_menu() -> None:
    from invoice_manager.ui.main_window import MainWindow

    root = tk.Tk()
    root.geometry("420x380+420+190")
    MainWindow(root)
    capture_window(root, "04_management_menu.png")
    root.destroy()


def main() -> None:
    invoice_id, invoice_ids = prepare_demo_data()
    screenshot_import()
    screenshot_invoice_list("税抜", "02_invoice_list_tax_excluded.png")
    screenshot_invoice_list("税込", "02b_invoice_list_tax_included.png")
    screenshot_detail(invoice_id, invoice_ids)
    screenshot_management_menu()
    print(SCREENSHOT_DIR)


if __name__ == "__main__":
    main()
