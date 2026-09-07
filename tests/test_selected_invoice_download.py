from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from invoice_manager.services import selected_invoice_download as selected
from invoice_manager.services.operation_cancellation import (
    CancellationToken,
    OperationCancelled,
    cancellation_scope,
)
from invoice_manager.services.csv_reader import REQUIRED_COLUMNS


def row(identifier: str, amount: int = 11000):
    raw = dict.fromkeys(REQUIRED_COLUMNS, "")
    raw.update({"ID": identifier, "工事名": "テスト工事", "工事コード": "TEST",
                "取引先名": "テスト取引先", "請求日": "2026-08-31",
                "請求金額(税込)": str(amount)})
    return SimpleNamespace(external_id=identifier, raw_data=raw)


class SelectedInvoiceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_csv(self, path: Path, rows) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows([item.raw_data for item in rows])

    def make_zip(self, name: str, entries: list[tuple[str, bytes]]) -> Path:
        path = self.root / name
        with ZipFile(path, "w") as archive:
            for archive_name, content in entries:
                archive.writestr(archive_name, content)
        return path

    def fake_page(self, page_sizes, *, delayed_sizes=None, header_delay=0):
        class Box:
            def __init__(self, page, index):
                self.page, self.index = page, index

            def locator(self, selector):
                self.page.assertEqual(selector, "..")
                return self

            def click(self):
                states = self.page.current_states()
                if self.index == 0:
                    self.page.header_clicks[self.page.page_index] += 1
                    self.page.header_pending[self.page.page_index] = not states[0]
                    self.page.header_polls[self.page.page_index] = 0
                else:
                    self.page.row_clicks[self.page.page_index] += 1
                    states[self.index] = not states[self.index]
                    states[0] = all(states[1:])

            def is_checked(self):
                return self.page.states[self.page.page_index][self.index]

        class Grid:
            def __init__(self, page):
                self.page = page

            def count(self):
                return 1

            def inner_text(self):
                return f"page-{self.page.page_index}"

        class Boxes:
            def __init__(self, page):
                self.page = page

            def evaluate_all(self, _script):
                self.page.poll_page()
                return list(self.page.current_states())

            def nth(self, index):
                return Box(self.page, index)

        class Next:
            def __init__(self, page):
                self.page = page

            def is_enabled(self):
                return self.page.page_index + 1 < len(self.page.states)

            def click(self):
                self.page.page_index += 1
                self.page.url = selected.APPLICATIONS_URL + f"?page={self.page.page_index + 1}"

        class Page:
            def __init__(self):
                self.page_index = 0
                self.states = [[False] * size for size in page_sizes]
                self.original_sizes = list(page_sizes)
                self.delayed_sizes = dict(delayed_sizes or {})
                self.page_polls = [0] * len(page_sizes)
                self.header_clicks = [0] * len(page_sizes)
                self.row_clicks = [0] * len(page_sizes)
                self.header_pending = [None] * len(page_sizes)
                self.header_polls = [0] * len(page_sizes)
                self.header_delay = header_delay
                self.url = selected.APPLICATIONS_URL

            def current_states(self):
                return self.states[self.page_index]

            def poll_page(self):
                index = self.page_index
                self.page_polls[index] += 1
                delayed = self.delayed_sizes.get(index)
                if delayed and self.page_polls[index] >= delayed[0] and len(self.states[index]) != delayed[1]:
                    self.states[index] = [False] * delayed[1]
                pending = self.header_pending[index]
                if pending is not None:
                    self.header_polls[index] += 1
                    if self.header_polls[index] > self.header_delay:
                        self.states[index][1:] = [pending] * (len(self.states[index]) - 1)
                        self.states[index][0] = pending
                        self.header_pending[index] = None

            def assertEqual(self, actual, expected):
                if actual != expected:
                    raise AssertionError((actual, expected))

            def locator(self, selector):
                if selector == selected.GRID:
                    return Grid(self)
                if selector == selected.BOXES:
                    return Boxes(self)
                raise AssertionError(selector)

            def get_by_role(self, role, name=None, exact=False):
                self.assertEqual((role, name, exact), ("button", ">", True))
                return Next(self)

            def wait_for_timeout(self, _milliseconds):
                return None

        return Page()

    def test_checkbox_selector_excludes_hidden_switches(self):
        self.assertEqual(selected.BOXES, '.chakra-table__container input.chakra-checkbox__input[type=checkbox]')

    def run_two_page_download(self, selected_ids):
        rows = [row("first")] + [row(f"filler-{index}") for index in range(1, 50)] + [row("second")]
        page = self.fake_page([51, 2])
        destination = self.root / "selected.zip"
        downloads = []

        def download_page(_page, part, expected):
            downloads.append([item.external_id for item in expected])
            with ZipFile(part, "w") as archive:
                for item in expected:
                    archive.writestr(f"invoices/{item.external_id}/invoice.pdf", item.external_id.encode())

        with patch.object(selected, "_close_export"), patch.object(selected, "_download_page", side_effect=download_page), \
             patch.object(selected, "wait_for_network_idle"):
            selected.download_selected_zip(page, destination, rows, selected_ids)
        return downloads, destination

    def test_two_page_selection_uses_exact_csv_order_and_accepts_query_url(self):
        downloads, destination = self.run_two_page_download({"first", "second"})
        self.assertEqual(downloads, [["first"], ["second"]])
        with ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"invoices/first/invoice.pdf", "invoices/second/invoice.pdf"})

    def test_all_eighty_one_rows_use_one_header_selection_per_page_and_clear_before_next(self):
        rows = [row(f"id-{index}") for index in range(81)]
        page = self.fake_page([51, 32])
        destination = self.root / "all.zip"
        downloads = []

        def download_page(_page, part, expected):
            downloads.append(len(expected))
            with ZipFile(part, "w") as archive:
                for item in expected:
                    archive.writestr(f"invoices/{item.external_id}/invoice.pdf", b"pdf")

        with patch.object(selected, "_close_export"), patch.object(selected, "_download_page", side_effect=download_page), \
             patch.object(selected, "wait_for_network_idle"):
            selected.download_selected_zip(page, destination, rows, {item.external_id for item in rows})
        self.assertEqual(downloads, [50, 31])
        self.assertEqual(page.header_clicks, [2, 1])
        self.assertEqual(page.row_clicks, [0, 0])

    def test_selection_snapshot_is_a_copy_before_page_state_changes(self):
        page = self.fake_page([3])
        snapshot = selected._selection(page)
        page.states[0][1] = True
        self.assertEqual(snapshot, [False, False, False])

    def test_first_page_without_target_is_skipped_without_pdf_download(self):
        downloads, destination = self.run_two_page_download({"second"})
        self.assertEqual(downloads, [["second"]])
        with ZipFile(destination) as archive:
            self.assertEqual(archive.namelist(), ["invoices/second/invoice.pdf"])

    def test_old_fifty_row_page_waits_for_new_thirty_one_row_page(self):
        rows = [row(f"id-{index}") for index in range(81)]
        page = self.fake_page([51, 51], delayed_sizes={1: (2, 32)})
        destination = self.root / "delayed.zip"
        downloads = []

        def download_page(_page, part, expected):
            downloads.append([item.external_id for item in expected])
            with ZipFile(part, "w") as archive:
                for item in expected:
                    archive.writestr(f"invoices/{item.external_id}/invoice.pdf", b"pdf")

        with patch.object(selected, "_close_export"), patch.object(selected, "_download_page", side_effect=download_page), \
             patch.object(selected, "wait_for_network_idle"):
            selected.download_selected_zip(page, destination, rows, {"id-80"})
        self.assertEqual(downloads, [["id-80"]])

    def test_delayed_header_clear_does_not_click_again_while_state_is_unchanged(self):
        page = self.fake_page([4], header_delay=2)
        page.states[0] = [False, True, False, False]
        selected._clear_page_selection(page, [True, False, False])
        self.assertEqual(page.header_clicks, [2])
        self.assertEqual(page.current_states(), [False, False, False, False])

    def test_rows_that_never_reach_expected_count_timeout(self):
        page = self.fake_page([3])
        with patch.object(selected.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(selected.DownloadError, "ページ更新が完了しません"):
                selected._wait_rows(page, total=4, expected_count=3)

    def test_initial_short_render_is_rechecked_until_fifty_rows_stabilize(self):
        page = self.fake_page([3], delayed_sizes={0: (2, 51)})
        selected._wait_rows(page, total=81)
        self.assertEqual(len(selected._selection(page)), 51)

    def test_selection_clear_honors_cancellation(self):
        page = self.fake_page([3])
        page.states[0] = [False, True, False]
        token = CancellationToken()
        with cancellation_scope(token):
            token.request()
            with self.assertRaises(OperationCancelled):
                selected._clear_page_selection(page, [True, False])
        self.assertEqual(page.header_clicks, [0])

    def test_selection_clear_rejects_url_escape_and_row_count_change(self):
        page = self.fake_page([3])
        page.url = "https://purchases.digitalbillder.com/other"
        with self.assertRaisesRegex(selected.DownloadError, "一覧から移動"):
            selected._selection(page)
        page.url = selected.APPLICATIONS_URL
        page = self.fake_page([2], delayed_sizes={0: (3, 3)})
        page.states[0] = [False, True]
        with self.assertRaisesRegex(selected.DownloadError, "行数が変わった"):
            selected._clear_page_selection(page, [True])

    def test_selected_csv_id_mismatch_is_rejected_before_pdf_download(self):
        expected = [row("one"), row("two")]
        csv_path = self.root / "selected.csv"
        self.write_csv(csv_path, [expected[0], row("other")])
        with self.assertRaisesRegex(selected.DownloadError, "一致しません"):
            selected._verify_selected_csv(csv_path, expected)

    def test_selected_csv_duplicate_id_is_rejected_before_pdf_download(self):
        expected = [row("one"), row("two")]
        csv_path = self.root / "selected.csv"
        self.write_csv(csv_path, [expected[0], expected[0]])
        with self.assertRaisesRegex(selected.DownloadError, "一致しません"):
            selected._verify_selected_csv(csv_path, expected)

    def test_selected_csv_content_change_is_rejected_before_pdf_download(self):
        expected = [row("one")]
        changed = row("one", 99999)
        csv_path = self.root / "selected.csv"
        self.write_csv(csv_path, [changed])
        with self.assertRaisesRegex(selected.DownloadError, "変わりました"):
            selected._verify_selected_csv(csv_path, expected)

    def test_csv_gate_does_not_click_file_download_on_mismatch(self):
        expected = [row("one")]
        csv_path = self.root / "selected.csv"
        self.write_csv(csv_path, [row("other")])
        download_button = MagicMock()
        csv_dialog = MagicMock()
        csv_dialog.get_by_role.return_value = MagicMock()
        pending = MagicMock()
        pending.value.save_as.side_effect = lambda path: Path(path).write_bytes(csv_path.read_bytes())
        download_context = MagicMock()
        download_context.__enter__.return_value = pending
        page = MagicMock()
        page.get_by_role.side_effect = lambda role, **_kwargs: (
            download_button if role == "button" else MagicMock()
        )
        with patch.object(selected, "_selection", return_value=[False, True]), \
             patch.object(selected, "_open_export_dialog", return_value=csv_dialog), \
             patch.object(selected, "_close_export"):
            page.expect_download.return_value = download_context
            with self.assertRaisesRegex(selected.DownloadError, "一致しません"):
                selected._download_page(page, self.root / "files.zip", expected)
        download_button.click.assert_not_called()

    def test_multiple_pdfs_are_preserved_under_their_invoice_ids(self):
        source = self.make_zip("source.zip", [
            ("invoices/one/first.pdf", b"one-a"), ("invoices/one/second.pdf", b"one-b"),
            ("invoices/two/only.pdf", b"two"),
        ])
        output = self.root / "result.zip"
        with ZipFile(output, "w") as archive:
            selected._append_pdfs(archive, source, {"one", "two"})
        with ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {
                "invoices/one/first.pdf", "invoices/one/second.pdf", "invoices/two/only.pdf",
            })

    def test_skipped_pdf_attachment_prevents_partial_import(self):
        for bad_name in ('invoices/one/' + 'x' * 260 + '.pdf', 'orphan.pdf'):
            with self.subTest(bad_name=bad_name):
                source = self.make_zip('source.zip', [('invoices/one/good.pdf', b'good'), (bad_name, b'bad')])
                with ZipFile(self.root / 'result.zip', 'w') as output:
                    with self.assertRaisesRegex(selected.DownloadError, '読み取れない添付PDF'):
                        selected._append_pdfs(output, source, {'one'})
                    self.assertEqual(output.namelist(), [])

    def test_non_pdf_attachment_does_not_block_pdf_import(self):
        source = self.make_zip('source.zip', [('invoices/one/good.pdf', b'good'), ('invoices/one/note.txt', b'note')])
        with ZipFile(self.root / 'result.zip', 'w') as output:
            selected._append_pdfs(output, source, {'one'})
            self.assertEqual(output.namelist(), ['invoices/one/good.pdf'])

    def test_aggregate_limit_stops_before_adding_the_next_page(self):
        source = self.make_zip('source.zip', [('invoices/one/good.pdf', b'good')])
        with ZipFile(self.root / 'result.zip', 'w') as output:
            output.writestr('invoices/prior/prior.pdf', b'prior')
            with patch.object(selected, 'MAX_PDF_COUNT', 1):
                with self.assertRaisesRegex(selected.DownloadError, '上限'):
                    selected._append_pdfs(output, source, {'one'})
            with patch.object(selected, 'MAX_TOTAL_PDF_SIZE_BYTES', 8):
                with self.assertRaisesRegex(selected.DownloadError, '上限'):
                    selected._append_pdfs(output, source, {'one'})
            self.assertEqual(output.namelist(), ['invoices/prior/prior.pdf'])

    def test_unselected_pdf_id_is_rejected(self):
        source = self.make_zip("source.zip", [("invoices/one/invoice.pdf", b"one"), ("invoices/other/invoice.pdf", b"other")])
        with ZipFile(self.root / "result.zip", "w") as output:
            with self.assertRaisesRegex(selected.DownloadError, "一致しない"):
                selected._append_pdfs(output, source, {"one"})

    def test_duplicate_pdf_name_is_rejected(self):
        source = self.make_zip("source.zip", [("invoices/one/invoice.pdf", b"first"), ("invoices/one/invoice.pdf", b"second")])
        with ZipFile(self.root / "result.zip", "w") as output:
            with self.assertRaisesRegex(selected.DownloadError, "重複"):
                selected._append_pdfs(output, source, {"one"})

    def test_cancellation_stops_before_processing_the_next_pdf(self):
        source = self.make_zip("source.zip", [("invoices/one/one.pdf", b"one"), ("invoices/two/two.pdf", b"two")])
        output_path = self.root / "result.zip"
        token = CancellationToken()
        original_check = selected.check_cancelled
        checks = 0

        def cancel_after_first_pdf():
            nonlocal checks
            checks += 1
            if checks == 3:
                token.request()
            original_check()

        with cancellation_scope(token), patch.object(selected, "check_cancelled", side_effect=cancel_after_first_pdf):
            with self.assertRaises(OperationCancelled):
                with ZipFile(output_path, "w") as output:
                    selected._append_pdfs(output, source, {"one", "two"})
        with ZipFile(output_path) as archive:
            self.assertEqual(archive.namelist(), ["invoices/one/one.pdf"])


if __name__ == "__main__":
    unittest.main()
