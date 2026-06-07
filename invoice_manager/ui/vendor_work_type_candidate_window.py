from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import (
    list_vendor_work_type_candidates,
    list_vendors,
    save_vendor_work_type_candidates,
)
from invoice_manager.work_type_catalog import WORK_TYPE_CODE_CATALOG, work_type_label


class VendorWorkTypeCandidateWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("取引先別工種候補")
        self.geometry("560x620")
        self.vendor_options: dict[str, int] = {}
        self.catalog_labels = [work_type_label(code, name) for code, name in WORK_TYPE_CODE_CATALOG]
        self.catalog_codes = {work_type_label(code, name): code for code, name in WORK_TYPE_CODE_CATALOG}
        self.selected_vendor_var = tk.StringVar()

        self.load_vendor_options()
        self.build()
        self.refresh()

    def load_vendor_options(self) -> None:
        self.vendor_options = {}
        for row in list_vendors():
            self.vendor_options[row["vendor_name"]] = int(row["id"])
        if self.vendor_options:
            self.selected_vendor_var.set(next(iter(self.vendor_options.keys())))

    def build(self) -> None:
        form = tk.Frame(self, padx=10, pady=10)
        form.pack(fill=tk.X)
        tk.Label(form, text="取引先").pack(anchor=tk.W)
        vendor_combo = ttk.Combobox(
            form,
            textvariable=self.selected_vendor_var,
            values=list(self.vendor_options.keys()),
            state="readonly",
            width=48,
        )
        vendor_combo.pack(fill=tk.X, pady=(4, 8))
        vendor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        tk.Label(form, text="候補にする工種コードを選択（Ctrl/Shiftで複数選択）").pack(anchor=tk.W)
        list_frame = tk.Frame(self, padx=10)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        for label in self.catalog_labels:
            self.listbox.insert(tk.END, label)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        actions = tk.Frame(self, padx=10, pady=10)
        actions.pack(fill=tk.X)
        tk.Button(actions, text="保存", command=self.save).pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="全選択", command=self.select_all).pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="選択解除", command=self.clear_selection).pack(side=tk.LEFT, padx=4)

    def selected_vendor_id(self) -> int | None:
        return self.vendor_options.get(self.selected_vendor_var.get())

    def refresh(self) -> None:
        self.listbox.selection_clear(0, tk.END)
        vendor_id = self.selected_vendor_id()
        if not vendor_id:
            return
        selected_codes = {row["code"] for row in list_vendor_work_type_candidates(vendor_id)}
        for index, label in enumerate(self.catalog_labels):
            if self.catalog_codes[label] in selected_codes:
                self.listbox.selection_set(index)

    def save(self) -> None:
        vendor_id = self.selected_vendor_id()
        if not vendor_id:
            messagebox.showwarning("取引先未選択", "取引先を選択してください。")
            return
        codes = [self.catalog_codes[self.catalog_labels[index]] for index in self.listbox.curselection()]
        saved_count = save_vendor_work_type_candidates(vendor_id, codes)
        messagebox.showinfo("保存完了", f"{saved_count}件の候補を保存しました。")

    def select_all(self) -> None:
        self.listbox.selection_set(0, tk.END)

    def clear_selection(self) -> None:
        self.listbox.selection_clear(0, tk.END)
