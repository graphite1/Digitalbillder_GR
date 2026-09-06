from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from invoice_manager.version import APP_VERSION, RELEASE_SEQUENCE


INSTALL_ROOT_ENV = "DIGITALBUILDER_INSTALL_ROOT"


class UpdateWindow(tk.Toplevel):
    """Check and stage signed releases without closing working windows."""

    def __init__(
        self,
        master,
        *,
        check_update: Callable | None = None,
        stage_release: Callable | None = None,
        readiness_check: Callable[[tk.Misc], tuple[bool, str]] | None = None,
        request_restart: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("アプリの更新")
        self.geometry("620x440")
        self.minsize(520, 380)
        self.transient(master)
        self._check_update = check_update or self._default_check_update
        self._stage_release = stage_release or self._default_stage_release
        self._readiness_check = readiness_check
        self._request_restart = request_restart
        self._manifest = None
        self._staged = None
        self._busy = False
        self._closing = False
        self._poll_id = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.current_version_var = tk.StringVar(value=APP_VERSION)
        self.latest_version_var = tk.StringVar(value="未確認")
        self.status_var = tk.StringVar(value="「更新を確認」を押すと最新版を確認します。")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close_window)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="アプリの更新", font=("TkDefaultFont", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 14)
        )
        ttk.Label(frame, text="現在のバージョン").grid(row=1, column=0, sticky=tk.W, pady=3)
        ttk.Label(frame, textvariable=self.current_version_var).grid(row=1, column=1, sticky=tk.W, padx=(12, 0))
        ttk.Label(frame, text="利用できる最新版").grid(row=2, column=0, sticky=tk.W, pady=3)
        ttk.Label(frame, textvariable=self.latest_version_var).grid(row=2, column=1, sticky=tk.W, padx=(12, 0))

        ttk.Label(frame, text="変更点").grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(14, 5))
        notes_frame = ttk.Frame(frame)
        notes_frame.grid(row=4, column=0, columnspan=2, sticky="nsew")
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(0, weight=1)
        self.notes = tk.Text(notes_frame, height=10, wrap=tk.WORD, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(notes_frame, orient=tk.VERTICAL, command=self.notes.yview)
        self.notes.configure(yscrollcommand=scrollbar.set)
        self.notes.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        ttk.Label(frame, textvariable=self.status_var, wraplength=570).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(12, 8)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.check_button = ttk.Button(buttons, text="更新を確認", command=self.check)
        self.check_button.pack(side=tk.LEFT)
        self.install_button = ttk.Button(
            buttons, text="ダウンロードして再起動", command=self.download_and_restart,
            state=tk.DISABLED,
        )
        self.install_button.pack(side=tk.LEFT, padx=(8, 0))
        self.restart_button = ttk.Button(
            buttons, text="再起動して適用", command=self.restart_when_ready,
            state=tk.DISABLED,
        )
        self.restart_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="閉じる", command=self.close_window).pack(side=tk.RIGHT)

    def _set_notes(self, text: str) -> None:
        self.notes.configure(state=tk.NORMAL)
        self.notes.delete("1.0", tk.END)
        self.notes.insert("1.0", text)
        self.notes.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = busy
        self.status_var.set(message)
        self.check_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        install_state = tk.NORMAL if not busy and self._manifest is not None and self._staged is None else tk.DISABLED
        self.install_button.configure(state=install_state)
        self.restart_button.configure(state=tk.NORMAL if not busy and self._staged is not None else tk.DISABLED)

    def _start_worker(self, operation: Callable[[], object], message: str) -> None:
        self._set_busy(True, message)

        def run() -> None:
            try:
                result = operation()
            except Exception as exc:
                self._events.put(("error", exc))
            else:
                self._events.put(("result", result))

        threading.Thread(target=run, name="app-update", daemon=True).start()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if not self._closing and self._poll_id is None:
            self._poll_id = self.after(100, self._poll_events)

    def check(self) -> None:
        if self._busy:
            return
        self._manifest = None
        self._staged = None
        self.latest_version_var.set("確認中")
        self._set_notes("")
        self._start_worker(self._check_update, "最新版を確認しています…")

    def _finish_check(self, manifest) -> None:
        self._manifest = manifest
        if manifest is None:
            self.latest_version_var.set(APP_VERSION)
            self._set_notes("現在のバージョンが最新版です。")
            self._set_busy(False, "利用できる更新はありません。")
            return
        version = str(getattr(manifest, "version", "")) or "不明"
        notes = str(
            getattr(manifest, "release_notes", None)
            or getattr(manifest, "notes", None)
            or getattr(manifest, "changelog", None)
            or "変更点の記載はありません。"
        )
        self.latest_version_var.set(version)
        self._set_notes(notes)
        self._set_busy(False, "更新を利用できます。内容を確認してからダウンロードしてください。")

    def download_and_restart(self) -> None:
        if self._busy or self._manifest is None:
            return
        self._start_worker(
            lambda: self._stage_release(self._manifest, self._queue_progress),
            "更新をダウンロードして検証しています…",
        )

    def _queue_progress(self, message: str) -> None:
        # A worker may call this. Tk is updated only by the polling method.
        self._events.put(("progress", str(message)))

    def _poll_events(self) -> None:
        self._poll_id = None
        if self._closing:
            return
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                self._schedule_poll()
                return
            if kind == "progress":
                self.status_var.set(str(value))
                continue
            if kind == "error":
                self._set_busy(False, self._error_message(value))
                return
            if self._manifest is None:
                self._finish_check(value)
            else:
                self._finish_stage(value)
            return

    @staticmethod
    def _error_message(error: object) -> str:
        try:
            from updater.errors import UpdateError
        except ImportError:
            UpdateError = ()
        if isinstance(error, UpdateError):
            return f"更新処理に失敗しました: {error}"
        return "更新処理に失敗しました。通信状態を確認して、時間をおいてやり直してください。"

    def _finish_stage(self, staged) -> None:
        self._staged = staged
        self._set_busy(False, "更新の準備ができました。再起動時に安全確認して適用します。")
        self.restart_when_ready()

    def restart_when_ready(self) -> None:
        if self._busy or self._staged is None or self._request_restart is None:
            return
        ready, reason = self._readiness_check(self) if self._readiness_check else (True, "")
        if not ready:
            self.status_var.set(reason or "他の作業画面を閉じてから再起動してください。")
            self.restart_button.configure(state=tk.NORMAL)
            return
        self._closing = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.destroy()
        self._request_restart()

    def close_window(self) -> None:
        if self._busy:
            messagebox.showinfo("更新処理中", "更新の確認またはダウンロードが完了してから閉じてください。", parent=self)
            return
        self._closing = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.destroy()

    @staticmethod
    def _install_root() -> Path:
        configured = os.environ.get(INSTALL_ROOT_ENV, "").strip()
        return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]

    @staticmethod
    def _default_check_update():
        from updater.config import DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS
        from updater.core import check_for_update
        from updater.runtime import get_runtime_fingerprint

        return check_for_update(
            DEFAULT_UPDATE_BASE_URL,
            TRUSTED_PUBLIC_KEYS,
            current_sequence=RELEASE_SEQUENCE,
            runtime_fingerprint=get_runtime_fingerprint(),
        )

    @classmethod
    def _default_stage_release(cls, manifest, progress):
        from updater.core import stage_update

        return stage_update(manifest, cls._install_root(), progress=progress)


__all__ = ["INSTALL_ROOT_ENV", "UpdateWindow"]
