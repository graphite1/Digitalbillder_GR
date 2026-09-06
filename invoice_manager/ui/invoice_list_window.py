from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import messagebox, ttk

from invoice_manager.repositories import (
    delete_invoices,
    get_app_setting,
    recalculate_invoice_billing_months,
    list_billing_months,
    list_invoice_dates,
    list_invoice_files,
    list_invoices,
    list_projects,
    list_vendors,
    set_app_setting,
    update_invoice_billing_month,
    update_invoice_memo,
)
from invoice_manager.utils.date_utils import (
    format_billing_month,
    format_invoice_date,
    validate_billing_month,
)
from invoice_manager.utils.file_safety import validate_original_pdf_path
from invoice_manager.utils.money_utils import format_amount, tax_excluded_amount
from invoice_manager.ui.background_activity import ActivityPanel, running_activities
from invoice_manager.services.test_tools_access import can_use_test_tools, require_test_tools_access


AMOUNT_DISPLAY_SETTING_KEY = "amount_display_mode"
AMOUNT_DISPLAY_MODES = ("税抜", "税込")
PROJECT_SELECTION_SETTING_KEY = "selected_project_id"


def billing_month_row_tags(months) -> dict[str, str]:
    return {month: "month_white" if index % 2 == 0 else "month_gray"
            for index, month in enumerate(sorted({str(month or "") for month in months}))}


class InvoiceListWindow(tk.Toplevel):
    def __init__(
        self,
        master,
        on_close: Callable[[], None] | None = None,
        open_hub: Callable[[tk.Toplevel], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("請求一覧【開発本体】" if os.environ.get("DIGITALBUILDER_DEVELOPMENT") == "1" else "請求一覧")
        self.geometry("1440x840")
        self.minsize(1100, 740)
        self.on_close = on_close
        self.open_hub_callback = open_hub
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
        saved_amount_display_mode = get_app_setting(AMOUNT_DISPLAY_SETTING_KEY)
        if saved_amount_display_mode not in AMOUNT_DISPLAY_MODES:
            saved_amount_display_mode = "税抜"
            set_app_setting(AMOUNT_DISPLAY_SETTING_KEY, saved_amount_display_mode)
        self.amount_display_var = tk.StringVar(value=saved_amount_display_mode)

        self.memo_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="表示件数: 0件    請求金額合計: 0")
        self.load_project_options()
        self.restore_project_selection()
        self.load_vendor_options()
        self.load_month_options()
        self.load_invoice_date_options()

        self._build_filters()
        self._build_tree()
        self._build_actions()
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.bind("<FocusIn>", self._refresh_test_access, add=True)
        self.refresh()

    def _build_filters(self) -> None:
        header = ttk.Frame(self, padding=(14, 10))
        header.pack(fill=tk.X)
        ttk.Label(header, text="請求一覧", font=("", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.summary_var, padding=(20, 0)).pack(side=tk.LEFT)
        if self.open_hub_callback is not None:
            ttk.Button(header, text="管理メニュー", command=self.open_hub).pack(side=tk.RIGHT)
        ttk.Button(header, text="一覧を再表示", command=self.reload_filter_options).pack(side=tk.RIGHT, padx=8)

        frame = ttk.LabelFrame(self, text="絞り込み・表示", padding=(10, 8))
        frame.pack(fill=tk.X, padx=12)
        frame.columnconfigure(1, weight=3)
        frame.columnconfigure(5, weight=2)
        ttk.Label(frame, text="工事").grid(row=0, column=0, sticky=tk.W, padx=4)
        self.project_combo = ttk.Combobox(frame, textvariable=self.selected_project_var,
                                         values=list(self.project_options), state="readonly", width=52)
        self.project_combo.grid(row=0, column=1, sticky=tk.EW, padx=4)
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_filter_selected)
        ttk.Label(frame, text="請求月").grid(row=0, column=2, padx=(12, 4))
        self.month_combo = ttk.Combobox(frame, textvariable=self.selected_month_var,
                                       values=list(self.month_options), state="readonly", width=13)
        self.month_combo.grid(row=0, column=3, padx=4)
        self.month_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        ttk.Label(frame, text="取引先").grid(row=0, column=4, padx=(12, 4))
        self.vendor_combo = ttk.Combobox(frame, textvariable=self.selected_vendor_var,
                                        values=list(self.vendor_options), state="readonly", width=27)
        self.vendor_combo.grid(row=0, column=5, sticky=tk.EW, padx=4)
        self.vendor_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)

        options = ttk.Frame(frame)
        options.grid(row=1, column=0, columnspan=6, sticky=tk.EW, pady=(10, 0))
        ttk.Label(options, text="請求日").pack(side=tk.LEFT, padx=4)
        self.date_from_combo = ttk.Combobox(options, textvariable=self.selected_date_from_var,
                                           values=list(self.invoice_date_options), state="readonly", width=12)
        self.date_from_combo.pack(side=tk.LEFT, padx=4)
        self.date_from_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        ttk.Label(options, text="～").pack(side=tk.LEFT)
        self.date_to_combo = ttk.Combobox(options, textvariable=self.selected_date_to_var,
                                         values=list(self.invoice_date_options), state="readonly", width=12)
        self.date_to_combo.pack(side=tk.LEFT, padx=4)
        self.date_to_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        ttk.Label(options, text="並び順").pack(side=tk.LEFT, padx=(16, 4))
        self.sort_combo = ttk.Combobox(options, textvariable=self.selected_sort_var,
                                      values=list(self.sort_options), state="readonly", width=20)
        self.sort_combo.pack(side=tk.LEFT, padx=4)
        self.sort_combo.bind("<<ComboboxSelected>>", self.on_filter_selected)
        ttk.Label(options, text="金額表示").pack(side=tk.LEFT, padx=(16, 4))
        self.amount_display_combo = ttk.Combobox(options, textvariable=self.amount_display_var,
                                                values=AMOUNT_DISPLAY_MODES, state="readonly", width=7)
        self.amount_display_combo.pack(side=tk.LEFT, padx=4)
        self.amount_display_combo.bind("<<ComboboxSelected>>", self.on_amount_display_selected)
        ttk.Label(options, text="請求月ごとに白／薄灰で色分け", foreground="#555555").pack(side=tk.RIGHT, padx=4)
        ActivityPanel(self).pack(fill=tk.X, padx=12, pady=(8, 0))

    def open_hub(self) -> None:
        if self.open_hub_callback is not None:
            self.open_hub_callback(self)

    def close_window(self) -> None:
        from invoice_manager.ui.main_window import has_busy_update
        if running_activities(self) or has_busy_update(self._root()):
            messagebox.showinfo("処理中", "検索・取得・取込が動いています。ほかの操作は続けられますが、アプリの終了は処理完了後に行ってください。", parent=self)
            return
        self.save_project_selection()
        if self.on_close is not None:
            self.on_close()
            return
        self.destroy()

    def load_project_options(self) -> None:
        self.project_options = {"すべて": None}
        for row in list_projects(active_only=True):
            label = f"{row['project_code']}｜{row['project_name']}"
            self.project_options[label] = int(row["id"])

    def restore_project_selection(self) -> None:
        saved_project_id = get_app_setting(PROJECT_SELECTION_SETTING_KEY)
        if not saved_project_id:
            return
        selected_label = next(
            (
                label
                for label, project_id in self.project_options.items()
                if project_id is not None and str(project_id) == saved_project_id
            ),
            "すべて",
        )
        self.selected_project_var.set(selected_label)
        if selected_label == "すべて":
            set_app_setting(PROJECT_SELECTION_SETTING_KEY, "")

    def save_project_selection(self) -> None:
        project_id = self.project_options.get(self.selected_project_var.get())
        set_app_setting(PROJECT_SELECTION_SETTING_KEY, "" if project_id is None else str(project_id))

    def load_vendor_options(self) -> None:
        self.vendor_options = {"すべて": None}
        for row in list_vendors(active_projects_only=True):
            self.vendor_options[row["vendor_name"]] = int(row["id"])

    def load_month_options(self) -> None:
        months = list_billing_months(include_blank=True, active_projects_only=True)
        self.month_options = {"すべて": None}
        if "" in months:
            self.month_options["未設定"] = "__blank__"
        for month in sorted((month for month in months if month), reverse=True):
            self.month_options[format_billing_month(month)] = month

    def load_invoice_date_options(self) -> None:
        self.invoice_date_options = {"すべて": None}
        for date_text in list_invoice_dates(active_projects_only=True):
            self.invoice_date_options[format_invoice_date(date_text)] = date_text

    def reload_filter_options(self) -> None:
        self.load_project_options()
        self.load_vendor_options()
        self.load_month_options()
        self.load_invoice_date_options()

        option_widgets = [
            (self.project_combo, self.selected_project_var, self.project_options),
            (self.vendor_combo, self.selected_vendor_var, self.vendor_options),
            (self.month_combo, self.selected_month_var, self.month_options),
            (self.date_from_combo, self.selected_date_from_var, self.invoice_date_options),
            (self.date_to_combo, self.selected_date_to_var, self.invoice_date_options),
        ]
        for combo, variable, options in option_widgets:
            combo.configure(values=list(options.keys()))
            if variable.get() not in options:
                variable.set("すべて")
        self.restore_project_selection()
        self.refresh()

    def _build_tree(self) -> None:
        columns = ("billing_month", "project_code", "project_name", "vendor_name", "invoice_date",
                   "total_amount", "file_count", "local_memo", "contact_name", "email", "phone")
        headers = {"billing_month": "請求月", "project_code": "工事コード", "project_name": "工事名",
                   "vendor_name": "取引先", "invoice_date": "請求日",
                   "total_amount": f"請求金額({self.amount_display_var.get()})", "file_count": "添付",
                   "local_memo": "メモ", "contact_name": "担当者", "email": "メール", "phone": "電話"}
        self.content_panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self.content_panes.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        tree_frame = ttk.Frame(self.content_panes)
        self.sidebar = ttk.Frame(self.content_panes, padding=(12, 0, 0, 0), width=300)
        self.content_panes.add(tree_frame, weight=4)
        self.content_panes.add(self.sidebar, weight=1)
        style = ttk.Style(self)
        style.configure("InvoiceList.Treeview", rowheight=29)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended",
                                 style="InvoiceList.Treeview",
                                 displaycolumns=("billing_month", "project_name", "vendor_name", "invoice_date",
                                                 "total_amount", "file_count", "local_memo"))
        y_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        widths = {"billing_month": 95, "project_name": 230, "vendor_name": 175,
                  "invoice_date": 100, "total_amount": 125, "file_count": 55, "local_memo": 130}
        for column in columns:
            self.tree.heading(column, text=headers[column])
            self.tree.column(column, width=widths.get(column, 120), minwidth=50,
                             anchor=tk.E if column in ("total_amount", "file_count") else tk.W)
        self.tree.tag_configure("month_white", background="#ffffff")
        self.tree.tag_configure("month_gray", background="#eeeeee")
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        x_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<MouseWheel>", self.on_tree_mousewheel)
        self.tree.bind("<Shift-MouseWheel>", self.on_tree_shift_mousewheel)

    def _build_actions(self) -> None:
        self.action_tabs = ttk.Notebook(self.sidebar)
        self.action_tabs.pack(fill=tk.BOTH, expand=True)
        frame = ttk.Frame(self.action_tabs, padding=10)
        self.action_tabs.add(frame, text="通常操作")
        self.selected_info_var = tk.StringVar(value="一覧から請求を選択してください。")
        info_frame = ttk.LabelFrame(frame, text="選択した請求", padding=4)
        info_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.selected_info_text = tk.Text(info_frame, height=6, width=30, wrap=tk.WORD,
                                          state=tk.DISABLED, relief=tk.FLAT)
        info_scroll = ttk.Scrollbar(info_frame, command=self.selected_info_text.yview)
        info_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.selected_info_text.configure(yscrollcommand=info_scroll.set)
        self.selected_info_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def update_selected_info(*_args):
            self.selected_info_text.configure(state=tk.NORMAL)
            self.selected_info_text.delete("1.0", tk.END)
            self.selected_info_text.insert("1.0", self.selected_info_var.get())
            self.selected_info_text.configure(state=tk.DISABLED)

        self.selected_info_var.trace_add("write", update_selected_info)
        update_selected_info()
        self.detail_button = ttk.Button(frame, text="請求詳細・工種振分を開く",
                                        command=self.open_detail, state=tk.DISABLED)
        self.detail_button.pack(fill=tk.X, pady=3)
        self.pdf_button = ttk.Button(frame, text="添付PDFを開く", command=self.open_first_pdf, state=tk.DISABLED)
        self.pdf_button.pack(fill=tk.X, pady=3)
        self.billing_month_button = ttk.Button(frame, text="選択した請求の請求月を変更",
                                               command=self.change_billing_month, state=tk.DISABLED)
        self.billing_month_button.pack(fill=tk.X, pady=3)
        ttk.Separator(frame).pack(fill=tk.X, pady=10)
        ttk.Label(frame, text="メモ").pack(anchor=tk.W)
        self.memo_entry = ttk.Entry(frame, textvariable=self.memo_var, state="disabled", width=32)
        self.memo_entry.pack(fill=tk.X, pady=4)
        self.memo_button = ttk.Button(frame, text="メモを保存", command=self.save_memo, state=tk.DISABLED)
        self.memo_button.pack(fill=tk.X, pady=3)
        self.delete_button = ttk.Button(frame, text="選択した請求を削除",
                                        command=self.delete_selected_invoices, state=tk.DISABLED)
        self.delete_button.pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0))

        trial = ttk.LabelFrame(self.action_tabs, text="試験用", padding=10)
        self.trial_frame = trial
        self.action_tabs.add(trial, text="試験用（管理者）")
        self.test_access_var = tk.StringVar()
        ttk.Label(trial, textvariable=self.test_access_var, foreground="#666666").pack(anchor=tk.W)
        self.reset_month_button = ttk.Button(trial, text="表示中の請求月を自動判定に戻す",
                                              command=self.reset_displayed_billing_months)
        self.reset_month_button.pack(fill=tk.X, pady=(7, 3))
        self.recalculate_month_button = ttk.Button(trial, text="選択工事の請求月を再計算",
                                                    command=self.recalculate_billing_months)
        self.recalculate_month_button.pack(fill=tk.X, pady=3)
        self._refresh_test_access()

    def _refresh_test_access(self, _event=None) -> None:
        allowed = can_use_test_tools()
        state = tk.NORMAL if allowed else tk.DISABLED
        self.reset_month_button.configure(state=state)
        self.recalculate_month_button.configure(state=state)
        self.action_tabs.tab(self.trial_frame, state="normal" if allowed else "disabled")
        self.test_access_var.set("管理者用の検証操作" if allowed else "管理者アカウントの登録が必要です")

    def _allow_test_action(self) -> bool:
        try:
            require_test_tools_access()
        except PermissionError as exc:
            self._refresh_test_access()
            messagebox.showerror("管理者専用", str(exc), parent=self)
            return False
        return True

    def reset_displayed_billing_months(self) -> None:
        from invoice_manager.repositories import reset_invoice_billing_months_to_auto
        if not self._allow_test_action():
            return
        ids = list(self.invoice_ids.values())
        if not ids:
            messagebox.showinfo("対象なし", "現在表示されている請求はありません。", parent=self)
            return
        if not messagebox.askyesno(
            "試験用：請求月を自動判定へ戻す",
            f"現在の絞り込みで表示中の{len(ids)}件が対象です。\n\n"
            "手動指定を解除し、請求日から現在の締めルールで請求月を判定し直します。\n"
            "表示されていない請求は変更しません。\n\n実行しますか？",
            parent=self,
        ):
            return
        try:
            count = reset_invoice_billing_months_to_auto(ids)
        except Exception as exc:
            messagebox.showerror("請求月リセット", str(exc), parent=self)
            return
        self.reload_filter_options()
        messagebox.showinfo("請求月リセット", f"{count}件を変更しました。変更不要の請求はそのままです。", parent=self)

    def refresh(self) -> None:
        filters = {"active_projects_only": "1"}
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
        if billing_month == "__blank__":
            filters["billing_month_blank"] = "1"
        elif billing_month:
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
        total_amount = 0
        row_count = 0
        rows = list_invoices(filters)
        month_tags = billing_month_row_tags(row["billing_month"] for row in rows)
        for row in rows:
            row_count += 1
            display_amount = self.amount_for_display(
                row["total_amount"] or 0,
                row["total_amount_excluded"],
            )
            total_amount += display_amount
            item_id = self.tree.insert(
                "",
                tk.END,
                tags=(month_tags[str(row["billing_month"] or "")],),
                values=(
                    format_billing_month(row["billing_month"]),
                    row["project_code"],
                    row["project_name"],
                    row["vendor_name"],
                    row["invoice_date"],
                    format_amount(display_amount),
                    row["file_count"],
                    row["local_memo"],
                    row["contact_name"],
                    row["email"],
                    row["phone"],
                ),
            )
            self.invoice_ids[item_id] = int(row["id"])
        self.summary_var.set(f"{row_count}件   合計({self.amount_display_var.get()}) {format_amount(total_amount)}円")
        self.selected_info_var.set("一覧から請求を選択してください。")
        self.memo_var.set("")
        self._update_action_state()

    def amount_for_display(self, amount, amount_excluded=None) -> int:
        if self.amount_display_var.get() == "税込":
            return int(amount)
        if amount_excluded is not None:
            return int(amount_excluded)
        return tax_excluded_amount(amount)

    def on_amount_display_selected(self, _event=None) -> None:
        mode = self.amount_display_var.get()
        set_app_setting(AMOUNT_DISPLAY_SETTING_KEY, mode)
        self.tree.heading("total_amount", text=f"請求金額({mode})")
        self.refresh()
        for child in self.winfo_children():
            if callable(getattr(child, "set_amount_display_mode", None)):
                child.set_amount_display_mode(mode)

    def on_filter_selected(self, _event=None) -> None:
        self.refresh()

    def on_project_filter_selected(self, _event=None) -> None:
        self.save_project_selection()
        self.refresh()

    def on_tree_mousewheel(self, event) -> str:
        units = -5 if event.delta > 0 else 5
        self.tree.yview_scroll(units, "units")
        return "break"

    def on_tree_shift_mousewheel(self, event) -> str:
        units = -5 if event.delta > 0 else 5
        self.tree.xview_scroll(units, "units")
        return "break"

    def _update_action_state(self) -> None:
        has_selection = bool(self.tree.selection())
        button_state = tk.NORMAL if has_selection else tk.DISABLED
        entry_state = "normal" if has_selection else "disabled"
        self.detail_button.configure(state=button_state)
        self.memo_button.configure(state=button_state)
        self.billing_month_button.configure(state=button_state)
        self.pdf_button.configure(state=button_state)
        self.delete_button.configure(state=button_state)
        self.memo_entry.configure(state=entry_state)

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
            self.memo_var.set("")
            self.selected_info_var.set("一覧から請求を選択してください。")
            self._update_action_state()
            return
        values = self.tree.item(selection[0], "values")
        self.selected_info_var.set(
            f"選択: {len(selection)}件\n\n{values[3]}\n{values[2]}\n工事コード: {values[1]}\n\n"
            f"請求月: {values[0]}\n請求日: {values[4]}\n金額({self.amount_display_var.get()}): {values[5]}円\n\n"
            f"担当者: {values[8]}\n{values[9]}\n{values[10]}"
        )
        self.memo_var.set(values[7])
        self._update_action_state()

    def on_tree_double_click(self, _event=None) -> None:
        if self.tree.selection():
            self.open_detail()

    def open_detail(self) -> None:
        from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow

        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("選択なし", "請求を選択してください。")
            return
        focused_item = self.tree.focus()
        current_item = focused_item if focused_item in selection else selection[0]
        invoice_id = self.invoice_ids[current_item]
        selected_items = set(selection)
        invoice_ids = [
            self.invoice_ids[item_id]
            for item_id in self.tree.get_children()
            if item_id in selected_items
        ]
        current_index = invoice_ids.index(invoice_id) if invoice_id in invoice_ids else 0
        InvoiceDetailWindow(
            self,
            invoice_id,
            on_saved=self.refresh,
            invoice_ids=invoice_ids,
            current_index=current_index,
            amount_display_mode=self.amount_display_var.get(),
        )

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
        dialog.geometry("300x150")
        dialog.transient(self)
        dialog.grab_set()

        now = datetime.now()
        years = [str(year) for year in range(now.year - 3, now.year + 4)]
        year_var = tk.StringVar(value=str(now.year))
        month_var = tk.StringVar(value=str(now.month))

        form = tk.Frame(dialog, padx=12, pady=12)
        form.pack(fill=tk.X)
        tk.Label(form, text="変更後の請求月").grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))
        tk.Label(form, text="年").grid(row=1, column=0, sticky=tk.W)
        ttk.Combobox(form, textvariable=year_var, values=years, state="readonly", width=8).grid(
            row=1, column=1, sticky=tk.W, padx=4
        )
        tk.Label(form, text="月").grid(row=1, column=2, sticky=tk.W, padx=(10, 0))
        tk.Entry(form, textvariable=month_var, width=6).grid(row=1, column=3, sticky=tk.W, padx=4)

        def apply() -> None:
            try:
                result["value"] = validate_billing_month(f"{year_var.get()}-{int(month_var.get()):02d}")
            except Exception:
                messagebox.showwarning("入力エラー", "月は1から12の数字で入力してください。")
                return
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
        try:
            os.startfile(str(validate_original_pdf_path(files[0]["stored_file_path"])))
        except Exception as exc:
            messagebox.showerror("PDFを開けません", str(exc))

    def recalculate_billing_months(self) -> None:
        if not self._allow_test_action():
            return
        selected_project = self.selected_project_var.get()
        project_id = self.project_options.get(selected_project)
        if project_id is None:
            messagebox.showwarning("請求月再計算", "工事選択で対象工事を1件選んでください。")
            return
        confirmed = messagebox.askyesno(
            "選択工事の請求月再計算",
            f"{selected_project} の請求月を請求日から再計算します。\n"
            "手動で補正した請求月は変更しません。試験機能として続けますか？",
        )
        if not confirmed:
            return
        try:
            updated_count = recalculate_invoice_billing_months(project_id)
        except Exception as exc:
            messagebox.showerror("請求月再計算エラー", str(exc))
            return
        self.load_month_options()
        self.month_combo.configure(values=list(self.month_options.keys()))
        self.refresh()
        messagebox.showinfo(
            "選択工事の請求月再計算",
            f"{updated_count}件の請求月を再計算しました。\n手動補正分は変更していません。",
        )

    def delete_selected_invoices(self) -> None:
        invoice_ids = self.selected_invoice_ids()
        if not invoice_ids:
            return
        confirmed = messagebox.askyesno(
            "請求削除",
            f"選択した{len(invoice_ids)}件の請求データを削除します。\n添付PDFと振分データも削除されます。続けますか？",
        )
        if not confirmed:
            return
        try:
            deleted_count, failed_paths = delete_invoices(invoice_ids)
        except Exception as exc:
            messagebox.showerror("請求削除エラー", str(exc))
            return
        self.load_month_options()
        self.month_combo.configure(values=list(self.month_options.keys()))
        self.refresh()
        if failed_paths:
            messagebox.showwarning(
                "請求削除",
                f"{deleted_count}件を削除しました。\n一部のPDFは手動確認が必要です。\n{failed_paths[0]}",
            )
            return
        messagebox.showinfo("請求削除", f"{deleted_count}件の請求データを削除しました。")
