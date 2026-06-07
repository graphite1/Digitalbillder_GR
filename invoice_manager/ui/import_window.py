from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from invoice_manager.services.csv_reader import read_invoice_csv
from invoice_manager.services.import_service import execute_import, preview_import
from invoice_manager.utils.date_utils import billing_month_candidates, format_billing_month
from invoice_manager.utils.money_utils import format_amount

try:
    from tkinterdnd2 import DND_FILES
except Exception:
    DND_FILES = None


class ImportWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("CSV + zip取込")
        self.geometry("760x620")
        self.preview = None

        self.csv_var = tk.StringVar()
        self.zip_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.memo_var = tk.StringVar()
        self.month_options: dict[str, str] = {}
        for variable in (self.csv_var, self.zip_var, self.month_var):
            variable.trace_add("write", self.update_preview_button)

        form = tk.Frame(self, padx=12, pady=12)
        form.pack(fill=tk.X)
        self._file_row(form, "CSVファイル選択", self.csv_var, self.select_csv, 0)
        self._file_row(form, "zipファイル選択", self.zip_var, self.select_zip, 1)
        tk.Label(form, text="請求月").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.month_combo = ttk.Combobox(form, textvariable=self.month_var, state="readonly")
        self.month_combo.grid(row=2, column=1, sticky=tk.EW, pady=4)
        tk.Label(form, text="メモ").grid(row=3, column=0, sticky=tk.W, pady=4)
        tk.Entry(form, textvariable=self.memo_var).grid(row=3, column=1, sticky=tk.EW, pady=4)
        form.columnconfigure(1, weight=1)

        actions = tk.Frame(self, padx=12)
        actions.pack(fill=tk.X)
        self.preview_button = tk.Button(actions, text="プレビュー", command=self.run_preview, state=tk.DISABLED)
        self.preview_button.pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="取込実行", command=self.run_import).pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="キャンセル", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self.drop_label = tk.Label(
            self,
            text="ここにCSVまたはzipをドラッグ＆ドロップ",
            relief=tk.GROOVE,
            padx=12,
            pady=14,
        )
        self.drop_label.pack(fill=tk.X, padx=12, pady=(10, 0))

        self.tree = ttk.Treeview(self, columns=("name", "value"), show="headings", height=16)
        self.tree.heading("name", text="項目")
        self.tree.heading("value", text="値")
        self.tree.column("name", width=220)
        self.tree.column("value", width=480)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self.message = tk.Text(self, height=8)
        self.message.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.setup_drop_target()
        self.set_month_candidates(billing_month_candidates())
        self.update_preview_button()

    def _file_row(self, frame, label, variable, command, row) -> None:
        tk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
        tk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky=tk.EW, pady=4)
        tk.Button(frame, text="選択", command=command).grid(row=row, column=2, padx=4)

    def select_csv(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if path:
            self.set_csv_path(path)

    def select_zip(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("zip", "*.zip")])
        if path:
            self.zip_var.set(path)

    def run_preview(self) -> None:
        if not self.validate_preview_inputs("プレビュー"):
            return
        try:
            self.preview = preview_import(Path(self.csv_var.get()), Path(self.zip_var.get()), self.selected_billing_month())
            self.render_preview()
        except Exception as exc:
            messagebox.showerror("プレビューエラー", str(exc))

    def run_import(self) -> None:
        if not self.validate_preview_inputs("取込"):
            return
        try:
            billing_month = self.selected_billing_month()
            self.preview = preview_import(Path(self.csv_var.get()), Path(self.zip_var.get()), billing_month)
            self.render_preview()
            self.update_idletasks()
            confirmed = messagebox.askyesno("取込確認", "表示中のプレビュー内容で取込を実行しますか？")
            if not confirmed:
                return
            result = execute_import(Path(self.csv_var.get()), Path(self.zip_var.get()), billing_month, self.memo_var.get())
            self.preview = result.preview
            self.render_preview()
            messagebox.showinfo(
                "取込完了",
                f"登録件数: {result.inserted_count}\n添付ファイル登録件数: {result.file_count}",
            )
        except Exception as exc:
            messagebox.showerror("取込エラー", str(exc))

    def setup_drop_target(self) -> None:
        if not DND_FILES:
            self.drop_label.configure(text="ドラッグ＆ドロップ機能は未セットアップです（ファイル選択は利用できます）")
            return
        try:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self.on_drop)
        except Exception:
            self.drop_label.configure(text="ドラッグ＆ドロップの初期化に失敗しました（ファイル選択は利用できます）")

    def on_drop(self, event) -> None:
        csv_found = False
        zip_found = False
        for path in self.split_drop_paths(event.data):
            suffix = Path(path).suffix.lower()
            if suffix == ".csv":
                self.set_csv_path(path)
                csv_found = True
            elif suffix == ".zip":
                self.zip_var.set(path)
                zip_found = True
            else:
                self.append_message(f"警告: 対象外ファイルをスキップしました: {path}\n")
        if csv_found or zip_found:
            self.append_message("ドラッグ＆ドロップされたファイルを設定しました。\n")

    def split_drop_paths(self, data: str) -> list[str]:
        try:
            return [path.strip("{}") for path in self.tk.splitlist(data)]
        except tk.TclError:
            return [data.strip().strip("{}")]

    def set_csv_path(self, path: str) -> None:
        self.csv_var.set(path)
        self.fill_month_from_csv(path)

    def fill_month_from_csv(self, path: str) -> None:
        try:
            rows, _errors, _encoding = read_invoice_csv(Path(path))
        except Exception:
            return
        if rows:
            center = rows[0].invoice_date[:7]
            self.set_month_candidates(billing_month_candidates(center, before=1, after=3), center)

    def set_month_candidates(self, months: list[str], selected_month: str | None = None) -> None:
        self.month_options = {"未設定": ""}
        self.month_options.update({format_billing_month(month): month for month in months})
        labels = list(self.month_options.keys())
        self.month_combo.configure(values=labels)
        if selected_month:
            self.month_var.set(format_billing_month(selected_month))
        elif labels and not self.month_var.get().strip():
            self.month_var.set(labels[0])

    def validate_preview_inputs(self, title: str) -> bool:
        if not self.csv_var.get().strip() or not self.zip_var.get().strip():
            messagebox.showwarning(title, "CSVファイルとzipファイルを指定してください。")
            return False
        return True

    def update_preview_button(self, *_args) -> None:
        if not hasattr(self, "preview_button"):
            return
        enabled = all((self.csv_var.get().strip(), self.zip_var.get().strip()))
        self.preview_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def selected_billing_month(self) -> str:
        selected = self.month_var.get()
        return self.month_options.get(selected, selected)

    def append_message(self, text: str) -> None:
        if not hasattr(self, "message"):
            return
        self.message.insert(tk.END, text)
        self.message.see(tk.END)

    def render_preview(self) -> None:
        preview = self.preview
        self.tree.delete(*self.tree.get_children())
        rows = [
            ("CSV件数", preview.csv_count),
            ("zip内IDフォルダ数", preview.zip_id_count),
            ("CSVとzipの一致件数", preview.matched_count),
            ("CSVのみ存在するID数", preview.csv_only_count),
            ("zipのみ存在するID数", preview.zip_only_count),
            ("新規登録件数", preview.new_count),
            ("既存スキップ件数", preview.existing_skip_count),
            ("更新候補件数", preview.update_candidate_count),
            ("重複候補件数", preview.duplicate_candidate_count),
            ("エラー件数", preview.error_count),
            ("請求金額合計", format_amount(preview.total_amount)),
            ("PDFファイル総数", preview.pdf_file_count),
        ]
        for name, value in rows:
            self.tree.insert("", tk.END, values=(name, value))
        for name, value in preview.project_totals.items():
            self.tree.insert("", tk.END, values=(f"工事別合計: {name}", format_amount(value)))
        for name, value in preview.vendor_totals.items():
            self.tree.insert("", tk.END, values=(f"取引先別合計: {name}", format_amount(value)))

        self.message.delete("1.0", tk.END)
        for warning in preview.warnings:
            self.message.insert(tk.END, f"警告: {warning}\n")
        for error in preview.errors:
            self.message.insert(tk.END, f"エラー: {error.row_number or ''} {error.message}\n")
