from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ArchiveMetadata:
    filename: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class Compatibility:
    updater: int
    schema: int
    runtime_fingerprint: str


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    schema: int
    product: str
    channel: str
    version: str
    sequence: int
    published_at: datetime
    expires_at: datetime
    notes: str
    archive: ArchiveMetadata
    compatibility: Compatibility
    key_id: str
    payload_b64: str
    signature_b64: str
    base_url: str


@dataclass(frozen=True, slots=True)
class StagedRelease:
    version: str
    sequence: int
    release_dir: str
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class ActivationResult:
    activated: bool
    version: str | None
    rolled_back: bool
    backup_path: str | None
