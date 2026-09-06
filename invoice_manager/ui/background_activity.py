"""Non-modal, root-local status for work performed by background workers.

Create and update activities on the Tk GUI thread (for example, in a queue
poller). Workers must not call this module or other Tk APIs directly.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from invoice_manager.services.operation_cancellation import CancellationToken


_REGISTRY_ATTRIBUTE = "_background_activity_registry"
_COMPLETED_LIMIT = 20


def _registry(widget: tk.Misc) -> list[BackgroundActivity]:
    root = widget._root()
    if not hasattr(root, _REGISTRY_ATTRIBUTE):
        setattr(root, _REGISTRY_ATTRIBUTE, [])
    return getattr(root, _REGISTRY_ATTRIBUTE)


def _remember(activity: BackgroundActivity) -> None:
    registry = _registry(activity.owner)
    if activity in registry:
        registry.remove(activity)
    registry.append(activity)
    completed = [item for item in registry if not item.running]
    for item in completed[:-_COMPLETED_LIMIT]:
        registry.remove(item)


class BackgroundActivity:
    """One operation whose current state can be shown in several windows."""

    def __init__(self, owner: tk.Misc, title: str):
        self.owner = owner
        self.title = title
        self.running = False
        self.message = "待機中"
        self.failed = False
        self.cancelled = False
        self.cancellation: CancellationToken | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def start(self, message: str, *, cancellation: CancellationToken | None = None) -> None:
        self.running = True
        self.failed = False
        self.cancelled = False
        self.cancellation = cancellation
        self.message = message
        self.started_at = time.monotonic()
        self.finished_at = None
        _remember(self)

    def update(self, message: str) -> None:
        if self.running and not self.cancel_requested:
            self.message = message

    @property
    def cancel_requested(self) -> bool:
        return self.cancellation is not None and self.cancellation.requested

    def request_cancel(self) -> None:
        if self.running and self.cancellation is not None and self.cancellation.request():
            self.message = "中断を要求しました。実行中の通信の終了・後片付けを待っています。"

    def finish(self, message: str, failed: bool = False, *, cancelled: bool = False) -> None:
        self.running = False
        self.message = message
        self.failed = failed
        self.cancelled = cancelled
        self.finished_at = time.monotonic()
        _remember(self)

    @property
    def elapsed_seconds(self) -> int:
        if self.started_at is None:
            return 0
        endpoint = time.monotonic() if self.running else self.finished_at
        return max(0, int((endpoint if endpoint is not None else self.started_at) - self.started_at))


def running_activities(widget: tk.Misc) -> tuple[BackgroundActivity, ...]:
    """Return all running activities belonging to this widget's Tk root."""
    return tuple(item for item in _registry(widget) if item.running)


def has_running_descendants(widget: tk.Misc) -> bool:
    """Include work owned by this widget itself or any of its child widgets."""
    for activity in running_activities(widget):
        current = activity.owner
        while current is not None:
            if current is widget:
                return True
            current = getattr(current, "master", None)
    return False


class ActivityPanel(ttk.Frame):
    """Compact status, elapsed time and optional navigation; never grabs input."""

    def __init__(self, parent: tk.Misc, activity: BackgroundActivity | None = None):
        super().__init__(parent, padding=(6, 4))
        self.activity = activity
        self._selected = activity
        self._choices: list[BackgroundActivity] = []
        self._after_id: str | None = None
        self._animating = False
        self._closed = False
        self.columnconfigure(0, weight=1)

        self.selector = ttk.Combobox(self, state="readonly")
        self.selector.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 3))
        self.selector.bind("<<ComboboxSelected>>", self._select_activity)
        self.status_label = ttk.Label(self, text="待機中")
        self.status_label.grid(row=1, column=0, sticky="w")
        self.elapsed_label = ttk.Label(self)
        self.elapsed_label.grid(row=1, column=1, columnspan=2, sticky="e", padx=(8, 0))
        self.message_label = ttk.Label(self, wraplength=640, justify="left")
        self.message_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=3)
        self.progress = ttk.Progressbar(self, mode="indeterminate", length=180)
        self.progress.grid(row=3, column=0, sticky="ew", padx=(0, 8))
        self.open_button = ttk.Button(self, text="処理画面へ戻る", command=self._show_owner)
        self.open_button.grid(row=3, column=1, sticky="e")
        self.cancel_button = ttk.Button(self, text="中断", command=self._cancel_selected)
        self.cancel_button.grid(row=3, column=2, sticky="e", padx=(6, 0))
        self.bind("<Configure>", self._on_resize, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._tick()

    def _select_activity(self, _event=None) -> None:
        index = self.selector.current()
        if 0 <= index < len(self._choices):
            self._selected = self._choices[index]
            self._render()

    def _cancel_selected(self) -> None:
        if self._selected is not None:
            self._selected.request_cancel()
            self._render()

    @staticmethod
    def _state(item: BackgroundActivity) -> str:
        if item.running:
            return "中断待ち" if item.cancel_requested else "実行中"
        if item.cancelled:
            return "中断済み"
        return "失敗" if item.failed else "完了" if item.finished_at is not None else "待機中"

    def _show_owner(self) -> None:
        if self._selected is None:
            return
        try:
            window = self._selected.owner.winfo_toplevel()
            if window.winfo_exists():
                # A transient child cannot become visible while its parent is
                # withdrawn. Restore the actual window chain from outside in;
                # keep the application's deliberately hidden Tk root hidden.
                ancestors = []
                current = window
                while current is not None:
                    if isinstance(current, tk.Toplevel):
                        ancestors.append(current)
                    current = getattr(current, "master", None)
                for ancestor in reversed(ancestors):
                    ancestor.deiconify()
                if ancestors:
                    window.lift()
                    window.focus_set()
        except tk.TclError:
            # A completed operation's window may already have been closed.
            pass

    def _on_resize(self, event) -> None:
        if event.widget is self:
            self.message_label.configure(wraplength=max(100, event.width - 20))

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self._closed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._animating = False

    def _tick(self) -> None:
        self._after_id = None
        if self._closed:
            return
        self._render()
        self._after_id = self.after(500, self._tick)

    def _render(self) -> None:
        if self.activity is None:
            registry = _registry(self)
            running = [item for item in registry if item.running]
            completed = [item for item in reversed(registry) if not item.running]
            self._choices = running + completed[:1]
            if self._selected not in self._choices or (
                running and self._selected is not None and not self._selected.running
            ):
                self._selected = self._choices[0] if self._choices else None
            self.selector.configure(values=[
                f"{self._state(item)}：{item.title}"
                for item in self._choices
            ])
            if len(self._choices) > 1:
                self.selector.grid()
                self.selector.current(self._choices.index(self._selected))
            else:
                self.selector.grid_remove()
        else:
            self.selector.grid_remove()

        item = self._selected
        running = item is not None and item.running
        failed = item is not None and item.failed
        if item is None:
            self.status_label.configure(text="実行中の処理はありません", foreground="")
            self.message_label.configure(text="", foreground="")
            self.elapsed_label.configure(text="")
        else:
            state = self._state(item)
            self.status_label.configure(
                text=f"{state}：{item.title}", foreground="#b00020" if failed else ""
            )
            self.message_label.configure(text=item.message, foreground="#b00020" if failed else "")
            minutes, seconds = divmod(item.elapsed_seconds, 60)
            self.elapsed_label.configure(text=f"経過 {minutes:02d}:{seconds:02d}")

        owner_exists = False
        if item is not None:
            try:
                owner_exists = bool(item.owner.winfo_exists())
            except tk.TclError:
                pass
        self.open_button.configure(state="normal" if owner_exists else "disabled")
        cancellable = running and item.cancellation is not None and item.cancellation.can_cancel
        saving = running and item.cancellation is not None and not item.cancellation.can_cancel and not item.cancel_requested
        self.cancel_button.configure(
            state="normal" if cancellable else "disabled",
            text="中断待ち" if running and item.cancel_requested else "保存中" if saving else "中断",
        )
        if running:
            self.progress.grid()
            # Use our cancellable refresh timer rather than ttk's separate Tcl
            # timer, which can outlive a parent window during root destruction.
            self.progress.step(10)
            self._animating = True
        else:
            self._animating = False
            self.progress.grid_remove()
