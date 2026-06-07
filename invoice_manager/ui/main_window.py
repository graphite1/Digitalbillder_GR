from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlparse

from invoice_manager.repositories import get_app_setting, list_billing_months, set_app_setting
from invoice_manager.services.export_excel import export_monthly_invoice_list
from invoice_manager.ui.import_window import ImportWindow
from invoice_manager.ui.invoice_list_window import InvoiceListWindow
from invoice_manager.ui.vendor_work_type_candidate_window import VendorWorkTypeCandidateWindow
from invoice_manager.ui.work_type_master_window import WorkTypeMasterWindow
from invoice_manager.utils.date_utils import format_billing_month, validate_billing_month


DEFAULT_DIGITAL_BILLDER_URL = "https://digitalbillder.com/"


def run_app() -> None:
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    MainWindow(root)
    root.mainloop()


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("請求書管理")
        root.geometry("420x380")

        frame = tk.Frame(root, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        buttons = [
            ("CSV + zip取込", self.open_import),
            ("請求一覧", self.open_invoice_list),
            ("工種コードマスタ", self.open_work_type_master),
            ("取引先別工種候補", self.open_vendor_work_type_candidates),
            ("Digital Billderを開く", self.open_digital_billder),
            ("Excel出力", self.export_excel),
        ]
        for label, command in buttons:
            tk.Button(frame, text=label, command=command, height=2).pack(fill=tk.X, pady=4)

    def open_import(self) -> None:
        ImportWindow(self.root)

    def open_invoice_list(self) -> None:
        InvoiceListWindow(self.root)

    def open_work_type_master(self) -> None:
        WorkTypeMasterWindow(self.root)

    def open_vendor_work_type_candidates(self) -> None:
        VendorWorkTypeCandidateWindow(self.root)

    def export_excel(self) -> None:
        month = self.ask_billing_month()
        if not month:
            return
        try:
            path = export_monthly_invoice_list(month)
            messagebox.showinfo("Excel出力", f"出力しました:\n{path}")
        except Exception as exc:
            messagebox.showerror("Excel出力エラー", str(exc))

    def open_digital_billder(self) -> None:
        url = get_app_setting("digital_billder_url") or DEFAULT_DIGITAL_BILLDER_URL
        if not url:
            url = simpledialog.askstring("Digital Billder URL", "Digital BillderのURL")
            if not url:
                return
            url = self.normalize_url(url)
            set_app_setting("digital_billder_url", url)
        url = self.normalize_url(url)
        if not self.is_allowed_digital_billder_url(url):
            messagebox.showerror("URLエラー", "Digital Billder以外のURLは開けません。")
            return
        webbrowser.open(url)

    def normalize_url(self, url: str) -> str:
        text = url.strip()
        parsed = urlparse(text)
        if not parsed.scheme:
            text = f"https://{text}"
        return text

    def is_allowed_digital_billder_url(self, url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower()
        return hostname == "digitalbillder.com" or hostname.endswith(".digitalbillder.com")

    def ask_billing_month(self) -> str | None:
        result = {"value": None}
        dialog = tk.Toplevel(self.root)
        dialog.title("Excel出力")
        dialog.geometry("280x120")
        dialog.transient(self.root)
        dialog.grab_set()

        months = list_billing_months()
        options = {format_billing_month(month): month for month in months}
        selected = tk.StringVar(value=next(iter(options.keys()), ""))

        tk.Label(dialog, text="出力する請求月").pack(anchor=tk.W, padx=12, pady=(12, 4))
        combo = ttk.Combobox(dialog, textvariable=selected, values=list(options.keys()), state="readonly")
        combo.pack(fill=tk.X, padx=12)

        def apply() -> None:
            if selected.get():
                result["value"] = validate_billing_month(selected.get())
            dialog.destroy()

        buttons = tk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        tk.Button(buttons, text="出力", command=apply).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        dialog.wait_window()
        return result["value"]
