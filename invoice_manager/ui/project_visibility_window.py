from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import list_projects_for_visibility, set_project_active


class ProjectVisibilityWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("工事表示設定")
        self.geometry("820x500")
        self.project_ids: dict[str, int] = {}
        self.all_projects: list = []

        search_frame = tk.Frame(self)
        search_frame.pack(fill=tk.X, padx=12, pady=(12, 6))
        tk.Label(search_frame, text="検索", width=8, anchor=tk.W).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_var.trace_add("write", lambda *_: self.apply_filter())

        tk.Label(
            self,
            text="更新が終了した工事を請求一覧から隠せます。非表示はアーカイブ扱いで、請求・PDF・振分データは削除されません。",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(0, 6))

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("project_code", "project_name", "invoice_count", "last_invoice_date", "status"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("project_code", text="工事コード")
        self.tree.heading("project_name", text="工事名")
        self.tree.heading("invoice_count", text="請求件数")
        self.tree.heading("last_invoice_date", text="最終請求日")
        self.tree.heading("status", text="状態")
        self.tree.column("project_code", width=150)
        self.tree.column("project_name", width=280)
        self.tree.column("invoice_count", width=80, anchor=tk.CENTER)
        self.tree.column("last_invoice_date", width=120, anchor=tk.CENTER)
        self.tree.column("status", width=80, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        buttons = tk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(6, 12))
        tk.Button(buttons, text="アーカイブにする", command=lambda: self.change_active(False)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="表示に戻す", command=lambda: self.change_active(True)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self.refresh()

    def refresh(self) -> None:
        self.all_projects = list(list_projects_for_visibility())
        self.apply_filter()

    def apply_filter(self) -> None:
        keyword = self.search_var.get().casefold().strip()
        self.tree.delete(*self.tree.get_children())
        self.project_ids.clear()
        for row in self.all_projects:
            project_code = str(row["project_code"])
            project_name = str(row["project_name"])
            if keyword and keyword not in project_code.casefold() and keyword not in project_name.casefold():
                continue
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    project_code,
                    project_name,
                    row["invoice_count"],
                    row["last_invoice_date"] or "",
                    "表示" if row["is_active"] else "アーカイブ",
                ),
            )
            self.project_ids[item_id] = int(row["id"])

    def change_active(self, is_active: bool) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("選択なし", "工事を選択してください。")
            return
        if not is_active:
            confirmed = messagebox.askyesno(
                "工事をアーカイブ",
                "選択した工事の請求を一覧から非表示にし、変更対象から外します。\n"
                "請求・PDF・振分データは削除されません。続けますか？",
            )
            if not confirmed:
                return
        set_project_active(self.project_ids[selection[0]], is_active)
        self.refresh()
