"""Read visible invoice tables only; no edit, save, action-tab or workflow calls."""
from __future__ import annotations

import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from invoice_manager.services.csv_reader import read_invoice_csv
from invoice_manager.services.digital_billder_download import DownloadError, authenticated_reader_session, download_csv, export_session
from invoice_manager.services.web_allocation_plan import AllocationLine, AllocationPlan

ORIGIN = "https://purchases.digitalbillder.com"
ASSESSMENT_REGION = "査定入力テーブル - パンしてスクロール可能"
ASSESSMENT_HEADERS = ("工種", "税抜金額", "税率", "消費税額", "税込金額", "摘要", "分析コード", "原価科目区分（変更時のみ入力）")
ASSESSMENT_ORDER_HEADERS = ASSESSMENT_HEADERS + ("行番号検索用", "発注行番号")
_sync_lock = threading.Lock()


class InvoiceReadError(DownloadError):
    pass


@dataclass(frozen=True)
class WebInvoiceRead:
    external_id: str
    project_code: str
    vendor_name: str
    invoice_date: str
    invoice_amount: int
    archived: bool
    lines: tuple[AllocationLine, ...]


def parse_web_amount(value: str) -> int:
    text = value.strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not re.fullmatch(r"-?\d+", text):
        raise InvoiceReadError("Webの金額を整数として読み取れません。")
    return int(text)


def parse_assessment_rows(headers, rows) -> tuple[AllocationLine, ...]:
    if tuple(headers) not in (ASSESSMENT_HEADERS, ASSESSMENT_ORDER_HEADERS):
        raise InvoiceReadError("査定入力の列構成が変わっています。取得を停止しました。")
    lines = []
    for cells in rows:
        if len(cells) != len(headers):
            raise InvoiceReadError("査定入力のセル数が一致しません。")
        match = re.fullmatch(r"([^\s()]+)\s*\((.+)\)", cells[0].strip(), flags=re.DOTALL)
        if not match:
            raise InvoiceReadError("Webの工種コードと工種名を読み取れません。")
        tax_label = cells[2].strip()
        if tax_label in ("10% (0.1)", "10%"):
            rate = "10"
        elif tax_label in ("8% (0.08)", "8%"):
            rate = "8"
        elif tax_label in ("非課税", "非課税 (0)"):
            rate = "exempt"
        else:
            raise InvoiceReadError("Webの税率が未確認の表示です。")
        net, tax, gross = (parse_web_amount(cells[index]) for index in (1, 3, 4))
        if net + tax != gross or (rate == "exempt" and tax != 0):
            raise InvoiceReadError("Webの税抜・税額・税込の関係を確認できません。")
        lines.append(AllocationLine(match[1], match[2], net, rate, tax, gross))
    return tuple(lines)


def read_invoice_page(page, external_id: str) -> WebInvoiceRead:
    """Navigate to one validated ID and inspect the rendered read-only tables."""
    UUID(external_id)
    page.goto(f"{ORIGIN}/invoices/{external_id}", wait_until="domcontentloaded")
    panel = page.get_by_role("tabpanel", name="請求書情報", exact=True)
    region = panel.get_by_role("region", name=ASSESSMENT_REGION, exact=True)
    region.wait_for(state="visible")
    page.wait_for_load_state("networkidle", timeout=30_000)
    parsed = urlparse(page.url)
    if f"{parsed.scheme}://{parsed.netloc}" != ORIGIN or parsed.path != f"/invoices/{external_id}":
        raise InvoiceReadError("別の請求ページへ移動したため取得を停止しました。")
    headers = region.get_by_role("columnheader").all_text_contents()
    rows = region.locator("tbody tr").evaluate_all("rows => rows.map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText.trim()))")
    lines = parse_assessment_rows([s.strip() for s in headers], rows)
    project_table = panel.get_by_role("table").filter(has=page.get_by_role("columnheader", name="工事コード", exact=True))
    project_rows = project_table.locator("tbody tr").evaluate_all("rows => rows.map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText.trim()))")
    if len(project_rows) != 1 or len(project_rows[0]) != 5:
        raise InvoiceReadError("工事情報の構成を確認できません。")
    item_table = panel.get_by_role("table").filter(has=page.get_by_role("columnheader", name="項目", exact=True))
    pairs = item_table.locator("tbody tr").evaluate_all("rows => rows.map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText.trim()))")
    # The current read-only table has a third, empty operation cell omitted by AX.
    if any(len(pair) != 3 or pair[2] for pair in pairs) or len({pair[0] for pair in pairs}) != len(pairs):
        raise InvoiceReadError("請求書項目の構成を確認できません。")
    fields = {pair[0]: pair[1] for pair in pairs}
    try:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\(.+\))?", fields["請求日"])
        if not match:
            raise InvoiceReadError("請求日の表示形式を確認できません。")
        date = f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
        vendor = fields["発行元企業名"]
        total = parse_web_amount(fields["請求金額 (税込)"])
        assessed_total = parse_web_amount(fields["査定合計金額(税込)"])
    except KeyError:
        raise InvoiceReadError("本人確認用の請求書項目が見つかりません。") from None
    if sum(line.amount_included for line in lines) != assessed_total:
        raise InvoiceReadError("査定合計と読み取った明細の税込合計が一致しません。")
    return WebInvoiceRead(external_id, project_rows[0][1], vendor, date, total,
                          panel.get_by_text("保管済みのため編集できません", exact=True).count() > 0, lines)


def verify_identity(read: WebInvoiceRead, *, external_id, project_code, vendor_name, invoice_date, invoice_amount) -> None:
    if (read.external_id, read.project_code, read.vendor_name, read.invoice_date, read.invoice_amount) != (
        external_id, project_code, vendor_name, str(invoice_date), int(invoice_amount)
    ):
        raise InvoiceReadError("工事・会社・請求日・金額が取得前と一致しません。確認をやり直してください。")


def read_for_plan(plan: AllocationPlan, progress=lambda _message: None) -> WebInvoiceRead:
    with export_session(progress) as page:
        progress("Webの査定入力を読み取っています…")
        result = read_invoice_page(page, plan.external_id)
    verify_identity(result, external_id=plan.external_id, project_code=plan.project_code,
                    vendor_name=plan.vendor_name, invoice_date=plan.invoice_date, invoice_amount=plan.invoice_amount)
    return result


def _cached_snapshot_matches(snapshot, row) -> bool:
    return snapshot is not None and (
        snapshot.project_code, snapshot.project_name, snapshot.vendor_name,
        snapshot.invoice_date, snapshot.gross_invoice_total
    ) == (row.project_code, row.project_name, row.vendor_name, str(row.invoice_date), row.total_amount)


def _read_archive_batches(rows, storage_state, progress) -> list[WebInvoiceRead]:
    """Up to three independent reader threads; Playwright objects never cross threads."""
    count = min(3, len(rows))
    batches = [rows[index::count] for index in range(count)]
    completed = 0
    progress_lock = threading.Lock()

    def read_batch(batch):
        nonlocal completed
        results = []
        with authenticated_reader_session(storage_state) as page:
            for row in batch:
                result = read_invoice_page(page, row.external_id)
                results.append(result)
                with progress_lock:
                    completed += 1
                    progress(f"追加・変更分の査定を取得中: {completed}/{len(rows)}件")
        return results

    results = []
    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="billder-read") as executor:
        futures = [executor.submit(read_batch, batch) for batch in batches]
        for future in as_completed(futures):
            results.extend(future.result())
    return results


def sync_archived_history(progress=lambda _message: None, *, full_refresh: bool = False) -> str:
    from invoice_manager.services.historical_costs import (
        ArchivedAllocationSnapshot, ArchivedInvoiceSnapshot, load_active_archived_snapshots,
        replace_active_archived_snapshots,
    )

    if not _sync_lock.acquire(blocking=False):
        raise InvoiceReadError("保管済み履歴の取得を実行中です。")
    try:
        snapshots = []
        empty_ids = []
        cached = {} if full_refresh else load_active_archived_snapshots()
        reused = 0
        read_results = []
        with tempfile.TemporaryDirectory(prefix="digitalbillder_history_") as folder:
            with export_session(progress, archived_only=True) as page:
                path = download_csv(page, Path(folder) / "archived.csv")
                rows, errors, _encoding = read_invoice_csv(path) if path else ([], [], None)
                if errors or len({row.external_id for row in rows}) != len(rows):
                    raise InvoiceReadError("保管済み一覧の重複または読取りエラーがあります。")
                pending = []
                for row in rows:
                    prior = cached.get(row.external_id)
                    if _cached_snapshot_matches(prior, row):
                        snapshots.append(prior)
                        reused += 1
                    else:
                        pending.append(row)
                progress(f"保管済み{len(rows)}件: 確認済み{reused}件 / 詳細取得{len(pending)}件")
                if len(pending) == 1:
                    read_results.append(read_invoice_page(page, pending[0].external_id))
                # Authentication state stays in process memory, never in files or logs.
                session_state = page.context.storage_state() if len(pending) > 1 else None
            if len(pending) > 1:
                read_results = _read_archive_batches(pending, session_state, progress)
            by_id = {result.external_id: result for result in read_results}
            if len(read_results) != len(pending) or len(by_id) != len(pending) or set(by_id) != {row.external_id for row in pending}:
                raise InvoiceReadError("取得した請求ID・件数が一致しません。履歴は更新していません。")
            for row in pending:
                result = by_id[row.external_id]
                verify_identity(result, external_id=row.external_id, project_code=row.project_code,
                                vendor_name=row.vendor_name, invoice_date=row.invoice_date, invoice_amount=row.total_amount)
                if not result.archived:
                    raise InvoiceReadError("保管状態が変わった請求があります。履歴は更新していません。")
                if not result.lines:
                    empty_ids.append(row.external_id)
                    continue
                snapshots.append(ArchivedInvoiceSnapshot(
                    row.external_id, row.project_code, row.project_name, row.vendor_name,
                    row.invoice_date, row.total_amount, "archived", tuple(
                        ArchivedAllocationSnapshot(line.code, line.name, line.amount_excluded,
                                                   line.tax_rate, line.tax_amount, line.amount_included)
                        for line in result.lines),
                ))
        # One complete scan replaces availability atomically; failed scans publish nothing.
        replace_active_archived_snapshots(snapshots)
        return f"保管済み{len(rows)}件を確認。詳細取得{len(pending)}件 / 確認済み再利用{reused}件 / 今回取得分の査定なし{len(empty_ids)}件。\n保存済み査定だけが修正された場合は「全件を再検証」で反映してください。"
    except InvoiceReadError:
        raise
    except (ValueError, AssertionError):
        raise InvoiceReadError("保管済み履歴の内容を確認できません。履歴は更新していません。") from None
    finally:
        _sync_lock.release()
