from __future__ import annotations

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
