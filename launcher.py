"""Fixed bootstrap that selects a verified release while preserving one data directory."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from updater import (
    TRUSTED_PUBLIC_KEYS,
    UpdateBusyError,
    UpdateError,
    activate_pending,
    application_lock,
    get_runtime_fingerprint,
    resolve_active_release,
)


DATA_DIR_ENV = "DIGITALBUILDER_DATA_DIR"
INSTALL_ROOT_ENV = "DIGITALBUILDER_INSTALL_ROOT"
HEALTH_FILE_ENV = "DIGITALBUILDER_UPDATE_HEALTH_FILE"
HEALTH_NONCE_ENV = "DIGITALBUILDER_UPDATE_HEALTH_NONCE"
RESTART_EXIT_CODE = 75
HEALTH_TIMEOUT_SECONDS = 60


def _base_environment(install_root: Path, data_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment[INSTALL_ROOT_ENV] = str(install_root)
    environment[DATA_DIR_ENV] = str(data_dir)
    environment.pop("DIGITALBUILDER_DEVELOPMENT", None)
    return environment


def _launch_development(install_root: Path, data_dir: Path, arguments: list[str], environment: dict[str, str]) -> int:
    app_path = install_root / "app.py"
    if not (install_root / ".git").exists() or not app_path.is_file():
        print("開発起動はGit管理された開発本体でのみ使用できます。", file=sys.stderr)
        return 1
    try:
        if (data_dir / "app.db").is_file():
            from invoice_manager.services.db_backup import create_database_backup
            create_database_backup("before_development", source_path=data_dir / "app.db")
        environment["DIGITALBUILDER_DEVELOPMENT"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(app_path), *arguments],
            cwd=install_root, env=environment, check=False,
        )
        return int(completed.returncode)
    except Exception:
        print("開発起動または起動前バックアップに失敗しました。アプリは起動できませんでした。", file=sys.stderr)
        return 1


def run_release_healthcheck(
    release_dir: Path,
    expected_version: str,
    *,
    install_root: Path,
    data_dir: Path,
    python_executable: str | Path = sys.executable,
) -> bool:
    """Run the candidate's read-only health command and verify its nonce-bound marker."""
    health_dir = install_root / ".updates" / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_urlsafe(32)
    marker_path = health_dir / f"{os.getpid()}-{secrets.token_hex(8)}.json"
    environment = _base_environment(install_root, data_dir)
    environment[HEALTH_FILE_ENV] = str(marker_path)
    environment[HEALTH_NONCE_ENV] = nonce
    try:
        completed = subprocess.run(
            [str(python_executable), "-B", "-X", "utf8", str(release_dir / "app.py"), "--update-health-check"],
            cwd=release_dir,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=HEALTH_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0 or not marker_path.is_file():
            return False
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return marker == {"schema": 1, "nonce": nonce, "version": expected_version}
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        marker_path.unlink(missing_ok=True)


def launch(argv: list[str] | None = None) -> int:
    install_root = Path(__file__).resolve().parent
    configured_data = os.environ.get(DATA_DIR_ENV, "").strip()
    data_dir = Path(configured_data).expanduser().resolve() if configured_data else install_root / "data"
    arguments = list(sys.argv[1:] if argv is None else argv)
    development = "--development" in arguments
    if development:
        arguments = [argument for argument in arguments if argument != "--development"]
    environment = _base_environment(install_root, data_dir)
    fingerprint = get_runtime_fingerprint()

    try:
        with application_lock(install_root):
            if development:
                return _launch_development(install_root, data_dir, arguments, environment)
            while True:
                try:
                    activate_pending(
                        install_root,
                        TRUSTED_PUBLIC_KEYS,
                        data_dir=data_dir,
                        runtime_fingerprint=fingerprint,
                        launch_healthcheck=lambda release, version: run_release_healthcheck(
                            release,
                            version,
                            install_root=install_root,
                            data_dir=data_dir,
                        ),
                    )
                except UpdateError as exc:
                    print(str(exc), file=sys.stderr)

                try:
                    release_dir = resolve_active_release(
                        install_root,
                        TRUSTED_PUBLIC_KEYS,
                        runtime_fingerprint=fingerprint,
                    )
                except UpdateError as exc:
                    print(str(exc), file=sys.stderr)
                    release_dir = install_root

                app_path = release_dir / "app.py"
                if not app_path.is_file():
                    print("起動するアプリを確認できません。", file=sys.stderr)
                    return 1
                try:
                    completed = subprocess.run(
                        [sys.executable, "-B", "-X", "utf8", str(app_path), *arguments],
                        cwd=release_dir,
                        env=environment,
                        check=False,
                    )
                except OSError:
                    print("アプリを起動できません。", file=sys.stderr)
                    return 1
                if completed.returncode != RESTART_EXIT_CODE:
                    return int(completed.returncode)
    except UpdateBusyError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
