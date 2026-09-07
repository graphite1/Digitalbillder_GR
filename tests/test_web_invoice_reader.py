from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from invoice_manager import db
from invoice_manager.services import historical_costs as history
from invoice_manager.services.operation_cancellation import CancellationToken, OperationCancelled, cancellation_scope
from invoice_manager.services import web_invoice_reader as reader
from invoice_manager.services.web_allocation_plan import AllocationLine


def assessment_row(
    code: str = "A-01",
    name: str = "架空工種",
    net: int = 1_000,
    tax_label: str = "10% (0.1)",
    tax: int = 100,
    gross: int = 1_100,
) -> list[str]:
    return [f"{code} ({name})", str(net), tax_label, str(tax), str(gross), "", "", ""]


class _TextLocator:
    def __init__(self, texts=None, rows=None, count=0, call_log=None, wait_label=None) -> None:
        self._texts = list(texts or [])
        self._rows = list(rows or [])
        self._count = count
        self._call_log = call_log
        self._wait_label = wait_label

    def all_text_contents(self):
        return list(self._texts)

    def evaluate_all(self, _script):
        return [list(row) for row in self._rows]

    def wait_for(self, **kwargs):
        if self._call_log is not None:
            self._call_log.append((self._wait_label, kwargs))
        return None

    def count(self):
        return self._count

    def locator(self, selector):
        if selector != "tbody tr":
            raise AssertionError(f"unexpected selector: {selector}")
        return self


class _RoleMarker:
    def __init__(self, name: str) -> None:
        self.name = name


class _TableSelector:
    def __init__(self, project_rows, item_rows) -> None:
        self.project_rows = project_rows
        self.item_rows = item_rows

    def filter(self, *, has):
        if has.name == "工事コード":
            return _TextLocator(rows=self.project_rows)
        if has.name == "項目":
            return _TextLocator(rows=self.item_rows)
        raise AssertionError(f"unexpected table marker: {has.name}")


class _Panel:
    def __init__(self, headers, assessment_rows, project_rows, item_rows, archived, call_log) -> None:
        self.region = _Region(headers, assessment_rows, call_log)
        self.tables = _TableSelector(project_rows, item_rows)
        self.archived = archived

    def get_by_role(self, role, **kwargs):
        if role == "region":
            return self.region
        if role == "table":
            return self.tables
        raise AssertionError(f"unexpected panel role: {role}, {kwargs}")

    def get_by_text(self, text, *, exact):
        if text != "保管済みのため編集できません" or not exact:
            raise AssertionError("unexpected archive marker lookup")
        return _TextLocator(count=1 if self.archived else 0)


class _Region(_TextLocator):
    def __init__(self, headers, rows, call_log) -> None:
        super().__init__(rows=rows, call_log=call_log, wait_label="region_wait")
        self.headers = headers

    def get_by_role(self, role):
        if role != "columnheader":
            raise AssertionError(f"unexpected region role: {role}")
        return _TextLocator(texts=self.headers)


class _ReadOnlyPage:
    """Small Playwright-shaped fake that intentionally has no click/fill APIs."""

    def __init__(
        self,
        *,
        headers=None,
        rows=None,
        project_rows=None,
        item_rows=None,
        archived=True,
    ) -> None:
        self.url = reader.ORIGIN
        self.calls = []
        self.panel = _Panel(
            headers or reader.ASSESSMENT_HEADERS,
            rows if rows is not None else [assessment_row()],
            project_rows if project_rows is not None else [["架空工事", "P-FAKE", "", "", ""]],
            item_rows if item_rows is not None else [
                ["請求日", "2026年9月5日(金)", ""],
                ["発行元企業名", "架空建設株式会社", ""],
                ["請求金額 (税込)", "¥1,100", ""],
                ["査定合計金額(税込)", "￥1,100", ""],
            ],
            archived,
            self.calls,
        )

    def goto(self, url, *, wait_until):
        self.calls.append(("goto", url, wait_until))
        self.url = url

    def get_by_role(self, role, **kwargs):
        self.calls.append(("get_by_role", role, kwargs.get("name")))
        if role == "tabpanel":
            return self.panel
        if role == "columnheader":
            return _RoleMarker(kwargs["name"])
        raise AssertionError(f"unexpected page role: {role}")

    def wait_for_load_state(self, state, *, timeout):
        self.calls.append(("wait_for_load_state", state, timeout))


def csv_row(external_id: str, *, project_code="P001", vendor_name="架空建設"):
    return SimpleNamespace(
        external_id=external_id,
        project_code=project_code,
        project_name="架空工事",
        vendor_name=vendor_name,
        invoice_date="2026-09-05",
        total_amount=1_100,
    )


def web_read(row, lines, *, archived=True):
    return reader.WebInvoiceRead(
        row.external_id,
        row.project_code,
        row.vendor_name,
        row.invoice_date,
        row.total_amount,
        archived,
        tuple(lines),
    )


class _ExportPage:
    def __init__(self) -> None:
        self.storage_state_calls = 0
        self.context = self

    def storage_state(self):
        self.storage_state_calls += 1
        return {"cookies": [{"name": "fictional-session"}]}


class WebInvoiceParserTests(unittest.TestCase):
    def test_exact_header_order_is_required(self) -> None:
        valid = reader.parse_assessment_rows(reader.ASSESSMENT_HEADERS, [assessment_row()])
        self.assertEqual(len(valid), 1)

        reordered = list(reader.ASSESSMENT_HEADERS)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        renamed = list(reader.ASSESSMENT_HEADERS)
        renamed[0] = "工種（名称変更）"
        for headers in (reordered, renamed):
            with self.subTest(headers=headers), self.assertRaisesRegex(reader.InvoiceReadError, "列構成"):
                reader.parse_assessment_rows(headers, [assessment_row()])

    def test_only_the_two_observed_assessment_header_variants_are_allowed(self) -> None:
        base_line = reader.parse_assessment_rows(
            reader.ASSESSMENT_HEADERS,
            [assessment_row(code="BASE")],
        )[0]
        order_line = reader.parse_assessment_rows(
            reader.ASSESSMENT_ORDER_HEADERS,
            [assessment_row(code="ORDER") + ["検索用の架空値", "架空発注行42"]],
        )[0]
        self.assertEqual((base_line.code, order_line.code), ("BASE", "ORDER"))

        unknown_headers = reader.ASSESSMENT_HEADERS + ("未確認の追加列",)
        with self.assertRaisesRegex(reader.InvoiceReadError, "列構成"):
            reader.parse_assessment_rows(
                unknown_headers,
                [assessment_row(code="UNKNOWN") + [""]],
            )

    def test_missing_or_extra_cell_stops_parsing(self) -> None:
        for cells in (assessment_row()[:-1], assessment_row() + ["余分"]):
            with self.subTest(cell_count=len(cells)), self.assertRaisesRegex(reader.InvoiceReadError, "セル数"):
                reader.parse_assessment_rows(reader.ASSESSMENT_HEADERS, [cells])

    def test_malformed_second_row_reports_id_row_and_only_first_cell(self) -> None:
        leaked = "秘密の他セル値"
        rows = [assessment_row(), ["不正な工種表示", "2", "10%", "0", "2", leaked, leaked, leaked]]

        with self.assertRaisesRegex(reader.InvoiceReadError, "Webの工種コードと工種名を読み取れません") as raised:
            reader.parse_assessment_rows(reader.ASSESSMENT_HEADERS, rows, external_id="invoice-二件目")

        message = str(raised.exception)
        self.assertIn("請求ID: invoice-二件目", message)
        self.assertIn("行: 2", message)
        self.assertIn("先頭セル: '不正な工種表示'", message)
        self.assertNotIn(leaked, message)

    def test_malformed_first_cell_escapes_controls_and_truncates_display(self) -> None:
        first_cell = "不正\nセル\t" + ("X" * 220)

        with self.assertRaisesRegex(reader.InvoiceReadError, "Webの工種コードと工種名を読み取れません") as raised:
            reader.parse_assessment_rows(
                reader.ASSESSMENT_HEADERS,
                [[first_cell, "2", "10%", "0", "2", "", "", ""]],
                external_id="invoice-1",
            )

        message = str(raised.exception)
        self.assertIn(r"'不正\nセル\t", message)
        self.assertEqual(message.count("X"), 200 - len("不正\nセル\t"))
        self.assertIn("…（省略）", message)
        self.assertNotIn("\n", message)
        self.assertNotIn("\t", message)

    def test_arbitrary_nonempty_work_type_code_is_preserved(self) -> None:
        line = reader.parse_assessment_rows(
            reader.ASSESSMENT_HEADERS,
            [assessment_row(code="任意-99_X", name="架空の特殊工種")],
        )[0]
        self.assertEqual((line.code, line.name), ("任意-99_X", "架空の特殊工種"))

    def test_mixed_confirmed_tax_rates_are_preserved_per_line(self) -> None:
        rows = [
            assessment_row(code="A", net=1_000, tax_label="10%", tax=100, gross=1_100),
            assessment_row(code="B", net=1_000, tax_label="8% (0.08)", tax=80, gross=1_080),
            assessment_row(code="C", net=1_000, tax_label="非課税", tax=0, gross=1_000),
        ]
        lines = reader.parse_assessment_rows(reader.ASSESSMENT_HEADERS, rows)
        self.assertEqual([line.tax_rate for line in lines], ["10", "8", "exempt"])
        self.assertEqual([line.tax_amount for line in lines], [100, 80, 0])

    def test_unknown_tax_label_and_invalid_amount_relationship_are_rejected(self) -> None:
        with self.assertRaisesRegex(reader.InvoiceReadError, "税率"):
            reader.parse_assessment_rows(
                reader.ASSESSMENT_HEADERS,
                [assessment_row(tax_label="5%", tax=50, gross=1_050)],
            )
        with self.assertRaisesRegex(reader.InvoiceReadError, "関係"):
            reader.parse_assessment_rows(
                reader.ASSESSMENT_HEADERS,
                [assessment_row(tax_label="非課税", tax=1, gross=1_001)],
            )

    def test_all_five_identity_fields_must_match(self) -> None:
        base = reader.WebInvoiceRead("id", "P1", "架空会社", "2026-09-05", 1_100, True, ())
        expected = {
            "external_id": "id",
            "project_code": "P1",
            "vendor_name": "架空会社",
            "invoice_date": "2026-09-05",
            "invoice_amount": 1_100,
        }
        reader.verify_identity(base, **expected)
        alternatives = {
            "external_id": "different",
            "project_code": "P2",
            "vendor_name": "別会社",
            "invoice_date": "2026-09-06",
            "invoice_amount": 1_101,
        }
        for field, value in alternatives.items():
            changed = {**expected, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(reader.InvoiceReadError, "一致しません"):
                reader.verify_identity(base, **changed)

    def test_page_reader_uses_read_only_surface_and_does_not_fabricate_empty_amounts(self) -> None:
        external_id = str(uuid4())
        page = _ReadOnlyPage(
            rows=[],
            item_rows=[
                ["請求日", "2026年9月5日(金)", ""],
                ["発行元企業名", "架空建設株式会社", ""],
                ["請求金額 (税込)", "¥9,999", ""],
                ["査定合計金額(税込)", "¥0", ""],
            ],
        )
        result = reader.read_invoice_page(page, external_id)
        self.assertEqual(result.invoice_amount, 9_999)
        self.assertEqual(result.lines, ())
        self.assertTrue(result.archived)
        self.assertEqual(
            page.calls[:4],
            [
                ("goto", f"{reader.ORIGIN}/invoices/{external_id}", "domcontentloaded"),
                ("get_by_role", "tabpanel", "請求書情報"),
                ("region_wait", {"state": "visible"}),
                ("wait_for_load_state", "networkidle", 30_000),
            ],
        )

    def test_page_reader_passes_invoice_id_to_assessment_parser(self) -> None:
        external_id = str(uuid4())
        with patch.object(reader, "parse_assessment_rows", wraps=reader.parse_assessment_rows) as parser:
            reader.read_invoice_page(_ReadOnlyPage(), external_id)

        parser.assert_called_once()
        self.assertEqual(parser.call_args.kwargs["external_id"], external_id)

    def test_page_reader_does_not_wait_when_rows_are_ready(self) -> None:
        page = _ReadOnlyPage()
        page.wait_for_timeout = lambda _milliseconds: self.fail("ready rows must not wait")

        result = reader.read_invoice_page(page, str(uuid4()))

        self.assertEqual(len(result.lines), 1)

    def test_page_reader_retries_placeholder_rows_without_reload(self) -> None:
        placeholder_rows = [
            ["該当項目なし", "0", "10%", "0", "0", "", "", ""],
            ["該当項目なし", "0", "10%", "0", "0", "", "", ""],
        ]
        valid_rows = [
            assessment_row(code="D603", name="土工", net=500, tax=50, gross=550),
            assessment_row(code="D607", name="舗装工", net=500, tax=50, gross=550),
        ]
        page = _ReadOnlyPage(rows=placeholder_rows)
        waits = []
        first_ready_rows = [valid_rows[0], placeholder_rows[1]]

        def wait_for_timeout(milliseconds):
            waits.append(milliseconds)
            page.panel.region._rows = first_ready_rows if len(waits) == 1 else valid_rows

        page.wait_for_timeout = wait_for_timeout
        external_id = str(uuid4())
        result = reader.read_invoice_page(page, external_id)

        self.assertEqual([(line.code, line.amount_included) for line in result.lines], [("D603", 550), ("D607", 550)])
        self.assertEqual(len([call for call in page.calls if call[0] == "goto"]), 1)
        self.assertEqual(waits, [250, 250])

    def test_permanent_placeholder_rows_hit_deadline_with_diagnostic(self) -> None:
        page = _ReadOnlyPage(rows=[["該当項目なし", "0", "10%", "0", "0", "", "", ""]])
        waits = []
        page.wait_for_timeout = lambda milliseconds: waits.append(milliseconds)
        external_id = str(uuid4())

        with patch.object(reader.time, "monotonic", side_effect=[0, 0, 31]):
            with self.assertRaisesRegex(reader.InvoiceReadError, "請求ID: .*行: 1.*該当項目なし"):
                reader.read_invoice_page(page, external_id)
        self.assertEqual(waits, [250])

    def test_cancellation_during_placeholder_wait_stops_without_parsing(self) -> None:
        page = _ReadOnlyPage(rows=[["該当項目なし", "0", "10%", "0", "0", "", "", ""]])
        token = CancellationToken()
        page.wait_for_timeout = lambda _milliseconds: token.request()

        with patch.object(reader, "parse_assessment_rows", side_effect=AssertionError("cancelled read must not parse")):
            with cancellation_scope(token), self.assertRaises(OperationCancelled):
                reader.read_invoice_page(page, str(uuid4()))

    def test_non_placeholder_malformed_rows_fail_closed_without_wait(self) -> None:
        page = _ReadOnlyPage(rows=[["不正な工種表示", "2", "10%", "0", "2", "", "", ""]])
        page.wait_for_timeout = lambda _milliseconds: self.fail("non-placeholder rows must not wait")

        with self.assertRaisesRegex(reader.InvoiceReadError, "工種コードと工種名"):
            reader.read_invoice_page(page, str(uuid4()))

    def test_changed_headers_or_cell_shape_fail_closed_without_wait(self) -> None:
        cases = [
            (list(reader.ASSESSMENT_HEADERS[:-1]) + ["変更列"], [["該当項目なし", "0", "10%", "0", "0", "", "", ""]]),
            (list(reader.ASSESSMENT_HEADERS), [["該当項目なし", "0", "10%", "0", "0", ""]]),
        ]
        for headers, rows in cases:
            with self.subTest(headers=headers, rows=rows):
                page = _ReadOnlyPage(headers=headers, rows=rows)
                page.wait_for_timeout = lambda _milliseconds: self.fail("changed table shape must not wait")
                with self.assertRaisesRegex(reader.InvoiceReadError, "(列構成|セル数)"):
                    reader.read_invoice_page(page, str(uuid4()))

    def test_redirect_during_placeholder_wait_is_rejected(self) -> None:
        page = _ReadOnlyPage(rows=[["該当項目なし", "0", "10%", "0", "0", "", "", ""]])

        def redirect_and_finish(_milliseconds):
            page.url = f"{reader.ORIGIN}/invoices/{uuid4()}"
            page.panel.region._rows = [assessment_row()]

        page.wait_for_timeout = redirect_and_finish
        with self.assertRaisesRegex(reader.InvoiceReadError, "別の請求ページ"):
            reader.read_invoice_page(page, str(uuid4()))

    def test_page_reader_requires_empty_third_operation_cell_in_item_table(self) -> None:
        external_id = str(uuid4())
        invalid_tables = [
            [["請求日", "2026年9月5日(金)"]],
            [["請求日", "2026年9月5日(金)", "編集"]],
        ]
        for item_rows in invalid_tables:
            with self.subTest(item_rows=item_rows), self.assertRaisesRegex(reader.InvoiceReadError, "請求書項目"):
                reader.read_invoice_page(_ReadOnlyPage(item_rows=item_rows), external_id)


class ArchivedHistorySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir, self.original_db_path = db.DATA_DIR, db.DB_PATH
        db.DATA_DIR = Path(self.temp.name)
        db.DB_PATH = db.DATA_DIR / "app.db"
        db.initialize_database()

    def tearDown(self) -> None:
        db.DATA_DIR, db.DB_PATH = self.original_data_dir, self.original_db_path
        self.temp.cleanup()

    def test_any_invoice_failure_keeps_the_previous_complete_history(self) -> None:
        self._seed_old_history()
        first, second = csv_row(str(uuid4())), csv_row(str(uuid4()))

        with self._patched_export([first, second]), patch.object(
            reader, "_read_archive_batches", side_effect=reader.InvoiceReadError("並列読取り失敗")
        ):
            with self.assertRaisesRegex(reader.InvoiceReadError, "並列"):
                reader.sync_archived_history()

        rows = history.list_actual_costs(project_code="P001")
        self.assertEqual([(row.work_type_code, row.net_amount) for row in rows], [("OLD", 1_000)])
        self.assertEqual(history.get_historical_sync_status().active_invoice_count, 1)

    def test_empty_assessment_is_skipped_then_complete_scan_is_reconciled(self) -> None:
        self._seed_old_history()
        empty, complete = csv_row(str(uuid4())), csv_row(str(uuid4()))
        new_line = AllocationLine("NEW", "新規工種", 1_000, "10", 100, 1_100)

        with self._patched_export([empty, complete]), patch.object(
            reader, "_read_archive_batches",
            return_value=[web_read(empty, []), web_read(complete, [new_line])],
        ):
            message = reader.sync_archived_history()

        self.assertIn("詳細取得2件", message)
        self.assertIn("査定なし1件", message)
        rows = history.list_actual_costs(project_code="P001")
        self.assertEqual([(row.work_type_code, row.net_amount) for row in rows], [("NEW", 1_000)])
        with db.get_connection() as connection:
            stored_ids = {
                row["external_id"]
                for row in connection.execute("SELECT external_id FROM historical_archived_invoices")
            }
        self.assertNotIn(empty.external_id, stored_ids)
        self.assertEqual(history.get_historical_sync_status().active_invoice_count, 1)

    def test_matching_cached_metadata_skips_all_detail_reads_by_default(self) -> None:
        row = csv_row(str(uuid4()))
        expected = self._snapshot_for(row, code="CACHED")
        history.replace_active_archived_snapshots([expected])

        with self._patched_export([row]), patch.object(reader, "read_invoice_page") as detail, patch.object(
            reader, "_read_archive_batches"
        ) as batches:
            message = reader.sync_archived_history()

        detail.assert_not_called()
        batches.assert_not_called()
        self.assertIn("詳細取得0件", message)
        self.assertIn("確認済み再利用1件", message)
        self.assertEqual(history.load_active_archived_snapshots(), {row.external_id: expected})

    def test_changed_csv_metadata_fetches_detail_and_replaces_cached_metadata(self) -> None:
        old_row = csv_row(str(uuid4()))
        history.replace_active_archived_snapshots([self._snapshot_for(old_row, code="OLD")])
        changed = csv_row(old_row.external_id)
        changed.project_name = "CSVで変更された架空工事名"
        changed.total_amount = 2_200
        changed_read = reader.WebInvoiceRead(
            changed.external_id, changed.project_code, changed.vendor_name,
            changed.invoice_date, changed.total_amount, True,
            (AllocationLine("NEW", "更新工種", 2_000, "10", 200, 2_200),),
        )

        with self._patched_export([changed]), patch.object(
            reader, "read_invoice_page", return_value=changed_read
        ) as detail:
            reader.sync_archived_history()

        detail.assert_called_once()
        cached = history.load_active_archived_snapshots()[changed.external_id]
        self.assertEqual((cached.project_name, cached.gross_invoice_total), (changed.project_name, 2_200))
        self.assertEqual(cached.allocations[0].work_type_code, "NEW")

    def test_full_refresh_rereads_detail_even_when_metadata_matches(self) -> None:
        row = csv_row(str(uuid4()))
        history.replace_active_archived_snapshots([self._snapshot_for(row, code="OLD")])
        refreshed = web_read(
            row,
            [AllocationLine("REFRESHED", "全件再検証工種", 1_000, "10", 100, 1_100)],
        )

        with self._patched_export([row]), patch.object(
            reader, "read_invoice_page", return_value=refreshed
        ) as detail:
            message = reader.sync_archived_history(full_refresh=True)

        detail.assert_called_once()
        self.assertIn("詳細取得1件", message)
        self.assertEqual(
            history.load_active_archived_snapshots()[row.external_id].allocations[0].work_type_code,
            "REFRESHED",
        )

    def test_cached_invoice_vanished_from_csv_is_deactivated(self) -> None:
        remains, vanished = csv_row(str(uuid4())), csv_row(str(uuid4()))
        history.replace_active_archived_snapshots(
            [self._snapshot_for(remains, code="KEEP"), self._snapshot_for(vanished, code="VANISH")]
        )

        with self._patched_export([remains]), patch.object(reader, "read_invoice_page") as detail:
            reader.sync_archived_history()

        detail.assert_not_called()
        self.assertEqual(set(history.load_active_archived_snapshots()), {remains.external_id})
        with db.get_connection() as connection:
            vanished_active = connection.execute(
                "SELECT is_active FROM historical_archived_invoices WHERE external_id = ?",
                (vanished.external_id,),
            ).fetchone()[0]
        self.assertEqual(vanished_active, 0)

    def test_parallel_batch_failure_publishes_no_partial_generation(self) -> None:
        old_row = self._seed_old_history()
        rows = [csv_row(str(uuid4())) for _ in range(4)]

        with self._patched_export(rows), patch.object(
            reader, "_read_archive_batches", side_effect=RuntimeError("worker failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "worker failure"):
                reader.sync_archived_history()

        self.assertEqual(set(history.load_active_archived_snapshots()), {old_row.external_id})
        self.assertEqual(history.get_historical_sync_status().active_invoice_count, 1)

    def test_missing_or_duplicate_parallel_results_are_rejected_before_publish(self) -> None:
        self._seed_old_history()
        first, second = csv_row(str(uuid4())), csv_row(str(uuid4()))
        line = AllocationLine("NEW", "新規工種", 1_000, "10", 100, 1_100)
        cases = {
            "missing": [web_read(first, [line])],
            "duplicate": [web_read(first, [line]), web_read(second, [line]), web_read(first, [line])],
        }
        for label, results in cases.items():
            with self.subTest(label=label), self._patched_export([first, second]), patch.object(
                reader, "_read_archive_batches", return_value=results
            ):
                with self.assertRaisesRegex(reader.InvoiceReadError, "ID・件数"):
                    reader.sync_archived_history()

    def test_parallel_reader_uses_at_most_three_independent_thread_local_sessions(self) -> None:
        rows = [csv_row(str(uuid4())) for _ in range(7)]
        row_by_id = {row.external_id: row for row in rows}
        created_pages = []
        received_states = []

        @contextmanager
        def fake_session(storage_state):
            page = SimpleNamespace(creator_thread=threading.get_ident(), token=object())
            created_pages.append(page)
            received_states.append(storage_state)
            yield page

        def fake_read(page, external_id):
            self.assertEqual(page.creator_thread, threading.get_ident())
            return web_read(row_by_id[external_id], [])

        storage_state = {"cookies": [{"name": "fictional-session"}]}
        with patch.object(reader, "authenticated_reader_session", side_effect=fake_session), patch.object(
            reader, "read_invoice_page", side_effect=fake_read
        ):
            results = reader._read_archive_batches(rows, storage_state, lambda _message: None)

        self.assertEqual(len(created_pages), 3)
        self.assertEqual(len({id(page) for page in created_pages}), 3)
        self.assertEqual(received_states, [storage_state] * 3)
        self.assertEqual({item.external_id for item in results}, set(row_by_id))

    def _patched_export(self, rows):
        export_page = _ExportPage()
        return _PatchGroup(
            patch.object(reader, "export_session", return_value=nullcontext(export_page)),
            patch.object(reader, "download_csv", return_value=Path("fictional-archive.csv")),
            patch.object(reader, "read_invoice_csv", return_value=(rows, [], "utf-8")),
        )

    def _snapshot_for(self, row, *, code="OLD") -> history.ArchivedInvoiceSnapshot:
        tax = row.total_amount // 11
        net = row.total_amount - tax
        line = history.ArchivedAllocationSnapshot(code, f"{code}工種", net, "10", tax, row.total_amount)
        return history.ArchivedInvoiceSnapshot(
            row.external_id, row.project_code, row.project_name, row.vendor_name,
            row.invoice_date, row.total_amount, "archived", (line,),
        )

    def _seed_old_history(self):
        row = csv_row(str(uuid4()))
        row.invoice_date = "2026-08-01"
        history.replace_active_archived_snapshots([self._snapshot_for(row)])
        return row


class _PatchGroup:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False


if __name__ == "__main__":
    unittest.main()
