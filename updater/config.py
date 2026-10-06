"""Public update-channel configuration.  No credential belongs in this file."""

LEGACY_OFFICIAL_UPDATE_BASE_URL = "https://digitalbuilder-gr-updates.rinntyu2000.chatgpt.site"
DEFAULT_UPDATE_BASE_URL = "https://gr-release-hub.rinntyu2000.chatgpt.site"


def installation_update_base_url(install_root) -> str:
    """Read the fixed installation's public channel without executing its code.

    Code-only updates replace the release config, not the fixed installation.
    Preserve a locally selected custom channel there instead of resetting it to
    the new release's default. Origin validation/migration happens at check time.
    """
    import ast
    from pathlib import Path
    from updater.errors import ManifestError

    path = Path(install_root) / "updater" / "config.py"
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("configuration too large")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "DEFAULT_UPDATE_BASE_URL" and isinstance(node.ctx, ast.Store):
                assignments.append(node)
        declarations = [node for node in tree.body if isinstance(node, ast.Assign) and
            len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and
            node.targets[0].id == "DEFAULT_UPDATE_BASE_URL"]
        if len(assignments) == len(declarations) == 1:
            value = ast.literal_eval(declarations[0].value)
            if isinstance(value, str):
                return value
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        raise ManifestError("インストール先の更新URL設定を確認できません。") from exc
    raise ManifestError("インストール先の更新URL設定を確認できません。")


def migrated_update_origin(origin: str) -> str:
    """Move only the official legacy channel after this signed code is active.

    No settings or installation files are rewritten. A failed activation leaves
    the old release's updater in charge; custom channels remain independent.
    Call only after origin validation, never on an untrusted redirect target.
    """
    if origin in {LEGACY_OFFICIAL_UPDATE_BASE_URL, LEGACY_OFFICIAL_UPDATE_BASE_URL + ":443"}:
        return DEFAULT_UPDATE_BASE_URL
    return origin

# Raw 32-byte Ed25519 public keys encoded with unpadded base64url.  The release
# build command prints the value to place here and in the update site secret.
TRUSTED_PUBLIC_KEYS: dict[str, str] = {
    "release-2026-01": "k-ETdElb3jM1-9qi2pF1FiM3ZpYPkfPZYKwitMwywG4",
}

UPDATER_PROTOCOL_VERSION = 1
APP_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_NOTES_LENGTH = 20_000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 2_000
