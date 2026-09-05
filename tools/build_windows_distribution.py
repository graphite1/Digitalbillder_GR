"""Build a signed, self-contained Windows x64 portable distribution.

The portable bundle keeps the fixed launcher and writable data directory at its
root.  Application code comes only from an already signed code-release ZIP;
the development checkout is never copied into the bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.build_release import DEFAULT_KEY_ID, load_or_create_signing_key
from tools.build_windows_setup import compile_launcher
from updater import DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS, get_runtime_fingerprint
from updater.archive import extract_validated_archive, validate_archive, verify_extracted_release
from updater.security import b64url_encode, verify_release_envelope

PRODUCT = "Digitalbuilder_GR"
KIND = "windows-portable"
PLATFORM = "windows-x64"
PART_SIZE = 8 * 1024 * 1024
MAX_DISTRIBUTION_SIZE = 2 * 1024 * 1024 * 1024
DEFAULT_PYTHON_ROOT = Path.home() / "AppData/Local/Python/pythoncore-3.14-64"
DEFAULT_PLAYWRIGHT_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ms-playwright"
DEFAULT_OUTPUT_DIR = ROOT.parent / "Digitalbuilder_GR-release-files/Windows bundles"
_RUNTIME_DIRECTORIES = ("DLLs", "Lib", "tcl")
_RUNTIME_FILES = (
    "LICENSE.txt",
    "python.exe",
    "pythonw.exe",
    "python3.dll",
    "python314.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
_PLAYWRIGHT_COMPONENTS = {
    "chromium-headless-shell": "chromium_headless_shell",
    "ffmpeg": "ffmpeg",
    "winldd": "winldd",
}
_BROWSER_MODES = ("full", "edge-or-download")
_SKIP_NAMES = {"__pycache__", ".git", ".hg", ".svn"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".egg-link"}
_STDLIB_RUNTIME_EXCLUDED_ROOTS = {"ensurepip", "test"}
_TEST_DIRECTORY_NAMES = {"idle_test", "test", "tests"}
_PRIVATE_PATH_TEXT = {
    str(Path.home()),
    str(Path.home()).replace("\\", "/"),
}
_PRIVATE_PATH_MARKERS = tuple(
    marker.lower().encode(encoding)
    for marker in sorted(_PRIVATE_PATH_TEXT)
    for encoding in ("utf-8", "utf-16-le")
)
_VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source(path: Path, label: str, *, directory: bool = False) -> Path:
    resolved = path.expanduser().resolve()
    present = resolved.is_dir() if directory else resolved.is_file()
    if not present or resolved.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(resolved)):
        raise SystemExit(f"{label}が見つからないか、リンクになっています: {resolved}")
    return resolved


def _skip_dependency_item(
    relative: Path,
    *,
    stdlib_tree: bool = False,
    venv_site: bool = False,
) -> bool:
    if any(part in _SKIP_NAMES for part in relative.parts):
        return True
    if relative.suffix.casefold() in _SKIP_SUFFIXES:
        return True
    if relative.name.casefold() == "direct_url.json":
        return True
    if relative.parts:
        root_name = relative.parts[0].casefold()
        if stdlib_tree:
            if root_name == "site-packages" or root_name in _STDLIB_RUNTIME_EXCLUDED_ROOTS:
                return True
            if any(part.casefold() in _TEST_DIRECTORY_NAMES for part in relative.parts):
                return True
        if venv_site and (
            root_name == "pip"
            or (root_name.startswith("pip-") and root_name.endswith(".dist-info"))
        ):
            return True
    return False


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    stdlib_tree: bool = False,
    venv_site: bool = False,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for current, directory_names, file_names in os.walk(source):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        kept_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            relative = relative_dir / name
            child = current_path / name
            if _skip_dependency_item(relative, stdlib_tree=stdlib_tree, venv_site=venv_site):
                continue
            if child.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(child)):
                raise SystemExit(f"配布元ツリー内にリンクがあります: {child}")
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names, key=str.casefold):
            relative = relative_dir / name
            if _skip_dependency_item(relative, stdlib_tree=stdlib_tree, venv_site=venv_site):
                continue
            source_file = current_path / name
            if source_file.is_symlink():
                raise SystemExit(f"配布元ツリー内にリンクがあります: {source_file}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)


def _copy_runtime(python_root: Path, runtime_root: Path) -> str:
    for name in _RUNTIME_FILES:
        shutil.copy2(_safe_source(python_root / name, f"Python runtime {name}"), runtime_root / name)
    for name in _RUNTIME_DIRECTORIES:
        source = _safe_source(python_root / name, f"Python runtime {name}", directory=True)
        _copy_tree(source, runtime_root / name, stdlib_tree=name == "Lib")
    completed = subprocess.run(
        [str(runtime_root / "python.exe"), "-I", "-B", "-X", "utf8", "-c", "import platform; print(platform.python_version())"],
        cwd=runtime_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"コピーしたPython runtimeを起動できません: {completed.stderr.strip()}")
    version = completed.stdout.strip()
    if version != "3.14.3":
        raise SystemExit(f"Python runtimeは3.14.3が必要です（検出: {version}）。")
    return version


def _audit_pth(site_packages: Path) -> None:
    for path in sorted(site_packages.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.casefold() == ".egg-link":
            raise SystemExit(f"editable dependencyは配布できません: {path.name}")
        if path.suffix.casefold() != ".pth":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise SystemExit(f".pthを監査できません: {path.name}") from exc
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith("#") or entry.startswith("import "):
                continue
            if Path(entry).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", entry):
                raise SystemExit(f".pthに絶対パスがあります: {path.name}")
            if entry.startswith(("..", "~")):
                raise SystemExit(f".pthがsite-packages外を参照します: {path.name}")


def _playwright_revisions(site_packages: Path) -> dict[str, str]:
    metadata = site_packages / "playwright/driver/package/browsers.json"
    try:
        document = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Playwright browsers.jsonを読めません: {metadata}") from exc
    revisions = {
        item.get("name"): str(item.get("revision"))
        for item in document.get("browsers", [])
        if isinstance(item, dict)
    }
    required = {"chromium", *_PLAYWRIGHT_COMPONENTS}
    if not required.issubset(revisions):
        raise SystemExit("Playwright browsers.jsonに必要なbrowser定義がありません。")
    if revisions["chromium"] != revisions["chromium-headless-shell"]:
        raise SystemExit("ChromiumとHeadless Shellのrevisionが一致しません。")
    return revisions


def _copy_playwright(playwright_root: Path, runtime_root: Path, revisions: dict[str, str]) -> None:
    browser_root = runtime_root / "browsers"
    browser_root.mkdir(parents=True, exist_ok=True)
    for component, directory_prefix in _PLAYWRIGHT_COMPONENTS.items():
        directory_name = f"{directory_prefix}-{revisions[component]}"
        source = _safe_source(playwright_root / directory_name, f"Playwright {component}", directory=True)
        _copy_tree(source, browser_root / directory_name)


def _launcher_batch(tcl_relative: str, tk_relative: str) -> bytes:
    lines = [
        "@echo off",
        "setlocal",
        'set "APP_ROOT=%~dp0"',
        'set "DIGITALBUILDER_INSTALL_ROOT=%APP_ROOT%"',
        'set "DIGITALBUILDER_DATA_DIR=%APP_ROOT%data"',
        'set "PLAYWRIGHT_BROWSERS_PATH=%APP_ROOT%runtime\\browsers"',
        'set "PYTHONDONTWRITEBYTECODE=1"',
        'set "PYTHONNOUSERSITE=1"',
        'set "PYTHONHOME="',
        'set "PYTHONPATH="',
        'set "PYTHONSAFEPATH="',
        'set "VIRTUAL_ENV="',
        f'set "TCL_LIBRARY=%APP_ROOT%runtime\\tcl\\{tcl_relative}"',
        f'set "TK_LIBRARY=%APP_ROOT%runtime\\tcl\\{tk_relative}"',
        'if not exist "%DIGITALBUILDER_DATA_DIR%" mkdir "%DIGITALBUILDER_DATA_DIR%"',
        '"%APP_ROOT%runtime\\python.exe" -E -s -B -X utf8 "%APP_ROOT%launcher.py" %*',
        'set "APP_EXIT=%ERRORLEVEL%"',
        'endlocal & exit /b %APP_EXIT%',
        "",
    ]
    return "\r\n".join(lines).encode("ascii")


def _readme(version: str, build: int, browser_mode: str) -> bytes:
    browser_note = (
        "ブラウザー実行環境は同梱されています。"
        if browser_mode == "full"
        else "WindowsのMicrosoft Edgeを使用します。Edgeがない場合だけ、初回取得時にブラウザー実行環境をダウンロードします。"
    )
    text = f"""Digitalbuilder GR v{version} Windows 11 x64 portable (build {build})

1. ZIPをフォルダーへすべて展開してください。
2. 展開したフォルダー内の「起動.bat」をダブルクリックしてください。
3. 台帳とバックアップは、このフォルダー内のdataフォルダーへ保存されます。

Pythonのインストール、仮想環境の作成、レジストリ登録は不要です。
runtimeフォルダーやlauncher.pyを単独で移動しないでください。
{browser_note}
アプリ内更新は署名、整合性、互換性を確認してから適用されます。
"""
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def _audit_portable_tree(portable_root: Path) -> None:
    identities: dict[str, str] = {}
    for path in sorted(portable_root.rglob("*")):
        relative = path.relative_to(portable_root).as_posix()
        identity = relative.casefold().rstrip(" .")
        previous = identities.setdefault(identity, relative)
        if previous != relative:
            raise SystemExit(f"Windows上で重複する配布パスがあります: {previous} / {relative}")
        if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
            raise SystemExit(f"配布ツリー内にリンクがあります: {relative}")
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            overlap = b""
            while chunk := stream.read(1024 * 1024):
                data = overlap + chunk
                lowered = data.lower()
                if any(marker in lowered for marker in _PRIVATE_PATH_MARKERS):
                    raise SystemExit(f"配布ファイルに利用者固有の絶対パスがあります: {relative}")
                overlap = data[-512:]


def _file_manifest(
    portable_root: Path,
    *,
    version: str,
    code_sequence: int,
    build: int,
    runtime_fingerprint: str,
    python_version: str,
    code_archive: Path,
    browser_mode: str,
) -> bytes:
    files = []
    for path in sorted(portable_root.rglob("*"), key=lambda item: item.relative_to(portable_root).as_posix().casefold()):
        if path.is_file():
            files.append({
                "path": path.relative_to(portable_root).as_posix(),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            })
    document = {
        "schema": 1,
        "product": PRODUCT,
        "kind": KIND,
        "version": version,
        "code_sequence": code_sequence,
        "build": build,
        "platform": PLATFORM,
        "runtime_fingerprint": runtime_fingerprint,
        "python_version": python_version,
        "browser_mode": browser_mode,
        "source_release": {"filename": code_archive.name, "sha256": _sha256(code_archive)},
        "base_path": ".",
        "manifest_path": "portable-files.json",
        "manifest_is_self_excluded": True,
        "files": files,
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _zip_info(name: str, *, directory: bool = False) -> zipfile.ZipInfo:
    normalized = name.rstrip("/") + ("/" if directory else "")
    info = zipfile.ZipInfo(normalized, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o40777 if directory else 0o100666) << 16
    return info


def _build_zip(portable_root: Path, archive: Path) -> None:
    root_name = portable_root.name
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        bundle.writestr(_zip_info(f"{root_name}/", directory=True), b"")
        for path in sorted(portable_root.rglob("*"), key=lambda item: item.relative_to(portable_root).as_posix().casefold()):
            relative = path.relative_to(portable_root).as_posix()
            member = f"{root_name}/{relative}"
            if path.is_dir():
                bundle.writestr(_zip_info(member, directory=True), b"")
            else:
                with path.open("rb") as source, bundle.open(_zip_info(member), "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    if archive.stat().st_size > MAX_DISTRIBUTION_SIZE:
        raise SystemExit("Windows配布ZIPが2GiB上限を超えています。")


def _part_hashes(archive: Path) -> list[dict[str, int | str]]:
    result = []
    with archive.open("rb") as stream:
        number = 1
        while chunk := stream.read(PART_SIZE):
            result.append({"number": number, "sha256": hashlib.sha256(chunk).hexdigest(), "size": len(chunk)})
            number += 1
    if not result or any(part["size"] != PART_SIZE for part in result[:-1]):
        raise SystemExit("配布ZIPのパートハッシュ生成に失敗しました。")
    return result


def _run_smoke_test(
    archive: Path,
    bundle_name: str,
    runtime_fingerprint: str,
    browser_mode: str,
) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="digitalbuilder-portable-smoke-") as folder:
        extraction = Path(folder)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extraction)
        root = extraction / bundle_name
        python = root / "runtime/python.exe"
        environment = os.environ.copy()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONSAFEPATH", "VIRTUAL_ENV"):
            environment.pop(variable, None)
        environment.update(
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONNOUSERSITE="1",
            DIGITALBUILDER_INSTALL_ROOT=str(root),
            DIGITALBUILDER_DATA_DIR=str(root / "data"),
            PLAYWRIGHT_BROWSERS_PATH=str(root / "runtime/browsers"),
            TCL_LIBRARY=str(root / "runtime/tcl/tcl8.6"),
            TK_LIBRARY=str(root / "runtime/tcl/tk8.6"),
        )
        checks = {
            "runtime": (
                "import sys, tkinter; "
                "import openpyxl, pymupdf, PIL, playwright, keyring, cryptography; "
                "from tkinterdnd2 import TkinterDnD; "
                "t=TkinterDnD.Tk(); t.withdraw(); t.update_idletasks(); t.destroy(); "
                "print(sys.version.split()[0])"
            ),
            "fingerprint": (
                "import sys; sys.path.insert(0, r'" + str(root).replace("'", "''") + "'); "
                "from updater.runtime import get_runtime_fingerprint; print(get_runtime_fingerprint())"
            ),
        }
        if browser_mode == "full":
            checks["playwright"] = (
                "from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
                "page=b.new_page(); page.goto('about:blank'); print(page.url); b.close(); p.stop()"
            )
        else:
            checks["playwright"] = (
                "import sys; sys.path.insert(0, r'" + str(root).replace("'", "''") + "'); "
                "from playwright.sync_api import sync_playwright; "
                "from invoice_manager.services.browser_runtime import launch_browser; "
                "p=sync_playwright().start(); b=launch_browser(p); "
                "page=b.new_page(); page.goto('about:blank'); print(page.url); b.close(); p.stop()"
            )
        results: dict[str, str] = {}
        for name, code in checks.items():
            completed = subprocess.run(
                [str(python), "-I", "-B", "-X", "utf8", "-c", code],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
            if completed.returncode != 0:
                raise SystemExit(f"portable smoke test ({name}) failed:\n{completed.stderr.strip()}")
            results[name] = completed.stdout.strip()
        if results["fingerprint"] != runtime_fingerprint:
            raise SystemExit(
                f"portable runtime fingerprintが一致しません: {results['fingerprint']} != {runtime_fingerprint}"
            )
        completed = subprocess.run(
            [str(root / "Digitalbuilder GR.exe"), "--init-db"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0 or not (root / "data/app.db").is_file():
            raise SystemExit(f"portable smoke test (launcher) failed:\n{completed.stderr.strip()}")
        results["launcher_exe"] = "init-db"
        return results


def build_distribution(args: argparse.Namespace) -> tuple[Path, Path, Path, dict[str, str]]:
    if sys.platform != "win32" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise SystemExit("Windows x64上で実行してください。")
    if args.distribution_sequence <= 0 or args.build <= 0:
        raise SystemExit("distribution-sequenceとbuildは1以上で指定してください。")
    if not 1 <= args.expires_days <= 90:
        raise SystemExit("expires-daysは1～90で指定してください。")

    code_archive = _safe_source(args.release_zip, "署名済みコードZIP")
    code_manifest = _safe_source(args.release_manifest, "署名済みコードmanifest")
    launcher = _safe_source(ROOT / "launcher.py", "固定launcher.py")
    python_root = _safe_source(args.python_root, "CPython root", directory=True)
    venv_site = _safe_source(args.venv / "Lib/site-packages", ".venv site-packages", directory=True)
    playwright_root = (
        _safe_source(args.playwright_root, "Playwright browser cache", directory=True)
        if args.browser_mode == "full"
        else None
    )

    runtime_fingerprint = get_runtime_fingerprint()
    release = verify_release_envelope(
        code_manifest.read_bytes(),
        TRUSTED_PUBLIC_KEYS,
        base_url=DEFAULT_UPDATE_BASE_URL,
        runtime_fingerprint=runtime_fingerprint,
    )
    if release.archive.filename != code_archive.name:
        raise SystemExit("コードZIP名が署名manifestと一致しません。")
    if release.archive.size != code_archive.stat().st_size or release.archive.sha256 != _sha256(code_archive):
        raise SystemExit("コードZIPのサイズまたはSHA-256が署名manifestと一致しません。")
    if args.version and args.version != release.version:
        raise SystemExit("指定versionが署名済みコードreleaseと一致しません。")
    if args.code_sequence is not None and args.code_sequence != release.sequence:
        raise SystemExit("指定code-sequenceが署名済みコードreleaseと一致しません。")
    validate_archive(code_archive)

    version = release.version
    code_sequence = release.sequence
    if not _VERSION_PATTERN.fullmatch(version):
        raise SystemExit("署名済みコードreleaseのversionが不正です。")
    bundle_name = f"{PRODUCT}-{version}-windows-x64-r{args.build}"
    archive_name = f"{bundle_name}.zip"
    output_root = args.output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_directory = output_root / bundle_name
    if final_directory.exists():
        raise SystemExit(f"既存の版・buildは上書きしません: {final_directory}")

    revisions = _playwright_revisions(venv_site) if args.browser_mode == "full" else None
    _audit_pth(venv_site)
    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle_name}-", dir=output_root))
    try:
        portable_root = temporary / "portable" / bundle_name
        portable_root.parent.mkdir(parents=True)
        extract_validated_archive(code_archive, portable_root)
        verify_extracted_release(code_archive, portable_root)
        shutil.copy2(launcher, portable_root / "launcher.py")
        compile_launcher(portable_root / "Digitalbuilder GR.exe")
        (portable_root / "data").mkdir()

        runtime_root = portable_root / "runtime"
        runtime_root.mkdir()
        python_version = _copy_runtime(python_root, runtime_root)
        _copy_tree(venv_site, runtime_root / "Lib/site-packages", venv_site=True)
        if args.browser_mode == "full":
            assert playwright_root is not None and revisions is not None
            _copy_playwright(playwright_root, runtime_root, revisions)
        else:
            (runtime_root / "browsers").mkdir()

        _write_bytes(portable_root / "起動.bat", _launcher_batch("tcl8.6", "tk8.6"))
        _write_bytes(
            portable_root / "はじめにお読みください.txt",
            _readme(version, args.build, args.browser_mode),
        )
        files_manifest = _file_manifest(
            portable_root,
            version=version,
            code_sequence=code_sequence,
            build=args.build,
            runtime_fingerprint=runtime_fingerprint,
            python_version=python_version,
            code_archive=code_archive,
            browser_mode=args.browser_mode,
        )
        _write_bytes(portable_root / "portable-files.json", files_manifest)
        _audit_portable_tree(portable_root)

        artifacts = temporary / "artifacts"
        artifacts.mkdir()
        archive = artifacts / archive_name
        _build_zip(portable_root, archive)
        smoke = _run_smoke_test(archive, bundle_name, runtime_fingerprint, args.browser_mode)

        published_at = datetime.now(timezone.utc)
        payload = {
            "schema": 1,
            "product": PRODUCT,
            "kind": KIND,
            "sequence": args.distribution_sequence,
            "version": version,
            "build": args.build,
            "platform": PLATFORM,
            "code_sequence": code_sequence,
            "code_sha256": release.archive.sha256,
            "runtime_fingerprint": runtime_fingerprint,
            "published_at": published_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "expires_at": (published_at + timedelta(days=args.expires_days)).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "archive": {
                "filename": archive.name,
                "sha256": _sha256(archive),
                "size": archive.stat().st_size,
            },
            "parts": _part_hashes(archive),
        }
        payload_bytes = _canonical(payload)
        private_key, public_key = load_or_create_signing_key(args.key_id)
        if TRUSTED_PUBLIC_KEYS.get(args.key_id) != public_key:
            raise SystemExit("Windows Vaultの署名鍵がアプリ組込み公開鍵と一致しません。")
        signature = private_key.sign(payload_bytes)
        private_key.public_key().verify(signature, payload_bytes)
        envelope = {
            "payload": b64url_encode(payload_bytes),
            "signature": b64url_encode(signature),
            "key_id": args.key_id,
        }
        manifest = artifacts / f"{bundle_name}.manifest.json"
        _write_bytes(manifest, _canonical(envelope))
        file_list = artifacts / f"{bundle_name}.files.json"
        _write_bytes(file_list, files_manifest)

        artifacts.rename(final_directory)
        return final_directory / archive.name, final_directory / manifest.name, final_directory / file_list.name, smoke
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自己完結型Windows 11 x64 portable配布ZIPを作成します")
    parser.add_argument("--release-zip", type=Path, required=True, help="署名済みコードrelease ZIP")
    parser.add_argument("--release-manifest", type=Path, required=True, help="署名済みコードrelease manifest")
    parser.add_argument("--version", help="コードreleaseのversionを明示確認する場合に指定")
    parser.add_argument("--code-sequence", type=int, help="コードreleaseのsequenceを明示確認する場合に指定")
    parser.add_argument("--distribution-sequence", type=int, default=1)
    parser.add_argument("--build", type=int, default=1)
    parser.add_argument("--python-root", type=Path, default=DEFAULT_PYTHON_ROOT)
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv")
    parser.add_argument("--playwright-root", type=Path, default=DEFAULT_PLAYWRIGHT_ROOT)
    parser.add_argument("--browser-mode", choices=_BROWSER_MODES, default="full")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID)
    parser.add_argument("--expires-days", type=int, default=30)
    return parser.parse_args()


def main() -> None:
    archive, manifest, files, smoke = build_distribution(_arguments())
    print(f"archive={archive}")
    print(f"manifest={manifest}")
    print(f"files={files}")
    print(f"smoke_runtime={smoke['runtime']}")
    print(f"smoke_fingerprint={smoke['fingerprint']}")
    print(f"smoke_playwright={smoke['playwright']}")
    print(f"smoke_launcher_exe={smoke['launcher_exe']}")


if __name__ == "__main__":
    main()
