from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import list_projects, list_work_type_codes, save_work_type_code


class WorkTypeMasterWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("工種コードマスタ")
        self.geometry("820x520")
        self.project_options: dict[str, int] = {}
        self.work_type_ids: dict[str, int] = {}
        self.selected_project_var = tk.StringVar()
        self.code_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.sort_order_var = tk.StringVar(value="0")
        self.is_active_var = tk.IntVar(value=1)
        self.editing_id: int | None = None

        self.load_project_options()
        self.build()
        self.refresh()

    def load_project_options(self) -> None:
        self.project_options = {}
        for row in list_projects():
            label = f"{row['project_code']}｜{row['project_name']}"
            self.project_options[label] = int(row["id"])
        if self.project_options:
            self.selected_project_var.set(next(iter(self.project_options.keys())))

    def build(self) -> None:
        form = tk.Frame(self, padx=10, pady=10)
        form.pack(fill=tk.X)
        tk.Label(form, text="工事").grid(row=0, column=0, sticky=tk.W)
        project_combo = ttk.Combobox(
            form,
            textvariable=self.selected_project_var,
            values=list(self.project_options.keys()),
            state="readonly",
            width=48,
        )
        project_combo.grid(row=0, column=1, columnspan=5, sticky=tk.W, padx=4)
        project_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        labels = [("工種コード", self.code_var, 12), ("工種名", self.name_var, 24), ("並び順", self.sort_order_var, 8)]
        for index, (label, var, width) in enumerate(labels):
            tk.Label(form, text=label).grid(row=1, column=index * 2, sticky=tk.W, pady=(8, 0))
            tk.Entry(form, textvariable=var, width=width).grid(row=1, column=index * 2 + 1, sticky=tk.W, padx=4, pady=(8, 0))
        tk.Checkbutton(form, text="有効", variable=self.is_active_var).grid(row=1, column=6, sticky=tk.W, pady=(8, 0))

        actions = tk.Frame(self, padx=10)
        actions.pack(fill=tk.X)
        tk.Button(actions, text="登録/更新", command=self.save).pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="入力クリア", command=self.clear_form).pack(side=tk.LEFT, padx=4)

        self.tree = ttk.Treeview(self, columns=("code", "name", "sort_order", "is_active"), show="headings")
        self.tree.heading("code", text="工種コード")
        self.tree.heading("name", text="工種名")
        self.tree.heading("sort_order", text="並び順")
        self.tree.heading("is_active", text="有効")
        self.tree.column("code", width=120)
        self.tree.column("name", width=360)
        self.tree.column("sort_order", width=80, anchor=tk.E)
        self.tree.column("is_active", width=80)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    def selected_project_id(self) -> int | None:
        return self.project_options.get(self.selected_project_var.get())

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.work_type_ids.clear()
        project_id = self.selected_project_id()
        if not project_id:
            return
        for row in list_work_type_codes(project_id):
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(row["code"], row["name"], row["sort_order"], "有効" if row["is_active"] else "無効"),
            )
            self.work_type_ids[item_id] = int(row["id"])

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        self.editing_id = self.work_type_ids[selection[0]]
        self.code_var.set(values[0])
        self.name_var.set(values[1])
        self.sort_order_var.set(values[2])
        self.is_active_var.set(1 if values[3] == "有効" else 0)

    def save(self) -> None:
        project_id = self.selected_project_id()
        if not project_id:
            messagebox.showwarning("工事未選択", "工事を選択してください。")
            return
        code = self.code_var.get().strip()
        name = self.name_var.get().strip()
        if not code or not name:
            messagebox.showwarning("入力不足", "工種コードと工種名を入力してください。")
            return
        try:
            save_work_type_code(
                project_id=project_id,
                code=code,
                name=name,
                sort_order=int(self.sort_order_var.get() or 0),
                is_active=self.is_active_var.get(),
                work_type_code_id=self.editing_id,
            )
        except Exception as exc:
            messagebox.showerror("保存エラー", str(exc))
            return
        self.clear_form()
        self.refresh()

    def clear_form(self) -> None:
        self.editing_id = None
        self.code_var.set("")
        self.name_var.set("")
        self.sort_order_var.set("0")
        self.is_active_var.set(1)
