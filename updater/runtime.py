from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import sys


_RUNTIME_PACKAGES = ("openpyxl", "tkinterdnd2", "PyMuPDF", "Pillow", "playwright", "keyring", "cryptography")


def get_runtime_fingerprint() -> str:
    """Return the stable compatibility identifier used by release manifests."""

    versions = []
    for package in _RUNTIME_PACKAGES:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        versions.append(f"{package.casefold()}={version}")
    dependency_hash = hashlib.sha256("\n".join(versions).encode("utf-8")).hexdigest()[:24]
    machine = platform.machine().casefold() or "unknown"
    return (
        f"python={sys.version_info.major}.{sys.version_info.minor};"
        f"platform={sys.platform.casefold()};arch={machine};deps={dependency_hash}"
    )
