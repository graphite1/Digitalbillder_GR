"""Credentials stay in the Windows credential vault, never in application files."""
from __future__ import annotations

from invoice_manager.repositories import get_app_setting, set_app_setting

SERVICE = "Digitalbillder_GR/purchases.digitalbillder.com"
ACCOUNT_KEY = "digital_billder_sync_account"
DEPENDENCY_MESSAGE = (
    "資格情報機能に必要な部品が見つかりません。手動更新ZIPの起動.batを使い、"
    "これまで使っていたアプリのフォルダーを選んでください。"
    "同じエラーになる場合は、アプリの初期設定の修復が必要です。"
)


class CredentialDependencyError(RuntimeError):
    """The selected application environment lacks the credential dependency."""


class CredentialVaultError(RuntimeError):
    """The Windows credential vault rejected or could not complete the operation."""


class CredentialSettingsError(RuntimeError):
    """The account identifier could not be saved in the application database."""


def _vault():
    try:
        from keyring.backends.Windows import WinVaultKeyring
    except (ImportError, ModuleNotFoundError) as exc:
        raise CredentialDependencyError(DEPENDENCY_MESSAGE) from exc

    return WinVaultKeyring()


def save_credentials(email: str, password: str) -> None:
    email = email.strip()
    if not email or not password:
        raise ValueError("メールアドレスとパスワードを入力してください。")
    try:
        _vault().set_password(SERVICE, email, password)
    except (CredentialDependencyError, ImportError, ModuleNotFoundError) as exc:
        if isinstance(exc, CredentialDependencyError):
            raise
        raise CredentialDependencyError(DEPENDENCY_MESSAGE) from exc
    except Exception as exc:
        raise CredentialVaultError(
            "Windowsの資格情報マネージャーへ保存できませんでした。"
            "Windowsのユーザー環境を確認して、もう一度お試しください。"
        ) from exc
    try:
        set_app_setting(ACCOUNT_KEY, email)
    except Exception as exc:
        raise CredentialSettingsError(
            "メールアドレスを台帳へ保存できませんでした。台帳を閉じて、もう一度お試しください。"
        ) from exc


def load_credentials() -> tuple[str, str]:
    email = get_app_setting(ACCOUNT_KEY)
    if email:
        try:
            password = _vault().get_password(SERVICE, email)
        except (CredentialDependencyError, ImportError, ModuleNotFoundError) as exc:
            if isinstance(exc, CredentialDependencyError):
                raise
            raise CredentialDependencyError(DEPENDENCY_MESSAGE) from exc
        except Exception as exc:
            raise CredentialVaultError(
                "Windowsの資格情報マネージャーから読み込めませんでした。"
                "Windowsのユーザー環境を確認して、もう一度お試しください。"
            ) from exc
    else:
        password = None
    if not password:
        raise ValueError("ログイン設定からメールアドレスとパスワードを保存してください。")
    return email, password
