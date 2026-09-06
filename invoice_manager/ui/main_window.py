from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlparse

from invoice_manager.repositories import get_app_setting, list_billing_months, set_app_setting
from invoice_manager.utils.date_utils import format_billing_month, validate_billing_month
from invoice_manager.ui.background_activity import has_running_descendants, running_activities


DEFAULT_DIGITAL_BILLDER_URL = "https://purchases.digitalbillder.com/invoices/applications"


UPDATE_RESTART_EXIT_CODE = 75


def open_management_hub(parent):
    existing = getattr(parent, "_management_hub", None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        return existing
    dialog = tk.Toplevel(parent)
    parent._management_hub = dialog
    MainWindow(dialog)
    dialog.transient(parent)
    return dialog


def has_busy_update(widget) -> bool:
    from invoice_manager.ui.update_window import UpdateWindow
    if isinstance(widget, UpdateWindow) and widget.is_busy:
        return True
    return any(has_busy_update(child) for child in widget.winfo_children())


def run_app() -> int:
    from invoice_manager.ui.invoice_list_window import InvoiceListWindow

    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.withdraw()
    root.update_restart_requested = False

    InvoiceListWindow(root, on_close=root.destroy, open_hub=open_management_hub)
    root.mainloop()
    return UPDATE_RESTART_EXIT_CODE if root.update_restart_requested else 0


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("請求書管理")
        root.geometry("880x480")
        root.minsize(760, 420)
        root.protocol("WM_DELETE_WINDOW", self.close_window)

        frame = ttk.Frame(root, padding=(18, 14))
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="請求書管理", font=("TkDefaultFont", 15, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 12)
        )

        groups = (
            (
                "取込",
                (
                    ("Digital Billder 新着取込", self.open_digital_billder_sync),
                    ("CSV + zip取込", self.open_import),
                    ("取込履歴", self.open_import_history),
                ),
            ),
            (
                "集計・予算",
                (
                    ("保管済み履歴・工事別実績", self.open_historical_costs),
                    ("工事予算・着地見込", self.open_project_budget),
                    ("Excel出力", self.export_excel),
                ),
            ),
            (
                "設定",
                (
                    ("工事表示設定", self.open_project_visibility),
                    ("工種コードマスタ", self.open_work_type_master),
                    ("取引先別工種候補", self.open_vendor_work_type_candidates),
                    ("アプリの更新", self.open_update),
                ),
            ),
        )
        groups_frame = ttk.Frame(frame)
        groups_frame.grid(row=1, column=0, sticky="nsew")
        for column, (title, buttons) in enumerate(groups):
            groups_frame.columnconfigure(column, weight=1)
            group = ttk.LabelFrame(groups_frame, text=title, padding=(10, 8))
            group.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == len(groups) - 1 else 6))
            group.columnconfigure(0, weight=1)
            for row, (label, command) in enumerate(buttons):
                ttk.Button(group, text=label, command=command).grid(
                    row=row, column=0, sticky="ew", pady=4
                )

        footer = ttk.Frame(frame)
        footer.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        ttk.Button(footer, text="Digital Billderを開く", command=self.open_digital_billder).pack(side=tk.LEFT)
        ttk.Button(footer, text="閉じる", command=self.close_window).pack(side=tk.RIGHT)

    def close_window(self) -> None:
        from invoice_manager.ui.update_window import UpdateWindow

        if has_running_descendants(self.root):
            self.root.withdraw()
            return

        if any(
            isinstance(child, UpdateWindow) and child.winfo_exists() and child.is_busy
            for child in self.root.winfo_children()
        ):
            messagebox.showinfo("更新処理中", "更新の確認またはダウンロードが完了してから閉じてください。", parent=self.root)
            return
        parent = self.root.master
        self.root.destroy()
        if callable(getattr(parent, "reload_filter_options", None)):
            parent.reload_filter_options()

    def open_digital_billder_sync(self) -> None:
        from invoice_manager.ui.digital_billder_sync_window import DigitalBillderSyncWindow

        for child in self.root.winfo_children():
            if isinstance(child, DigitalBillderSyncWindow):
                child.deiconify()
                child.lift()
                return
        DigitalBillderSyncWindow(self.root)

    def open_import(self) -> None:
        from invoice_manager.ui.import_window import ImportWindow

        ImportWindow(self.root)

    def open_historical_costs(self) -> None:
        from functools import partial
        from invoice_manager.services.web_invoice_reader import sync_archived_history
        from invoice_manager.ui.historical_cost_window import HistoricalCostWindow

        for child in self.root.winfo_children():
            if isinstance(child, HistoricalCostWindow):
                child.deiconify()
                child.lift()
                return
        HistoricalCostWindow(self.root, on_refresh_history=sync_archived_history,
                             on_full_refresh_history=partial(sync_archived_history, full_refresh=True))

    def open_project_budget(self) -> None:
        from invoice_manager.ui.project_budget_window import ProjectBudgetWindow

        ProjectBudgetWindow(self.root)

    def open_import_history(self) -> None:
        from invoice_manager.ui.import_history_window import ImportHistoryWindow

        ImportHistoryWindow(self.root)

    def open_project_visibility(self) -> None:
        from invoice_manager.ui.project_visibility_window import ProjectVisibilityWindow

        ProjectVisibilityWindow(self.root)

    def open_work_type_master(self) -> None:
        from invoice_manager.ui.work_type_master_window import WorkTypeMasterWindow

        WorkTypeMasterWindow(self.root)

    def open_vendor_work_type_candidates(self) -> None:
        from invoice_manager.ui.vendor_work_type_candidate_window import VendorWorkTypeCandidateWindow

        VendorWorkTypeCandidateWindow(self.root)

    def open_update(self) -> None:
        from invoice_manager.ui.update_window import UpdateWindow

        for child in self.root.winfo_children():
            if isinstance(child, UpdateWindow) and child.winfo_exists():
                child.lift()
                child.focus_set()
                return
        UpdateWindow(
            self.root,
            readiness_check=self.update_restart_readiness,
            request_restart=self.request_update_restart,
        )

    def update_restart_readiness(self, update_window: tk.Misc) -> tuple[bool, str]:
        invoice_window = getattr(self.root, "master", None)
        app_root = self.root
        while getattr(app_root, "master", None) is not None:
            app_root = app_root.master
        allowed = (self.root, invoice_window, update_window)
        other_windows = []

        def collect(widget) -> None:
            for child in widget.winfo_children() if hasattr(widget, "winfo_children") else ():
                if (
                    isinstance(child, tk.Toplevel)
                    and all(child is not item for item in allowed)
                    and bool(child.winfo_exists())
                ):
                    other_windows.append(child)
                collect(child)

        collect(app_root)
        if other_windows:
            return False, "編集中・取込中・同期中の画面を閉じてから再起動してください。更新は保留されています。"
        if self._invoice_list_has_unsaved_memo():
            return False, "請求一覧のメモを保存するか元に戻してから再起動してください。更新は保留されています。"
        return True, ""

    def _invoice_list_has_unsaved_memo(self) -> bool:
        invoice_window = self.root.master
        tree = getattr(invoice_window, "tree", None)
        memo_var = getattr(invoice_window, "memo_var", None)
        if tree is None or memo_var is None:
            return False
        selection = tree.selection()
        if not selection:
            return False
        values = tree.item(selection[0], "values")
        saved_memo = str(values[7]) if len(values) > 7 else ""
        return memo_var.get() != saved_memo

    def request_update_restart(self) -> None:
        if running_activities(self.root):
            messagebox.showinfo("処理中", "検索・取得・取込の完了後にアプリを再起動してください。", parent=self.root)
            return
        app_root = self.root
        while app_root.master is not None:
            app_root = app_root.master
        app_root.update_restart_requested = True
        app_root.destroy()

    def export_excel(self) -> None:
        from invoice_manager.services.export_excel import export_monthly_invoice_list

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
