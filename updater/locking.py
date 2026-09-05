from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from updater.errors import UpdateBusyError


@contextmanager
def _file_lock(path: Path, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UpdateBusyError(f"{label}を別の処理が使用中です。") from exc
        else:  # pragma: no cover - release target is Windows
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateBusyError(f"{label}を別の処理が使用中です。") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def update_lock(install_root: str | Path):
    return _file_lock(Path(install_root).resolve() / ".updates" / "update.lock", "更新処理")


def application_lock(install_root: str | Path):
    return _file_lock(Path(install_root).resolve() / ".updates" / "app.lock", "アプリ")
