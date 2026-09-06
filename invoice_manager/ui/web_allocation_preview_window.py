from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from invoice_manager.services.web_allocation_guard import WebWriteGuard
from invoice_manager.services.web_allocation_plan import build_allocation_plan, compare_allocations
from invoice_manager.services.web_invoice_reader import read_for_plan
from invoice_manager.services.operation_cancellation import (
    CancellationToken, OperationCancelled, cancellation_scope, check_cancelled,
)
from invoice_manager.utils.money_utils import TAX_RATE_LABELS
from invoice_manager.ui.background_activity import BackgroundActivity, ActivityPanel


class WebAllocationPreviewWindow(tk.Toplevel):
    def __init__(self, master, invoice_id: int) -> None:
        super().__init__(master)
        self.invoice_id = invoice_id
        self.title("Web転記プレビュー")
        self.geometry("1000x680")
        self.transient(master)
        self.plan = build_allocation_plan(invoice_id)
        self.messages = queue.Queue()
        self.busy = False
        self.poll_id = None
        self.activity = BackgroundActivity(self, f"Web現在値：{self.plan.vendor_name} / {self.plan.invoice_date} / 請求ID {invoice_id}")
        self.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        ActivityPanel(frame, activity=self.activity).pack(fill=tk.X, pady=(0, 8))
        ttk.Label(frame, text=f"{self.plan.project_code}  /  {self.plan.vendor_name}  /  {self.plan.invoice_date}", font=("", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="転記の到達点は「編集を保存」です。「アクション → 次に回す」はユーザーが操作します。").pack(anchor=tk.W, pady=6)
        guard = WebWriteGuard().status()
        self.guard_label = ttk.Label(frame, text=f"Web更新: {'凍結中' if guard.state == 'frozen' else '実機検証待ち'}  {guard.reason}", foreground="#a33621", wraplength=950)
        self.guard_label.pack(anchor=tk.W, pady=4)
        columns = ("code", "name", "net", "rate", "tax", "gross")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        for key, label, width in zip(columns, ("工種コード", "工種名", "税抜金額", "税率", "消費税額", "税込金額"), (110, 230, 130, 80, 130, 130)):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor=tk.W if key in ("code", "name") else tk.E)
        tree.pack(fill=tk.X, pady=8)
        for line in self.plan.lines:
            tree.insert("", tk.END, values=(line.code, line.name, f"{line.amount_excluded:,}", TAX_RATE_LABELS.get(line.tax_rate, line.tax_rate), f"{line.tax_amount:,}", f"{line.amount_included:,}"))
        ttk.Label(frame, text=f"ローカル振分合計(税込): {self.plan.total_included:,}円   請求書(税込): {self.plan.invoice_amount:,}円").pack(anchor=tk.W)
        ttk.Label(frame, text="\n".join(self.plan.errors) or "ローカル振分の金額・工種を確認しました。", foreground="#a33621" if self.plan.errors else "#236442", wraplength=950).pack(anchor=tk.W, pady=6)
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=6)
        self.read_button = ttk.Button(actions, text="Webの現在値を取得して差分表示", command=self.read_current)
        self.read_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="Webへ編集を保存（実機検証待ち）", state=tk.DISABLED).pack(side=tk.LEFT, padx=10)
        self.progress = tk.StringVar(value="Webの現在値は未取得です。差分を確認してから転記する設計です。")
        ttk.Label(frame, textvariable=self.progress, wraplength=950).pack(anchor=tk.W, pady=4)
        self.diff_tree = ttk.Treeview(frame, columns=("row", "field", "local", "web"), show="headings", height=8)
        for key, label, width in (("row", "行", 50), ("field", "項目", 140), ("local", "アプリの入力", 330), ("web", "Webの現在値", 330)):
            self.diff_tree.heading(key, text=label)
            self.diff_tree.column(key, width=width)
        self.diff_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(frame, text="閉じる", command=self.close).pack(anchor=tk.E, pady=(8, 0))

    def read_current(self) -> None:
        if self.busy:
            return
        try:
            changed = build_allocation_plan(self.invoice_id).fingerprint != self.plan.fingerprint
        except Exception as exc:
            self.activity.finish(f"振分の確認に失敗しました: {exc}", failed=True)
            return
        if changed:
            self.activity.finish("振分が変更されています。この画面を閉じて、プレビューを開き直してください。", failed=True)
            return
        token = self.cancellation = CancellationToken()
        self.activity.start("対象請求書のWeb現在値を確認中… 他の画面も操作できます。", cancellation=token)
        self.busy = True
        self.read_button.configure(state=tk.DISABLED)
        self.diff_tree.delete(*self.diff_tree.get_children())

        def progress(message):
            check_cancelled()
            self.messages.put(("progress", message))

        def worker():
            try:
                with cancellation_scope(token):
                    result = read_for_plan(self.plan, progress)
                    check_cancelled()
                self.messages.put(("result", result))
            except OperationCancelled as exc:
                self.messages.put(("cancelled", str(exc)))
            except Exception as exc:
                self.messages.put(("cancelled", str(OperationCancelled())) if token.requested else ("error", str(exc)))

        try:
            threading.Thread(target=worker, name="web-allocation-read", daemon=False).start()
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        self.poll_id = self.after(100, self.poll)

    def poll(self) -> None:
        self.poll_id = None
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "progress":
                if self.cancellation.requested:
                    self.progress.set("中断を待っています。現在の通信が終了するまでお待ちください。")
                    continue
                self.progress.set(value)
                self.activity.update(str(value))
                continue
            self.busy = False
            self.read_button.configure(state=tk.NORMAL)
            if kind == "cancelled" or (kind == "result" and self.cancellation.requested):
                value = str(OperationCancelled())
                self.progress.set(str(value))
                self.activity.finish(str(value), cancelled=True)
                continue
            try:
                if kind == "error":
                    raise RuntimeError(str(value))
                if build_allocation_plan(self.invoice_id).fingerprint != self.plan.fingerprint:
                    self.progress.set("読取り中にローカル振分が変わりました。この画面を閉じて再確認してください。")
                    self.activity.finish(self.progress.get(), failed=True)
                else:
                    differences = compare_allocations(self.plan.lines, value.lines)
                    for diff in differences:
                        self.diff_tree.insert("", tk.END, values=(diff.row_number, diff.field, diff.local, diff.web))
                    status = "保管済みのため編集できません。" if value.archived else "編集可否は実機検証待ちです。"
                    self.progress.set(f"差分 {len(differences)}項目。{status} 取得時点の値を表示しています。")
                    self.activity.finish(self.progress.get())
            except Exception as exc:
                self.progress.set(f"現在値を確認できませんでした: {exc}。Webへの変更はありません。")
                self.activity.finish(self.progress.get(), failed=True)
        if self.busy:
            self.poll_id = self.after(100, self.poll)

    def close(self) -> None:
        if self.busy:
            self.withdraw()
            return
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
        self.destroy()
