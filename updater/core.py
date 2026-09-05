from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from updater.archive import extract_validated_archive, verify_extracted_release
from updater.config import MAX_ARCHIVE_BYTES
from updater.errors import ActivationError, DownloadError, ManifestError, UpdateError
from updater.locking import update_lock
from updater.models import ActivationResult, ReleaseManifest, StagedRelease
from updater.runtime import get_runtime_fingerprint
from updater.security import validate_update_origin, verify_release_envelope


ProgressCallback = Callable[[str], None]
HealthcheckCallback = Callable[[Path, str], bool]
FaultInjector = Callable[[str], None]


def _request_bytes(url: str, *, limit: int, opener=None) -> bytes:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Digitalbuilder-GR-Updater/1"})
    open_request = opener or urlopen
    try:
        response = open_request(request, timeout=30)
        with response:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            if final_url != url:
                raise DownloadError("更新URLのリダイレクトは禁止されています。")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise DownloadError("更新データがサイズ上限を超えています。")
            data = response.read(limit + 1)
    except HTTPError:
        raise
    except UpdateError:
        raise
    except Exception as exc:
        raise DownloadError("更新サイトからデータを取得できません。") from exc
    if len(data) > limit:
        raise DownloadError("更新データがサイズ上限を超えています。")
    return data


def check_for_update(
    base_url: str,
    trusted_keys: Mapping[str, str],
    *,
    current_sequence: int,
    runtime_fingerprint: str | None = None,
    now: datetime | None = None,
    opener=None,
) -> ReleaseManifest | None:
    origin = validate_update_origin(base_url)
    try:
        envelope = _request_bytes(origin + "/api/releases/latest", limit=64 * 1024, opener=opener)
    except HTTPError as exc:
        try:
            if exc.code == 404:
                content_type = exc.headers.get("Content-Type", "")
                body = exc.read(1025)
                if (
                    len(body) <= 1024
                    and "application/json" in content_type.casefold()
                    and json.loads(body.decode("utf-8")) == {"error": "no_release"}
                ):
                    return None
        except Exception:
            pass
        finally:
            exc.close()
        raise DownloadError("更新情報を取得できません。") from exc
    manifest = verify_release_envelope(
        envelope,
        trusted_keys,
        base_url=origin,
        runtime_fingerprint=runtime_fingerprint or get_runtime_fingerprint(),
        now=now,
    )
    if manifest.sequence <= int(current_sequence):
        return None
    return manifest


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{secrets.token_hex(6)}.tmp")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _envelope_dict(manifest: ReleaseManifest) -> dict[str, str]:
    return {"payload": manifest.payload_b64, "signature": manifest.signature_b64, "key_id": manifest.key_id}


def _release_relative(manifest: ReleaseManifest) -> str:
    return f"releases/{manifest.sequence}-{manifest.version}"


def _validate_version_file(release_dir: Path, manifest: ReleaseManifest) -> None:
    path = release_dir / "version.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("配布物のversion.jsonを確認できません。") from exc
    expected = {
        "product": "Digitalbuilder_GR",
        "version": manifest.version,
        "sequence": manifest.sequence,
        "schema": manifest.compatibility.schema,
        "runtime_fingerprint": manifest.compatibility.runtime_fingerprint,
    }
    if value != expected:
        raise ManifestError("配布物のversion.jsonが署名manifestと一致しません。")


def stage_update(
    manifest: ReleaseManifest,
    install_root: str | Path,
    *,
    opener=None,
    progress: ProgressCallback | None = None,
) -> StagedRelease:
    notify = progress or (lambda _message: None)
    root = Path(install_root).resolve()
    updates = root / ".updates"
    releases = updates / "releases"
    packages = updates / "packages"
    release_relative = _release_relative(manifest)
    release_dir = updates / release_relative
    download_url = f"{manifest.base_url}/api/releases/{manifest.sequence}/download"
    with update_lock(root):
        updates.mkdir(parents=True, exist_ok=True)
        releases.mkdir(parents=True, exist_ok=True)
        packages.mkdir(parents=True, exist_ok=True)
        package_path = packages / f"{manifest.archive.sha256}.zip"
        if release_dir.exists():
            _validate_version_file(release_dir, manifest)
            if not package_path.is_file():
                raise DownloadError("検証用の署名ZIPが見つかりません。更新を再取得してください。")
            if package_path.stat().st_size != manifest.archive.size:
                raise DownloadError("検証用の署名ZIPサイズが一致しません。")
            if hashlib.sha256(package_path.read_bytes()).hexdigest() != manifest.archive.sha256:
                raise DownloadError("検証用の署名ZIPが改変されています。")
            verify_extracted_release(package_path, release_dir)
        else:
            notify("更新ファイルを取得しています…")
            try:
                archive_bytes = _request_bytes(download_url, limit=MAX_ARCHIVE_BYTES, opener=opener)
            except HTTPError as exc:
                exc.close()
                raise DownloadError("更新ZIPを取得できません。") from exc
            if len(archive_bytes) != manifest.archive.size:
                raise DownloadError("更新ZIPのサイズが署名manifestと一致しません。")
            digest = hashlib.sha256(archive_bytes).hexdigest()
            if digest != manifest.archive.sha256:
                raise DownloadError("更新ZIPのハッシュが署名manifestと一致しません。")
            fd, archive_name = tempfile.mkstemp(prefix="release-", suffix=".zip", dir=packages)
            os.close(fd)
            archive_path = Path(archive_name)
            temporary_release = releases / f".{manifest.sequence}-{manifest.version}.{os.getpid()}.tmp"
            try:
                archive_path.write_bytes(archive_bytes)
                notify("更新ファイルを検証しています…")
                extract_validated_archive(archive_path, temporary_release)
                _validate_version_file(temporary_release, manifest)
                _atomic_json(
                    temporary_release / ".release.json",
                    {"schema": 1, "envelope": _envelope_dict(manifest), "base_url": manifest.base_url},
                )
                os.replace(archive_path, package_path)
                os.replace(temporary_release, release_dir)
            finally:
                archive_path.unlink(missing_ok=True)
                if temporary_release.exists():
                    shutil.rmtree(temporary_release, ignore_errors=True)
        staged = StagedRelease(manifest.version, manifest.sequence, release_relative, manifest.archive.sha256)
        _atomic_json(
            updates / "pending.json",
            {
                "schema": 1,
                "sequence": staged.sequence,
                "version": staged.version,
                "release": staged.release_dir,
                "archive_sha256": staged.archive_sha256,
                "envelope": _envelope_dict(manifest),
                "base_url": manifest.base_url,
                "staged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
    notify("更新準備が完了しました。次回起動時に適用します。")
    return staged


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError(f"{label}を確認できません。") from exc
    if not isinstance(value, dict):
        raise ActivationError(f"{label}が不正です。")
    return value


def _resolve_release(updates: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not re_full_release(relative):
        raise ActivationError("release参照が不正です。")
    releases = (updates / "releases").resolve()
    path = (updates / Path(relative)).resolve()
    if not path.is_relative_to(releases) or not path.is_dir():
        raise ActivationError("release参照が配布領域外または存在しません。")
    return path


def re_full_release(value: str) -> bool:
    import re

    return re.fullmatch(r"releases/[1-9]\d*-(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value) is not None


def _verify_pointer(pointer: dict, updates: Path, trusted_keys: Mapping[str, str], runtime_fingerprint: str) -> tuple[Path, ReleaseManifest]:
    required = {"schema", "sequence", "version", "release", "archive_sha256", "activated_at", "envelope", "base_url"}
    if set(pointer) != required or pointer.get("schema") != 1:
        raise ActivationError("current参照の形式が不正です。")
    release_dir = _resolve_release(updates, pointer["release"])
    envelope = json.dumps(pointer["envelope"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    manifest = verify_release_envelope(
        envelope,
        trusted_keys,
        base_url=pointer["base_url"],
        runtime_fingerprint=runtime_fingerprint,
        allow_expired=True,
    )
    if (pointer["sequence"], pointer["version"], pointer["archive_sha256"]) != (
        manifest.sequence, manifest.version, manifest.archive.sha256
    ):
        raise ActivationError("current参照が署名manifestと一致しません。")
    _validate_version_file(release_dir, manifest)
    package_path = updates / "packages" / f"{manifest.archive.sha256}.zip"
    if not package_path.is_file() or package_path.stat().st_size != manifest.archive.size:
        raise ActivationError("有効releaseの署名ZIPがありません。")
    if hashlib.sha256(package_path.read_bytes()).hexdigest() != manifest.archive.sha256:
        raise ActivationError("有効releaseの署名ZIPが改変されています。")
    verify_extracted_release(package_path, release_dir)
    return release_dir, manifest


def _pointer_identity(pointer: dict) -> tuple[object, object, object, object]:
    return (
        pointer.get("sequence"),
        pointer.get("version"),
        pointer.get("release"),
        pointer.get("archive_sha256"),
    )


def _restore_previous_pointer(current_path: Path, previous: dict | None) -> None:
    if previous is None:
        current_path.unlink(missing_ok=True)
    else:
        _atomic_json(current_path, previous)


def _recover_activation_journal(
    updates: Path,
    trusted_keys: Mapping[str, str],
    runtime_fingerprint: str,
) -> str | None:
    """Recover an activation interrupted between pointer switch and commit.

    A ``prepared`` journal never makes its candidate launchable: the previous
    verified pointer is restored and the still-pending candidate can be tried
    again.  ``health_passed`` is the durable commit record, so recovery only
    has to finish deleting pending metadata before accepting the candidate.
    """

    journal_path = updates / "activation-journal.json"
    if not journal_path.exists():
        return None
    journal = _read_json(journal_path, "更新復旧journal")
    required = {"schema", "state", "candidate", "previous", "created_at"}
    if set(journal) != required or journal.get("schema") != 1:
        raise ActivationError("更新復旧journalの形式が不正です。")
    if journal.get("state") not in {"prepared", "health_passed"}:
        raise ActivationError("更新復旧journalの状態が不正です。")
    candidate = journal.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != {
        "sequence", "version", "release", "archive_sha256"
    }:
        raise ActivationError("更新復旧journalの候補参照が不正です。")
    previous = journal.get("previous")
    if previous is not None:
        if not isinstance(previous, dict):
            raise ActivationError("更新復旧journalの直前版参照が不正です。")
        _verify_pointer(previous, updates, trusted_keys, runtime_fingerprint)

    current_path = updates / "current.json"
    current = _read_json(current_path, "current参照") if current_path.exists() else None
    current_is_candidate = current is not None and _pointer_identity(current) == _pointer_identity(candidate)
    current_is_previous = previous is not None and current == previous
    current_is_baseline = previous is None and current is None

    if journal["state"] == "prepared":
        if current_is_candidate:
            _verify_pointer(current, updates, trusted_keys, runtime_fingerprint)
        elif not (current_is_previous or current_is_baseline):
            raise ActivationError("更新復旧journalとcurrent参照が一致しません。")
        _restore_previous_pointer(current_path, previous)
        journal_path.unlink(missing_ok=True)
        return "rolled_back"

    if not current_is_candidate:
        raise ActivationError("起動確認済み更新とcurrent参照が一致しません。")
    _verify_pointer(current, updates, trusted_keys, runtime_fingerprint)
    pending_path = updates / "pending.json"
    if pending_path.exists():
        pending = _read_json(pending_path, "pending更新")
        if _pointer_identity(pending) != _pointer_identity(candidate):
            raise ActivationError("更新復旧journalとpending更新が一致しません。")
        pending_path.unlink()
    journal_path.unlink(missing_ok=True)
    return "committed"


def resolve_active_release(
    install_root: str | Path,
    trusted_keys: Mapping[str, str] | None = None,
    *,
    runtime_fingerprint: str | None = None,
) -> Path:
    root = Path(install_root).resolve()
    updates = root / ".updates"
    pointer_path = updates / "current.json"
    if (updates / "activation-journal.json").exists():
        if not trusted_keys:
            raise ActivationError("更新復旧journalの署名検証鍵がありません。")
        with update_lock(root):
            _recover_activation_journal(
                updates, trusted_keys, runtime_fingerprint or get_runtime_fingerprint()
            )
    if not pointer_path.exists():
        return root
    if not trusted_keys:
        raise ActivationError("有効releaseの署名検証鍵がありません。")
    release_dir, _ = _verify_pointer(
        _read_json(pointer_path, "current参照"), updates, trusted_keys,
        runtime_fingerprint or get_runtime_fingerprint(),
    )
    return release_dir


def backup_database(db_path: Path, backup_dir: Path, reason: str) -> Path | None:
    if not db_path.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = backup_dir / f"{db_path.stem}_{timestamp}_{reason}.db"
    temporary = destination.with_suffix(".db.tmp")
    source_connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    target_connection = sqlite3.connect(temporary)
    succeeded = False
    try:
        source_connection.backup(target_connection)
        checked = target_connection.execute("PRAGMA quick_check").fetchone()
        if checked is None or checked[0] != "ok":
            raise ActivationError("更新前DBバックアップの整合性を確認できません。")
        succeeded = True
    finally:
        target_connection.close()
        source_connection.close()
        if not succeeded:
            temporary.unlink(missing_ok=True)
    os.replace(temporary, destination)
    return destination


def activate_pending(
    install_root: str | Path,
    trusted_keys: Mapping[str, str],
    *,
    data_dir: str | Path,
    launch_healthcheck: HealthcheckCallback,
    runtime_fingerprint: str | None = None,
    _fault_inject: FaultInjector | None = None,
) -> ActivationResult:
    root = Path(install_root).resolve()
    updates = root / ".updates"
    pending_path = updates / "pending.json"
    journal_path = updates / "activation-journal.json"
    if not pending_path.exists() and not journal_path.exists():
        return ActivationResult(False, None, False, None)
    fingerprint = runtime_fingerprint or get_runtime_fingerprint()
    fault = _fault_inject or (lambda _boundary: None)
    with update_lock(root):
        recovery = _recover_activation_journal(updates, trusted_keys, fingerprint)
        if recovery == "committed":
            current = _read_json(updates / "current.json", "current参照")
            return ActivationResult(True, str(current["version"]), False, None)
        if not pending_path.exists():
            return ActivationResult(False, None, recovery == "rolled_back", None)
        pending = _read_json(pending_path, "pending更新")
        required = {"schema", "sequence", "version", "release", "archive_sha256", "envelope", "base_url", "staged_at"}
        if set(pending) != required or pending.get("schema") != 1:
            raise ActivationError("pending更新の形式が不正です。")
        envelope = json.dumps(pending["envelope"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        manifest = verify_release_envelope(
            envelope, trusted_keys, base_url=pending["base_url"], runtime_fingerprint=fingerprint
        )
        if (pending["sequence"], pending["version"], pending["archive_sha256"]) != (
            manifest.sequence, manifest.version, manifest.archive.sha256
        ):
            raise ActivationError("pending更新が署名manifestと一致しません。")
        release_dir = _resolve_release(updates, pending["release"])
        _validate_version_file(release_dir, manifest)
        package_path = updates / "packages" / f"{manifest.archive.sha256}.zip"
        if not package_path.is_file() or package_path.stat().st_size != manifest.archive.size:
            raise ActivationError("pending更新の署名ZIPがありません。")
        if hashlib.sha256(package_path.read_bytes()).hexdigest() != manifest.archive.sha256:
            raise ActivationError("pending更新の署名ZIPが改変されています。")
        verify_extracted_release(package_path, release_dir)
        current_path = updates / "current.json"
        previous = _read_json(current_path, "current参照") if current_path.exists() else None
        if previous is not None:
            _previous_path, previous_manifest = _verify_pointer(
                previous, updates, trusted_keys, fingerprint
            )
            if manifest.sequence <= previous_manifest.sequence:
                raise ActivationError("現在版より古い、または同じsequenceの更新は適用できません。")
        data_root = Path(data_dir).resolve()
        backup = backup_database(data_root / "app.db", data_root / "backups", "before_update")
        pointer = {
            "schema": 1,
            "sequence": manifest.sequence,
            "version": manifest.version,
            "release": pending["release"],
            "archive_sha256": manifest.archive.sha256,
            "activated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "envelope": _envelope_dict(manifest),
            "base_url": manifest.base_url,
        }
        journal = {
            "schema": 1,
            "state": "prepared",
            "candidate": {
                "sequence": manifest.sequence,
                "version": manifest.version,
                "release": pending["release"],
                "archive_sha256": manifest.archive.sha256,
            },
            "previous": previous,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _atomic_json(journal_path, journal)
        fault("after_journal")
        _atomic_json(current_path, pointer)
        fault("after_switch")
        try:
            healthy = bool(launch_healthcheck(release_dir, manifest.version))
        except Exception:
            healthy = False
        if not healthy:
            _restore_previous_pointer(current_path, previous)
            failed_dir = updates / "failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            failure_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            os.replace(
                pending_path,
                failed_dir / f"{manifest.sequence}-{manifest.version}-{failure_stamp}.json",
            )
            journal_path.unlink(missing_ok=True)
            raise ActivationError("更新版の起動確認に失敗したため、直前版へ戻しました。")
        fault("after_health")
        journal["state"] = "health_passed"
        _atomic_json(journal_path, journal)
        fault("after_health_commit")
        pending_path.unlink(missing_ok=True)
        fault("after_pending_delete")
        journal_path.unlink(missing_ok=True)
        return ActivationResult(True, manifest.version, False, str(backup) if backup else None)
