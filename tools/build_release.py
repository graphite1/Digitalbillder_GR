"""Build a deterministic, Ed25519-signed code-only update release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from updater.archive import ALLOWED_ASSETS, release_member_allowed, validate_archive
from updater.config import APP_SCHEMA_VERSION, MAX_ARCHIVE_BYTES, MAX_MANIFEST_BYTES, UPDATER_PROTOCOL_VERSION
from updater.runtime import get_runtime_fingerprint
from updater.security import b64url_decode, b64url_encode


SIGNING_SERVICE = "Digitalbuilder_GR/release-signing"
DEFAULT_KEY_ID = "release-2026-01"
_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SystemExit("cryptographyが必要です。秘密鍵を別形式へ変換せず処理を停止しました。") from exc
    return serialization, Ed25519PrivateKey


def _vault():
    try:
        from keyring.backends.Windows import WinVaultKeyring
    except ImportError as exc:
        raise SystemExit("Windows Credential Vault用keyringが必要です。") from exc
    return WinVaultKeyring()


def load_or_create_signing_key(key_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id):
        raise SystemExit("key_idは英数字・._-の64文字以内で指定してください。")
    serialization, private_type = _crypto()
    vault = _vault()
    stored = vault.get_password(SIGNING_SERVICE, key_id)
    if stored:
        raw = b64url_decode(stored, "署名秘密鍵")
        if len(raw) != 32:
            raise SystemExit("Windows Vault内の署名秘密鍵が不正です。")
        private_key = private_type.from_private_bytes(raw)
    else:
        private_key = private_type.generate()
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        vault.set_password(SIGNING_SERVICE, key_id, b64url_encode(raw))
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private_key, b64url_encode(public_raw)


def _source_files() -> list[tuple[Path, str]]:
    result = []
    for path in [ROOT / "app.py", *sorted((ROOT / "invoice_manager").rglob("*.py")), *sorted((ROOT / "updater").rglob("*.py"))]:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit(f"配布元にリンクまたは欠落があります: {path}")
        relative = path.relative_to(ROOT).as_posix()
        if not release_member_allowed(relative):
            raise SystemExit(f"allowlist外の配布元です: {relative}")
        result.append((path, relative))
    for relative in sorted(ALLOWED_ASSETS):
        path = ROOT / PurePosixPath(relative)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit(f"配布対象アイコンにリンクまたは欠落があります: {path}")
        result.append((path, relative))
    names = [name.casefold() for _, name in result]
    if len(names) != len(set(names)):
        raise SystemExit("配布元ファイル名がWindows上で重複します。")
    return result


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def _version_module(version: str, sequence: int) -> bytes:
    return (
        '"""Application release identity used by the signed updater."""\n'
        "from __future__ import annotations\n\n\n"
        f'APP_VERSION = "{version}"\n'
        f"RELEASE_SEQUENCE = {sequence}\n\n\n"
        '__all__ = ["APP_VERSION", "RELEASE_SEQUENCE"]\n'
    ).encode("utf-8")


def build_release(
    *,
    version: str,
    sequence: int,
    output_dir: Path,
    key_id: str,
    notes: str,
    published_at: datetime,
    expires_at: datetime,
    runtime_fingerprint: str,
) -> tuple[Path, Path, Path]:
    if not _VERSION.fullmatch(version) or sequence <= 0:
        raise SystemExit("versionまたはsequenceが不正です。")
    if len(notes) > 20_000:
        raise SystemExit("notesは20,000文字以下にしてください。")
    if expires_at <= published_at or expires_at - published_at > timedelta(days=90):
        raise SystemExit("有効期限は公開後90日以内にしてください。")
    private_key, public_key = load_or_create_signing_key(key_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"Digitalbuilder_GR-{version}.zip"
    version_data = _canonical({
        "product": "Digitalbuilder_GR",
        "version": version,
        "sequence": sequence,
        "schema": APP_SCHEMA_VERSION,
        "runtime_fingerprint": runtime_fingerprint,
    })
    temporary = archive.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            bundle.writestr(_zip_info("version.json"), version_data)
            for path, name in _source_files():
                content = _version_module(version, sequence) if name == "invoice_manager/version.py" else path.read_bytes()
                bundle.writestr(_zip_info(name), content)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    validate_archive(archive)
    archive_bytes = archive.read_bytes()
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        archive.unlink(missing_ok=True)
        raise SystemExit("配布ZIPが50MiB上限を超えています。")
    payload = {
        "schema": 1,
        "product": "Digitalbuilder_GR",
        "channel": "stable",
        "version": version,
        "sequence": sequence,
        "published_at": published_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "notes": notes,
        "archive": {
            "filename": archive.name,
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "size": len(archive_bytes),
        },
        "compatibility": {
            "updater": UPDATER_PROTOCOL_VERSION,
            "schema": APP_SCHEMA_VERSION,
            "runtime_fingerprint": runtime_fingerprint,
        },
    }
    payload_bytes = _canonical(payload)
    if len(payload_bytes) > MAX_MANIFEST_BYTES:
        archive.unlink(missing_ok=True)
        raise SystemExit("manifest payloadが64KiB上限を超えています。")
    envelope = {
        "payload": b64url_encode(payload_bytes),
        "signature": b64url_encode(private_key.sign(payload_bytes)),
        "key_id": key_id,
    }
    manifest_path = output_dir / f"Digitalbuilder_GR-{version}.manifest.json"
    public_path = output_dir / f"{key_id}.public.json"
    manifest_path.write_bytes(_canonical(envelope))
    public_path.write_bytes(_canonical({"key_id": key_id, "public_key": public_key}))
    return archive, manifest_path, public_path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="署名済みコード更新ZIPを作成します")
    parser.add_argument("--version")
    parser.add_argument("--sequence", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID)
    parser.add_argument("--notes", default="")
    parser.add_argument("--expires-days", type=int, default=30)
    parser.add_argument("--runtime-fingerprint", default="")
    parser.add_argument("--public-key-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.public_key_only:
        _private, public = load_or_create_signing_key(args.key_id)
        print(json.dumps({"key_id": args.key_id, "public_key": public}, separators=(",", ":")))
        return
    if not args.version or args.sequence is None or args.output_dir is None:
        raise SystemExit("release作成には--version、--sequence、--output-dirが必要です。")
    now = datetime.now(timezone.utc)
    archive, manifest, public_path = build_release(
        version=args.version,
        sequence=args.sequence,
        output_dir=args.output_dir.resolve(),
        key_id=args.key_id,
        notes=args.notes,
        published_at=now,
        expires_at=now + timedelta(days=args.expires_days),
        runtime_fingerprint=args.runtime_fingerprint or get_runtime_fingerprint(),
    )
    print(f"archive={archive}")
    print(f"manifest={manifest}")
    print(f"public_key_file={public_path}")


if __name__ == "__main__":
    main()
