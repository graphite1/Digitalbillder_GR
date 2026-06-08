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
    delete_invoice_allocation,
    ensure_work_type_codes_for_project,
    get_invoice_allocation_total,
    get_invoice_detail,
    list_invoice_allocations,
    list_invoice_files,
    list_recent_work_type_codes_for_project_vendor,
    list_vendor_work_type_candidates,
    list_work_type_codes,
    save_invoice_allocation,
    update_invoice_memo,
)
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
        self.allocation_ids: dict[str, int] = {}
        self.work_type_options: dict[str, int] = {}
        self.pdf_path: str | None = None
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
        self.invoice_total = 0
        self.project_id: int | None = None
        self.vendor_id: int | None = None
        self.topmost_var = tk.IntVar(value=0)
        self.memo_text = ""
        self._build()
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
        self.files.column("size", width=80)
        self.files.pack(fill=tk.BOTH, expand=True)
        self.files.bind("<<TreeviewSelect>>", self.on_pdf_file_selected)
        tk.Button(files_frame, text="PDFを開く", command=self.open_pdf).pack(anchor=tk.W, pady=(6, 0))

        pdf_frame = tk.LabelFrame(self, text="PDFプレビュー（原本は編集しません）", padx=8, pady=8)
        pdf_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        pdf_actions = tk.Frame(pdf_frame)
        pdf_actions.pack(fill=tk.X)
        tk.Button(pdf_actions, text="前ページ", command=self.previous_pdf_page).pack(side=tk.LEFT, padx=2)
        tk.Button(pdf_actions, text="次ページ", command=self.next_pdf_page).pack(side=tk.LEFT, padx=2)
        tk.Button(pdf_actions, text="縮小", command=self.zoom_out_pdf).pack(side=tk.LEFT, padx=8)
        tk.Button(pdf_actions, text="拡大", command=self.zoom_in_pdf).pack(side=tk.LEFT, padx=2)
        self.pdf_status_var = tk.StringVar(value="PDF未選択")
        tk.Label(pdf_actions, textvariable=self.pdf_status_var).pack(side=tk.LEFT, padx=8)

        canvas_frame = tk.Frame(pdf_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.pdf_canvas = tk.Canvas(canvas_frame, bg="#666666")
        pdf_y_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.pdf_canvas.yview)
        pdf_x_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.pdf_canvas.xview)
        self.pdf_canvas.configure(yscrollcommand=pdf_y_scrollbar.set, xscrollcommand=pdf_x_scrollbar.set)
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
            item_id = self.allocations.insert(
                "",
                tk.END,
                values=(row["code"], row["name"], format_amount(row["amount"]), row["memo"] or "", row["sort_order"]),
            )
            self.allocation_ids[item_id] = int(row["id"])
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

    def load_files(self) -> None:
        self.files.delete(*self.files.get_children())
        self.file_ids.clear()
        first_item_id = None
        for file_row in list_invoice_files(self.invoice_id):
            item_id = self.files.insert("", tk.END, values=(file_row["original_file_name"], file_row["file_size"]))
            self.file_ids[item_id] = file_row["stored_file_path"]
            if first_item_id is None:
                first_item_id = item_id
        if first_item_id:
            self.files.selection_set(first_item_id)
            self.files.focus(first_item_id)
            self.show_pdf_file(self.file_ids[first_item_id])
        else:
            self.clear_pdf_preview("PDF添付なし")

    def save_memo(self) -> None:
        update_invoice_memo(self.invoice_id, self.memo_text.strip())
        if self.on_saved:
            self.on_saved()
        self.load()

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
                save_invoice_allocation(
                    invoice_id=self.invoice_id,
                    work_type_code_id=self.work_type_options[selected_work_type.get()],
                    amount=int(amount_var.get().replace(",", "")),
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

    def on_pdf_file_selected(self, _event=None) -> None:
        selection = self.files.selection()
        if selection:
            self.show_pdf_file(self.file_ids[selection[0]])

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
