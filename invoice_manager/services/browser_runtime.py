"""Select or acquire the trusted Chromium runtime used by Digital Billder reads."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping

INSTALL_TIMEOUT_SECONDS = 15 * 60
LOCK_TIMEOUT_SECONDS = 15 * 60
LOCK_POLL_SECONDS = 0.25


class BrowserRuntimeError(RuntimeError):
    """A safe, user-facing browser runtime failure."""


def _missing_executable(error: BaseException) -> bool:
    message = str(error).casefold()
    return "executable doesn't exist" in message and (
        "playwright install" in message
        or "chrome-headless-shell" in message
        or "chromium" in message
    )


def _edge_executable(environment: Mapping[str, str] | None = None) -> Path | None:
    values = os.environ if environment is None else environment
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = values.get(variable, "").strip()
        if root:
            candidates.append(Path(root) / "Microsoft/Edge/Application/msedge.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _browser_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    configured = values.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = values.get("LOCALAPPDATA", "").strip()
    base = Path(local).expanduser() if local else Path.home() / "AppData/Local"
    return (base / "ms-playwright").resolve()


@contextmanager
def _browser_install_lock(
    path: Path,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Serialize browser installation across app processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while not locked:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - release target is Windows
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise BrowserRuntimeError(
                        "ブラウザー実行環境の準備が別の処理で続いています。しばらく待ってから再試行してください。"
                    ) from exc
                time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        if locked:
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


def _install_headless_shell(progress: Callable[[str], None]) -> None:
    progress("ブラウザー実行環境を初回ダウンロードしています…")
    environment = os.environ.copy()
    # Never allow an inherited debug override to disable certificate checks.
    environment.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
    command = [
        sys.executable,
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "playwright",
        "install",
        "chromium",
        "--only-shell",
    ]
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserRuntimeError(
            "ブラウザー実行環境のダウンロードが時間内に完了しませんでした。通信状態を確認して再試行してください。"
        ) from exc
    except OSError as exc:
        raise BrowserRuntimeError(
            "ブラウザー実行環境の準備を開始できませんでした。アプリフォルダーと通信状態を確認してください。"
        ) from exc
    if completed.returncode != 0:
        raise BrowserRuntimeError(
            "ブラウザー実行環境をダウンロードできませんでした。通信状態と空き容量を確認して再試行してください。"
        )
    progress("ブラウザー実行環境の準備が完了しました。")


def _launch_default(playwright):
    return playwright.chromium.launch(headless=True)


def launch_browser(playwright, progress: Callable[[str], None] = lambda _message: None):
    """Launch bundled Chromium, system Edge, or download Chromium when absent.

    Only a confirmed missing Playwright executable enters the fallback path.
    Any installed-browser or Edge launch failure is surfaced without silently
    changing browsers.
    """

    try:
        return _launch_default(playwright)
    except Exception as exc:
        if not _missing_executable(exc):
            raise BrowserRuntimeError(
                "同梱ブラウザーを起動できませんでした。端末のセキュリティ設定とアプリフォルダーを確認してください。"
            ) from exc

    if sys.platform != "win32":
        raise BrowserRuntimeError("ブラウザー実行環境を確認できませんでした。Windows 11で実行してください。")

    edge = _edge_executable()
    if edge is not None:
        progress("WindowsのMicrosoft Edgeを使用します。")
        try:
            return playwright.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            raise BrowserRuntimeError(
                "Microsoft Edgeを起動できませんでした。Edgeの更新状態と組織のポリシーを確認してください。"
            ) from exc

    browser_root = _browser_root()
    with _browser_install_lock(browser_root / ".digitalbuilder-install.lock"):
        # A different app process may have completed installation while this
        # process waited for the lock.
        try:
            return _launch_default(playwright)
        except Exception as exc:
            if not _missing_executable(exc):
                raise BrowserRuntimeError(
                    "ブラウザー実行環境を起動できませんでした。端末のセキュリティ設定を確認してください。"
                ) from exc
        _install_headless_shell(progress)
        try:
            return _launch_default(playwright)
        except Exception as exc:
            raise BrowserRuntimeError(
                "ダウンロードしたブラウザー実行環境を起動できませんでした。アプリを再起動してください。"
            ) from exc


__all__ = ["BrowserRuntimeError", "launch_browser"]
