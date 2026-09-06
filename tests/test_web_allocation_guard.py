from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from invoice_manager import db
from invoice_manager.services.web_allocation_guard import (
    Control,
    ScreenContract,
    ScreenSnapshot,
    WebTransferBlocked,
    WebWriteGuard,
    structure_mismatch,
)


def make_contract(version: str = "contract-v1") -> ScreenContract:
    return ScreenContract(
        version=version,
        origin="https://example.test",
        screen="invoice-edit",
        controls=(
            Control("allocation-code", "allocations", "textbox", "工種コード", "text"),
            Control("allocation-amount", "allocations", "textbox", "税抜金額", "number"),
            Control("save", "footer", "button", "保存", "submit"),
        ),
    )


def make_snapshot(contract: ScreenContract, *, external_id: str = "invoice-1", **changes) -> ScreenSnapshot:
    values = {
        "origin": contract.origin,
        "screen": contract.screen,
        "external_id": external_id,
        "editable": True,
        "controls": contract.controls,
    }
    values.update(changes)
    return ScreenSnapshot(**values)


class WebAllocationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = db.DATA_DIR
        self.original_db_path = db.DB_PATH
        db.DATA_DIR = Path(self.temp_dir.name) / "data"
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()
        self.contract = make_contract()

    def tearDown(self) -> None:
        db.DATA_DIR = self.original_data_dir
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_structure_mismatch_rejects_missing_duplicate_and_renamed_controls(self) -> None:
        missing = replace(make_snapshot(self.contract), controls=self.contract.controls[:-1])
        duplicate = replace(
            make_snapshot(self.contract),
            controls=self.contract.controls + (self.contract.controls[0],),
        )
        renamed = replace(
            make_snapshot(self.contract),
            controls=(*self.contract.controls[:-1], replace(self.contract.controls[-1], label="次へ")),
        )

        self.assertIn("追加または削除", structure_mismatch(self.contract, missing))
        self.assertIn("一意", structure_mismatch(self.contract, duplicate))
        self.assertIn("名称", structure_mismatch(self.contract, renamed))

    def test_correct_screen_and_invoice_can_write_and_persist_ready_state(self) -> None:
        guard = WebWriteGuard(self.contract)
        writes: list[str] = []

        result = guard.run_guarded(
            "fill_field",
            "invoice-1",
            lambda: make_snapshot(self.contract),
            lambda: writes.append("saved") or "ok",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(writes, ["saved"])
        self.assertEqual(guard.status().state, "ready")
        self.assertEqual(WebWriteGuard(self.contract).status().state, "ready")

    def test_wrong_screen_freezes_and_wrong_invoice_freezes_before_write(self) -> None:
        guard = WebWriteGuard(self.contract)
        with self.assertRaises(WebTransferBlocked):
            guard.before_write(
                make_snapshot(self.contract, screen="invoice-list"),
                "invoice-1",
                "fill_field",
            )
        self.assertEqual(guard.status().state, "frozen")

        with db.get_connection() as conn:
            conn.execute("DELETE FROM web_allocation_guard")
        guard = WebWriteGuard(self.contract)
        with self.assertRaises(WebTransferBlocked):
            guard.before_write(make_snapshot(self.contract), "invoice-2", "fill_field")
        self.assertEqual(guard.status().state, "frozen")
        self.assertIn("請求書ID", guard.status().reason)

    def test_freeze_is_persisted_across_instances_and_reason_cannot_be_overwritten(self) -> None:
        guard = WebWriteGuard(self.contract)
        guard.freeze("最初の停止理由")
        guard.freeze("後からの理由")

        fresh_guard = WebWriteGuard(self.contract)
        self.assertEqual(fresh_guard.status().state, "frozen")
        self.assertEqual(fresh_guard.status().reason, "最初の停止理由")
        with self.assertRaises(WebTransferBlocked):
            fresh_guard.run_guarded(
                "fill_field",
                "invoice-1",
                lambda: make_snapshot(self.contract),
                lambda: self.fail("frozen guard must not write"),
            )

    def test_unverified_and_frozen_guards_never_call_write(self) -> None:
        unverified = WebWriteGuard(None)
        observe_calls: list[str] = []
        write_calls: list[str] = []
        with self.assertRaises(WebTransferBlocked):
            unverified.run_guarded(
                "fill_field",
                "invoice-1",
                lambda: observe_calls.append("observed") or make_snapshot(self.contract),
                lambda: write_calls.append("written"),
            )
        self.assertEqual(observe_calls, [])
        self.assertEqual(write_calls, [])

        frozen = WebWriteGuard(self.contract)
        frozen.freeze("手動停止")
        with self.assertRaises(WebTransferBlocked):
            frozen.run_guarded(
                "fill_field",
                "invoice-1",
                lambda: observe_calls.append("observed"),
                lambda: write_calls.append("written"),
            )
        self.assertEqual(observe_calls, [])
        self.assertEqual(write_calls, [])

    def test_next_and_forward_actions_are_blocked_and_freeze(self) -> None:
        for action in ("next", "forward"):
            with self.subTest(action=action):
                with db.get_connection() as conn:
                    conn.execute("DELETE FROM web_allocation_guard")
                guard = WebWriteGuard(self.contract)
                writes: list[str] = []
                with self.assertRaisesRegex(WebTransferBlocked, "次に回す"):
                    guard.run_guarded(
                        action,
                        "invoice-1",
                        lambda: make_snapshot(self.contract),
                        lambda: writes.append("written"),
                    )
                self.assertEqual(writes, [])
                self.assertEqual(guard.status().state, "frozen")

    def test_each_mutation_observes_fresh_screen_and_stops_on_second_change(self) -> None:
        guard = WebWriteGuard(self.contract)
        changed = replace(make_snapshot(self.contract), controls=self.contract.controls[:-1])
        observations = iter([make_snapshot(self.contract), changed])
        observed: list[ScreenSnapshot] = []
        writes: list[str] = []

        def observe() -> ScreenSnapshot:
            snapshot = next(observations)
            observed.append(snapshot)
            return snapshot

        guard.run_guarded("fill_field", "invoice-1", observe, lambda: writes.append("first"))
        with self.assertRaises(WebTransferBlocked):
            guard.run_guarded("save_edit", "invoice-1", observe, lambda: writes.append("second"))

        self.assertEqual(len(observed), 2)
        self.assertEqual(writes, ["first"])
        self.assertEqual(guard.status().state, "frozen")

    def test_observation_and_write_errors_freeze(self) -> None:
        observe_error = WebWriteGuard(self.contract)
        with self.assertRaises(WebTransferBlocked):
            observe_error.run_guarded(
                "fill_field",
                "invoice-1",
                lambda: (_ for _ in ()).throw(RuntimeError("cannot inspect")),
                lambda: self.fail("write must not run"),
            )
        self.assertEqual(observe_error.status().state, "frozen")
        self.assertIn("読み取れず", observe_error.status().reason)

        with db.get_connection() as conn:
            conn.execute("DELETE FROM web_allocation_guard")
        write_error = WebWriteGuard(self.contract)
        with self.assertRaises(WebTransferBlocked):
            write_error.run_guarded(
                "save_edit",
                "invoice-1",
                lambda: make_snapshot(self.contract),
                lambda: (_ for _ in ()).throw(RuntimeError("cannot save")),
            )
        self.assertEqual(write_error.status().state, "frozen")
        self.assertIn("操作途中", write_error.status().reason)


if __name__ == "__main__":
    unittest.main()
