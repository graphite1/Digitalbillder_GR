"""Validate and bundle an explicitly supplied Microsoft x64 VC runtime.

This is a build-time helper, never an installed-app repair or system installer.
The caller must own a fresh, disposable build runtime and discard it on failure.
Supply the CRT folder extracted from an official Microsoft redistributable source;
the helper deliberately does not discover DLLs from System32, PATH or other apps.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess


CRT_FILES = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
_REPLACEABLE_FILES = frozenset(CRT_FILES[1:])
_METADATA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$paths = [Console]::In.ReadToEnd() | ConvertFrom-Json
$results = @(
    foreach ($path in $paths) {
        $signature = Get-AuthenticodeSignature -LiteralPath $path
        $version = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($path)
        $signer = ''
        $subject = ''
        if ($null -ne $signature.SignerCertificate) {
            $subject = $signature.SignerCertificate.Subject
            $signer = $signature.SignerCertificate.GetNameInfo(
                [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
        }
        [pscustomobject]@{
            filename = [System.IO.Path]::GetFileName($path)
            signature_status = $signature.Status.ToString()
            signer = $signer
            signer_subject = $subject
            version = @($version.FileMajorPart, $version.FileMinorPart,
                        $version.FileBuildPart, $version.FilePrivatePart)
        }
    }
)
ConvertTo-Json -InputObject $results -Depth 4 -Compress
"""


def _reject_links(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("VC runtime source and destination must not contain links")


def _regular_file(path: Path) -> None:
    _reject_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"VC runtime requires an unlinked regular file: {path.name}")


def _require_amd64(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError(f"Invalid VC runtime PE file: {path.name}")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        if offset < 64 or offset > path.stat().st_size - 6:
            raise ValueError(f"Invalid VC runtime PE offset: {path.name}")
        stream.seek(offset)
        pe = stream.read(6)
        if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
            raise ValueError(f"VC runtime must be PE AMD64 (x64): {path.name}")


def _read_metadata(paths: list[Path]) -> list[dict]:
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    encoded = base64.b64encode(_METADATA_SCRIPT.encode("utf-16-le")).decode("ascii")
    environment = os.environ.copy()
    # PowerShell 7 passes an incompatible module path to child Windows
    # PowerShell 5.1 processes. Use only this engine's built-in modules.
    environment["PSModulePath"] = str(powershell.parent / "Modules")
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        input=json.dumps([str(path) for path in paths]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ValueError("Could not verify VC runtime Microsoft Authenticode signatures")
    try:
        metadata = json.loads(result.stdout.lstrip("\ufeff"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid VC runtime signature metadata") from exc
    if not isinstance(metadata, list):
        raise ValueError("Invalid VC runtime signature metadata")
    return metadata


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_app_local_crt(source_dir: Path, runtime_root: Path) -> dict:
    """Copy a verified coherent CRT set into a fresh disposable build runtime.

    Only the two VC DLLs bundled by Python may already exist. They are replaced
    together with the matching msvcp140.dll, inside this app only. Existing apps
    must never be supplied as runtime_root. All validation precedes any copy.
    """
    source_dir = Path(os.path.abspath(source_dir))
    runtime_root = Path(os.path.abspath(runtime_root))
    for directory in (source_dir, runtime_root):
        _reject_links(directory)
        if not directory.is_dir():
            raise ValueError("VC runtime source and destination must be existing directories")
    if source_dir == runtime_root or source_dir.is_relative_to(runtime_root) or runtime_root.is_relative_to(source_dir):
        raise ValueError("VC runtime source and build destination must be separate")

    sources = [source_dir / name for name in CRT_FILES]
    for source in sources:
        _regular_file(source)
        _require_amd64(source)
        target = runtime_root / source.name
        if target.exists() or target.is_symlink():
            if source.name not in _REPLACEABLE_FILES:
                raise ValueError(f"Refusing to overwrite existing VC runtime: {source.name}")
            _regular_file(target)

    hashes = {source.name: _sha256(source) for source in sources}
    metadata = _read_metadata(sources)
    if len(metadata) != len(CRT_FILES):
        raise ValueError("Incomplete VC runtime signature metadata")
    versions = set()
    verified = []
    for source, item in zip(sources, metadata, strict=True):
        if not isinstance(item, dict) or item.get("filename") != source.name:
            raise ValueError("VC runtime signature metadata does not match input files")
        allowed_signers = {"Microsoft Corporation", "Microsoft Windows Software Compatibility Publisher"}
        subject = item.get("signer_subject", "")
        microsoft_org = isinstance(subject, str) and re.search(r"(?:^|,\s*)O=Microsoft Corporation(?:,|$)", subject)
        if item.get("signature_status") != "Valid" or item.get("signer") not in allowed_signers or not microsoft_org:
            raise ValueError(f"VC runtime requires a valid Microsoft Corporation signature: {source.name}")
        version = item.get("version")
        if not isinstance(version, list) or len(version) != 4 or any(type(v) is not int or v < 0 for v in version) or version[0] != 14:
            raise ValueError(f"VC runtime must have a valid version 14: {source.name}")
        versions.add(tuple(version))
        verified.append({"filename": source.name, "sha256": hashes[source.name],
                         "version": ".".join(map(str, version)), "signer": item["signer"]})
    if len(versions) != 1:
        raise ValueError("VC runtime DLL versions must match")
    for source in sources:
        if _sha256(source) != hashes[source.name]:
            raise ValueError(f"VC runtime source changed during validation: {source.name}")
    for source in sources:
        target = runtime_root / source.name
        shutil.copy2(source, target)
        if _sha256(target) != hashes[source.name]:
            raise ValueError(f"VC runtime copy verification failed: {source.name}")
    return {"architecture": "amd64", "placement": "runtime", "files": verified}
