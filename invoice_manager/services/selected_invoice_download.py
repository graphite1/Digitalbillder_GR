"""Read-only selected exports. Never fall back to the all-files download."""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from invoice_manager.services.csv_reader import read_invoice_csv
from invoice_manager.services.digital_billder_download import (
    APPLICATIONS_URL, CSV_FORMAT, DownloadError, _open_export_dialog, wait_for_network_idle,
)
from invoice_manager.services.operation_cancellation import check_cancelled
from invoice_manager.services.zip_reader import (
    MAX_PDF_COUNT, MAX_TOTAL_PDF_SIZE_BYTES, MAX_ZIP_SIZE_BYTES, read_zip_index,
)

GRID = '.chakra-table__container'
BOXES = GRID + ' input.chakra-checkbox__input[type=checkbox]'


def _close_export(page) -> None:
    check_cancelled()
    close = page.get_by_role('dialog').get_by_role('button', name='Close', exact=True)
    if close.is_visible():
        close.click()
    check_cancelled()


def _selection(page) -> list[bool]:
    actual, expected = urlsplit(page.url), urlsplit(APPLICATIONS_URL)
    if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise DownloadError('請求一覧から移動したため、選択PDFの取得を停止しました。')
    return page.locator(BOXES).evaluate_all('els => els.map(e => e.checked)')


def _state_summary(states, offset, total, expected_count=None) -> str:
    return (f'確認済み{offset}件 / CSV全{total}件 / 表示{max(0, len(states)-1)}行 / '
            f'選択{sum(states[1:])}行 / 全選択{bool(states and states[0])}'
            + (f' / 期待{expected_count}行' if expected_count is not None else ''))


def _wait_rows(page, previous: str | None = None, *, offset=0, total=None,
               expected_count=None, progress=lambda _text: None) -> None:
    """Wait only while the expected page is incomplete; never refetch the page."""
    deadline = time.monotonic() + 30
    reported = False
    initial_ready = None
    while True:
        check_cancelled()
        states = _selection(page)
        count = len(states) - 1
        rows_match = count > 0 and (expected_count is None or count == expected_count)
        if total is not None:
            rows_match = rows_match and offset + count <= total and not any(states)
        if page.locator(GRID).count() == 1 and rows_match:
            text = page.locator(GRID).inner_text()
            if previous is None and total is not None:
                # The first page has no preceding page from which to infer its capacity.
                # Confirm consecutive identical renders before fixing the page size.
                ready = (count, text)
                if ready == initial_ready:
                    return
                initial_ready = ready
            elif previous is None or text != previous:
                return
        else:
            initial_ready = None
        if time.monotonic() >= deadline:
            detail = _state_summary(states, offset, total, expected_count)
            reason = ('行を読み取れません' if count <= 0 else
                      'チェックが残っています' if any(states) else
                      '表示行数が一致しません' if not rows_match else 'ページ切替を確認できません')
            raise DownloadError(f'請求一覧のページ更新が完了しませんでした（{reason}）。{detail}。取込は行いません。')
        if not reported:
            progress(f'請求一覧の表示完了を待っています（確認済み{offset}件）…')
            reported = True
        page.wait_for_timeout(250)


def _clear_page_selection(page, expected_rows: list[bool]) -> None:
    """Use at most two header clicks, and verify their result before pagination."""
    check_cancelled()
    states = _selection(page)
    if states[1:] != expected_rows:
        raise DownloadError('PDF取得後に選択行が変わったため停止しました。')
    if not any(states):
        return
    header = page.locator(BOXES).nth(0).locator('..')
    # With a partial selection, the first header click may select every row.
    for attempt in range(2):
        check_cancelled()
        header.click()
        deadline = time.monotonic() + 30
        while True:
            check_cancelled()
            states = _selection(page)
            if len(states) != len(expected_rows) + 1:
                raise DownloadError('選択解除中に請求一覧の行数が変わったため停止しました。')
            if not any(states):
                return
            if attempt == 0 and not all(expected_rows) and all(states):
                break
            if time.monotonic() >= deadline:
                raise DownloadError(f'請求一覧の選択を解除できませんでした（選択{sum(states[1:])}行）。')
            page.wait_for_timeout(250)
    raise DownloadError('請求一覧の選択を解除できませんでした。')


def _select_page_rows(page, expected_rows: list[bool]) -> None:
    check_cancelled()
    if all(expected_rows):
        page.locator(BOXES).nth(0).locator('..').click()
    else:
        for index, wanted in enumerate(expected_rows, start=1):
            if wanted:
                check_cancelled()
                page.locator(BOXES).nth(index).locator('..').click()
    deadline = time.monotonic() + 30
    while True:
        check_cancelled()
        states = _selection(page)
        if states[1:] == expected_rows:
            return
        if len(states) != len(expected_rows) + 1:
            raise DownloadError('請求選択中に一覧の行数が変わったため停止しました。')
        if time.monotonic() >= deadline:
            raise DownloadError(f'対象の請求を選択できませんでした（期待{sum(expected_rows)}件／選択{sum(states[1:])}件）。')
        page.wait_for_timeout(250)


def _verify_selected_csv(path: Path, expected) -> None:
    rows, errors, _ = read_invoice_csv(path)
    actual = {row.external_id: row for row in rows}
    wanted = {row.external_id: row for row in expected}
    if errors or len(actual) != len(rows) or set(actual) != set(wanted):
        raise DownloadError('Webの選択請求と取込対象が一致しません。PDFは取得せず停止しました。')
    if any(actual[key].raw_data != row.raw_data for key, row in wanted.items()):
        raise DownloadError('選択確認中に請求内容が変わりました。新着確認をやり直してください。')


def _download_page(page, path: Path, expected) -> None:
    selection = _selection(page)
    if sum(selection[1:]) != len(expected):
        raise DownloadError('請求の選択件数が一致しません。')
    dialog = _open_export_dialog(page, 'CSVダウンロード')
    with page.expect_download(timeout=180_000) as pending:
        check_cancelled()
        dialog.get_by_role('button', name=CSV_FORMAT, exact=True).click()
    check_cancelled()
    selected_csv = path.with_suffix('.csv')
    pending.value.save_as(selected_csv)
    check_cancelled()
    _verify_selected_csv(selected_csv, expected)
    _close_export(page)
    if _selection(page) != selection:
        raise DownloadError('確認後に請求の選択状態が変わったため停止しました。')
    page.get_by_role('button', name='ダウンロード', exact=True).click()
    check_cancelled()
    if _selection(page) != selection:
        raise DownloadError('確認後に請求の選択状態が変わったため停止しました。')
    # The selected-files menu starts a download directly; the all-files dialog is not used.
    with page.expect_download(timeout=300_000) as pending:
        page.get_by_role('button', name='ファイルダウンロード', exact=True).click()
    check_cancelled()
    pending.value.save_as(path)
    check_cancelled()
    if _selection(page) != selection:
        raise DownloadError('取得中に請求の選択状態が変わったため停止しました。')


def _append_pdfs(output: ZipFile, source: Path, expected_ids: set[str]) -> None:
    index = read_zip_index(source)
    if set(index.pdf_by_id) != expected_ids:
        raise DownloadError('選択PDFの請求IDが一致しないか、PDFが不足しています。取込は行いません。')
    with ZipFile(source) as archive:
        names = [item.filename for item in archive.infolist()]
        if len(names) != len(set(names)):
            raise DownloadError('取得ZIP内のファイル名が重複しています。')
        pdf_names = {item.filename for item in archive.infolist()
                     if not item.is_dir() and item.filename.lower().endswith('.pdf')}
        indexed_names = {item.zip_name for items in index.pdf_by_id.values() for item in items}
        if pdf_names != indexed_names:
            raise DownloadError('読み取れない添付PDFが含まれています。欠落を防ぐため取込は行いません。')
        new_items = [item for items in index.pdf_by_id.values() for item in items]
        if len(output.infolist()) + len(new_items) > MAX_PDF_COUNT or (
            sum(item.file_size for item in output.infolist())
            + sum(item.file_size for item in new_items) > MAX_TOTAL_PDF_SIZE_BYTES
        ):
            raise DownloadError('選択PDFの合計件数または展開サイズが上限を超えています。')
        written = set(output.namelist())
        for identifier, items in index.pdf_by_id.items():
            for item in items:
                check_cancelled()
                name = f'invoices/{identifier}/{item.original_file_name}'
                if name in written:
                    raise DownloadError('取得PDFの名前が重複しています。')
                written.add(name)
                with archive.open(item.zip_name) as src, output.open(name, 'w') as dst:
                    while block := src.read(1024 * 1024):
                        check_cancelled()
                        dst.write(block)


def download_selected_zip(page, destination: Path, rows, selected_ids: set[str], progress=lambda _text: None) -> Path:
    """CSV order is only a selection hint; the selected CSV proves each ID before PDF access."""
    check_cancelled()
    all_ids = [row.external_id for row in rows]
    if not selected_ids or len(all_ids) != len(set(all_ids)) or not selected_ids <= set(all_ids):
        raise DownloadError('選択した請求を一覧で確認できません。')
    positions = {index for index, identifier in enumerate(all_ids) if identifier in selected_ids}
    _close_export(page)
    _wait_rows(page, total=len(rows), progress=progress)
    offset = 0
    completed = 0
    page_capacity = len(_selection(page)) - 1
    with ZipFile(destination, 'x', compression=ZIP_DEFLATED) as output:
        while positions:
            check_cancelled()
            states = _selection(page)
            count = len(states) - 1
            if count != min(page_capacity, len(rows) - offset) or any(states):
                detail = _state_summary(states, offset, len(rows), min(page_capacity, len(rows)-offset))
                raise DownloadError(f'表示確認後に請求一覧の件数または選択状態が変わりました。{detail}。')
            page_positions = sorted(pos for pos in positions if offset <= pos < offset + count)
            if page_positions:
                progress(f'PDF取得 {completed}/{len(selected_ids)}件完了・次の{len(page_positions)}件を選択中…')
                expected_rows = [offset+i in page_positions for i in range(count)]
                _select_page_rows(page, expected_rows)
                expected = [rows[pos] for pos in page_positions]
                progress(f'PDF取得 {completed}/{len(selected_ids)}件完了・次の{len(expected)}件を確認／取得中…')
                part = destination.with_name(f'{destination.stem}-page-{offset}.zip')
                _download_page(page, part, expected)
                _append_pdfs(output, part, {row.external_id for row in expected})
                positions.difference_update(page_positions)
                completed += len(expected)
                progress(f'PDF取得 {completed}/{len(selected_ids)}件完了')
            if positions:
                if page_positions:
                    _clear_page_selection(page, expected_rows)
                # Capture text after clearing selection, then wait for expected new rows.
                previous = page.locator(GRID).inner_text()
                next_button = page.get_by_role('button', name='>', exact=True)
                if not next_button.is_enabled():
                    raise DownloadError('選択した請求のページが見つかりません。')
                next_button.click()
                wait_for_network_idle(page)
                offset += count
                _wait_rows(page, previous, offset=offset, total=len(rows),
                           expected_count=min(page_capacity, len(rows)-offset), progress=progress)
    check_cancelled()
    if destination.stat().st_size > MAX_ZIP_SIZE_BYTES:
        raise DownloadError('選択PDFの合計がZIPサイズ上限を超えています。')
    # Also apply the aggregate PDF count and expanded-size limits across pages.
    if set(read_zip_index(destination).pdf_by_id) != selected_ids:
        raise DownloadError('取得したPDFが選択請求と一致しません。')
    progress(f'選択した未取込請求のPDFを取得しました: {completed}件')
    return destination
