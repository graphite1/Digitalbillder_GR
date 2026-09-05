from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from invoice_manager.repositories import list_projects, list_work_type_codes
from invoice_manager.services.project_budget import (
    BudgetRowInput,
    ExtractedBudgetCandidate,
    SourcePreview,
    build_project_forecast,
    get_project_budget,
    prepare_budget_rows_from_candidates,
    preview_source_document,
    resolve_budget_source,
    save_project_budget,
)


def _amount_text(value: int | None) -> str:
    return "未入力" if value is None else f"{value:,}"


def _parse_amount(value: str, label: str, *, optional: bool = False) -> int | None:
    text = value.strip().replace(",", "")
    if optional and not text:
        return None
    try:
        amount = int(text)
    except ValueError as exc:
        raise ValueError(f"{label}は0以上の整数で入力してください。") from exc
    if amount < 0:
        raise ValueError(f"{label}は0以上の整数で入力してください。")
    return amount


class ProjectBudgetWindow(tk.Toplevel):
    """Review a source document, edit budget rows, and compare a net-cost forecast."""

    def __init__(self, master, project_id: int | None = None) -> None:
        super().__init__(master)
        self.title("工事予算・最終原価見込（税抜）")
        self.geometry("1180x820")
        self.minsize(980, 680)
        self.project_options: dict[str, int] = {}
        self.code_name_options: dict[str, tuple[str, str]] = {}
        self.row_values: dict[str, dict[str, object]] = {}
        self.candidate_values: dict[str, ExtractedBudgetCandidate] = {}
        self.editing_item: str | None = None
        self.active_candidate: ExtractedBudgetCandidate | None = None
        self.source_preview: SourcePreview | None = None
        self.source_path: Path | None = None
        self.stored_source_path: Path | None = None

        self.project_var = tk.StringVar()
        self.source_var = tk.StringVar(value="原本未選択")
        self.code_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.budget_var = tk.StringVar()
        self.scheduled_var = tk.StringVar()
        self.remaining_var = tk.StringVar()
        self.include_var = tk.BooleanVar(value=False)
        self.actual_code_var = tk.StringVar()
        self.original_var = tk.StringVar(value="原本値: なし（手入力）")
        self.rows_summary_var = tk.StringVar()
        self.batch_status_var = tk.StringVar(value="全候補を追加 → 集計対象を選択 → 予算を保存")

        self._load_projects(project_id)
        self._build()
        self._project_changed()

    def _load_projects(self, initial_project_id: int | None) -> None:
        for row in list_projects():
            label = f"{row['project_code']}｜{row['project_name']}"
            self.project_options[label] = int(row["id"])
            if initial_project_id is not None and int(row["id"]) == int(initial_project_id):
                self.project_var.set(label)
        if not self.project_var.get() and self.project_options:
            self.project_var.set(next(iter(self.project_options)))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(top, text="工事").pack(side=tk.LEFT)
        project_combo = ttk.Combobox(
            top, textvariable=self.project_var, values=list(self.project_options),
            state="readonly", width=48,
        )
        project_combo.pack(side=tk.LEFT, padx=(6, 14))
        project_combo.bind("<<ComboboxSelected>>", lambda _event: self._project_changed())
        ttk.Button(top, text="PDF原本を確認", command=self._choose_source).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="原本を開く", command=self._open_source).pack(side=tk.LEFT, padx=3)
        ttk.Label(top, textvariable=self.source_var).pack(side=tk.LEFT, padx=8)

        source_frame = ttk.LabelFrame(self, text="2ページ目の抽出候補（親集計と明細の二重計上に注意）", padding=6)
        source_frame.grid(row=1, column=0, sticky=tk.EW, padx=8, pady=(0, 6))
        source_frame.columnconfigure(0, weight=1)
        source_table = ttk.Frame(source_frame)
        source_table.grid(row=0, column=0, sticky=tk.NSEW)
        self.candidate_tree = ttk.Treeview(
            source_table,
            columns=("code", "name", "budget", "scheduled", "location"),
            show="headings", height=3,
        )
        headings = {
            "code": "原本コード", "name": "原本科目", "budget": "実行予算",
            "scheduled": "予定金額", "location": "位置 / 集計階層",
        }
        widths = {"code": 110, "name": 230, "budget": 130, "scheduled": 130, "location": 260}
        for column, label in headings.items():
            self.candidate_tree.heading(column, text=label)
            self.candidate_tree.column(column, width=widths[column], anchor=tk.E if column in {"budget", "scheduled"} else tk.W)
        candidate_xscroll = ttk.Scrollbar(source_table, orient=tk.HORIZONTAL, command=self.candidate_tree.xview)
        candidate_xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        candidate_scroll = ttk.Scrollbar(source_table, orient=tk.VERTICAL, command=self.candidate_tree.yview)
        candidate_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.candidate_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set, xscrollcommand=candidate_xscroll.set)
        self.candidate_tree.bind("<Double-1>", lambda _event: self._candidate_to_form())
        source_actions = ttk.Frame(source_frame)
        source_actions.grid(row=0, column=1, sticky=tk.N, padx=(8, 0))
        ttk.Button(source_actions, text="全候補を予算行へ追加", command=self._add_all_candidates).pack(fill=tk.X, pady=2)
        ttk.Button(source_actions, text="選択候補をまとめて追加", command=self._add_selected_candidates).pack(fill=tk.X, pady=2)
        ttk.Button(source_actions, text="選択候補を編集", command=self._candidate_to_form).pack(fill=tk.X, pady=2)
        ttk.Label(self, textvariable=self.batch_status_var, padding=(8, 0, 8, 6), wraplength=950).grid(row=2, column=0, sticky=tk.EW)
        editor = ttk.LabelFrame(self, text="登録行の編集", padding=7)
        editor.grid(row=3, column=0, sticky=tk.EW, padx=8, pady=(0, 6))
        for column in range(6):
            editor.columnconfigure(column, weight=1, uniform="editor")
        labels = ("原本コード", "科目", "実行予算(税抜)", "予定金額(税抜)", "残工事見込(税抜)", "Web工種対応")
        for column, label in enumerate(labels):
            ttk.Label(editor, text=label).grid(row=0, column=column, sticky=tk.W, padx=3)
        ttk.Entry(editor, textvariable=self.code_var, width=13).grid(row=1, column=0, sticky=tk.EW, padx=3)
        ttk.Entry(editor, textvariable=self.name_var, width=22).grid(row=1, column=1, sticky=tk.EW, padx=3)
        ttk.Entry(editor, textvariable=self.budget_var, width=15).grid(row=1, column=2, sticky=tk.EW, padx=3)
        ttk.Entry(editor, textvariable=self.scheduled_var, width=15).grid(row=1, column=3, sticky=tk.EW, padx=3)
        ttk.Entry(editor, textvariable=self.remaining_var, width=17).grid(row=1, column=4, sticky=tk.EW, padx=3)
        self.actual_combo = ttk.Combobox(editor, textvariable=self.actual_code_var, width=24, state="normal")
        self.actual_combo.grid(row=1, column=5, sticky=tk.EW, padx=3)
        ttk.Checkbutton(editor, text="この行を予算合計へ含める", variable=self.include_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, padx=3, pady=(6, 0)
        )
        ttk.Label(editor, textvariable=self.original_var, foreground="#555555").grid(
            row=2, column=2, columnspan=3, sticky=tk.W, padx=3, pady=(6, 0)
        )
        ttk.Button(editor, text="行を登録 / 更新", command=self._apply_row).grid(row=2, column=5, sticky=tk.E, pady=(5, 0))

        current = ttk.LabelFrame(self, text="予算行（変更後は「予算を保存」で一括保存・保存後も修正できます）", padding=6)
        current.grid(row=4, column=0, sticky=tk.NSEW, padx=8, pady=(0, 6))
        row_actions = ttk.Frame(current)
        row_actions.pack(side=tk.TOP, fill=tk.X, pady=(0, 3))
        ttk.Button(row_actions, text="全行を選択", command=self._select_all_rows).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_actions, text="選択行を集計に含める", command=lambda: self._set_selected_inclusion(True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_actions, text="選択行を集計から外す", command=lambda: self._set_selected_inclusion(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_actions, text="選択行を編集", command=self._edit_current_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_actions, text="選択行を外す", command=self._remove_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_actions, text="入力クリア", command=self._clear_form).pack(side=tk.LEFT, padx=2)
        ttk.Label(current, textvariable=self.rows_summary_var).pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        row_table = ttk.Frame(current)
        row_table.pack(fill=tk.BOTH, expand=True)
        self.row_tree = ttk.Treeview(
            row_table,
            columns=("include", "source_code", "name", "budget", "scheduled", "remaining", "actual_code", "original"),
            show="headings", height=8,
        )
        row_headings = {
            "include": "集計", "source_code": "原本コード", "name": "科目", "budget": "実行予算",
            "scheduled": "予定金額", "remaining": "残工事見込", "actual_code": "Web工種対応",
            "original": "抽出時の原本値",
        }
        row_widths = {"include": 55, "source_code": 100, "name": 170, "budget": 110, "scheduled": 110,
                      "remaining": 115, "actual_code": 125, "original": 220}
        for column, label in row_headings.items():
            self.row_tree.heading(column, text=label)
            self.row_tree.column(column, width=row_widths[column], anchor=tk.E if column in {"budget", "scheduled", "remaining"} else tk.W)
        row_xscroll = ttk.Scrollbar(row_table, orient=tk.HORIZONTAL, command=self.row_tree.xview)
        row_xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        row_scroll = ttk.Scrollbar(row_table, orient=tk.VERTICAL, command=self.row_tree.yview)
        row_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.row_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.row_tree.configure(yscrollcommand=row_scroll.set, xscrollcommand=row_xscroll.set)
        self.row_tree.bind("<Double-1>", lambda _event: self._edit_current_row())

        forecast = ttk.LabelFrame(self, text="予算構成と最終原価見込（税抜）", padding=6)
        forecast.grid(row=5, column=0, sticky=tk.EW, padx=8, pady=(0, 6))
        forecast.columnconfigure(0, weight=1)
        forecast_table = ttk.Frame(forecast)
        forecast_table.grid(row=0, column=0, sticky=tk.NSEW)
        self.forecast_tree = ttk.Treeview(
            forecast_table,
            columns=("code", "budget", "actual", "remaining", "projected", "variance"),
            show="headings", height=4,
        )
        for column, label, width in (
            ("code", "予算コード / Web対応", 220), ("budget", "実行予算", 115),
            ("actual", "保管済Web実績", 120), ("remaining", "残工事見込", 120),
            ("projected", "最終見込", 120), ("variance", "予算差", 120),
        ):
            self.forecast_tree.heading(column, text=label)
            self.forecast_tree.column(column, width=width, anchor=tk.E if column != "code" else tk.W)
        forecast_xscroll = ttk.Scrollbar(forecast_table, orient=tk.HORIZONTAL, command=self.forecast_tree.xview)
        forecast_xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        forecast_scroll = ttk.Scrollbar(forecast_table, orient=tk.VERTICAL, command=self.forecast_tree.yview)
        forecast_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.forecast_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.forecast_tree.configure(xscrollcommand=forecast_xscroll.set, yscrollcommand=forecast_scroll.set)
        self.chart = tk.Canvas(forecast, width=365, height=125, background="white", highlightthickness=1)
        self.chart.grid(row=0, column=1, sticky=tk.NS, padx=(8, 0))

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=6, column=0, sticky=tk.EW)
        ttk.Label(bottom, text="メモ").pack(side=tk.LEFT)
        self.note_entry = ttk.Entry(bottom)
        self.note_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(bottom, text="予算を保存", command=self._save).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bottom, text="見込を再表示", command=self._refresh_forecast).pack(side=tk.RIGHT, padx=3)

    def _project_id(self) -> int | None:
        return self.project_options.get(self.project_var.get())

    def _project_changed(self) -> None:
        self.row_values.clear()
        self.row_tree.delete(*self.row_tree.get_children())
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.candidate_values.clear()
        self.source_preview = None
        self.source_path = None
        self.stored_source_path = None
        self.source_var.set("原本未選択")
        self.batch_status_var.set("全候補を追加 → 集計対象を選択 → 予算を保存")
        self._update_rows_summary()
        self.code_name_options.clear()
        project_id = self._project_id()
        if project_id is None:
            return
        mapping_labels = []
        for row in list_work_type_codes(project_id):
            code, name = str(row["code"]), str(row["name"])
            label = f"{code}｜{name}"
            self.code_name_options[label] = (code, name)
            mapping_labels.append(label)
        try:
            from invoice_manager.services.historical_costs import list_actual_costs

            for row in list_actual_costs(project_id):
                code, name = str(row.work_type_code), str(row.work_type_name)
                label = f"{code}｜{name}"
                if label not in self.code_name_options:
                    self.code_name_options[label] = (code, name)
                    mapping_labels.append(label)
        except Exception:
            # Budget editing remains available before historical data is initialized.
            pass
        self.actual_combo.configure(values=mapping_labels)
        budget = get_project_budget(project_id)
        self.note_entry.delete(0, tk.END)
        if budget is not None:
            self.note_entry.insert(0, budget.note)
            if budget.source_original_name:
                self.source_var.set(f"保存済: {budget.source_original_name}")
            if budget.source_stored_path:
                try:
                    self.stored_source_path = resolve_budget_source(budget.source_stored_path)
                except ValueError:
                    self.stored_source_path = None
            for row in budget.rows:
                source_candidate = self._candidate_from_json(row.source_candidate_json)
                values = {
                    "row_id": row.id,
                    "work_type_code": row.work_type_code, "work_type_name": row.work_type_name,
                    "budget_net": row.budget_net, "scheduled_net": row.scheduled_net,
                    "remaining_net": row.remaining_net, "include_in_total": row.include_in_total,
                    "actual_work_type_code": row.actual_work_type_code,
                    "source_candidate": source_candidate, "edit_version": row.edit_version,
                }
                item = self.row_tree.insert("", tk.END)
                self.row_values[item] = values
                self._render_row(item)
        self._clear_form()
        self._update_rows_summary()
        self._refresh_forecast()

    def _candidate_from_json(self, payload: str | None) -> ExtractedBudgetCandidate | None:
        if not payload:
            return None
        try:
            values = json.loads(payload)
            return ExtractedBudgetCandidate(**values)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _choose_source(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="実行予算原本を選択",
            filetypes=(("PDF", "*.pdf"), ("Excel", "*.xlsx *.xlsm"), ("すべて", "*.*")),
        )
        if not path:
            return
        try:
            preview = preview_source_document(path, page_number=2)
        except Exception as exc:
            messagebox.showerror("原本読取エラー", str(exc), parent=self)
            return
        self.source_preview = preview
        self.source_path = preview.path
        self.source_var.set(preview.path.name)
        self._show_candidates(preview)
        self.batch_status_var.set(f"{len(preview.candidates)}件を抽出しました。まとめて追加後、集計対象を選んで保存できます。")
        if preview.warnings:
            messagebox.showwarning("抽出候補の確認", "\n".join(preview.warnings), parent=self)

    def _show_candidates(self, preview: SourcePreview) -> None:
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.candidate_values.clear()
        for candidate in preview.candidates:
            name = candidate.work_type_name or self._master_name(candidate.work_type_code) or "（名称要確認）"
            item = self.candidate_tree.insert(
                "", tk.END,
                values=(candidate.work_type_code, name, _amount_text(candidate.budget_net),
                        _amount_text(candidate.scheduled_net),
                        f"{candidate.source_location} / {candidate.aggregation_hint}"),
            )
            self.candidate_values[item] = candidate

    def _add_all_candidates(self) -> None:
        self._add_candidates(self.candidate_tree.get_children())

    def _add_selected_candidates(self) -> None:
        selected = set(self.candidate_tree.selection())
        self._add_candidates(tuple(item for item in self.candidate_tree.get_children() if item in selected))

    def _add_candidates(self, items) -> None:
        if not items:
            messagebox.showinfo("抽出候補", "PDFの抽出候補を確認し、追加する候補を選択してください。", parent=self)
            return
        try:
            rows, skipped = prepare_budget_rows_from_candidates(
                [self.candidate_values[item] for item in items],
                existing_codes=[str(row["work_type_code"]) for row in self.row_values.values()],
                fallback_names={code: name for code, name in self.code_name_options.values()},
            )
        except ValueError as exc:
            messagebox.showwarning("一括追加の確認", f"{exc}\n\n候補を個別に修正するか、確認できた候補だけ選択して追加してください。", parent=self)
            return
        added_items = []
        for row in rows:
            item = self.row_tree.insert("", tk.END)
            self.row_values[item] = {
                "row_id": None,
                "work_type_code": row.work_type_code, "work_type_name": row.work_type_name,
                "budget_net": row.budget_net, "scheduled_net": row.scheduled_net,
                "remaining_net": row.remaining_net, "include_in_total": row.include_in_total,
                "actual_work_type_code": row.actual_work_type_code,
                "source_candidate": row.source_candidate,
            }
            self._render_row(item)
            added_items.append(item)
        if added_items:
            self.row_tree.selection_set(added_items)
            self.row_tree.see(added_items[0])
        skipped_text = f" 既存の{len(skipped)}行は変更していません。" if skipped else ""
        self.batch_status_var.set(
            f"{len(rows)}行を追加しました（未保存）。{skipped_text} 集計対象の明細を選び、「予算を保存」で一括登録します。"
        )
        self._update_rows_summary()

    def _select_all_rows(self) -> None:
        self.row_tree.selection_set(self.row_tree.get_children())

    def _set_selected_inclusion(self, included: bool) -> None:
        selection = self.row_tree.selection()
        if not selection:
            messagebox.showinfo("集計対象", "予算行を選択してください。Ctrl・Shiftで複数行を選べます。", parent=self)
            return
        for item in selection:
            self.row_values[item]["include_in_total"] = included
            self._render_row(item)
        if self.editing_item in selection:
            self.include_var.set(included)
        self._update_rows_summary()

    def _update_rows_summary(self) -> None:
        included = [row for row in self.row_values.values() if row["include_in_total"]]
        total = sum(int(row["budget_net"]) for row in included)
        self.rows_summary_var.set(
            f"予算行 {len(self.row_values)}件 ｜ 集計対象 {len(included)}件 ｜ 実行予算合計（税抜） {total:,}円"
            "　Ctrl・Shiftで複数選択"
        )

    def _master_name(self, code: str) -> str:
        for mapped_code, name in self.code_name_options.values():
            if mapped_code == code:
                return name
        return ""

    def _candidate_to_form(self) -> None:
        selection = self.candidate_tree.selection()
        if not selection:
            return
        candidate = self.candidate_values[selection[0]]
        self.editing_item = None
        self.active_candidate = candidate
        self.code_var.set(candidate.work_type_code)
        self.name_var.set(candidate.work_type_name or self._master_name(candidate.work_type_code))
        self.budget_var.set("" if candidate.budget_net is None else str(candidate.budget_net))
        self.scheduled_var.set("" if candidate.scheduled_net is None else str(candidate.scheduled_net))
        self.remaining_var.set("")
        self.include_var.set(False)
        self.actual_code_var.set("")
        self._set_original_label(candidate)

    def _set_original_label(self, candidate: ExtractedBudgetCandidate | None) -> None:
        if candidate is None:
            self.original_var.set("原本値: なし（手入力）")
        else:
            self.original_var.set(
                f"抽出時: {candidate.work_type_code} / {_amount_text(candidate.budget_net)} / "
                f"予定 {_amount_text(candidate.scheduled_net)}"
            )

    def _actual_code(self) -> str | None:
        text = self.actual_code_var.get().strip()
        if not text:
            return None
        return self.code_name_options.get(text, (text.split("｜", 1)[0].strip(), ""))[0] or None

    def _apply_row(self) -> None:
        try:
            code = self.code_var.get().strip()
            name = self.name_var.get().strip()
            if not code or not name:
                raise ValueError("原本コードと科目は必須です。名称候補がない場合は原本を見て入力してください。")
            values = {
                "row_id": (
                    self.row_values[self.editing_item].get("row_id")
                    if self.editing_item in self.row_values else None
                ),
                "work_type_code": code, "work_type_name": name,
                "budget_net": _parse_amount(self.budget_var.get(), "実行予算"),
                "scheduled_net": _parse_amount(self.scheduled_var.get(), "予定金額", optional=True),
                "remaining_net": _parse_amount(self.remaining_var.get(), "残工事見込", optional=True),
                "include_in_total": self.include_var.get(), "actual_work_type_code": self._actual_code(),
                "source_candidate": self.active_candidate,
            }
        except ValueError as exc:
            messagebox.showwarning("入力確認", str(exc), parent=self)
            return
        duplicate = next(
            (item for item, row in self.row_values.items()
             if row["work_type_code"] == code and item != self.editing_item),
            None,
        )
        if duplicate is not None:
            messagebox.showwarning("コード重複", "同じ原本コードは1行だけ登録できます。既存行を編集してください。", parent=self)
            return
        item = self.editing_item or self.row_tree.insert("", tk.END)
        self.row_values[item] = values
        self._render_row(item)
        self._update_rows_summary()
        self._clear_form()

    def _render_row(self, item: str) -> None:
        row = self.row_values[item]
        source = row.get("source_candidate")
        original = "手入力"
        if isinstance(source, ExtractedBudgetCandidate):
            original = f"{source.work_type_code} / {_amount_text(source.budget_net)} / {_amount_text(source.scheduled_net)}"
        self.row_tree.item(item, values=(
            "含む" if row["include_in_total"] else "除外",
            row["work_type_code"], row["work_type_name"], _amount_text(row["budget_net"]),
            _amount_text(row["scheduled_net"]), _amount_text(row["remaining_net"]),
            row["actual_work_type_code"] or "未対応", original,
        ))

    def _edit_current_row(self) -> None:
        selection = self.row_tree.selection()
        if not selection:
            return
        item = selection[0]
        row = self.row_values[item]
        self.editing_item = item
        source = row.get("source_candidate")
        self.active_candidate = source if isinstance(source, ExtractedBudgetCandidate) else None
        self.code_var.set(str(row["work_type_code"]))
        self.name_var.set(str(row["work_type_name"]))
        self.budget_var.set(str(row["budget_net"]))
        self.scheduled_var.set("" if row["scheduled_net"] is None else str(row["scheduled_net"]))
        self.remaining_var.set("" if row["remaining_net"] is None else str(row["remaining_net"]))
        self.include_var.set(bool(row["include_in_total"]))
        self.actual_code_var.set("" if row["actual_work_type_code"] is None else str(row["actual_work_type_code"]))
        self._set_original_label(self.active_candidate)

    def _remove_row(self) -> None:
        for item in self.row_tree.selection():
            self.row_values.pop(item, None)
            self.row_tree.delete(item)
        self._clear_form()
        self._update_rows_summary()

    def _clear_form(self) -> None:
        self.editing_item = None
        self.active_candidate = None
        for variable in (self.code_var, self.name_var, self.budget_var, self.scheduled_var,
                         self.remaining_var, self.actual_code_var):
            variable.set("")
        self.include_var.set(False)
        self._set_original_label(None)

    def _save(self) -> None:
        project_id = self._project_id()
        if project_id is None:
            return
        rows = [
            BudgetRowInput(
                work_type_code=str(row["work_type_code"]), work_type_name=str(row["work_type_name"]),
                budget_net=int(row["budget_net"]), remaining_net=row["remaining_net"],
                scheduled_net=row["scheduled_net"], include_in_total=bool(row["include_in_total"]),
                actual_work_type_code=row["actual_work_type_code"], source_candidate=row["source_candidate"],
                row_id=row.get("row_id"),
            )
            for row in self.row_values.values()
        ]
        included = [row for row in rows if row.include_in_total]
        if not included:
            messagebox.showwarning("集計対象の確認", "予算行から集計対象の明細を選び、「選択行を集計に含める」を押してください。", parent=self)
            return
        total = sum(row.budget_net for row in included)
        code_preview = "、".join(row.work_type_code for row in included[:12])
        if len(included) > 12:
            code_preview += " ほか"
        confirmed = messagebox.askyesno(
            "集計対象と合計の確認",
            f"保存する予算行: {len(rows)}行\n"
            f"集計対象: {len(included)}行\n"
            f"コード: {code_preview or 'なし'}\n"
            f"実行予算合計(税抜): {total:,}円\n\n"
            "親集計とその明細を両方含めていないこと、原本の合計と一致することを確認して保存しますか？",
            parent=self,
        )
        if not confirmed:
            return
        try:
            save_project_budget(
                project_id, rows, source_path=self.source_path, source_preview=self.source_preview,
                confirmed=True, note=self.note_entry.get(),
            )
        except Exception as exc:
            messagebox.showerror("保存エラー", str(exc), parent=self)
            return
        messagebox.showinfo("予算保存", "確認済みの予算行を保存しました。金額はすべて税抜です。", parent=self)
        self._project_changed()

    def _refresh_forecast(self) -> None:
        self.forecast_tree.delete(*self.forecast_tree.get_children())
        self.chart.delete("all")
        project_id = self._project_id()
        if project_id is None:
            return
        try:
            forecast = build_project_forecast(project_id)
        except Exception as exc:
            self.chart.create_text(10, 15, anchor=tk.NW, text=f"実績読込エラー: {exc}", fill="#a00000")
            return
        for row in forecast:
            code_label = f"Web未対応: {row.work_type_code}" if row.is_unmapped_actual else row.work_type_code
            if row.actual_work_type_code:
                code_label += f" → {row.actual_work_type_code}"
            self.forecast_tree.insert("", tk.END, values=(
                code_label, _amount_text(row.budget_net), _amount_text(row.actual_net),
                _amount_text(row.remaining_net), _amount_text(row.projected_final_net),
                _amount_text(row.variance_net),
            ))
        self._draw_chart(forecast)

    def _draw_chart(self, forecast) -> None:
        width = max(self.chart.winfo_width(), 365) - 20
        included = [row for row in forecast if row.include_in_total]
        total_budget = sum(row.budget_net for row in included)
        self.chart.create_text(10, 10, anchor=tk.NW, text="予算構成（集計対象のみ）")
        if total_budget:
            x = 10.0
            colors = ("#4e79a7", "#59a14f", "#f28e2b", "#b07aa1", "#76b7b2", "#e15759")
            for index, row in enumerate(included):
                segment = width * row.budget_net / total_budget
                self.chart.create_rectangle(x, 32, x + segment, 54, fill=colors[index % len(colors)], outline="white")
                x += segment
            self.chart.create_text(10, 58, anchor=tk.NW, text=f"実行予算合計 {_amount_text(total_budget)} 円")
        else:
            self.chart.create_text(10, 34, anchor=tk.NW, text="集計対象行がありません。", fill="#777777")
        self.chart.create_text(10, 82, anchor=tk.NW, text="最終原価見込 = 保管済Web実績 + 残工事見込")
        unmapped = [row for row in forecast if row.is_unmapped_actual]
        if unmapped:
            unmapped_total = sum(int(row.actual_net or 0) for row in unmapped)
            self.chart.create_text(
                10, 104, anchor=tk.NW,
                text=f"Web未対応実績 {len(unmapped)}件 / {_amount_text(unmapped_total)}円があるため全体見込は未確定です。",
                fill="#a00000", width=345,
            )
            return
        if not included or any(row.remaining_net is None or row.actual_net is None for row in included):
            self.chart.create_text(
                10, 104, anchor=tk.NW,
                text="実績未同期・Web対応未設定・残工事未入力の行があるため算出できません。",
                fill="#9a4d00", width=345,
            )
            return
        actual = sum(int(row.actual_net or 0) for row in included)
        remaining = sum(int(row.remaining_net or 0) for row in included)
        projected = actual + remaining
        scale = max(total_budget, projected, 1)
        actual_width = width * actual / scale
        remaining_width = width * remaining / scale
        self.chart.create_rectangle(10, 104, 10 + actual_width, 122, fill="#4e79a7", outline="")
        self.chart.create_rectangle(10 + actual_width, 104, 10 + actual_width + remaining_width, 122, fill="#f28e2b", outline="")
        self.chart.create_rectangle(10, 104, 10 + width * total_budget / scale, 122, outline="#222222", width=2)

    def _open_source(self) -> None:
        path = self.source_path or self.stored_source_path
        if path is None or not path.exists():
            messagebox.showwarning("原本", "開ける予算原本がありません。", parent=self)
            return
        os.startfile(path)  # type: ignore[attr-defined]
