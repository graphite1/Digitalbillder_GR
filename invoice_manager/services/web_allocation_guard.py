"""Fail-closed gate for the future Web writer; no browser mutations live here.

The reviewed editable-screen contract cannot be populated until a writable test
invoice is available. A persisted freeze has no automatic or UI reset path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

from invoice_manager import db


@dataclass(frozen=True)
class Control:
    key: str
    section: str
    role: str
    label: str
    input_type: str = ""
    count: int = 1


@dataclass(frozen=True)
class ScreenContract:
    version: str
    origin: str
    screen: str
    controls: tuple[Control, ...]
    repeated_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ScreenSnapshot:
    origin: str
    screen: str
    external_id: str
    editable: bool
    controls: tuple[Control, ...]


@dataclass(frozen=True)
class GuardStatus:
    state: str
    reason: str
    contract_version: str = ""


# Populate only from a reviewed editable invoice, never from a live auto-baseline.
APPROVED_CONTRACT: ScreenContract | None = None
ALLOWED_ACTIONS = frozenset({"fill_field", "add_allocation_row", "save_edit"})
T = TypeVar("T")


class WebTransferBlocked(RuntimeError):
    pass


def structure_mismatch(expected: ScreenContract, actual: ScreenSnapshot) -> str | None:
    if actual.origin != expected.origin or actual.screen != expected.screen:
        return "接続先または編集画面の種類が想定と異なります。"
    keys = [control.key for control in actual.controls]
    expected_keys = [control.key for control in expected.controls]
    if len(keys) != len(set(keys)) or len(expected_keys) != len(set(expected_keys)):
        return "入力欄・操作ボタンを一意に識別できません。"
    controls = {control.key: control for control in actual.controls}
    if set(controls) != set(expected_keys):
        return "編集画面の入力欄・操作ボタンが追加または削除されています。"
    for wanted in expected.controls:
        found = controls[wanted.key]
        if (found.section, found.role, found.label, found.input_type) != (
            wanted.section, wanted.role, wanted.label, wanted.input_type
        ):
            return "入力欄・操作ボタンの位置付け、名称、種類が変わっています。"
        if found.count < 1 or (wanted.key not in expected.repeated_keys and found.count != wanted.count):
            return "入力欄・操作ボタンの件数が想定と異なり、操作先を特定できません。"
    return None


class WebWriteGuard:
    def __init__(self, contract: ScreenContract | None = APPROVED_CONTRACT):
        self.contract = contract

    def status(self) -> GuardStatus:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM web_allocation_guard WHERE id=1").fetchone()
        if row and row["state"] == "frozen":
            return GuardStatus("frozen", row["reason"], row["contract_version"])
        if self.contract is None:
            return GuardStatus("unverified", "編集可能な試験請求での画面構造・保存動作の検証待ちです。")
        if not row:
            return GuardStatus("unverified", "確認済みの画面構造との照合が必要です。")
        return GuardStatus(row["state"], row["reason"], row["contract_version"])

    def freeze(self, reason: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        version = self.contract.version if self.contract else ""
        with db.get_connection() as conn:
            conn.execute("""
                INSERT INTO web_allocation_guard(id, state, contract_version, reason, checked_at)
                VALUES(1, 'frozen', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state='frozen',
                    reason=CASE WHEN web_allocation_guard.state='frozen' THEN web_allocation_guard.reason ELSE excluded.reason END,
                    checked_at=CASE WHEN web_allocation_guard.state='frozen' THEN web_allocation_guard.checked_at ELSE excluded.checked_at END
            """, (version, reason, now))

    def before_write(self, actual: ScreenSnapshot, external_id: str, action: str) -> None:
        status = self.status()
        if status.state == "frozen":
            raise WebTransferBlocked(f"Web転記は凍結中です。{status.reason}")
        if action not in ALLOWED_ACTIONS:
            self.freeze("許可されていない操作が要求されたため停止しました。")
            raise WebTransferBlocked("「次に回す」など、編集保存以外の操作は実行しません。")
        if self.contract is None:
            raise WebTransferBlocked(status.reason)
        if status.contract_version and status.contract_version != self.contract.version:
            self.freeze("確認済みの画面構造のバージョンが変わりました。再確認が必要です。")
            raise WebTransferBlocked(self.status().reason)
        mismatch = structure_mismatch(self.contract, actual)
        if mismatch:
            self.freeze(mismatch)
            raise WebTransferBlocked(f"画面構造の変更を検知してWeb転記を凍結しました。{mismatch}")
        if not external_id or actual.external_id != external_id:
            self.freeze("操作対象の請求書IDが一致しません。")
            raise WebTransferBlocked("請求書IDが一致しないため停止しました。")
        if not actual.editable:
            raise WebTransferBlocked("この請求は編集できない状態です。")
        with db.get_connection() as conn:
            conn.execute("""
                INSERT INTO web_allocation_guard(id, state, contract_version, reason, checked_at)
                VALUES(1, 'ready', ?, '', ?)
                ON CONFLICT(id) DO UPDATE SET state='ready', checked_at=excluded.checked_at
                WHERE web_allocation_guard.state != 'frozen'
            """, (self.contract.version, datetime.now().isoformat(timespec="seconds")))
        if self.status().state == "frozen":
            raise WebTransferBlocked("Web転記は凍結中です。")

    def run_guarded(
        self, action: str, external_id: str,
        observe: Callable[[], ScreenSnapshot], write: Callable[[], T],
    ) -> T:
        # Observe again immediately before every individual mutation, including save.
        if self.status().state == "frozen":
            raise WebTransferBlocked(f"Web転記は凍結中です。{self.status().reason}")
        if self.contract is None:
            raise WebTransferBlocked(self.status().reason)
        try:
            actual = observe()
        except Exception:
            self.freeze("画面構造を読み取れず、操作先の確認ができませんでした。")
            raise WebTransferBlocked(self.status().reason) from None
        self.before_write(actual, external_id, action)
        try:
            return write()
        except Exception:
            self.freeze("操作途中で結果を確認できなくなりました。Webの状態を確認するまで再実行できません。")
            raise WebTransferBlocked(self.status().reason) from None
