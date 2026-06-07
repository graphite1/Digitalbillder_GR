from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import (
    delete_invoice_allocation,
    ensure_work_type_codes_for_project,
    get_invoice_allocation_total,
    get_invoice_detail,
    list_invoice_allocations,
    list_invoice_files,
    list_work_type_codes,
    save_invoice_allocation,
    update_invoice_memo,
)
from invoice_manager.utils.date_utils import format_billing_month


class InvoiceDetailWindow(tk.Toplevel):
    def __init__(self, master, invoice_id: int, on_saved=None) -> None:
        super().__init__(master)
        self.invoice_id = invoice_id
        self.on_saved = on_saved
        self.title("請求詳細")
        self.geometry("920x760")
        self.file_ids: dict[str, str] = {}
        self.allocation_ids: dict[str, int] = {}
        self.work_type_options: dict[str, int] = {}
        self.invoice_total = 0
        self.project_id: int | None = None
        self._build()
        self.load()

    def _build(self) -> None:
        self.info = tk.Text(self, height=10)
        self.info.pack(fill=tk.X, padx=10, pady=10)

        action = tk.Frame(self, padx=10)
        action.pack(fill=tk.X)
        tk.Button(action, text="メモ保存", command=self.save_memo).pack(side=tk.LEFT, padx=4)

        self.memo = tk.Text(self, height=3)
        self.memo.pack(fill=tk.X, padx=10, pady=8)

        allocation_frame = tk.LabelFrame(self, text="工種コード別振分", padx=8, pady=8)
        allocation_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.allocation_summary_var = tk.StringVar()
        tk.Label(allocation_frame, textvariable=self.allocation_summary_var).pack(anchor=tk.W)
        self.allocations = ttk.Treeview(
            allocation_frame,
            columns=("code", "name", "amount", "memo", "sort_order"),
            show="headings",
            height=7,
        )
        for column, label, width in [
            ("code", "工種コード", 100),
            ("name", "工種名", 220),
            ("amount", "振分金額", 110),
            ("memo", "メモ", 220),
            ("sort_order", "並び順", 70),
        ]:
            self.allocations.heading(column, text=label)
            self.allocations.column(column, width=width)
        self.allocations.pack(fill=tk.BOTH, expand=True, pady=6)
        buttons = tk.Frame(allocation_frame)
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="振分行を追加", command=self.add_allocation).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="振分行を編集", command=self.edit_allocation).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="振分行を削除", command=self.delete_allocation).pack(side=tk.LEFT, padx=4)

        files_frame = tk.LabelFrame(self, text="添付ファイル", padx=8, pady=8)
        files_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.files = ttk.Treeview(files_frame, columns=("name", "size"), show="headings", height=5)
        self.files.heading("name", text="添付ファイル一覧")
        self.files.heading("size", text="サイズ")
        self.files.column("name", width=620)
        self.files.column("size", width=100)
        self.files.pack(fill=tk.BOTH, expand=True)
        tk.Button(files_frame, text="PDFを開く", command=self.open_pdf).pack(anchor=tk.W, pady=(6, 0))

    def load(self) -> None:
        row = get_invoice_detail(self.invoice_id)
        if not row:
            messagebox.showerror("エラー", "請求データが見つかりません。")
            self.destroy()
            return
        self.invoice_total = int(row["total_amount"])
        self.project_id = int(row["project_id"])
        self.info.delete("1.0", tk.END)
        text = "\n".join(
            [
                f"請求ID: {row['external_id']}",
                f"請求月: {format_billing_month(row['billing_month'])}",
                f"請求日: {row['invoice_date']}",
                f"請求金額(税込): {row['total_amount']}",
                f"工事コード: {row['project_code']}",
                f"工事名: {row['project_name']}",
                f"取引先名: {row['vendor_name']}",
                f"担当者: {row['last_name'] or ''} {row['first_name'] or ''}",
                f"メール: {row['email'] or ''}",
                f"電話番号: {row['phone'] or ''}",
            ]
        )
        self.info.insert(tk.END, text)
        self.memo.delete("1.0", tk.END)
        self.memo.insert(tk.END, row["local_memo"] or "")
        self.load_work_type_options()
        self.load_allocations()
        self.load_files()

    def load_work_type_options(self) -> None:
        self.work_type_options = {}
        if not self.project_id:
            return
        ensure_work_type_codes_for_project(self.project_id)
        for row in list_work_type_codes(self.project_id, active_only=True):
            self.work_type_options[f"{row['code']}｜{row['name']}"] = int(row["id"])

    def load_allocations(self) -> None:
        self.allocations.delete(*self.allocations.get_children())
        self.allocation_ids.clear()
        for row in list_invoice_allocations(self.invoice_id):
            item_id = self.allocations.insert(
                "",
                tk.END,
                values=(row["code"], row["name"], row["amount"], row["memo"] or "", row["sort_order"]),
            )
            self.allocation_ids[item_id] = int(row["id"])
        allocated = get_invoice_allocation_total(self.invoice_id)
        remaining = self.invoice_total - allocated
        if remaining < 0:
            self.allocation_summary_var.set(
                f"請求金額: {self.invoice_total:,}円 / 振分合計: {allocated:,}円 / 超過額: {abs(remaining):,}円"
            )
            self.allocation_summary_var.set(self.allocation_summary_var.get() + "  ※超過しています")
        else:
            self.allocation_summary_var.set(
                f"請求金額: {self.invoice_total:,}円 / 振分合計: {allocated:,}円 / 未振分額: {remaining:,}円"
            )

    def load_files(self) -> None:
        self.files.delete(*self.files.get_children())
        self.file_ids.clear()
        for file_row in list_invoice_files(self.invoice_id):
            item_id = self.files.insert("", tk.END, values=(file_row["original_file_name"], file_row["file_size"]))
            self.file_ids[item_id] = file_row["stored_file_path"]

    def save_memo(self) -> None:
        update_invoice_memo(self.invoice_id, self.memo.get("1.0", tk.END).strip())
        if self.on_saved:
            self.on_saved()
        self.load()

    def add_allocation(self) -> None:
        self.open_allocation_dialog()

    def edit_allocation(self) -> None:
        selection = self.allocations.selection()
        if not selection:
            messagebox.showwarning("選択なし", "振分行を選択してください。")
            return
        item_id = selection[0]
        values = self.allocations.item(item_id, "values")
        self.open_allocation_dialog(self.allocation_ids[item_id], values)

    def delete_allocation(self) -> None:
        selection = self.allocations.selection()
        if not selection:
            messagebox.showwarning("選択なし", "振分行を選択してください。")
            return
        delete_invoice_allocation(self.allocation_ids[selection[0]])
        self.load_allocations()

    def open_allocation_dialog(self, allocation_id: int | None = None, values=None) -> None:
        if not self.work_type_options:
            messagebox.showwarning("工種コードなし", "この工事の有効な工種コードを先に登録してください。")
            return
        dialog = tk.Toplevel(self)
        dialog.title("振分行")
        dialog.geometry("420x220")
        dialog.transient(self)
        dialog.grab_set()

        selected_work_type = tk.StringVar(value=next(iter(self.work_type_options.keys())))
        amount_var = tk.StringVar()
        memo_var = tk.StringVar()
        sort_order_var = tk.StringVar(value="0")
        if values:
            label_prefix = f"{values[0]}｜{values[1]}"
            for label in self.work_type_options:
                if label == label_prefix:
                    selected_work_type.set(label)
                    break
            amount_var.set(values[2])
            memo_var.set(values[3])
            sort_order_var.set(values[4])

        tk.Label(dialog, text="工種コード").grid(row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4))
        ttk.Combobox(
            dialog,
            textvariable=selected_work_type,
            values=list(self.work_type_options.keys()),
            state="readonly",
            width=34,
        ).grid(row=0, column=1, sticky=tk.W, padx=4, pady=(12, 4))
        tk.Label(dialog, text="金額").grid(row=1, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=amount_var).grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)
        tk.Label(dialog, text="メモ").grid(row=2, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=memo_var, width=36).grid(row=2, column=1, sticky=tk.W, padx=4, pady=4)
        tk.Label(dialog, text="並び順").grid(row=3, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=sort_order_var).grid(row=3, column=1, sticky=tk.W, padx=4, pady=4)

        def save() -> None:
            try:
                save_invoice_allocation(
                    invoice_id=self.invoice_id,
                    work_type_code_id=self.work_type_options[selected_work_type.get()],
                    amount=int(amount_var.get().replace(",", "")),
                    memo=memo_var.get(),
                    sort_order=int(sort_order_var.get() or 0),
                    allocation_id=allocation_id,
                )
            except Exception as exc:
                messagebox.showerror("保存エラー", str(exc))
                return
            dialog.destroy()
            self.load_allocations()

        buttons = tk.Frame(dialog)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=12, pady=10)
        tk.Button(buttons, text="保存", command=save).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        dialog.wait_window()

    def open_pdf(self) -> None:
        selection = self.files.selection()
        if not selection:
            messagebox.showwarning("選択なし", "PDFを選択してください。")
            return
        os.startfile(self.file_ids[selection[0]])
