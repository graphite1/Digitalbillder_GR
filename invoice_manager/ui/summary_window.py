from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from invoice_manager.repositories import get_summary
from invoice_manager.utils.date_utils import format_billing_month


class SummaryWindow(tk.Toplevel):
    def __init__(self, master, initial_tab: str | None = None) -> None:
        super().__init__(master)
        self.title("集計")
        self.geometry("760x520")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.load(initial_tab)

    def load(self, initial_tab: str | None = None) -> None:
        selected_index = 0
        for title, rows in get_summary().items():
            frame = tk.Frame(self.notebook)
            self.notebook.add(frame, text=title)
            if title == initial_tab:
                selected_index = len(self.notebook.tabs()) - 1
            tree = ttk.Treeview(frame, columns=("label", "count", "total"), show="headings")
            tree.heading("label", text="項目")
            tree.heading("count", text="件数")
            tree.heading("total", text="請求金額合計")
            tree.column("label", width=480)
            tree.column("count", width=80, anchor=tk.E)
            tree.column("total", width=140, anchor=tk.E)
            tree.pack(fill=tk.BOTH, expand=True)
            for row in rows:
                tree.insert("", tk.END, values=(self.format_row_label(title, row), row["count"], row["total"] or ""))
        self.notebook.select(selected_index)

    def format_row_label(self, title: str, row) -> str:
        label = self.format_label(title, row["label"])
        if "工種コード" in title:
            return f"{label} / {row['work_type_code']} {row['work_type_name']}"
        return label

    def format_label(self, title: str, label: str) -> str:
        if "月" not in title:
            return label
        try:
            return format_billing_month(label)
        except ValueError:
            return label
