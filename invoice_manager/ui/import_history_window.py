from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from invoice_manager.repositories import list_import_batches, list_import_errors


STATUS_LABELS = {
    "running": "処理中",
    "completed": "完了",
    "failed": "失敗",
}


class ImportHistoryWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("取込履歴")
        self.geometry("1220x680")
        self.rows_by_item: dict[str, object] = {}
        self.error_cache: dict[int, list] = {}

        tk.Label(
            self,
            text="CSV + zip取込の実行結果を新しい順に表示します。",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        columns = (
            "imported_at",
            "completed_at",
            "billing_month",
            "csv_file_name",
            "zip_file_name",
            "registered_count",
            "pdf_count",
            "error_count",
            "status",
            "memo",
        )
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "imported_at": "開始日時",
            "completed_at": "完了日時",
            "billing_month": "請求月",
            "csv_file_name": "CSVファイル",
            "zip_file_name": "zipファイル",
            "registered_count": "登録件数",
            "pdf_count": "PDF件数",
            "error_count": "エラー件数",
            "status": "状態",
            "memo": "メモ",
        }
        widths = {
            "imported_at": 145,
            "completed_at": 145,
            "billing_month": 80,
            "csv_file_name": 170,
            "zip_file_name": 170,
            "registered_count": 75,
            "pdf_count": 70,
            "error_count": 80,
            "status": 110,
            "memo": 200,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column])
        y_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        x_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        result_frame = ttk.LabelFrame(self, text="処理結果")
        result_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.result_text = tk.Text(result_frame, height=3, wrap=tk.WORD, state=tk.DISABLED)
        self.result_text.pack(fill=tk.X, padx=6, pady=6)

        errors_frame = ttk.LabelFrame(self, text="取込エラー詳細")
        errors_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.errors_text = tk.Text(errors_frame, height=7, wrap=tk.WORD, state=tk.DISABLED)
        self.errors_text.pack(fill=tk.X, padx=6, pady=6)

        buttons = tk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Button(buttons, text="更新", command=self.refresh).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows_by_item.clear()
        self.error_cache.clear()
        for row in list_import_batches():
            status = str(row["status"] or "")
            status_label = STATUS_LABELS.get(status, status)
            if status == "completed" and int(row["error_count"] or 0):
                status_label = "完了（エラーあり）"
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    row["imported_at"] or "",
                    row["completed_at"] or "",
                    row["billing_month"] or "",
                    row["csv_file_name"] or "",
                    row["zip_file_name"] or "",
                    row["registered_count"] or 0,
                    row["pdf_count"] or 0,
                    row["error_count"] or 0,
                    status_label,
                    row["memo"] or "",
                ),
            )
            self.rows_by_item[item_id] = row
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.on_select()
        else:
            self._set_text(self.result_text, "履歴はありません。")
            self._set_text(self.errors_text, "なし")

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self.rows_by_item[selection[0]]
        status = str(row["status"] or "")
        if status == "failed":
            result = str(row["failure_message"] or "失敗理由の記録はありません。")
        elif status == "running":
            result = "処理中、または前回処理が途中で終了しています。"
        else:
            result = "取込処理は完了しています。"
        self._set_text(self.result_text, result)

        batch_id = int(row["id"])
        if batch_id not in self.error_cache:
            self.error_cache[batch_id] = list_import_errors(batch_id)
        errors = self.error_cache[batch_id]
        if not errors:
            self._set_text(self.errors_text, "なし")
            return
        lines = []
        for error in errors:
            row_number = f"{error['row_number']}行目 " if error["row_number"] else ""
            lines.append(f"{row_number}{error['error_type']}: {error['message']}")
            if error["raw_data"]:
                lines.append(f"  元データ: {error['raw_data']}")
        self._set_text(self.errors_text, "\n".join(lines))

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)
