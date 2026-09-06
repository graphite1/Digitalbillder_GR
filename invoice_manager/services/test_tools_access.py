"""Restrict test-only actions to the configured administrator account."""

from __future__ import annotations

import hashlib
import hmac


_ALLOWED_ACCOUNT_SHA256 = "262fd3c7a2200c59f16899d853ca74296fc6b4985dca3a7b20aa96d6b2094c0a"
_ACCESS_ERROR = "試験用機能は管理者アカウントの登録が必要です。"


def can_use_test_tools() -> bool:
    """Check the current registered account each time; storage failures deny access."""
    try:
        from invoice_manager.repositories import get_app_setting

        account = get_app_setting("digital_billder_sync_account").strip().casefold()
        if not account:
            return False
        digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, _ALLOWED_ACCOUNT_SHA256)
    except Exception:
        return False


def require_test_tools_access() -> None:
    if not can_use_test_tools():
        raise PermissionError(_ACCESS_ERROR)
