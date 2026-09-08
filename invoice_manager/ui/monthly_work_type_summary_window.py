from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from invoice_manager.services.historical_costs import (
    list_archived_billing_months,
    list_historical_cost_filter_options,
    list_monthly_archived_work_type_summary,
)
from invoice_manager.utils.money_utils import format_amount


class MonthlyWorkTypeSummaryWindow(tk.Toplevel):
    """Trial, read-only monthly work-type summary sourced from archived Web data."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("月別工種振分集計（試験）")
        self.geometry("900x580")
        self.minsize(760, 460)
        self.project_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        self.project_codes: dict[str, str] = {}
        self._build()
        self.reload_projects()

    def _build(self) -> None:
        ttk.Label(
            self,
            text="試験表示: 保管済み請求書の請求日から月を判定して集計します。データの保存・変更は行いません。",
            wraplength=840,
        ).pack(anchor=tk.W, padx=12, pady=(12, 6))
        filters = ttk.Frame(self, padding=(12, 4))
        filters.pack(fill=tk.X)
        ttk.Label(filters, text="工事").grid(row=0, column=0, sticky=tk.W)
        self.project_combo = ttk.Combobox(filters, textvariable=self.project_var, state="readonly", width=48)
        self.project_combo.grid(row=1, column=0, sticky=tk.EW, padx=(0, 10))
        ttk.Label(filters, text="請求月").grid(row=0, column=1, sticky=tk.W)
        self.month_combo = ttk.Combobox(filters, textvariable=self.month_var, state="readonly", width=14)
        self.month_combo.grid(row=1, column=1, sticky=tk.W, padx=(0, 10))
        ttk.Button(filters, text="表示を更新", command=self.refresh).grid(row=1, column=2, sticky=tk.W)
        filters.columnconfigure(0, weight=1)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self.reload_months())
        self.month_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        columns = ("code", "name", "invoice_count", "line_count", "net", "gross")
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=columns, show="headings")
        headings = {
            "code": "工種コード", "name": "工種名", "invoice_count": "請求書数",
            "line_count": "振分行数", "net": "振分金額(税抜)", "gross": "振分金額(税込)",
        }
        widths = {"code": 110, "name": 220, "invoice_count": 85, "line_count": 85, "net": 140, "gross": 140}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.E if column in {"invoice_count", "line_count", "net", "gross"} else tk.W)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(self, textvariable=self.summary_var, padding=(12, 0, 12, 12)).pack(anchor=tk.W)

    def reload_projects(self) -> None:
        options = list_historical_cost_filter_options()
        self.project_codes = {f"{code} {name}": code for code, name in options.projects}
        labels = list(self.project_codes)
        self.project_combo.configure(values=labels)
        self.project_var.set(labels[0] if labels else "")
        self.reload_months()

    def reload_months(self) -> None:
        project_code = self.project_codes.get(self.project_var.get())
        months = list_archived_billing_months(project_code) if project_code else ()
        self.month_combo.configure(values=months)
        self.month_var.set(months[0] if months else "")
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        project_code = self.project_codes.get(self.project_var.get())
        billing_month = self.month_var.get()
        if not project_code or not billing_month:
            self.summary_var.set("保管済みデータに対象工事または請求月がありません。")
            return
        rows = list_monthly_archived_work_type_summary(project_code, billing_month)
        net_total = gross_total = 0
        for row in rows:
            net_total += row.net_amount
            gross_total += row.gross_amount
            self.tree.insert("", tk.END, values=(
                row.work_type_code, row.work_type_name, row.invoice_count, row.allocation_line_count,
                f"{format_amount(row.net_amount)}円", f"{format_amount(row.gross_amount)}円",
            ))
        self.summary_var.set(
            f"{billing_month}: {len(rows)}工種　振分合計（税抜）{format_amount(net_total)}円　（税込）{format_amount(gross_total)}円"
            if rows else f"{billing_month}: 保管済みの振分データはありません。"
        )
