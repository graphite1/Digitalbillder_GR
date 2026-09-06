from __future__ import annotations

import hashlib
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from updater.config import MAX_ARCHIVE_FILES, MAX_EXTRACTED_BYTES
from updater.errors import ArchiveError


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
ALLOWED_ASSETS = frozenset({"assets/DigitalBuileder_GR.ico", "assets/DigitalBuileder_GR.png"})


def release_member_allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if name in {"app.py", "version.json"}:
        return True
    if len(path.parts) >= 2 and path.parts[0] in {"invoice_manager", "updater"}:
        return path.suffix == ".py"
    if name in ALLOWED_ASSETS:
        return True
    return False


def _validated_name(info: zipfile.ZipInfo) -> str | None:
    raw = info.filename
    if "\x00" in raw or "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ArchiveError(f"ZIP内パスが不正です: {raw!r}")
    path = PurePosixPath(raw)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveError(f"ZIP内パスが不正です: {raw!r}")
    for part in path.parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ArchiveError(f"Windowsで安全でないパスです: {raw!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED:
            raise ArchiveError(f"Windows予約名を含むパスです: {raw!r}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
        raise ArchiveError(f"リンクまたは特殊ファイルは禁止です: {raw!r}")
    if info.flag_bits & 0x1:
        raise ArchiveError("暗号化ZIPは使用できません。")
    if info.is_dir():
        raise ArchiveError(f"明示的なディレクトリエントリは禁止です: {raw}")
    normalized = path.as_posix()
    if not release_member_allowed(normalized):
        raise ArchiveError(f"配布allowlist外のファイルです: {normalized}")
    return normalized


def validate_archive(path: Path) -> tuple[tuple[zipfile.ZipInfo, str], ...]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError("更新ZIPを開けません。") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise ArchiveError("ZIP内ファイル数が上限を超えています。")
        total = 0
        seen = set()
        result = []
        for info in infos:
            name = _validated_name(info)
            if name is None:
                continue
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise ArchiveError("ZIP展開サイズが上限を超えています。")
            windows_identity = "/".join(part.rstrip(" .").casefold() for part in PurePosixPath(name).parts)
            if windows_identity in seen:
                raise ArchiveError(f"ZIP内パスが重複しています: {name}")
            seen.add(windows_identity)
            result.append((info, name))
        required = {"app.py", "version.json", "updater/__init__.py"}
        missing = required - {name for _, name in result}
        if missing:
            raise ArchiveError(f"必須ファイルがありません: {', '.join(sorted(missing))}")
        return tuple(result)


def extract_validated_archive(path: Path, destination: Path) -> None:
    entries = validate_archive(path)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(path) as archive:
            root = destination.resolve()
            for info, name in entries:
                target = (destination / PurePosixPath(name)).resolve()
                if not target.is_relative_to(root):
                    raise ArchiveError("ZIP展開先が配布領域外です。")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def verify_extracted_release(path: Path, release_dir: Path) -> None:
    entries = validate_archive(path)
    if any(item.is_symlink() for item in release_dir.rglob("*")):
        raise ArchiveError("展開済みreleaseにリンクが含まれています。")
    expected = {name for _, name in entries}
    actual = set()
    for item in release_dir.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(release_dir).as_posix()
        if relative != ".release.json":
            actual.add(relative)
    if actual != expected:
        raise ArchiveError("展開済みreleaseのファイル構成が署名ZIPと一致しません。")
    with zipfile.ZipFile(path) as archive:
        for info, name in entries:
            zip_digest = hashlib.sha256()
            with archive.open(info) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    zip_digest.update(chunk)
            disk_digest = hashlib.sha256()
            with (release_dir / PurePosixPath(name)).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    disk_digest.update(chunk)
            if zip_digest.digest() != disk_digest.digest():
                raise ArchiveError(f"展開済みreleaseが署名ZIPと一致しません: {name}")
