from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import (
    list_billing_months,
    list_invoice_dates,
    list_invoice_files,
    list_invoices,
    list_projects,
    list_vendors,
    update_invoice_billing_month,
    update_invoice_memo,
)
from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow
from invoice_manager.utils.date_utils import (
    billing_month_candidates,
    format_billing_month,
    format_invoice_date,
    validate_billing_month,
)


class InvoiceListWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("請求一覧")
        self.geometry("1320x700")
        self.invoice_ids: dict[str, int] = {}
        self.selected_project_var = tk.StringVar(value="すべて")
        self.project_options = {"すべて": None}
        self.selected_vendor_var = tk.StringVar(value="すべて")
        self.vendor_options = {"すべて": None}
        self.selected_month_var = tk.StringVar(value="すべて")
        self.month_options = {"すべて": None}
        self.selected_date_from_var = tk.StringVar(value="すべて")
        self.selected_date_to_var = tk.StringVar(value="すべて")
        self.invoice_date_options = {"すべて": None}
        self.selected_sort_var = tk.StringVar(value="請求日（新しい順）")
        self.sort_options = {
            "請求日（新しい順）": "invoice_date_desc",
            "請求日（古い順）": "invoice_date_asc",
            "請求月（新しい順）": "billing_month_desc",
            "工事コード順": "project_code_asc",
            "取引先名順": "vendor_name_asc",
            "金額（高い順）": "amount_desc",
            "金額（低い順）": "amount_asc",
        }

        self.memo_var = tk.StringVar()
        self.load_project_options()
        self.load_vendor_options()
        self.load_month_options()
        self.load_invoice_date_options()

        self._build_filters()
        self._build_tree()
        self._build_actions()
        self.refresh()

    def _build_filters(self) -> None:
        frame = tk.Frame(self, padx=10, pady=8)
        frame.pack(fill=tk.X)
        tk.Label(frame, text="工事選択").grid(row=0, column=0, sticky=tk.W)
        self.project_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_project_var,
            values=list(self.project_options.keys()),
            width=42,
            state="readonly",
        )
        self.project_combo.grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=4)
        self.project_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        tk.Label(frame, text="請求月").grid(row=0, column=4, sticky=tk.W, padx=(12, 0))
        self.month_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_month_var,
            values=list(self.month_options.keys()),
            width=14,
            state="readonly",
        )
        self.month_combo.grid(row=0, column=5, sticky=tk.W, padx=4)
        self.month_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        tk.Label(frame, text="取引先").grid(row=0, column=6, sticky=tk.W, padx=(12, 0))
        self.vendor_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_vendor_var,
            values=list(self.vendor_options.keys()),
            width=24,
            state="readonly",
        )
        self.vendor_combo.grid(row=0, column=7, columnspan=3, sticky=tk.W, padx=4)
        self.vendor_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        tk.Label(frame, text="請求日").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.date_from_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_date_from_var,
            values=list(self.invoice_date_options.keys()),
            width=12,
            state="readonly",
        )
        self.date_from_combo.grid(row=1, column=1, sticky=tk.W, padx=4, pady=(8, 0))
        self.date_from_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        tk.Label(frame, text="から").grid(row=1, column=2, sticky=tk.W, pady=(8, 0))
        self.date_to_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_date_to_var,
            values=list(self.invoice_date_options.keys()),
            width=12,
            state="readonly",
        )
        self.date_to_combo.grid(row=1, column=3, sticky=tk.W, padx=4, pady=(8, 0))
        self.date_to_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        tk.Label(frame, text="まで").grid(row=1, column=4, sticky=tk.W, pady=(8, 0))
        tk.Label(frame, text="並び順").grid(row=1, column=5, sticky=tk.W, padx=(12, 0), pady=(8, 0))
        self.sort_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_sort_var,
            values=list(self.sort_options.keys()),
            width=18,
            state="readonly",
        )
        self.sort_combo.grid(row=1, column=6, sticky=tk.W, padx=4, pady=(8, 0))
        self.sort_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)

    def load_project_options(self) -> None:
        self.project_options = {"すべて": None}
        for row in list_projects():
            label = f"{row['project_code']}｜{row['project_name']}"
            self.project_options[label] = int(row["id"])

    def load_vendor_options(self) -> None:
        self.vendor_options = {"すべて": None}
        for row in list_vendors():
            self.vendor_options[row["vendor_name"]] = int(row["id"])

    def load_month_options(self) -> None:
        months = set(list_billing_months())
        for month in list(months):
            months.update(billing_month_candidates(month, before=1, after=3))
        if not months:
            months.update(billing_month_candidates())
        self.month_options = {"すべて": None}
        for month in sorted(months, reverse=True):
            self.month_options[format_billing_month(month)] = month

    def load_invoice_date_options(self) -> None:
        self.invoice_date_options = {"すべて": None}
        for date_text in list_invoice_dates():
            self.invoice_date_options[format_invoice_date(date_text)] = date_text

    def _build_tree(self) -> None:
        columns = (
            "billing_month",
            "project_code",
            "project_name",
            "vendor_name",
            "contact_name",
            "email",
            "phone",
            "invoice_date",
            "total_amount",
            "file_count",
            "local_memo",
        )
        headers = {
            "billing_month": "請求月",
            "project_code": "工事コード",
            "project_name": "工事名",
            "vendor_name": "取引先名",
            "contact_name": "担当者名",
            "email": "メールアドレス",
            "phone": "電話番号",
            "invoice_date": "請求日",
            "total_amount": "請求金額(税込)",
            "file_count": "添付ファイル数",
            "local_memo": "メモ",
        }
        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        y_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        for column in columns:
            self.tree.heading(column, text=headers[column])
            self.tree.column(column, width=120)
        self.tree.column("project_name", width=260)
        self.tree.column("vendor_name", width=180)
        self.tree.column("email", width=220)
        self.tree.column("local_memo", width=180)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        x_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<MouseWheel>", self.on_tree_mousewheel)
        self.tree.bind("<Shift-MouseWheel>", self.on_tree_shift_mousewheel)

    def _build_actions(self) -> None:
        frame = tk.Frame(self, padx=10, pady=8)
        frame.pack(fill=tk.X)
        tk.Button(frame, text="詳細を開く", command=self.open_detail).pack(side=tk.LEFT, padx=4)
        tk.Entry(frame, textvariable=self.memo_var, width=36).pack(side=tk.LEFT, padx=4)
        tk.Button(frame, text="メモ編集", command=self.save_memo).pack(side=tk.LEFT, padx=4)
        tk.Button(frame, text="請求月変更", command=self.change_billing_month).pack(side=tk.LEFT, padx=4)
        tk.Button(frame, text="添付PDFを開く", command=self.open_first_pdf).pack(side=tk.LEFT, padx=4)

    def refresh(self) -> None:
        filters = {}
        selected = self.selected_project_var.get()
        project_id = self.project_options.get(selected)
        if project_id:
            filters["project_id"] = str(project_id)
        selected_vendor = self.selected_vendor_var.get()
        vendor_id = self.vendor_options.get(selected_vendor)
        if vendor_id:
            filters["vendor_id"] = str(vendor_id)
        selected_month = self.selected_month_var.get()
        billing_month = self.month_options.get(selected_month)
        if billing_month:
            filters["billing_month"] = billing_month
        invoice_date_from = self.invoice_date_options.get(self.selected_date_from_var.get())
        invoice_date_to = self.invoice_date_options.get(self.selected_date_to_var.get())
        if invoice_date_from and invoice_date_to and invoice_date_from > invoice_date_to:
            invoice_date_from, invoice_date_to = invoice_date_to, invoice_date_from
        if invoice_date_from:
            filters["invoice_date_from"] = invoice_date_from
        if invoice_date_to:
            filters["invoice_date_to"] = invoice_date_to
        sort_key = self.sort_options.get(self.selected_sort_var.get())
        if sort_key:
            filters["sort"] = sort_key
        self.tree.delete(*self.tree.get_children())
        self.invoice_ids.clear()
        for row in list_invoices(filters):
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    format_billing_month(row["billing_month"]),
                    row["project_code"],
                    row["project_name"],
                    row["vendor_name"],
                    row["contact_name"],
                    row["email"],
                    row["phone"],
                    row["invoice_date"],
                    row["total_amount"],
                    row["file_count"],
                    row["local_memo"],
                ),
            )
            self.invoice_ids[item_id] = int(row["id"])

    def on_filter_selected(self, _event=None) -> None:
        self.refresh()

    def on_tree_mousewheel(self, event) -> str:
        units = -5 if event.delta > 0 else 5
        self.tree.yview_scroll(units, "units")
        return "break"

    def on_tree_shift_mousewheel(self, event) -> str:
        units = -5 if event.delta > 0 else 5
        self.tree.xview_scroll(units, "units")
        return "break"

    def selected_invoice_id(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("選択なし", "請求を選択してください。")
            return None
        return self.invoice_ids[selection[0]]

    def selected_invoice_ids(self) -> list[int]:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("選択なし", "請求を選択してください。")
            return []
        return [self.invoice_ids[item_id] for item_id in selection]

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        self.memo_var.set(values[10])

    def open_detail(self) -> None:
        invoice_id = self.selected_invoice_id()
        if invoice_id:
            InvoiceDetailWindow(self, invoice_id, on_saved=self.refresh)

    def save_memo(self) -> None:
        invoice_id = self.selected_invoice_id()
        if not invoice_id:
            return
        update_invoice_memo(invoice_id, self.memo_var.get())
        self.refresh()

    def change_billing_month(self) -> None:
        invoice_ids = self.selected_invoice_ids()
        if not invoice_ids:
            return
        month = self.ask_billing_month()
        if not month:
            return
        try:
            updated_count = update_invoice_billing_month(invoice_ids, month)
        except Exception as exc:
            messagebox.showerror("請求月変更エラー", str(exc))
            return
        messagebox.showinfo("請求月変更", f"{updated_count}件の請求月を変更しました。")
        self.load_month_options()
        self.month_combo.configure(values=list(self.month_options.keys()))
        self.refresh()

    def ask_billing_month(self) -> str | None:
        result = {"value": None}
        dialog = tk.Toplevel(self)
        dialog.title("請求月変更")
        dialog.geometry("280x120")
        dialog.transient(self)
        dialog.grab_set()

        selected = tk.StringVar()
        labels = [label for label in self.month_options.keys() if label != "すべて"]
        if labels:
            selected.set(labels[0])
        tk.Label(dialog, text="変更後の請求月").pack(anchor=tk.W, padx=12, pady=(12, 4))
        combo = ttk.Combobox(dialog, textvariable=selected, values=labels, state="readonly")
        combo.pack(fill=tk.X, padx=12)

        def apply() -> None:
            if selected.get():
                result["value"] = validate_billing_month(selected.get())
            dialog.destroy()

        buttons = tk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        tk.Button(buttons, text="変更", command=apply).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        dialog.wait_window()
        return result["value"]

    def open_first_pdf(self) -> None:
        invoice_id = self.selected_invoice_id()
        if not invoice_id:
            return
        files = list_invoice_files(invoice_id)
        if not files:
            messagebox.showinfo("添付なし", "添付PDFがありません。")
            return
        os.startfile(files[0]["stored_file_path"])
