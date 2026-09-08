from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import list_deleted_invoices, restore_deleted_invoices
from invoice_manager.utils.money_utils import format_amount


class DeletedInvoiceHistoryWindow(tk.Toplevel):
    """Select individually preserved deleted invoices and restore them safely."""

    def __init__(self, master, on_restored=None) -> None:
        super().__init__(master)
        self.title("削除履歴・請求書の復元")
        self.geometry("1100x610")
        self.minsize(820, 430)
        self.on_restored = on_restored
        self.rows_by_item: dict[str, object] = {}

        tk.Label(
            self,
            text="削除時点の請求情報・振分・添付PDFを保管しています。復元する請求書だけを選んでください。",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        columns = ("deleted_at", "project", "vendor", "invoice_date", "billing_month", "amount", "files", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "deleted_at": "削除日時", "project": "工事", "vendor": "取引先", "invoice_date": "請求日",
            "billing_month": "請求月", "amount": "請求金額（税抜）", "files": "PDF", "status": "状態",
        }
        widths = {"deleted_at": 150, "project": 240, "vendor": 175, "invoice_date": 100,
                  "billing_month": 100, "amount": 130, "files": 60, "status": 90}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.E if column in ("amount", "files") else tk.W)
        y_scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.detail_var = tk.StringVar(value="削除履歴を選択してください。")
        ttk.Label(self, textvariable=self.detail_var, wraplength=1040, justify=tk.LEFT).pack(fill=tk.X, padx=12, pady=(0, 8))

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(buttons, text="更新", command=self.refresh).pack(side=tk.LEFT)
        self.restore_button = ttk.Button(buttons, text="選択した請求書を復元", command=self.restore_selected, state=tk.DISABLED)
        self.restore_button.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(buttons, text="閉じる", command=self.destroy).pack(side=tk.RIGHT)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows_by_item.clear()
        for row in list_deleted_invoices():
            restored = bool(row["restored_at"])
            item = self.tree.insert(
                "", tk.END,
                values=(
                    row["deleted_at"], f"{row['project_code']} | {row['project_name']}", row["vendor_name"],
                    row["invoice_date"], row["billing_month"], format_amount(row["total_amount_excluded"] or row["total_amount"]),
                    row["file_count"], "復元済み" if restored else "復元可能",
                ),
                tags=("restored",) if restored else (),
            )
            self.rows_by_item[item] = row
        self.tree.tag_configure("restored", foreground="#777777")
        self.on_select()

    def on_select(self, _event=None) -> None:
        selected = [self.rows_by_item[item] for item in self.tree.selection()]
        restorable = [row for row in selected if not row["restored_at"]]
        self.restore_button.configure(state=tk.NORMAL if restorable else tk.DISABLED)
        if not selected:
            self.detail_var.set("削除履歴を選択してください。")
            return
        if len(selected) > 1:
            self.detail_var.set(f"{len(selected)}件を選択中です。復元済みの請求書は対象から除きます。")
            return
        row = selected[0]
        status = "既に復元済みです。" if row["restored_at"] else "復元できます。"
        self.detail_var.set(f"請求ID: {row['external_id']}　{status} 振分と保全済みPDFも復元します。")

    def restore_selected(self) -> None:
        rows = [self.rows_by_item[item] for item in self.tree.selection() if not self.rows_by_item[item]["restored_at"]]
        if not rows:
            return
        if not messagebox.askyesno(
            "請求書を復元",
            f"選択した{len(rows)}件の請求書を請求一覧へ戻します。\n"
            "同じ請求IDが既にある場合は復元できません。続けますか？",
            parent=self,
        ):
            return
        try:
            restored, missing_files = restore_deleted_invoices([int(row["id"]) for row in rows])
        except Exception as exc:
            messagebox.showerror("請求書の復元", str(exc), parent=self)
            return
        self.refresh()
        if self.on_restored:
            self.on_restored()
        message = f"{restored}件を復元しました。"
        if missing_files:
            message += f"\nPDF {len(missing_files)}件は削除履歴に見つからず、請求情報と振分だけを復元しました。"
        messagebox.showinfo("請求書の復元", message, parent=self)
