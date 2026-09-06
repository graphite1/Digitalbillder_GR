"""Public update-channel configuration.  No credential belongs in this file."""

DEFAULT_UPDATE_BASE_URL = "https://digitalbuilder-gr-updates.rinntyu2000.chatgpt.site"

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
