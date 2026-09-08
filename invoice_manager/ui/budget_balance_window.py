from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from invoice_manager.services.budget_consumption import build_budget_consumption


class BudgetBalanceWindow(tk.Toplevel):
    """Large, read-only budget balance view for one project."""

    def __init__(self, master, project_id: int, project_label: str) -> None:
        super().__init__(master)
        self.project_id = project_id
        self.title(f"予算残高 - {project_label}")
        self.geometry("1080x680")
        self.minsize(850, 500)
        self.summary_var = tk.StringVar()
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text=project_label, font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="再表示", command=self.reload).pack(side=tk.RIGHT)
        ttk.Label(self, textvariable=self.summary_var, padding=(10, 0, 10, 8)).pack(anchor=tk.W)

        body = ttk.Panedwindow(self, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        table_frame = ttk.Frame(body)
        bars_frame = ttk.Frame(body)
        body.add(table_frame, weight=2)
        body.add(bars_frame, weight=3)
        columns = ("code", "name", "budget", "actual", "remaining", "rate", "unconfirmed")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=7)
        for key, label, width in (
            ("code", "正式コード", 120), ("name", "科目", 180), ("budget", "予算", 130),
            ("actual", "確認済み実績", 130), ("remaining", "残予算", 130),
            ("rate", "消化率", 90), ("unconfirmed", "未確認", 80),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=tk.E if key not in {"code", "name"} else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(bars_frame, background="white", highlightthickness=1)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        self.summary = None
        self.reload()

    def reload(self) -> None:
        self.summary = build_budget_consumption(self.project_id)
        self.tree.delete(*self.tree.get_children())
        total_budget = sum(row.budget_net for row in self.summary.rows)
        actual = sum(row.actual_net for row in self.summary.rows)
        self.summary_var.set(f"予算 {total_budget:,} 円　確認済み実績 {actual:,} 円　残額 {total_budget - actual:,} 円　未確認 {len(self.summary.unconfirmed_invoices)} 件")
        for row in self.summary.rows:
            self.tree.insert("", tk.END, values=(
                row.official_work_type_code or "未登録", row.work_type_name,
                f"{row.budget_net:,}", f"{row.actual_net:,}", f"{row.remaining_net:,}",
                "-" if row.utilization_rate is None else f"{row.utilization_rate * 100:.1f}%",
                row.unconfirmed_invoice_count,
            ))
        self.after_idle(self._draw)

    def _draw(self) -> None:
        if self.summary is None:
            return
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 780)
        rows = self.summary.rows
        if not rows:
            self.canvas.create_text(20, 20, anchor=tk.NW, text="予算が未登録です。")
            return
        self.canvas.create_text(16, 14, anchor=tk.NW, text="枠線: 予算　青: 確認済み実績　白: 残予算")
        y = 44
        bar_left, bar_width = 210, width - 250
        for row in rows:
            self.canvas.create_text(16, y + 11, anchor=tk.W, text=f"{row.official_work_type_code or '未登録'}  {row.work_type_name}")
            self.canvas.create_rectangle(bar_left, y, bar_left + bar_width, y + 22, outline="#222222")
            used = 0 if row.budget_net == 0 else bar_width * min(1, row.actual_net / row.budget_net)
            self.canvas.create_rectangle(bar_left, y, bar_left + used, y + 22, fill="#4e79a7", outline="")
            self.canvas.create_text(bar_left + bar_width + 8, y + 11, anchor=tk.W, text=f"残 {row.remaining_net:,} 円")
            y += 42
