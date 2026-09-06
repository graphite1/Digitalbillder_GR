from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from invoice_manager.services import digital_billder_credentials as credentials
from invoice_manager.ui.digital_billder_sync_window import credential_save_error


class CredentialServiceTests(unittest.TestCase):
    def test_empty_values_are_rejected_before_vault_access(self) -> None:
        with patch.object(credentials, "_vault") as vault:
            with self.assertRaisesRegex(ValueError, "メールアドレスとパスワード"):
                credentials.save_credentials(" ", "secret")
            vault.assert_not_called()

    def test_success_preserves_vault_identity_and_account_setting(self) -> None:
        vault = Mock()
        with patch.object(credentials, "_vault", return_value=vault), patch.object(credentials, "set_app_setting") as setting:
            credentials.save_credentials(" user@example.com ", "secret")
        vault.set_password.assert_called_once_with(credentials.SERVICE, "user@example.com", "secret")
        setting.assert_called_once_with(credentials.ACCOUNT_KEY, "user@example.com")

    def test_missing_dependency_is_classified_without_raw_exception(self) -> None:
        with patch.object(credentials, "_vault", side_effect=ModuleNotFoundError("keyring")):
            with self.assertRaises(credentials.CredentialDependencyError) as raised:
                credentials.save_credentials("user@example.com", "secret")
        self.assertIn("必要な部品が見つかりません", str(raised.exception))
        self.assertNotIn("keyring", str(raised.exception))

    def test_vault_failure_does_not_expose_raw_error_or_write_account_setting(self) -> None:
        with patch.object(credentials, "_vault", side_effect=OSError("password=secret")), patch.object(credentials, "set_app_setting") as setting:
            with self.assertRaises(credentials.CredentialVaultError) as raised:
                credentials.save_credentials("user@example.com", "secret")
        self.assertNotIn("password=secret", str(raised.exception))
        setting.assert_not_called()

    def test_database_setting_failure_is_separate_from_vault_failure(self) -> None:
        vault = Mock()
        with patch.object(credentials, "_vault", return_value=vault), patch.object(credentials, "set_app_setting", side_effect=OSError("db secret")):
            with self.assertRaises(credentials.CredentialSettingsError) as raised:
                credentials.save_credentials("user@example.com", "secret")
        self.assertIn("台帳へ保存できません", str(raised.exception))
        self.assertNotIn("db secret", str(raised.exception))

    def test_ui_error_mapper_keeps_known_messages_and_hides_unknown_text(self) -> None:
        known = credentials.CredentialVaultError("Windowsの資格情報マネージャーへ保存できませんでした。")
        self.assertIn("資格情報マネージャー", credential_save_error(known))
        self.assertNotIn("raw", credential_save_error(RuntimeError("raw secret")))


if __name__ == "__main__":
    unittest.main()
