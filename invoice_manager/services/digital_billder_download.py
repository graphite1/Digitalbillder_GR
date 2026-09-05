"""UI-based, read-only exports from Digital Billder in a headless browser."""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from invoice_manager.services.browser_runtime import BrowserRuntimeError, launch_browser
from invoice_manager.services.digital_billder_credentials import load_credentials

APPLICATIONS_URL = "https://purchases.digitalbillder.com/invoices/applications"
CSV_FORMAT = "デフォルトのフォーマット"


class DownloadError(RuntimeError):
    pass


def _open_export_dialog(page, title: str):
    from playwright.sync_api import expect

    dialog = page.get_by_role("dialog", name=title, exact=True)
    for attempt in range(2):
        page.get_by_role("button", name="ダウンロード", exact=True).click()
        page.get_by_role("button", name=title, exact=True).click()
        try:
            expect(dialog).to_be_visible(timeout=5000)
            return dialog
        except AssertionError:
            if attempt:
                raise DownloadError("ダウンロード画面を開けませんでした。新着確認をやり直してください。") from None
            page.keyboard.press("Escape")


@contextmanager
def export_session(progress: Callable[[str], None], *, archived_only: bool = False):
    from playwright.sync_api import Error, TimeoutError, expect, sync_playwright

    email, password = load_credentials()
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, progress)
            try:
                context = browser.new_context(accept_downloads=True, locale="ja-JP")
                page = context.new_page()
                page.set_default_timeout(30_000)
                progress("Digital Billderにログインしています…")
                page.goto(APPLICATIONS_URL, wait_until="domcontentloaded")
                page.locator("#signin-input-mail_address").fill(email)
                page.locator("#signin-input-password").fill(password)
                page.locator("#signin-button-login").click()
                try:
                    page.wait_for_url(APPLICATIONS_URL, timeout=30_000)
                    expect(page.get_by_role("radio", name="すべて", exact=True)).to_be_visible()
                except (TimeoutError, AssertionError):
                    raise DownloadError("ログインできません。ログイン情報、追加認証、通信状態を確認してください。") from None
                progress("保管済みの請求を検索しています…" if archived_only else "破棄済みを除く請求を検索しています…")
                # Each session starts with no saved browser profile / filters.
                all_radio = page.get_by_role("radio", name="すべて", exact=True)
                page.locator("label").filter(has=all_radio).click()
                expect(all_radio).to_be_checked()
                pattern = r"^保管済\s+Alt" if archived_only else r"^破棄済を除くすべて"
                active_radio = page.get_by_role("radio", name=re.compile(pattern))
                page.locator("label").filter(has=active_radio).click()
                expect(active_radio).to_be_checked()
                # Wait for the debounced search and rendering to finish before export.
                page.wait_for_timeout(4000)
                page.wait_for_load_state("networkidle", timeout=30_000)
                expect(page.get_by_text(re.compile(r"検索結果:\s*\d+\s*件"))).to_be_visible(timeout=30_000)
                yield page
            finally:
                browser.close()
    except DownloadError:
        raise
    except BrowserRuntimeError as exc:
        raise DownloadError(str(exc)) from None
    except (Error, AssertionError):
        # Browser exceptions can include input values / call logs. Do not expose them.
        raise DownloadError(
            "自動取得に失敗しました。通信状態や画面変更、追加認証を確認してください。"
        ) from None


def download_csv(page, destination: Path) -> Path | None:
    text = page.get_by_text(re.compile(r"検索結果:\s*\d+\s*件")).inner_text()
    match = re.search(r"検索結果:\s*(\d+)\s*件", text)
    if not match:
        raise DownloadError("検索結果の件数を確認できません。画面変更の可能性があります。")
    if int(match.group(1)) == 0:
        return None
    dialog = _open_export_dialog(page, "CSV全件ダウンロード")
    with page.expect_download(timeout=180_000) as pending:
        dialog.get_by_role("button", name=CSV_FORMAT, exact=True).click()
    pending.value.save_as(destination)
    from invoice_manager.services.csv_reader import read_invoice_csv

    rows, errors, _ = read_invoice_csv(destination)
    if errors or len(rows) != int(match.group(1)):
        raise DownloadError("画面の件数とCSVの内容を確認できませんでした。新着確認をやり直してください。")
    return destination


def download_zip(page, destination: Path) -> Path:
    # Export dialogs can remain open after a completed download.
    close = page.get_by_role("dialog").get_by_role("button", name="Close", exact=True)
    if close.is_visible():
        close.click()
    dialog = _open_export_dialog(page, "ファイル全件ダウンロード")
    with page.expect_download(timeout=300_000) as pending:
        dialog.get_by_role("button", name="ダウンロード", exact=True).click()
    pending.value.save_as(destination)
    return destination


@contextmanager
def authenticated_reader_session(storage_state):
    """Use in-memory session state in a dedicated thread; never persist cookies."""
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            try:
                context = browser.new_context(storage_state=storage_state, locale="ja-JP")
                page = context.new_page()
                page.set_default_timeout(30_000)
                yield page
            finally:
                browser.close()
    except BrowserRuntimeError as exc:
        raise DownloadError(str(exc)) from None
    except (Error, AssertionError):
        raise DownloadError("保管済みの詳細取得に失敗しました。通信状態・画面変更・ログイン状態を確認してください。") from None
