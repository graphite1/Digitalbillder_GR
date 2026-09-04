from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import list_projects, set_project_active


class ProjectVisibilityWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("工事表示設定")
        self.geometry("720x460")
        self.project_ids: dict[str, int] = {}

        tk.Label(
            self,
            text="更新が終了した工事を請求一覧から隠せます。請求・PDF・振分データは削除されません。",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("project_code", "project_name", "status"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("project_code", text="工事コード")
        self.tree.heading("project_name", text="工事名")
        self.tree.heading("status", text="請求一覧")
        self.tree.column("project_code", width=150)
        self.tree.column("project_name", width=400)
        self.tree.column("status", width=100, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        buttons = tk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(6, 12))
        tk.Button(buttons, text="非表示にする", command=lambda: self.change_active(False)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="表示に戻す", command=lambda: self.change_active(True)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.project_ids.clear()
        for row in list_projects():
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    row["project_code"],
                    row["project_name"],
                    "表示" if row["is_active"] else "非表示",
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
                "工事を非表示",
                "選択した工事の請求を一覧から非表示にします。\nデータは削除されません。続けますか？",
            )
            if not confirmed:
                return
        set_project_active(self.project_ids[selection[0]], is_active)
        self.refresh()
