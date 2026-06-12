from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import fitz
    from PIL import Image, ImageTk
except Exception:
    fitz = None
    Image = None
    ImageTk = None

from invoice_manager.repositories import (
    create_pdf_mark,
    delete_invoice_allocation,
    delete_pdf_mark,
    ensure_work_type_codes_for_project,
    get_invoice_allocation_total,
    get_or_create_pdf_mark_label,
    get_invoice_detail,
    list_invoice_allocations,
    list_invoice_files,
    list_pdf_marks,
    list_recent_work_type_codes_for_project_vendor,
    list_vendor_work_type_candidates,
    list_work_type_codes,
    save_invoice_allocation,
    update_pdf_mark_position,
    update_invoice_memo,
)
from invoice_manager.services.export_marked_pdf import export_marked_pdf
from invoice_manager.utils.file_size_utils import format_file_size
from invoice_manager.utils.date_utils import format_billing_month
from invoice_manager.utils.file_safety import validate_original_pdf_path
from invoice_manager.utils.money_utils import format_amount


class InvoiceDetailWindow(tk.Toplevel):
    def __init__(self, master, invoice_id: int, on_saved=None, invoice_ids: list[int] | None = None, current_index: int = 0) -> None:
        super().__init__(master)
        self.invoice_id = invoice_id
        self.on_saved = on_saved
        self.invoice_ids = invoice_ids or [invoice_id]
        self.current_index = max(0, min(current_index, len(self.invoice_ids) - 1))
        self.title("請求詳細")
        self.geometry("1500x900")
        self.file_ids: dict[str, str] = {}
        self.file_db_ids: dict[str, int] = {}
        self.allocation_ids: dict[str, int] = {}
        self.work_type_options: dict[str, int] = {}
        self.pdf_path: str | None = None
        self.current_invoice_file_id: int | None = None
        self.pdf_page_index = 0
        self.pdf_page_count = 0
        self.pdf_zoom = 1.25
        self.pdf_image = None
        self.pdf_image_item = None
        self.pdf_image_width = 0
        self.pdf_image_height = 0
        self.pdf_pan_x = 0
        self.pdf_pan_y = 0
        self.pdf_drag_last_x = 0
        self.pdf_drag_last_y = 0
        self.selected_mark_id: int | None = None
        self.pdf_mark_item_ids: dict[int, tuple[int, int]] = {}
        self.current_pdf_mark_rows: dict[int, dict] = {}
        self.dragging_mark_id: int | None = None
        self.mark_drag_last_canvas_x = 0.0
        self.mark_drag_last_canvas_y = 0.0
        self.mark_undo_stack: list[int] = []
        self.invoice_total = 0
        self.project_id: int | None = None
        self.vendor_id: int | None = None
        self.topmost_var = tk.IntVar(value=0)
        self.mark_mode_var = tk.IntVar(value=0)
        self.memo_text = ""
        self._build()
        self.bind("<Delete>", self.delete_selected_pdf_mark_shortcut)
        self.bind("<Control-z>", self.undo_last_pdf_mark)
        self.load()

    def _build(self) -> None:
        top_area = tk.Frame(self, padx=10, pady=8)
        top_area.pack(fill=tk.X)
        top_area.columnconfigure(0, weight=0)
        top_area.columnconfigure(1, weight=1)
        top_area.rowconfigure(0, weight=1)
        header = tk.Frame(top_area, width=390, height=210)
        header.grid(row=0, column=0, sticky=tk.NW)
        header.grid_propagate(False)
        self.vendor_name_var = tk.StringVar()
        tk.Label(
            header,
            textvariable=self.vendor_name_var,
            font=("", 18, "bold"),
            anchor=tk.W,
            wraplength=360,
            justify=tk.LEFT,
        ).pack(fill=tk.X)

        self.info_vars = {
            "billing_month": tk.StringVar(),
            "invoice_date": tk.StringVar(),
            "total_amount": tk.StringVar(),
            "contact": tk.StringVar(),
        }
        info_frame = tk.Frame(header)
        info_frame.pack(fill=tk.X, pady=(6, 0))
        info_items = [
            ("請求月", "billing_month"),
            ("請求日", "invoice_date"),
            ("請求金額", "total_amount"),
            ("担当", "contact"),
        ]
        for index, (label, key) in enumerate(info_items):
            tk.Label(info_frame, text=f"{label}:", fg="#555555").grid(
                row=index, column=0, sticky=tk.W, padx=(0, 4), pady=1
            )
            tk.Label(
                info_frame,
                textvariable=self.info_vars[key],
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=300,
            ).grid(
                row=index, column=1, sticky=tk.W, padx=(0, 4), pady=1
            )

        action = tk.Frame(header)
        action.pack(fill=tk.X, pady=(10, 0))
        tk.Button(action, text="前の請求", command=self.previous_invoice).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(action, text="次の請求", command=self.next_invoice).pack(side=tk.LEFT, padx=4)
        self.invoice_nav_var = tk.StringVar()
        tk.Label(action, textvariable=self.invoice_nav_var).pack(side=tk.LEFT, padx=8)
        tk.Button(action, text="メモを開く", command=self.open_memo_dialog).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(action, text="最前面表示", variable=self.topmost_var, command=self.toggle_topmost).pack(
            side=tk.LEFT, padx=8
        )

        side_panel = tk.Frame(top_area, height=210)
        side_panel.grid(row=0, column=1, sticky=tk.NSEW, padx=(10, 0))
        side_panel.columnconfigure(0, weight=3)
        side_panel.columnconfigure(1, weight=2)
        side_panel.rowconfigure(0, weight=1)

        allocation_frame = tk.LabelFrame(side_panel, text="工種コード別振分", padx=8, pady=8)
        allocation_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))
        self.allocation_summary_var = tk.StringVar()
        tk.Label(allocation_frame, textvariable=self.allocation_summary_var).pack(anchor=tk.W)
        self.allocations = ttk.Treeview(
            allocation_frame,
            columns=("code", "name", "amount", "memo", "sort_order"),
            show="headings",
            height=5,
        )
        for column, label, width in [
            ("code", "工種コード", 80),
            ("name", "工種名", 180),
            ("amount", "振分金額", 110),
            ("memo", "メモ", 160),
            ("sort_order", "並び順", 50),
        ]:
            self.allocations.heading(column, text=label)
            self.allocations.column(column, width=width)
        self.allocations.pack(fill=tk.BOTH, expand=True, pady=6)
        self.allocations.bind("<<TreeviewSelect>>", self.on_allocation_selected)
        buttons = tk.Frame(allocation_frame)
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="振分行を追加", command=self.add_allocation).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="振分行を編集", command=self.edit_allocation).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="振分行を削除", command=self.delete_allocation).pack(side=tk.LEFT, padx=4)

        files_frame = tk.LabelFrame(side_panel, text="添付ファイル", padx=8, pady=8)
        files_frame.grid(row=0, column=1, sticky=tk.NSEW)
        self.files = ttk.Treeview(files_frame, columns=("name", "size"), show="headings", height=5)
        self.files.heading("name", text="添付ファイル一覧")
        self.files.heading("size", text="サイズ")
        self.files.column("name", width=300)
        self.files.column("size", width=90, anchor=tk.E)
        self.files.pack(fill=tk.BOTH, expand=True)
        self.files.bind("<<TreeviewSelect>>", self.on_pdf_file_selected)
        tk.Button(files_frame, text="PDFを開く", command=self.open_pdf).pack(anchor=tk.W, pady=(6, 0))
        tk.Button(files_frame, text="確認用PDF出力", command=self.export_current_pdf).pack(anchor=tk.W, pady=(6, 0))

        pdf_frame = tk.LabelFrame(self, text="PDFプレビュー（原本は編集しません）", padx=8, pady=8)
        pdf_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        pdf_actions = tk.Frame(pdf_frame)
        pdf_actions.pack(fill=tk.X)
        tk.Button(pdf_actions, text="前ページ", command=self.previous_pdf_page).pack(side=tk.LEFT, padx=2)
        tk.Button(pdf_actions, text="次ページ", command=self.next_pdf_page).pack(side=tk.LEFT, padx=2)
        tk.Button(pdf_actions, text="縮小", command=self.zoom_out_pdf).pack(side=tk.LEFT, padx=8)
        tk.Button(pdf_actions, text="拡大", command=self.zoom_in_pdf).pack(side=tk.LEFT, padx=2)
        tk.Checkbutton(pdf_actions, text="マーク配置", variable=self.mark_mode_var).pack(side=tk.LEFT, padx=8)
        tk.Button(pdf_actions, text="マーク一覧", command=self.open_pdf_mark_list).pack(side=tk.LEFT, padx=2)
        self.mark_selection_var = tk.StringVar(value="マーク対象: 振分未選択")
        tk.Label(pdf_actions, textvariable=self.mark_selection_var).pack(side=tk.LEFT, padx=8)
        self.pdf_status_var = tk.StringVar(value="PDF未選択")
        tk.Label(pdf_actions, textvariable=self.pdf_status_var).pack(side=tk.LEFT, padx=8)

        canvas_frame = tk.Frame(pdf_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.pdf_canvas = tk.Canvas(canvas_frame, bg="#666666")
        pdf_y_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.pdf_canvas.yview)
        pdf_x_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.pdf_canvas.xview)
        self.pdf_canvas.configure(yscrollcommand=pdf_y_scrollbar.set, xscrollcommand=pdf_x_scrollbar.set)
        self.pdf_canvas.bind("<ButtonPress-1>", self.place_pdf_mark)
        self.pdf_canvas.bind("<B1-Motion>", self.drag_selected_mark)
        self.pdf_canvas.bind("<ButtonRelease-1>", self.finish_mark_drag)
        self.pdf_canvas.bind("<ButtonPress-3>", self.start_pdf_drag)
        self.pdf_canvas.bind("<B3-Motion>", self.drag_pdf)
        self.pdf_canvas.bind("<Control-MouseWheel>", self.zoom_pdf_with_mousewheel)
        self.pdf_canvas.bind("<Enter>", lambda _event: self.pdf_canvas.focus_set())
        self.pdf_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        pdf_y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        pdf_x_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

    def load(self) -> None:
        row = get_invoice_detail(self.invoice_id)
        if not row:
            messagebox.showerror("エラー", "請求データが見つかりません。")
            self.destroy()
            return
        self.invoice_total = int(row["total_amount"])
        self.project_id = int(row["project_id"])
        self.vendor_id = int(row["vendor_id"])
        self.vendor_name_var.set(row["vendor_name"])
        self.info_vars["billing_month"].set(format_billing_month(row["billing_month"]))
        self.info_vars["invoice_date"].set(row["invoice_date"])
        self.info_vars["total_amount"].set(f"{format_amount(row['total_amount'])}円")
        self.info_vars["contact"].set(f"{row['last_name'] or ''} {row['first_name'] or ''}".strip())
        self.memo_text = row["local_memo"] or ""
        self.update_invoice_navigation()
        self.load_work_type_options()
        self.load_allocations()
        self.load_files()

    def load_work_type_options(self) -> None:
        self.work_type_options = {}
        if not self.project_id:
            return
        ensure_work_type_codes_for_project(self.project_id)
        recent_codes = []
        if self.vendor_id:
            recent_codes = list_recent_work_type_codes_for_project_vendor(self.project_id, self.vendor_id, self.invoice_id)
        vendor_codes = []
        if self.vendor_id:
            vendor_codes = [row["code"] for row in list_vendor_work_type_candidates(self.vendor_id)]
        priority_codes = recent_codes + [code for code in vendor_codes if code not in recent_codes]
        work_type_rows = list_work_type_codes(self.project_id, active_only=True)
        if priority_codes:
            priority_set = set(priority_codes)
            work_type_rows.sort(
                key=lambda row: (
                    priority_codes.index(row["code"]) if row["code"] in priority_set else len(priority_codes),
                    row["sort_order"],
                    row["code"],
                )
            )
        for row in work_type_rows:
            self.work_type_options[f"{row['code']}｜{row['name']}"] = int(row["id"])

    def load_allocations(self) -> None:
        self.allocations.delete(*self.allocations.get_children())
        self.allocation_ids.clear()
        for row in list_invoice_allocations(self.invoice_id):
            display_amount = "" if int(row["amount"]) == 0 else format_amount(row["amount"])
            item_id = self.allocations.insert(
                "",
                tk.END,
                values=(row["code"], row["name"], display_amount, row["memo"] or "", row["sort_order"]),
            )
            self.allocation_ids[item_id] = int(row["id"])
        first_item = next(iter(self.allocations.get_children()), None)
        if first_item:
            self.allocations.selection_set(first_item)
            self.allocations.focus(first_item)
        allocated = get_invoice_allocation_total(self.invoice_id)
        remaining = self.invoice_total - allocated
        if remaining < 0:
            self.allocation_summary_var.set(
                f"請求金額: {self.invoice_total:,}円 / 振分合計: {allocated:,}円 / 超過額: {abs(remaining):,}円"
            )
            self.allocation_summary_var.set(self.allocation_summary_var.get() + "  ※超過しています")
        else:
            self.allocation_summary_var.set(
                f"請求金額: {self.invoice_total:,}円 / 振分合計: {allocated:,}円 / 未振分額: {remaining:,}円"
            )
        self.update_mark_selection_status()

    def load_files(self) -> None:
        self.files.delete(*self.files.get_children())
        self.file_ids.clear()
        self.file_db_ids.clear()
        first_item_id = None
        for file_row in list_invoice_files(self.invoice_id):
            item_id = self.files.insert(
                "",
                tk.END,
                values=(file_row["original_file_name"], format_file_size(file_row["file_size"])),
            )
            self.file_ids[item_id] = file_row["stored_file_path"]
            self.file_db_ids[item_id] = int(file_row["id"])
            if first_item_id is None:
                first_item_id = item_id
        if first_item_id:
            self.files.selection_set(first_item_id)
            self.files.focus(first_item_id)
            self.current_invoice_file_id = self.file_db_ids[first_item_id]
            self.show_pdf_file(self.file_ids[first_item_id])
        else:
            self.current_invoice_file_id = None
            self.clear_pdf_preview("PDF添付なし")

    def save_memo(self) -> None:
        update_invoice_memo(self.invoice_id, self.memo_text.strip())
        if self.on_saved:
            self.on_saved()
        self.load()

    def on_allocation_selected(self, _event=None) -> None:
        self.update_mark_selection_status()

    def get_selected_allocation(self):
        selection = self.allocations.selection()
        if not selection:
            return None
        item_id = selection[0]
        values = self.allocations.item(item_id, "values")
        return {
            "allocation_id": self.allocation_ids[item_id],
            "code": values[0],
            "name": values[1],
        }

    def update_mark_selection_status(self) -> None:
        allocation = self.get_selected_allocation()
        if not allocation:
            self.mark_selection_var.set("マーク対象: 振分未選択")
            return
        label = get_or_create_pdf_mark_label(self.invoice_id, allocation["allocation_id"])
        self.mark_selection_var.set(f"マーク対象: {label} ({allocation['code']} {allocation['name']})")

    def open_memo_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("メモ")
        dialog.geometry("520x260")
        dialog.transient(self)
        dialog.grab_set()

        memo = tk.Text(dialog, height=8)
        memo.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        memo.insert(tk.END, self.memo_text)

        def apply() -> None:
            self.memo_text = memo.get("1.0", tk.END).strip()
            self.save_memo()
            dialog.destroy()

        buttons = tk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Button(buttons, text="保存", command=apply).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        dialog.wait_window()

    def toggle_topmost(self) -> None:
        self.attributes("-topmost", bool(self.topmost_var.get()))

    def update_invoice_navigation(self) -> None:
        total = len(self.invoice_ids)
        self.invoice_nav_var.set(f"{self.current_index + 1}/{total}件")

    def previous_invoice(self) -> None:
        if self.current_index <= 0:
            return
        self.current_index -= 1
        self.invoice_id = self.invoice_ids[self.current_index]
        self.load()

    def next_invoice(self) -> None:
        if self.current_index + 1 >= len(self.invoice_ids):
            return
        self.current_index += 1
        self.invoice_id = self.invoice_ids[self.current_index]
        self.load()

    def add_allocation(self) -> None:
        self.open_allocation_dialog()

    def edit_allocation(self) -> None:
        selection = self.allocations.selection()
        if not selection:
            messagebox.showwarning("選択なし", "振分行を選択してください。")
            return
        item_id = selection[0]
        values = self.allocations.item(item_id, "values")
        self.open_allocation_dialog(self.allocation_ids[item_id], values)

    def delete_allocation(self) -> None:
        selection = self.allocations.selection()
        if not selection:
            messagebox.showwarning("選択なし", "振分行を選択してください。")
            return
        delete_invoice_allocation(self.allocation_ids[selection[0]])
        self.load_allocations()
        if self.pdf_path:
            self.render_pdf_page()

    def open_allocation_dialog(self, allocation_id: int | None = None, values=None) -> None:
        if not self.work_type_options:
            messagebox.showwarning("工種コードなし", "この工事の有効な工種コードを先に登録してください。")
            return
        dialog = tk.Toplevel(self)
        dialog.title("振分行")
        dialog.geometry("420x220")
        dialog.transient(self)
        dialog.grab_set()

        selected_work_type = tk.StringVar(value=next(iter(self.work_type_options.keys())))
        amount_var = tk.StringVar()
        memo_var = tk.StringVar()
        sort_order_var = tk.StringVar(value="0")
        if values:
            label_prefix = f"{values[0]}｜{values[1]}"
            for label in self.work_type_options:
                if label == label_prefix:
                    selected_work_type.set(label)
                    break
            amount_var.set(values[2])
            memo_var.set(values[3])
            sort_order_var.set(values[4])

        tk.Label(dialog, text="工種コード").grid(row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4))
        ttk.Combobox(
            dialog,
            textvariable=selected_work_type,
            values=list(self.work_type_options.keys()),
            state="readonly",
            width=34,
        ).grid(row=0, column=1, sticky=tk.W, padx=4, pady=(12, 4))
        tk.Label(dialog, text="金額").grid(row=1, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=amount_var).grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)
        tk.Label(dialog, text="メモ").grid(row=2, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=memo_var, width=36).grid(row=2, column=1, sticky=tk.W, padx=4, pady=4)
        tk.Label(dialog, text="並び順").grid(row=3, column=0, sticky=tk.W, padx=12, pady=4)
        tk.Entry(dialog, textvariable=sort_order_var).grid(row=3, column=1, sticky=tk.W, padx=4, pady=4)

        def save() -> None:
            try:
                amount_text = amount_var.get().replace(",", "").strip()
                save_invoice_allocation(
                    invoice_id=self.invoice_id,
                    work_type_code_id=self.work_type_options[selected_work_type.get()],
                    amount=int(amount_text) if amount_text else None,
                    memo=memo_var.get(),
                    sort_order=int(sort_order_var.get() or 0),
                    allocation_id=allocation_id,
                )
            except Exception as exc:
                messagebox.showerror("保存エラー", str(exc))
                return
            dialog.destroy()
            self.load_allocations()

        buttons = tk.Frame(dialog)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=12, pady=10)
        tk.Button(buttons, text="保存", command=save).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        dialog.wait_window()

    def open_pdf(self) -> None:
        selection = self.files.selection()
        if not selection:
            messagebox.showwarning("選択なし", "PDFを選択してください。")
            return
        try:
            os.startfile(str(validate_original_pdf_path(self.file_ids[selection[0]])))
        except Exception as exc:
            messagebox.showerror("PDFを開けません", str(exc))

    def export_current_pdf(self) -> None:
        selection = self.files.selection()
        if not selection:
            messagebox.showwarning("選択なし", "PDFを選択してください。")
            return
        item_id = selection[0]
        file_id = self.file_db_ids.get(item_id)
        file_path = self.file_ids.get(item_id)
        if file_id is None or not file_path:
            messagebox.showwarning("PDF未選択", "出力するPDFを選択してください。")
            return
        try:
            path = export_marked_pdf(self.invoice_id, file_id, file_path)
            messagebox.showinfo("確認用PDF出力", f"出力しました:\n{path}")
        except Exception as exc:
            messagebox.showerror("確認用PDF出力エラー", str(exc))

    def on_pdf_file_selected(self, _event=None) -> None:
        selection = self.files.selection()
        if selection:
            item_id = selection[0]
            self.current_invoice_file_id = self.file_db_ids.get(item_id)
            self.show_pdf_file(self.file_ids[item_id])

    def show_pdf_file(self, path: str) -> None:
        try:
            self.pdf_path = str(validate_original_pdf_path(path))
            self.pdf_page_index = 0
            self.reset_pdf_pan()
            self.render_pdf_page()
        except Exception as exc:
            self.pdf_path = None
            self.clear_pdf_preview(f"PDF表示エラー: {exc}")

    def render_pdf_page(self) -> None:
        self.pdf_canvas.delete("all")
        if not self.pdf_path:
            self.clear_pdf_preview("PDF未選択")
            return
        if fitz is None or Image is None or ImageTk is None:
            self.clear_pdf_preview("PDF表示には PyMuPDF と Pillow が必要です")
            return
        try:
            with fitz.open(self.pdf_path) as document:
                self.pdf_page_count = document.page_count
                if self.pdf_page_count <= 0:
                    self.clear_pdf_preview("PDFページなし")
                    return
                self.pdf_page_index = max(0, min(self.pdf_page_index, self.pdf_page_count - 1))
                page = document.load_page(self.pdf_page_index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(self.pdf_zoom, self.pdf_zoom), alpha=False)
                image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                self.pdf_image = ImageTk.PhotoImage(image)
                self.pdf_image_width = pixmap.width
                self.pdf_image_height = pixmap.height
                self.pdf_canvas.update_idletasks()
                canvas_width = self.pdf_canvas.winfo_width()
                image_x = max((canvas_width - pixmap.width) // 2, 0) + self.pdf_pan_x
                image_y = self.pdf_pan_y
                self.pdf_image_item = self.pdf_canvas.create_image(image_x, image_y, image=self.pdf_image, anchor=tk.NW)
                self.render_pdf_marks()
                self.update_pdf_scrollregion()
                self.pdf_status_var.set(
                    f"{self.pdf_page_index + 1}/{self.pdf_page_count}ページ  {int(self.pdf_zoom * 100)}%"
                )
        except Exception as exc:
            self.clear_pdf_preview(f"PDF表示エラー: {exc}")

    def clear_pdf_preview(self, message: str) -> None:
        self.pdf_canvas.delete("all")
        self.pdf_canvas.configure(scrollregion=(0, 0, 0, 0))
        self.pdf_status_var.set(message)
        self.pdf_image = None
        self.pdf_image_item = None
        self.pdf_mark_item_ids.clear()
        self.current_pdf_mark_rows.clear()

    def _get_pdf_mark_display_text(self, row: dict) -> str:
        work_type_code = str(row["work_type_code"] or "").strip()
        return work_type_code or str(row["label"])

    def _get_pdf_mark_fill_color(self, work_type_code: str) -> str:
        palette = (
            "#1d4ed8",
            "#047857",
            "#b45309",
            "#be123c",
            "#6d28d9",
            "#0f766e",
            "#92400e",
            "#b91c1c",
        )
        if not work_type_code:
            return "#475569"
        index = sum(ord(char) for char in work_type_code) % len(palette)
        return palette[index]

    def render_pdf_marks(self) -> None:
        self.pdf_canvas.delete("pdf_mark_overlay")
        self.pdf_mark_item_ids.clear()
        self.current_pdf_mark_rows.clear()
        if self.pdf_image_item is None or self.current_invoice_file_id is None:
            return
        image_x, image_y = self.pdf_canvas.coords(self.pdf_image_item)
        for row in list_pdf_marks(self.invoice_id, self.current_invoice_file_id, self.pdf_page_index + 1):
            row_id = int(row["id"])
            self.current_pdf_mark_rows[row_id] = dict(row)
            center_x = image_x + float(row["x_ratio"]) * self.pdf_image_width
            center_y = image_y + float(row["y_ratio"]) * self.pdf_image_height
            display_text = self._get_pdf_mark_display_text(row)
            fill_color = self._get_pdf_mark_fill_color(str(row["work_type_code"] or ""))
            horizontal_padding = 10
            half_width = max(22, len(display_text) * 4 + horizontal_padding)
            half_height = 14
            tag = f"pdf_mark_{row_id}"
            oval_id = self.pdf_canvas.create_rectangle(
                center_x - half_width,
                center_y - half_height,
                center_x + half_width,
                center_y + half_height,
                fill=fill_color,
                outline="#ffeb3b" if self.selected_mark_id == row_id else "#ffffff",
                width=2,
                tags=("pdf_mark_overlay", tag),
            )
            text_id = self.pdf_canvas.create_text(
                center_x,
                center_y,
                text=display_text,
                fill="#ffffff",
                font=("", 10, "bold"),
                tags=("pdf_mark_overlay", tag),
            )
            self.pdf_mark_item_ids[row_id] = (oval_id, text_id)

    def update_pdf_mark_selection_outline(self) -> None:
        for mark_id, item_ids in self.pdf_mark_item_ids.items():
            self.pdf_canvas.itemconfigure(
                item_ids[0],
                outline="#ffeb3b" if self.selected_mark_id == mark_id else "#ffffff",
            )

    def previous_pdf_page(self) -> None:
        if self.pdf_page_index > 0:
            self.pdf_page_index -= 1
            self.reset_pdf_pan()
            self.render_pdf_page()

    def next_pdf_page(self) -> None:
        if self.pdf_page_index + 1 < self.pdf_page_count:
            self.pdf_page_index += 1
            self.reset_pdf_pan()
            self.render_pdf_page()

    def zoom_in_pdf(self) -> None:
        self.pdf_zoom = min(3.0, self.pdf_zoom + 0.25)
        self.render_pdf_page()

    def zoom_out_pdf(self) -> None:
        self.pdf_zoom = max(0.5, self.pdf_zoom - 0.25)
        self.render_pdf_page()

    def start_pdf_drag(self, event) -> str:
        self.pdf_drag_last_x = event.x
        self.pdf_drag_last_y = event.y
        return "break"

    def drag_pdf(self, event) -> str:
        dx = event.x - self.pdf_drag_last_x
        dy = event.y - self.pdf_drag_last_y
        self.pdf_drag_last_x = event.x
        self.pdf_drag_last_y = event.y
        self.pdf_pan_x += dx
        self.pdf_pan_y += dy
        if self.pdf_image_item is not None:
            self.pdf_canvas.move(self.pdf_image_item, dx, dy)
            self.pdf_canvas.move("pdf_mark_overlay", dx, dy)
            self.update_pdf_scrollregion()
        return "break"

    def zoom_pdf_with_mousewheel(self, event) -> str:
        if event.delta > 0:
            self.pdf_zoom = min(3.0, self.pdf_zoom + 0.25)
        else:
            self.pdf_zoom = max(0.5, self.pdf_zoom - 0.25)
        self.render_pdf_page()
        return "break"

    def reset_pdf_pan(self) -> None:
        self.pdf_pan_x = 0
        self.pdf_pan_y = 0

    def update_pdf_scrollregion(self) -> None:
        if self.pdf_image_item is None:
            return
        x1, y1 = self.pdf_canvas.coords(self.pdf_image_item)
        x2 = x1 + self.pdf_image_width
        y2 = y1 + self.pdf_image_height
        canvas_width = self.pdf_canvas.winfo_width()
        canvas_height = self.pdf_canvas.winfo_height()
        self.pdf_canvas.configure(
            scrollregion=(
                0,
                0,
                max(canvas_width, x2),
                max(canvas_height, y2),
            )
        )

    def place_pdf_mark(self, event) -> str | None:
        canvas_x = self.pdf_canvas.canvasx(event.x)
        canvas_y = self.pdf_canvas.canvasy(event.y)
        mark_id = self._mark_id_at_canvas_point(canvas_x, canvas_y)
        if mark_id is not None:
            self.selected_mark_id = mark_id
            self.dragging_mark_id = mark_id
            self.mark_drag_last_canvas_x = canvas_x
            self.mark_drag_last_canvas_y = canvas_y
            self.update_pdf_mark_selection_outline()
            return "break"
        if not self.mark_mode_var.get():
            return None
        if self.current_invoice_file_id is None or self.pdf_image_item is None:
            messagebox.showwarning("PDF未選択", "マークを置くPDFを選択してください。")
            return "break"
        allocation = self.get_selected_allocation()
        if not allocation:
            messagebox.showwarning("振分未選択", "先に工種コード振分行を選択してください。")
            return "break"
        image_x, image_y = self.pdf_canvas.coords(self.pdf_image_item)
        relative_x = canvas_x - image_x
        relative_y = canvas_y - image_y
        if not (0 <= relative_x <= self.pdf_image_width and 0 <= relative_y <= self.pdf_image_height):
            return "break"
        label = get_or_create_pdf_mark_label(self.invoice_id, allocation["allocation_id"])
        try:
            mark_id = create_pdf_mark(
                invoice_file_id=self.current_invoice_file_id,
                invoice_id=self.invoice_id,
                allocation_id=allocation["allocation_id"],
                page_number=self.pdf_page_index + 1,
                x_ratio=relative_x / self.pdf_image_width,
                y_ratio=relative_y / self.pdf_image_height,
                mark_type="allocation",
                label=label,
            )
        except Exception as exc:
            messagebox.showerror("PDFマーク保存エラー", str(exc))
            return "break"
        self.mark_undo_stack.append(int(mark_id))
        self.selected_mark_id = int(mark_id)
        self.render_pdf_page()
        self.update_mark_selection_status()
        return "break"

    def drag_selected_mark(self, event) -> str:
        if self.dragging_mark_id is None or self.pdf_image_item is None:
            return None
        current_x = self.pdf_canvas.canvasx(event.x)
        current_y = self.pdf_canvas.canvasy(event.y)
        dx = current_x - self.mark_drag_last_canvas_x
        dy = current_y - self.mark_drag_last_canvas_y
        self.mark_drag_last_canvas_x = current_x
        self.mark_drag_last_canvas_y = current_y
        tag = f"pdf_mark_{self.dragging_mark_id}"
        item_ids = self.pdf_mark_item_ids.get(self.dragging_mark_id)
        if not item_ids:
            return "break"
        bbox = self.pdf_canvas.coords(item_ids[0])
        image_x, image_y = self.pdf_canvas.coords(self.pdf_image_item)
        image_right = image_x + self.pdf_image_width
        image_bottom = image_y + self.pdf_image_height
        center_x = (bbox[0] + bbox[2]) / 2
        center_y = (bbox[1] + bbox[3]) / 2
        new_center_x = min(max(center_x + dx, image_x), image_right)
        new_center_y = min(max(center_y + dy, image_y), image_bottom)
        move_x = new_center_x - center_x
        move_y = new_center_y - center_y
        self.pdf_canvas.move(tag, move_x, move_y)
        self.update_pdf_scrollregion()
        return "break"

    def finish_mark_drag(self, _event=None) -> str:
        if self.dragging_mark_id is None or self.pdf_image_item is None:
            return None
        item_ids = self.pdf_mark_item_ids.get(self.dragging_mark_id)
        if item_ids:
            bbox = self.pdf_canvas.coords(item_ids[0])
            center_x = (bbox[0] + bbox[2]) / 2
            center_y = (bbox[1] + bbox[3]) / 2
            image_x, image_y = self.pdf_canvas.coords(self.pdf_image_item)
            x_ratio = (center_x - image_x) / self.pdf_image_width
            y_ratio = (center_y - image_y) / self.pdf_image_height
            try:
                update_pdf_mark_position(self.dragging_mark_id, x_ratio, y_ratio)
            except Exception as exc:
                messagebox.showerror("PDFマーク更新エラー", str(exc))
        self.dragging_mark_id = None
        return "break"

    def delete_selected_pdf_mark_shortcut(self, _event=None) -> str:
        if self.selected_mark_id is None:
            return "break"
        self.delete_pdf_mark_and_refresh(self.selected_mark_id)
        return "break"

    def undo_last_pdf_mark(self, _event=None) -> str:
        while self.mark_undo_stack:
            mark_id = self.mark_undo_stack.pop()
            if mark_id:
                self.delete_pdf_mark_and_refresh(mark_id)
                break
        return "break"

    def delete_pdf_mark_and_refresh(self, mark_id: int) -> None:
        delete_pdf_mark(mark_id)
        if self.selected_mark_id == mark_id:
            self.selected_mark_id = None
        self.render_pdf_page()
        self.update_mark_selection_status()

    def _extract_pdf_mark_id(self, tag: str) -> int | None:
        if not tag.startswith("pdf_mark_"):
            return None
        suffix = tag.removeprefix("pdf_mark_")
        if not suffix.isdigit():
            return None
        return int(suffix)

    def _mark_id_from_current_item(self) -> int | None:
        current = self.pdf_canvas.find_withtag("current")
        if not current:
            return None
        for tag in self.pdf_canvas.gettags(current[0]):
            mark_id = self._extract_pdf_mark_id(tag)
            if mark_id is not None:
                return mark_id
        return None

    def _mark_id_at_canvas_point(self, canvas_x: float, canvas_y: float) -> int | None:
        # Check a small halo around the pointer so text and circle are both easy to grab.
        items = self.pdf_canvas.find_overlapping(canvas_x - 4, canvas_y - 4, canvas_x + 4, canvas_y + 4)
        for item_id in reversed(items):
            for tag in self.pdf_canvas.gettags(item_id):
                mark_id = self._extract_pdf_mark_id(tag)
                if mark_id is not None:
                    return mark_id
        return self._mark_id_from_current_item()

    def open_pdf_mark_list(self) -> None:
        if self.current_invoice_file_id is None:
            messagebox.showwarning("PDF未選択", "先にPDFを選択してください。")
            return
        dialog = tk.Toplevel(self)
        dialog.title("PDFマーク一覧")
        dialog.geometry("540x320")
        dialog.transient(self)
        dialog.grab_set()

        tree = ttk.Treeview(dialog, columns=("label", "page", "code", "name"), show="headings", height=10)
        tree.heading("label", text="マーク")
        tree.heading("page", text="ページ")
        tree.heading("code", text="工種コード")
        tree.heading("name", text="工種名")
        tree.column("label", width=60)
        tree.column("page", width=70)
        tree.column("code", width=90)
        tree.column("name", width=220)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        mark_ids: dict[str, int] = {}

        def reload_marks() -> None:
            tree.delete(*tree.get_children())
            mark_ids.clear()
            for row in list_pdf_marks(self.invoice_id, self.current_invoice_file_id):
                item_id = tree.insert(
                    "",
                    tk.END,
                    values=(row["label"], f"{row['page_number']}ページ", row["work_type_code"], row["work_type_name"]),
                )
                mark_ids[item_id] = int(row["id"])

        def delete_selected_mark() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("選択なし", "削除するマークを選択してください。")
                return
            self.delete_pdf_mark_and_refresh(mark_ids[selection[0]])
            reload_marks()

        buttons = tk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Button(buttons, text="削除", command=delete_selected_mark).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="閉じる", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
        reload_marks()
        dialog.wait_window()
