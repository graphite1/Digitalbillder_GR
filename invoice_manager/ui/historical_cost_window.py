from __future__ import annotations

import inspect
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import ttk

from invoice_manager.services.historical_costs import (
    ACTUAL_SOURCE,
    PLANNED_SOURCE,
    has_historical_costs,
    get_historical_sync_status,
    list_costs,
    list_historical_cost_filter_options,
    list_historical_work_type_suggestions,
)
from invoice_manager.utils.money_utils import format_amount
from invoice_manager.ui.background_activity import BackgroundActivity, ActivityPanel
from invoice_manager.services.operation_cancellation import (
    CancellationToken, OperationCancelled, cancellation_scope, check_cancelled,
)


ALL_LABEL = "すべて"
SOURCE_ACTUAL_LABEL = "Web保管済み実績"
SOURCE_WITH_PLANNED_LABEL = "Web実績＋ローカル振分（予定）"


class HistoricalCostWindow(tk.Toplevel):
    """Read-only cost and learned-candidate view for archived Web invoices."""

    def __init__(self, master, on_refresh_history: Callable[..., object] | None = None,
                 on_full_refresh_history: Callable[..., object] | None = None) -> None:
        super().__init__(master)
        self.title("保管済み請求書の実績・履歴候補")
        self.geometry("1120x680")
        self.minsize(880, 520)
        self.on_refresh_history = on_refresh_history
        self.on_full_refresh_history = on_full_refresh_history

        self.project_var = tk.StringVar(value=ALL_LABEL)
        self.vendor_var = tk.StringVar(value=ALL_LABEL)
        self.work_type_var = tk.StringVar(value=ALL_LABEL)
        self.source_var = tk.StringVar(value=SOURCE_ACTUAL_LABEL)
        self.suggestion_vendor_var = tk.StringVar()
        self.suggestion_project_var = tk.StringVar(value=ALL_LABEL)
        self.project_codes: dict[str, str | None] = {ALL_LABEL: None}
        self.work_type_codes: dict[str, str | None] = {ALL_LABEL: None}
        self.suggestion_project_codes: dict[str, str | None] = {ALL_LABEL: None}
        self.refresh_button: ttk.Button | None = None
        self.full_refresh_button: ttk.Button | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.closing = False
        self.poll_id: str | None = None
        self.activity = BackgroundActivity(self, "保管済み実績の取得")

        self._build()
        self.reload()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add=True)
        self.poll_id = self.after(100, self._poll_events)

    def _build(self) -> None:
        ActivityPanel(self, activity=self.activity).pack(fill=tk.X, padx=10, pady=(8, 0))
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="表示を更新", command=self.reload).pack(side=tk.LEFT)
        if self.on_refresh_history is not None:
            self.refresh_button = ttk.Button(
                toolbar, text="追加・変更分を確認", command=self._run_refresh
            )
            self.refresh_button.pack(side=tk.LEFT, padx=(8, 0))
        if self.on_full_refresh_history is not None:
            self.full_refresh_button = ttk.Button(toolbar, text="全件を再検証（時間がかかります）", command=lambda: self._run_refresh(full=True))
            self.full_refresh_button.pack(side=tk.LEFT, padx=8)
        ttk.Label(self, text="実績＝保管済みの査定金額。履歴候補＝会社別によく使う工種。請求書を重複登録する機能ではありません。", wraplength=1000).pack(anchor=tk.W, padx=10)
        ttk.Label(self, text="通常は確認済み明細を再利用します。過去の査定だけをWebで修正したときは「全件を再検証」を使ってください。", wraplength=1000).pack(anchor=tk.W, padx=10, pady=(2, 4))
        self.history_status = ttk.Label(self, text="", wraplength=1000)
        self.history_status.pack(anchor=tk.W, padx=10, pady=(0, 6))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self._build_cost_tab()
        self._build_suggestion_tab()

    def _build_cost_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="工事・取引先・工種別コスト")

        filters = ttk.Frame(frame)
        filters.pack(fill=tk.X, pady=(0, 8))
        self.project_combo = self._filter_combo(filters, "工事", self.project_var, 30)
        self.vendor_combo = self._filter_combo(filters, "取引先", self.vendor_var, 26)
        self.work_type_combo = self._filter_combo(filters, "工種", self.work_type_var, 28)
        self.source_combo = self._filter_combo(filters, "表示対象", self.source_var, 31)
        self.source_combo.configure(values=(SOURCE_ACTUAL_LABEL, SOURCE_WITH_PLANNED_LABEL))
        for combo in (self.project_combo, self.vendor_combo, self.work_type_combo, self.source_combo):
            combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_costs())

        columns = (
            "source", "project", "vendor", "work_type", "invoice_count",
            "line_count", "net", "gross",
        )
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill=tk.BOTH, expand=True)
        self.cost_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "source": "区分",
            "project": "工事",
            "vendor": "取引先",
            "work_type": "工種",
            "invoice_count": "請求書数",
            "line_count": "振分行数",
            "net": "税抜金額",
            "gross": "税込金額",
        }
        widths = {
            "source": 155, "project": 200, "vendor": 160, "work_type": 190,
            "invoice_count": 75, "line_count": 75, "net": 110, "gross": 110,
        }
        for column in columns:
            anchor = tk.E if column in {"invoice_count", "line_count", "net", "gross"} else tk.W
            self.cost_tree.heading(column, text=headings[column])
            self.cost_tree.column(column, width=widths[column], anchor=anchor)
        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.cost_tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.cost_tree.xview)
        self.cost_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.cost_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.cost_empty_label = ttk.Label(frame, text="")
        self.cost_empty_label.pack(fill=tk.X, pady=(7, 0))

    def _build_suggestion_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="履歴からの工種候補")

        filters = ttk.Frame(frame)
        filters.pack(fill=tk.X, pady=(0, 8))
        self.suggestion_vendor_combo = self._filter_combo(
            filters, "取引先（必須）", self.suggestion_vendor_var, 30
        )
        self.suggestion_project_combo = self._filter_combo(
            filters, "工事（任意）", self.suggestion_project_var, 34
        )
        self.suggestion_vendor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_suggestions())
        self.suggestion_project_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_suggestions())

        columns = ("work_type", "invoice_count", "line_count", "net", "gross")
        self.suggestion_tree = ttk.Treeview(frame, columns=columns, show="headings")
        headings = {
            "work_type": "候補工種（請求書利用回数→税抜実績の順）",
            "invoice_count": "利用請求書数",
            "line_count": "振分行数",
            "net": "税抜実績",
            "gross": "税込実績",
        }
        widths = {"work_type": 440, "invoice_count": 110, "line_count": 90, "net": 130, "gross": 130}
        for column in columns:
            anchor = tk.W if column == "work_type" else tk.E
            self.suggestion_tree.heading(column, text=headings[column])
            self.suggestion_tree.column(column, width=widths[column], anchor=anchor)
        self.suggestion_tree.pack(fill=tk.BOTH, expand=True)
        self.suggestion_empty_label = ttk.Label(
            frame,
            text="履歴候補は手動の取引先別工種候補を変更せず、別の参考情報として表示します。",
        )
        self.suggestion_empty_label.pack(fill=tk.X, pady=(6, 0))

    def _filter_combo(self, parent, label: str, variable: tk.StringVar, width: int) -> ttk.Combobox:
        group = ttk.Frame(parent)
        group.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(group, text=label).pack(anchor=tk.W)
        combo = ttk.Combobox(group, textvariable=variable, state="readonly", width=width)
        combo.pack(anchor=tk.W, pady=(2, 0))
        return combo

    def reload(self) -> None:
        options = list_historical_cost_filter_options()
        project_labels = [f"{code} {name}" for code, name in options.projects]
        self.project_codes = {ALL_LABEL: None, **dict(zip(project_labels, (code for code, _ in options.projects)))}
        self.suggestion_project_codes = dict(self.project_codes)
        work_type_labels = [f"{code} {name}" for code, name in options.work_types]
        self.work_type_codes = {ALL_LABEL: None, **dict(zip(work_type_labels, (code for code, _ in options.work_types)))}

        self._set_combo_values(self.project_combo, self.project_var, list(self.project_codes), ALL_LABEL)
        self._set_combo_values(self.vendor_combo, self.vendor_var, [ALL_LABEL, *options.vendors], ALL_LABEL)
        self._set_combo_values(self.work_type_combo, self.work_type_var, list(self.work_type_codes), ALL_LABEL)
        self._set_combo_values(
            self.suggestion_vendor_combo,
            self.suggestion_vendor_var,
            list(options.vendors),
            options.vendors[0] if options.vendors else "",
        )
        self._set_combo_values(
            self.suggestion_project_combo,
            self.suggestion_project_var,
            list(self.suggestion_project_codes),
            ALL_LABEL,
        )
        status = get_historical_sync_status()
        if status.last_successful_refresh:
            try:
                refreshed = datetime.fromisoformat(status.last_successful_refresh).astimezone().strftime("%Y/%m/%d %H:%M")
            except ValueError:
                refreshed = status.last_successful_refresh
            status_text = f"最終取得: {refreshed}（現在有効 {status.active_invoice_count}件）"
        elif has_historical_costs():
            status_text = "保管済み履歴を取得済み（最終取得日時なし）"
        else:
            status_text = "保管済み履歴はまだ取得されていません"
        self.history_status.configure(text=status_text)
        self.refresh_costs()
        self.refresh_suggestions()

    @staticmethod
    def _set_combo_values(combo: ttk.Combobox, variable: tk.StringVar, values: list[str], fallback: str) -> None:
        combo.configure(values=values)
        if variable.get() not in values:
            variable.set(fallback)

    def refresh_costs(self) -> None:
        self.cost_tree.delete(*self.cost_tree.get_children())
        include_planned = self.source_var.get() == SOURCE_WITH_PLANNED_LABEL
        rows = list_costs(
            project_code=self.project_codes.get(self.project_var.get()),
            vendor_name=None if self.vendor_var.get() == ALL_LABEL else self.vendor_var.get(),
            work_type_code=self.work_type_codes.get(self.work_type_var.get()),
            include_planned=include_planned,
        )
        source_labels = {ACTUAL_SOURCE: "Web保管済み実績", PLANNED_SOURCE: "ローカル振分（予定）"}
        for row in rows:
            self.cost_tree.insert(
                "",
                tk.END,
                values=(
                    source_labels[row.source],
                    f"{row.project_code} {row.project_name}",
                    row.vendor_name,
                    f"{row.work_type_code} {row.work_type_name}",
                    row.invoice_count,
                    row.allocation_line_count,
                    format_amount(row.net_amount),
                    format_amount(row.gross_amount),
                ),
            )
        if rows:
            self.cost_empty_label.configure(text=f"{len(rows)}件の集計結果")
        elif has_historical_costs():
            self.cost_empty_label.configure(text="選択した条件に該当する履歴はありません。")
        else:
            self.cost_empty_label.configure(text="保管済み履歴が未取得のため、実績金額は未確認です。")

    def refresh_suggestions(self) -> None:
        self.suggestion_tree.delete(*self.suggestion_tree.get_children())
        vendor_name = self.suggestion_vendor_var.get().strip()
        if not vendor_name:
            self.suggestion_empty_label.configure(text="履歴取得後、取引先を選ぶと候補が表示されます。")
            return
        rows = list_historical_work_type_suggestions(
            vendor_name,
            self.suggestion_project_codes.get(self.suggestion_project_var.get()),
        )
        for row in rows:
            self.suggestion_tree.insert(
                "",
                tk.END,
                values=(
                    f"{row.work_type_code} {row.work_type_name}",
                    row.invoice_count,
                    row.allocation_line_count,
                    format_amount(row.net_amount),
                    format_amount(row.gross_amount),
                ),
            )
        self.suggestion_empty_label.configure(
            text=(
                "履歴候補は手動設定を変更しません。利用請求書数と金額は右列で確認できます。"
                if rows else "選択した条件に該当する保管済み履歴はありません。"
            )
        )

    def _run_refresh(self, *, full: bool = False) -> None:
        callback = self.on_full_refresh_history if full else self.on_refresh_history
        if callback is None or self.busy or self.closing:
            return
        self.busy = True
        if self.refresh_button is not None:
            self.refresh_button.configure(state=tk.DISABLED)
        if self.full_refresh_button is not None:
            self.full_refresh_button.configure(state=tk.DISABLED)
        token = self.cancellation = CancellationToken()
        self.activity.start("保管済み履歴を全件再検証中…" if full else "保管済み履歴の追加・変更分を確認中…", cancellation=token)
        events = self.events

        def progress(message: str) -> None:
            check_cancelled()
            events.put(("progress", str(message)))

        def worker() -> None:
            try:
                if callback is None:
                    return
                try:
                    accepts_progress = bool(inspect.signature(callback).parameters)
                except (TypeError, ValueError):
                    accepts_progress = False
                with cancellation_scope(token):
                    result = callback(progress) if accepts_progress else callback()
            except OperationCancelled as exc:
                events.put(("cancelled", str(exc)))
            except Exception as exc:
                events.put(("cancelled", str(OperationCancelled())) if token.requested else ("error", exc))
            else:
                events.put(("done", result))

        try:
            threading.Thread(target=worker, name="historical-cost-refresh", daemon=False).start()
        except Exception as exc:
            events.put(("error", exc))

    def _poll_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.activity.update(str(value))
                elif kind == "cancelled":
                    self.busy = False
                    if self.refresh_button is not None:
                        self.refresh_button.configure(state=tk.NORMAL)
                    if self.full_refresh_button is not None:
                        self.full_refresh_button.configure(state=tk.NORMAL)
                    self.activity.finish(str(value), cancelled=True)
                elif kind == "done":
                    self.busy = False
                    try:
                        self._refresh_completed(value)
                    except Exception as exc:
                        self._refresh_failed(exc)
                elif kind == "error":
                    self.busy = False
                    error = value if isinstance(value, Exception) else RuntimeError(str(value))
                    self._refresh_failed(error)
        except queue.Empty:
            pass
        if not self.closing:
            self.poll_id = self.after(100, self._poll_events)

    def _refresh_completed(self, result: object) -> None:
        if self.refresh_button is not None:
            self.refresh_button.configure(state=tk.NORMAL)
        if self.full_refresh_button is not None:
            self.full_refresh_button.configure(state=tk.NORMAL)
        self.reload()
        message = result.strip() if isinstance(result, str) and result.strip() else "保管済み履歴の取得が完了しました。"
        self.activity.finish(message)

    def _refresh_failed(self, error: Exception) -> None:
        if self.refresh_button is not None:
            self.refresh_button.configure(state=tk.NORMAL)
        if self.full_refresh_button is not None:
            self.full_refresh_button.configure(state=tk.NORMAL)
        suffix = "保存済みの実績は「表示を更新」で確認できます。"
        self.history_status.configure(text=f"履歴取得に失敗しました。{suffix}")
        self.activity.finish(f"履歴取得に失敗しました: {error}。{suffix}", failed=True)

    def close(self) -> None:
        if self.busy:
            self.withdraw()
            return
        self.closing = True
        self._cancel_poll()
        self.destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self.closing = True
        self._cancel_poll()

    def _cancel_poll(self) -> None:
        poll_id, self.poll_id = self.poll_id, None
        if poll_id is None:
            return
        try:
            self.after_cancel(poll_id)
        except tk.TclError:
            pass
