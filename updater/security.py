from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit

from updater.config import (
    APP_SCHEMA_VERSION,
    MAX_ARCHIVE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_NOTES_LENGTH,
    UPDATER_PROTOCOL_VERSION,
)
from updater.errors import ManifestError, SignatureBackendUnavailable
from updater.models import ArchiveMetadata, Compatibility, ReleaseManifest


_TOP_LEVEL_KEYS = {"payload", "signature", "key_id"}
_PAYLOAD_KEYS = {
    "schema", "product", "channel", "version", "sequence", "published_at",
    "expires_at", "notes", "archive", "compatibility",
}
_ARCHIVE_KEYS = {"filename", "sha256", "size"}
_COMPATIBILITY_KEYS = {"updater", "schema", "runtime_fingerprint"}
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.zip")
_SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def b64url_decode(value: object, label: str) -> bytes:
    if not isinstance(value, str) or not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ManifestError(f"{label}のbase64url形式が不正です。")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ManifestError(f"{label}を復号できません。") from exc


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _strict_object(raw: bytes, label: str) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ManifestError(f"{label}に重複キーがあります: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{label}がUTF-8 JSONではありません。") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{label}はJSON objectである必要があります。")
    return value


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ManifestError(f"{label}の項目が不正です（不足={missing}, 余剰={extra}）。")


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ManifestError(f"{label}はUTCのISO日時で指定してください。")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError(f"{label}が不正です。") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"{label}にはタイムゾーンが必要です。")
    return parsed.astimezone(timezone.utc)


def _require_int(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(f"{label}が不正です。")
    return value


def validate_update_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ManifestError("更新サイトURLは認証情報を含まないHTTPS originで指定してください。")
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{parsed.hostname.lower()}{port}"


def verify_release_envelope(
    envelope_bytes: bytes,
    trusted_keys: Mapping[str, str],
    *,
    base_url: str,
    runtime_fingerprint: str,
    now: datetime | None = None,
    allow_expired: bool = False,
) -> ReleaseManifest:
    if len(envelope_bytes) > MAX_MANIFEST_BYTES:
        raise ManifestError("リリースmanifestが64KiBを超えています。")
    envelope = _strict_object(envelope_bytes, "署名envelope")
    _exact_keys(envelope, _TOP_LEVEL_KEYS, "署名envelope")
    key_id = envelope["key_id"]
    if not isinstance(key_id, str) or key_id not in trusted_keys:
        raise ManifestError("信頼されていない署名鍵です。")
    payload_bytes = b64url_decode(envelope["payload"], "payload")
    signature = b64url_decode(envelope["signature"], "signature")
    public_key = b64url_decode(trusted_keys[key_id], "公開鍵")
    if len(public_key) != 32 or len(signature) != 64:
        raise ManifestError("Ed25519鍵または署名の長さが不正です。")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SignatureBackendUnavailable("署名検証に必要なcryptographyがありません。") from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise ManifestError("リリース署名を確認できません。") from exc

    payload = _strict_object(payload_bytes, "payload")
    _exact_keys(payload, _PAYLOAD_KEYS, "payload")
    archive = payload["archive"]
    compatibility = payload["compatibility"]
    if not isinstance(archive, dict) or not isinstance(compatibility, dict):
        raise ManifestError("archiveまたはcompatibilityが不正です。")
    _exact_keys(archive, _ARCHIVE_KEYS, "archive")
    _exact_keys(compatibility, _COMPATIBILITY_KEYS, "compatibility")

    if payload["schema"] != 1 or payload["product"] != "Digitalbuilder_GR" or payload["channel"] != "stable":
        raise ManifestError("製品・channel・manifest schemaが一致しません。")
    version = payload["version"]
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise ManifestError("versionはX.Y.Z形式で指定してください。")
    sequence = _require_int(payload["sequence"], "sequence")
    notes = payload["notes"]
    if not isinstance(notes, str) or len(notes) > MAX_NOTES_LENGTH:
        raise ManifestError("notesが不正または長すぎます。")
    filename = archive["filename"]
    sha256 = archive["sha256"]
    size = _require_int(archive["size"], "archive.size")
    if size > MAX_ARCHIVE_BYTES:
        raise ManifestError("archive.sizeが50MiB上限を超えています。")
    if not isinstance(filename, str) or not _SAFE_FILENAME.fullmatch(filename):
        raise ManifestError("archive.filenameが不正です。")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ManifestError("archive.sha256が不正です。")
    if compatibility["updater"] != UPDATER_PROTOCOL_VERSION or compatibility["schema"] != APP_SCHEMA_VERSION:
        raise ManifestError("updaterまたはDB schemaの互換性がありません。")
    if compatibility["runtime_fingerprint"] != runtime_fingerprint:
        raise ManifestError("実行環境がこのリリースと一致しません。")
    published = _parse_time(payload["published_at"], "published_at")
    expires = _parse_time(payload["expires_at"], "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if published > current + timedelta(minutes=5):
        raise ManifestError("公開日時が未来すぎます。")
    if expires <= current and not allow_expired:
        raise ManifestError("リリースmanifestの有効期限が切れています。")
    if expires <= published or expires - published > timedelta(days=90):
        raise ManifestError("リリースmanifestの有効期間が不正です。")
    origin = validate_update_origin(base_url)
    return ReleaseManifest(
        schema=1,
        product="Digitalbuilder_GR",
        channel="stable",
        version=version,
        sequence=sequence,
        published_at=published,
        expires_at=expires,
        notes=notes,
        archive=ArchiveMetadata(filename, sha256, size),
        compatibility=Compatibility(
            compatibility["updater"], compatibility["schema"], compatibility["runtime_fingerprint"]
        ),
        key_id=key_id,
        payload_b64=envelope["payload"],
        signature_b64=envelope["signature"],
        base_url=origin,
    )
