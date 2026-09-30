from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from invoice_manager.services.digital_billder_download import DownloadError, export_session


class ExportSessionTests(unittest.TestCase):
    def test_missing_login_settings_is_a_safe_download_error(self):
        with patch(
            "invoice_manager.services.digital_billder_download.load_credentials",
            side_effect=ValueError("ログイン設定からメールアドレスとパスワードを保存してください。"),
        ):
            with self.assertRaisesRegex(DownloadError, "ログイン設定からメールアドレス"):
                with export_session(lambda _message: None):
                    self.fail("ログイン設定がない場合はセッションを開かない")

    def test_archived_wait_applies_to_login_and_list_without_changing_normal_defaults(self):
        from invoice_manager.services import digital_billder_download as download

        for chosen, expected, assertion_expected in ((None, 30_000, 5000), (120_000, 120_000, 120_000)):
            with self.subTest(chosen=chosen):
                browser = MagicMock()
                page = browser.new_context.return_value.new_page.return_value
                expectation = MagicMock()
                with patch.object(download, "load_credentials", return_value=("fictional", "fictional")), patch(
                    "playwright.sync_api.sync_playwright", return_value=nullcontext(object()),
                ), patch.object(download, "launch_browser", return_value=browser), patch(
                    "playwright.sync_api.expect", return_value=expectation,
                ), patch.object(download, "_wait_for_search_debounce"), patch.object(download, "wait_for_network_idle") as idle:
                    kwargs = {} if chosen is None else {"archived_only": True, "timeout_ms": chosen}
                    with download.export_session(lambda _text: None, **kwargs):
                        pass
                page.set_default_timeout.assert_called_once_with(expected)
                page.wait_for_url.assert_called_once_with(download.APPLICATIONS_URL, timeout=expected)
                idle.assert_called_once_with(page, timeout=expected)
                self.assertEqual(expectation.to_be_visible.call_args_list[0].kwargs["timeout"], assertion_expected)
                self.assertEqual(expectation.to_be_visible.call_args_list[-1].kwargs["timeout"], expected)
                self.assertTrue(all(call.kwargs["timeout"] == assertion_expected
                                    for call in expectation.to_be_checked.call_args_list))
                browser.close.assert_called_once()

    def test_reader_session_applies_selected_wait_to_page(self):
        from invoice_manager.services import digital_billder_download as download

        browser = MagicMock()
        with patch("playwright.sync_api.sync_playwright", return_value=nullcontext(object())), patch.object(
            download, "launch_browser", return_value=browser,
        ):
            with download.authenticated_reader_session({}, timeout_ms=300_000):
                pass
        browser.new_context.return_value.new_page.return_value.set_default_timeout.assert_called_once_with(300_000)
        browser.close.assert_called_once()

    def test_csv_generation_keeps_existing_minimum_or_selected_longer_wait(self):
        from pathlib import Path
        from invoice_manager.services import digital_billder_download as download

        for chosen, expected in ((None, 180_000), (60_000, 180_000), (300_000, 300_000)):
            with self.subTest(chosen=chosen):
                page = MagicMock()
                page.get_by_text.return_value.inner_text.return_value = "検索結果: 1 件"
                with patch.object(download, "_open_export_dialog", return_value=MagicMock()) as dialog, patch(
                    "invoice_manager.services.csv_reader.read_invoice_csv", return_value=([object()], [], "utf-8"),
                ):
                    download.download_csv(page, Path("fictional.csv"), timeout_ms=chosen)
                dialog.assert_called_once_with(page, "CSV全件ダウンロード", timeout_ms=chosen or 5000)
                page.expect_download.assert_called_once_with(timeout=expected)


if __name__ == "__main__":
    unittest.main()
