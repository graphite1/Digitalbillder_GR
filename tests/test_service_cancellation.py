from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error, TimeoutError

from invoice_manager import db
from invoice_manager.services import digital_billder_download as download
from invoice_manager.services import digital_billder_sync as sync
from invoice_manager.services import historical_costs as history
from invoice_manager.services import web_invoice_reader as reader
from invoice_manager.services.operation_cancellation import (
    CancellationToken, OperationCancelled, cancellation_scope, current_token, check_cancelled,
)


class ServiceCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(db, "DATA_DIR", self.root)
        self.path_patch = patch.object(db, "DB_PATH", self.root / "unused.db")
        self.data_patch.start()
        self.path_patch.start()
        self.token = CancellationToken()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def test_fetch_cancelled_before_remember_closes_session_and_releases_lock(self) -> None:
        closed = []

        @contextmanager
        def session(_progress):
            try:
                yield object()
            finally:
                closed.append(True)

        def fetch(*_args):
            self.token.request()
            return None

        with (cancellation_scope(self.token), patch.object(sync, "export_session", side_effect=session),
              patch.object(sync, "download_csv", side_effect=fetch), patch.object(sync, "remember_candidates") as remember):
            with self.assertRaises(OperationCancelled):
                sync.fetch_candidates()
        remember.assert_not_called()
        self.assertEqual(closed, [True])
        self.assertFalse(sync.SYNC_LOCK.locked())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_fetch_commit_rejects_late_cancel_and_reports_completion(self) -> None:
        def remember(_rows):
            self.assertFalse(self.token.request())

        with (cancellation_scope(self.token), patch.object(sync, "export_session", return_value=nullcontext(object())),
              patch.object(sync, "download_csv", return_value=None), patch.object(sync, "remember_candidates", side_effect=remember),
              patch.object(sync, "list_candidates", return_value=[])):
            self.assertEqual(sync.fetch_candidates(), 0)
        self.assertFalse(sync.SYNC_LOCK.locked())

    def import_patches(self):
        from contextlib import ExitStack

        stack = ExitStack()
        row = SimpleNamespace(external_id="synthetic", raw_data={"ID": "synthetic"})
        stack.enter_context(patch.object(sync, "export_session", return_value=nullcontext(object())))
        stack.enter_context(patch.object(sync, "download_csv", return_value=self.root / "mock.csv"))
        stack.enter_context(patch.object(sync, "validate_rows", return_value=[row]))
        stack.enter_context(patch.object(sync, "list_candidates", return_value=[row]))
        stack.enter_context(patch.object(sync, "write_selected_csv"))
        return stack

    def test_import_cancelled_after_download_does_not_save_pdf_or_import(self) -> None:
        def fetch(*_args):
            self.token.request()
            return self.root / "mock.zip"

        with (cancellation_scope(self.token), self.import_patches(),
              patch.object(sync, "download_selected_zip", side_effect=fetch), patch.object(sync, "preview_import") as preview,
              patch.object(sync, "execute_import") as execute):
            with self.assertRaises(OperationCancelled):
                sync.import_selected({"synthetic"})
        preview.assert_not_called()
        execute.assert_not_called()
        self.assertFalse(sync.SYNC_LOCK.locked())

    def test_import_cancel_at_last_progress_prevents_commit(self) -> None:
        preview = SimpleNamespace(zip_index=SimpleNamespace(pdf_by_id={"synthetic": "fake.pdf"}), errors=[], new_count=1)

        def progress(text):
            if "台帳に登録" in text:
                self.token.request()

        with (cancellation_scope(self.token), self.import_patches(),
              patch.object(sync, "download_selected_zip", return_value=self.root / "mock.zip"),
              patch.object(sync, "preview_import", return_value=preview), patch.object(sync, "execute_import") as execute):
            with self.assertRaises(OperationCancelled):
                sync.import_selected({"synthetic"}, progress)
        execute.assert_not_called()
        self.assertFalse(sync.SYNC_LOCK.locked())

    def test_import_commit_cannot_be_cancelled_halfway(self) -> None:
        preview = SimpleNamespace(zip_index=SimpleNamespace(pdf_by_id={"synthetic": "fake.pdf"}), errors=[], new_count=1)
        result = object()

        def execute(*_args, **_kwargs):
            self.assertFalse(self.token.request())
            check_cancelled()
            return result

        connection = MagicMock()
        with (cancellation_scope(self.token), self.import_patches(),
              patch.object(sync, "download_selected_zip", return_value=self.root / "mock.zip"),
              patch.object(sync, "preview_import", return_value=preview), patch.object(sync, "execute_import", side_effect=execute),
              patch.object(db, "atomic_transaction", return_value=nullcontext()),
              patch.object(db, "get_connection", return_value=nullcontext(connection))):
            self.assertIs(sync.import_selected({"synthetic"}), result)
        connection.executemany.assert_called_once()
        self.assertFalse(sync.SYNC_LOCK.locked())

    def test_archive_cancel_does_not_replace_availability_and_releases_lock(self) -> None:
        def fetch(*_args):
            self.token.request()
            return None

        with (cancellation_scope(self.token), patch.object(history, "load_active_archived_snapshots", return_value={}),
              patch.object(reader, "export_session", return_value=nullcontext(object())),
              patch.object(reader, "download_csv", side_effect=fetch), patch.object(history, "replace_active_archived_snapshots") as replace):
            with self.assertRaises(OperationCancelled):
                reader.sync_archived_history()
        replace.assert_not_called()
        self.assertFalse(reader._sync_lock.locked())

    def test_archive_commit_rejects_late_cancel_and_returns_summary(self) -> None:
        def replace(_snapshots):
            self.assertFalse(self.token.request())

        with (cancellation_scope(self.token), patch.object(history, "load_active_archived_snapshots", return_value={}),
              patch.object(reader, "export_session", return_value=nullcontext(object())),
              patch.object(reader, "download_csv", return_value=None),
              patch.object(history, "replace_active_archived_snapshots", side_effect=replace)):
            self.assertIn("保管済み0件", reader.sync_archived_history())
        self.assertFalse(reader._sync_lock.locked())

    def test_read_for_plan_checks_cancel_before_return(self) -> None:
        plan = SimpleNamespace(external_id="id", project_code="P", vendor_name="V", invoice_date="2026-08-01", invoice_amount=110)

        def read(*_args):
            self.token.request()
            return object()

        with (cancellation_scope(self.token), patch.object(reader, "export_session", return_value=nullcontext(object())),
              patch.object(reader, "read_invoice_page", side_effect=read), patch.object(reader, "verify_identity")):
            with self.assertRaises(OperationCancelled):
                reader.read_for_plan(plan)

    def test_archive_threads_inherit_token_and_close_their_own_sessions(self) -> None:
        events = []
        events_lock = threading.Lock()

        @contextmanager
        def session(_state):
            self.assertIs(current_token(), self.token)
            identifier = threading.get_ident()
            with events_lock:
                events.append(("open", identifier))
            try:
                yield identifier
            finally:
                with events_lock:
                    events.append(("close", identifier))

        def read(page, _identifier):
            self.assertEqual(page, threading.get_ident())
            self.token.request()
            check_cancelled()

        rows = [SimpleNamespace(external_id=str(index)) for index in range(6)]
        with (cancellation_scope(self.token), patch.object(reader, "authenticated_reader_session", side_effect=session),
              patch.object(reader, "read_invoice_page", side_effect=read)):
            with self.assertRaises(OperationCancelled):
                reader._read_archive_batches(rows, {}, lambda _text: None)
        self.assertTrue(events)
        self.assertEqual(sorted(identifier for action, identifier in events if action == "open"),
                         sorted(identifier for action, identifier in events if action == "close"))

    def test_uncancelled_archive_reads_each_invoice_once(self) -> None:
        rows = [SimpleNamespace(external_id=str(index)) for index in range(6)]
        with (cancellation_scope(self.token), patch.object(reader, "authenticated_reader_session", side_effect=lambda _state: nullcontext(object())),
              patch.object(reader, "read_invoice_page", side_effect=lambda _page, identifier: identifier) as read):
            self.assertEqual(sorted(reader._read_archive_batches(rows, {}, lambda _text: None)), [str(index) for index in range(6)])
        self.assertEqual(read.call_count, 6)

    def test_export_closes_browser_and_preserves_cancelled_exception_on_navigation_error(self) -> None:
        browser = MagicMock()
        page = browser.new_context.return_value.new_page.return_value

        def navigate(*_args, **_kwargs):
            self.token.request()
            raise Error("synthetic browser error")

        page.goto.side_effect = navigate
        with (cancellation_scope(self.token), patch.object(download, "load_credentials", return_value=("synthetic", "synthetic")),
              patch("playwright.sync_api.sync_playwright", return_value=nullcontext(object())),
              patch.object(download, "launch_browser", return_value=browser)):
            with self.assertRaises(OperationCancelled):
                with download.export_session(lambda _text: None):
                    self.fail("cancelled session must not yield")
        browser.close.assert_called_once()

    def test_reader_session_closes_browser_on_cancel(self) -> None:
        browser = MagicMock()
        with (cancellation_scope(self.token), patch("playwright.sync_api.sync_playwright", return_value=nullcontext(object())),
              patch.object(download, "launch_browser", return_value=browser)):
            with self.assertRaises(OperationCancelled):
                with download.authenticated_reader_session({}):
                    self.token.request()
                    check_cancelled()
        browser.close.assert_called_once()

    def test_cancel_before_download_save_does_not_write_destination(self) -> None:
        page = MagicMock()
        page.get_by_text.return_value.inner_text.return_value = "検索結果: 1 件"
        pending = MagicMock()
        page.expect_download.return_value.__enter__.return_value = pending
        page.expect_download.return_value.__exit__.side_effect = lambda *_args: (self.token.request() and False)
        with cancellation_scope(self.token), patch.object(download, "_open_export_dialog", return_value=MagicMock()):
            with self.assertRaises(OperationCancelled):
                download.download_csv(page, self.root / "unused.csv")
        pending.value.save_as.assert_not_called()
        self.assertFalse((self.root / "unused.csv").exists())

    def test_network_idle_without_token_keeps_original_single_wait(self) -> None:
        page = MagicMock()
        download.wait_for_network_idle(page)
        page.wait_for_load_state.assert_called_once_with("networkidle", timeout=30_000)

    def test_network_idle_observes_cancellation_after_short_wait(self) -> None:
        page = MagicMock()

        def waiting(*_args, **_kwargs):
            self.token.request()
            raise TimeoutError("still waiting")

        page.wait_for_load_state.side_effect = waiting
        with cancellation_scope(self.token), self.assertRaises(OperationCancelled):
            download.wait_for_network_idle(page)
        self.assertEqual(page.wait_for_load_state.call_count, 1)
        self.assertLessEqual(page.wait_for_load_state.call_args.kwargs["timeout"], 250)

    def test_network_idle_keeps_overall_deadline_without_extra_browser_requests(self) -> None:
        page = MagicMock()
        page.wait_for_load_state.side_effect = TimeoutError("still waiting")
        with (cancellation_scope(self.token), patch.object(download.time, "monotonic", side_effect=[0, 0, 30.001]),
              self.assertRaises(TimeoutError)):
            download.wait_for_network_idle(page)
        page.wait_for_load_state.assert_called_once_with("networkidle", timeout=250)


if __name__ == "__main__":
    unittest.main()
