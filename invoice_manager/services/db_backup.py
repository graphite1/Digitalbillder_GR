from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from invoice_manager import db


def create_database_backup(
    reason: str,
    source_path: Path | None = None,
    backup_dir: Path | None = None,
) -> Path:
    source = Path(source_path) if source_path is not None else db.DB_PATH
    if not source.is_file():
        raise FileNotFoundError(f"バックアップ元DBが見つかりません: {source}")

    destination_dir = Path(backup_dir) if backup_dir is not None else source.parent / "backups"
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^0-9A-Za-z_-]+", "_", reason).strip("_") or "operation"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = destination_dir / f"{source.stem}_{timestamp}_{safe_reason}{source.suffix}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    source_connection = None
    backup_connection = None
    succeeded = False
    try:
        source_connection = sqlite3.connect(source)
        backup_connection = sqlite3.connect(temporary)
        source_connection.backup(backup_connection)
        succeeded = True
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()
        if not succeeded:
            temporary.unlink(missing_ok=True)
    temporary.replace(destination)
    return destination
