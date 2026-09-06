"""UI-based, read-only exports from Digital Billder in a headless browser."""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from invoice_manager.services.browser_runtime import BrowserRuntimeError, launch_browser
from invoice_manager.services.digital_billder_credentials import load_credentials
from invoice_manager.services.operation_cancellation import check_cancelled, current_token

APPLICATIONS_URL = "https://purchases.digitalbillder.com/invoices/applications"
CSV_FORMAT = "デフォルトのフォーマット"


class DownloadError(RuntimeError):
    pass


def wait_for_network_idle(page, timeout: int = 30_000) -> None:
    """Poll only the local wait state, retaining the original overall deadline."""
    from playwright.sync_api import TimeoutError

    check_cancelled()
    if current_token() is None:
        page.wait_for_load_state("networkidle", timeout=timeout)
        return
    deadline = time.monotonic() + timeout / 1000
    while True:
        check_cancelled()
        remaining = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            page.wait_for_load_state("networkidle", timeout=min(250, remaining))
            check_cancelled()
            return
        except TimeoutError:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise


def _wait_for_search_debounce(page) -> None:
    if current_token() is None:
        page.wait_for_timeout(4000)
        return
    for _ in range(16):
        check_cancelled()
        page.wait_for_timeout(250)
    check_cancelled()


def _open_export_dialog(page, title: str):
    from playwright.sync_api import expect

    dialog = page.get_by_role("dialog", name=title, exact=True)
    for attempt in range(2):
        check_cancelled()
        page.get_by_role("button", name="ダウンロード", exact=True).click()
        check_cancelled()
        page.get_by_role("button", name=title, exact=True).click()
        try:
            expect(dialog).to_be_visible(timeout=5000)
            check_cancelled()
            return dialog
        except AssertionError:
            check_cancelled()
            if attempt:
                raise DownloadError("ダウンロード画面を開けませんでした。新着確認をやり直してください。") from None
            page.keyboard.press("Escape")


@contextmanager
def export_session(progress: Callable[[str], None], *, archived_only: bool = False):
    from playwright.sync_api import Error, TimeoutError, expect, sync_playwright

    check_cancelled()
    email, password = load_credentials()
    check_cancelled()
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, progress)
            try:
                check_cancelled()
                context = browser.new_context(accept_downloads=True, locale="ja-JP")
                check_cancelled()
                page = context.new_page()
                page.set_default_timeout(30_000)
                progress("Digital Billderにログインしています…")
                page.goto(APPLICATIONS_URL, wait_until="domcontentloaded")
                check_cancelled()
                page.locator("#signin-input-mail_address").fill(email)
                check_cancelled()
                page.locator("#signin-input-password").fill(password)
                check_cancelled()
                page.locator("#signin-button-login").click()
                try:
                    check_cancelled()
                    page.wait_for_url(APPLICATIONS_URL, timeout=30_000)
                    check_cancelled()
                    expect(page.get_by_role("radio", name="すべて", exact=True)).to_be_visible()
                except (TimeoutError, AssertionError):
                    check_cancelled()
                    raise DownloadError("ログインできません。ログイン情報、追加認証、通信状態を確認してください。") from None
                progress("保管済みの請求を検索しています…" if archived_only else "破棄済みを除く請求を検索しています…")
                # Each session starts with no saved browser profile / filters.
                all_radio = page.get_by_role("radio", name="すべて", exact=True)
                check_cancelled()
                page.locator("label").filter(has=all_radio).click()
                check_cancelled()
                expect(all_radio).to_be_checked()
                pattern = r"^保管済\s+Alt" if archived_only else r"^破棄済を除くすべて"
                active_radio = page.get_by_role("radio", name=re.compile(pattern))
                check_cancelled()
                page.locator("label").filter(has=active_radio).click()
                check_cancelled()
                expect(active_radio).to_be_checked()
                # Wait for the debounced search and rendering to finish before export.
                _wait_for_search_debounce(page)
                wait_for_network_idle(page)
                expect(page.get_by_text(re.compile(r"検索結果:\s*\d+\s*件"))).to_be_visible(timeout=30_000)
                check_cancelled()
                yield page
            finally:
                browser.close()
    except DownloadError:
        check_cancelled()
        raise
    except BrowserRuntimeError as exc:
        check_cancelled()
        raise DownloadError(str(exc)) from None
    except (Error, AssertionError):
        check_cancelled()
        # Browser exceptions can include input values / call logs. Do not expose them.
        raise DownloadError(
            "自動取得に失敗しました。通信状態や画面変更、追加認証を確認してください。"
        ) from None


def download_csv(page, destination: Path) -> Path | None:
    check_cancelled()
    text = page.get_by_text(re.compile(r"検索結果:\s*\d+\s*件")).inner_text()
    check_cancelled()
    match = re.search(r"検索結果:\s*(\d+)\s*件", text)
    if not match:
        raise DownloadError("検索結果の件数を確認できません。画面変更の可能性があります。")
    if int(match.group(1)) == 0:
        return None
    dialog = _open_export_dialog(page, "CSV全件ダウンロード")
    with page.expect_download(timeout=180_000) as pending:
        check_cancelled()
        dialog.get_by_role("button", name=CSV_FORMAT, exact=True).click()
    check_cancelled()
    pending.value.save_as(destination)
    check_cancelled()
    from invoice_manager.services.csv_reader import read_invoice_csv

    rows, errors, _ = read_invoice_csv(destination)
    check_cancelled()
    if errors or len(rows) != int(match.group(1)):
        raise DownloadError("画面の件数とCSVの内容を確認できませんでした。新着確認をやり直してください。")
    return destination


def download_zip(page, destination: Path) -> Path:
    check_cancelled()
    # Export dialogs can remain open after a completed download.
    close = page.get_by_role("dialog").get_by_role("button", name="Close", exact=True)
    if close.is_visible():
        check_cancelled()
        close.click()
    dialog = _open_export_dialog(page, "ファイル全件ダウンロード")
    with page.expect_download(timeout=300_000) as pending:
        check_cancelled()
        dialog.get_by_role("button", name="ダウンロード", exact=True).click()
    check_cancelled()
    pending.value.save_as(destination)
    check_cancelled()
    return destination


@contextmanager
def authenticated_reader_session(storage_state):
    """Use in-memory session state in a dedicated thread; never persist cookies."""
    from playwright.sync_api import Error, sync_playwright

    check_cancelled()
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            try:
                check_cancelled()
                context = browser.new_context(storage_state=storage_state, locale="ja-JP")
                check_cancelled()
                page = context.new_page()
                page.set_default_timeout(30_000)
                check_cancelled()
                yield page
            finally:
                browser.close()
    except BrowserRuntimeError as exc:
        check_cancelled()
        raise DownloadError(str(exc)) from None
    except (Error, AssertionError):
        check_cancelled()
        raise DownloadError("保管済みの詳細取得に失敗しました。通信状態・画面変更・ログイン状態を確認してください。") from None
