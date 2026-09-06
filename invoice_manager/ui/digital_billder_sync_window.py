from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from invoice_manager.repositories import get_app_setting
from invoice_manager.ui.background_activity import BackgroundActivity, ActivityPanel
from invoice_manager.services.digital_billder_credentials import (
    ACCOUNT_KEY,
    CredentialDependencyError,
    CredentialSettingsError,
    CredentialVaultError,
    save_credentials,
)
from invoice_manager.services.digital_billder_sync import (
    fetch_candidates, import_selected, list_candidates, set_excluded,
)


def credential_save_error(error: Exception) -> str:
    if isinstance(error, (ValueError, CredentialDependencyError, CredentialVaultError, CredentialSettingsError)):
        return str(error)
    return "保存できませんでした。Windowsの資格情報マネージャーと台帳を確認してください。"


class DigitalBillderSyncWindow(tk.Toplevel):
    def __init__(self, master, on_close=None):
        super().__init__(master)
        self.title("Digital Billder 新着取込")
        self.geometry("1100x660")
        self.minsize(820, 440)
        if master.state() != "withdrawn":
            self.transient(master)
        self.busy = False
        self.closing = False
        self.activity = BackgroundActivity(self, "新着確認・請求取込")
        self.on_close = on_close
        self.events = queue.Queue()
        self.show_excluded = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="新着確認で一覧を取得し、取り込む請求を選んでください。")
        self.buttons = []
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill=tk.X)
        for label, command in [
            ("新着確認", self.fetch), ("ログイン設定", self.credentials),
            ("全選択", self.select_all), ("選択を取込", self.import_rows),
            ("選択を除外", self.exclude), ("選択の除外解除", self.restore),
        ]:
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0, 6))
            self.buttons.append(button)
        self.toggle = ttk.Checkbutton(bar, text="除外済みを表示", variable=self.show_excluded, command=self.refresh)
        self.toggle.pack(side=tk.LEFT)
        ttk.Label(self, text="破棄済み・取込済み・除外済みは新着候補から外します。複数選択: Ctrl / Shift", padding=(12, 0)).pack(anchor=tk.W)
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        columns = ("date", "project", "vendor", "amount", "id")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for key, title, width in [
            ("date", "請求日", 100), ("project", "工事名", 300),
            ("vendor", "取引先", 250), ("amount", "請求金額(税込)", 125), ("id", "請求書ID", 280),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=80, anchor=tk.E if key == "amount" else tk.W)
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ActivityPanel(self, activity=self.activity).pack(fill=tk.X, padx=12, before=frame)
        ttk.Label(self, textvariable=self.status, padding=12, wraplength=1050).pack(fill=tk.X)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.poll_id = self.after(150, self.poll)
        self.bind("<Destroy>", self._on_destroy, add=True)

    def refresh(self):
        rows = list_candidates("excluded" if self.show_excluded.get() else "pending")
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", tk.END, iid=row.external_id, values=(
                row.invoice_date, row.project_name, row.vendor_name, f"{row.total_amount:,}", row.external_id,
            ))
        if not self.busy:
            self.status.set(f"{'除外済み' if self.show_excluded.get() else '未確認'}: {len(rows)}件")
        self.update_buttons()

    def update_buttons(self):
        for button in self.buttons:
            button.configure(state=tk.DISABLED if self.busy else tk.NORMAL)
        self.toggle.configure(state=tk.DISABLED if self.busy else tk.NORMAL)
        if not self.busy:
            self.buttons[3].configure(state=tk.DISABLED if self.show_excluded.get() else tk.NORMAL)
            self.buttons[4].configure(state=tk.DISABLED if self.show_excluded.get() else tk.NORMAL)
            self.buttons[5].configure(state=tk.NORMAL if self.show_excluded.get() else tk.DISABLED)

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def start(self, function, message="処理を開始しています…"):
        if self.busy:
            return
        self.busy = True
        self.update_buttons()
        self.status.set(message)
        self.activity.start(message)

        def work():
            try:
                result = function(lambda text: self.events.put(("progress", text)))
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        try:
            threading.Thread(target=work, daemon=False).start()
        except Exception:
            self.busy = False
            self.status.set("処理を開始できませんでした。もう一度実行してください。")
            self.activity.finish(self.status.get(), failed=True)
            self.update_buttons()

    def poll(self):
        self.poll_id = None
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(value)
                    self.activity.update(value)
                    continue
                self.busy = False
                self.activity.finish(str(value), failed=kind == "error")
                try:
                    self.refresh()
                except Exception:
                    message = f"{value}\n処理後の一覧再表示に失敗しました。もう一度一覧を表示してください。"
                    self.status.set(message)
                    self.activity.finish(message, failed=True)
                    self.update_buttons()
                    continue
                if kind == "error":
                    self.status.set(value)
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        if not self.closing:
            self.poll_id = self.after(150, self.poll)

    def fetch(self):
        self.show_excluded.set(False)

        def run(progress):
            count = fetch_candidates(progress)
            return f"未確認: {count}件。必要な請求を選択して取り込むか、不要な請求を除外してください。"

        self.start(run, "新着確認を開始しています。ほかの画面も操作できます。")

    def import_rows(self):
        ids = set(self.tree.selection())
        if not ids or self.show_excluded.get():
            return

        def run(progress):
            result = import_selected(ids, progress)
            return f"取込完了: {result.inserted_count}件 / PDF: {result.file_count}件"

        self.start(run, f"選択した{len(ids)}件の取込を開始しています。ほかの画面も操作できます。")

    def exclude(self):
        ids = set(self.tree.selection())
        if ids and not self.show_excluded.get():
            set_excluded(ids, True)
            self.refresh()
            self.status.set(f"{len(ids)}件を除外しました。「除外済みを表示」から戻せます。")

    def restore(self):
        ids = set(self.tree.selection())
        if ids and self.show_excluded.get():
            set_excluded(ids, False)
            self.refresh()

    def credentials(self):
        dialog = tk.Toplevel(self)
        dialog.title("Digital Billder ログイン設定")
        dialog.transient(self)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=20)
        form.pack(fill=tk.BOTH, expand=True)
        email = tk.StringVar(value=get_app_setting(ACCOUNT_KEY))
        password = tk.StringVar()
        for row, label, variable, show in [
            (0, "メールアドレス", email, ""), (1, "パスワード", password, "*"),
        ]:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky=tk.W, pady=6)
            ttk.Entry(form, textvariable=variable, show=show, width=42).grid(row=row, column=1, pady=6)
        ttk.Label(form, text="パスワードはWindowsの資格情報マネージャーに保存します。\n接続先: purchases.digitalbillder.com").grid(row=2, column=0, columnspan=2, pady=12)

        def close():
            password.set("")
            dialog.destroy()

        def save():
            try:
                save_credentials(email.get(), password.get())
            except Exception as exc:
                messagebox.showerror("ログイン設定", credential_save_error(exc), parent=dialog)
                return
            close()
            self.status.set("ログイン情報を保存しました。新着確認を実行できます。")

        ttk.Button(form, text="保存", command=save).grid(row=3, column=1, sticky=tk.E)
        dialog.protocol("WM_DELETE_WINDOW", close)

    def close(self):
        if self.busy:
            self.withdraw()
            return
        self.destroy()
        if self.on_close:
            self.on_close()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        self.closing = True
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
            self.poll_id = None
