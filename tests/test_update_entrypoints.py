from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as application
import launcher
from updater.errors import ManifestError
from invoice_manager.ui import main_window
from invoice_manager.ui.update_window import UpdateWindow
from invoice_manager.version import APP_VERSION, RELEASE_SEQUENCE


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _minimal_database(path: Path, *, complete: bool = True) -> None:
    names = sorted(
        {"projects", "vendors", "invoices", "invoice_files", "invoice_allocations", "app_settings"}
        if complete else {"projects"}
    )
    with closing(sqlite3.connect(path)) as connection:
        for name in names:
            connection.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
        connection.commit()


class UpdateHealthTests(unittest.TestCase):
    def test_health_check_reads_existing_database_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            data_dir = Path(folder) / "fixed-data"
            data_dir.mkdir()
            database = data_dir / "app.db"
            _minimal_database(database)
            before = hashlib.sha256(database.read_bytes()).digest()
            health_dir = Path(folder) / ".updates" / "health"
            health_dir.mkdir(parents=True)
            marker = health_dir / "health.json"
            nonce = "test-nonce"
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.update(
                DIGITALBUILDER_DATA_DIR=str(data_dir),
                DIGITALBUILDER_INSTALL_ROOT=str(Path(folder)),
                DIGITALBUILDER_UPDATE_HEALTH_FILE=str(marker),
                DIGITALBUILDER_UPDATE_HEALTH_NONCE=nonce,
            )
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "app.py"), "--update-health-check"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8")),
                {"schema": 1, "nonce": nonce, "version": APP_VERSION},
            )
            self.assertEqual(hashlib.sha256(database.read_bytes()).digest(), before)

    def test_health_check_does_not_acknowledge_incomplete_database(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            data_dir = Path(folder) / "fixed-data"
            data_dir.mkdir()
            _minimal_database(data_dir / "app.db", complete=False)
            health_dir = Path(folder) / ".updates" / "health"
            health_dir.mkdir(parents=True)
            marker = health_dir / "health.json"
            environment = os.environ.copy()
            environment.update(
                DIGITALBUILDER_DATA_DIR=str(data_dir),
                DIGITALBUILDER_INSTALL_ROOT=str(Path(folder)),
                DIGITALBUILDER_UPDATE_HEALTH_FILE=str(marker),
                DIGITALBUILDER_UPDATE_HEALTH_NONCE="test-nonce",
            )
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "app.py"), "--update-health-check"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(marker.exists())

    def test_health_check_does_not_acknowledge_gui_construction_failure(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            install_root = Path(folder)
            data_dir = install_root / "fixed-data"
            health_dir = install_root / ".updates" / "health"
            data_dir.mkdir()
            health_dir.mkdir(parents=True)
            database = data_dir / "app.db"
            _minimal_database(database)
            before = hashlib.sha256(database.read_bytes()).digest()
            marker = health_dir / "health.json"
            environment = {
                "DIGITALBUILDER_DATA_DIR": str(data_dir),
                "DIGITALBUILDER_INSTALL_ROOT": str(install_root),
                "DIGITALBUILDER_UPDATE_HEALTH_FILE": str(marker),
                "DIGITALBUILDER_UPDATE_HEALTH_NONCE": "test-nonce",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(application, "DATA_DIR", data_dir),
                patch.object(application, "DB_PATH", database),
                patch("invoice_manager.ui.main_window.MainWindow", side_effect=RuntimeError("constructor failure")),
            ):
                with self.assertRaisesRegex(RuntimeError, "constructor failure"):
                    application.run_update_health_check()
            self.assertFalse(marker.exists())
            self.assertEqual(hashlib.sha256(database.read_bytes()).digest(), before)


class LauncherTests(unittest.TestCase):
    def test_candidate_health_requires_exact_nonce_and_version_marker(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            release = root / "release"
            data = root / "data"
            release.mkdir()
            data.mkdir()
            (release / "app.py").write_text(
                "import json, os\n"
                "import helper\n"
                "from pathlib import Path\n"
                "Path(os.environ['DIGITALBUILDER_UPDATE_HEALTH_FILE']).write_text(\n"
                " json.dumps({'schema':1,'nonce':os.environ['DIGITALBUILDER_UPDATE_HEALTH_NONCE'],'version':'2.0.0'}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            (release / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertTrue(
                launcher.run_release_healthcheck(
                    release,
                    "2.0.0",
                    install_root=root,
                    data_dir=data,
                    python_executable=sys.executable,
                )
            )
            self.assertFalse(list((root / ".updates" / "health").glob("*.json")))
            self.assertFalse(
                launcher.run_release_healthcheck(
                    release,
                    "2.0.1",
                    install_root=root,
                    data_dir=data,
                    python_executable=sys.executable,
                )
            )
            self.assertFalse(list(release.rglob("__pycache__")))

    def test_old_style_app_invocation_does_not_modify_release_tree(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            release = Path(folder) / "release"
            data = Path(folder) / "data"
            release.mkdir()
            data.mkdir()
            shutil.copy2(PROJECT_ROOT / "app.py", release / "app.py")
            shutil.copytree(
                PROJECT_ROOT / "invoice_manager",
                release / "invoice_manager",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.update(DIGITALBUILDER_DATA_DIR=str(data), DIGITALBUILDER_INSTALL_ROOT=str(release))
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(release / "app.py"), "--init-db"],
                cwd=release,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(list(release.rglob("__pycache__")))

    def test_restart_exit_code_reenters_activation_then_launches_again(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            release = Path(folder)
            (release / "app.py").write_text("", encoding="utf-8")
            completed = [SimpleNamespace(returncode=launcher.RESTART_EXIT_CODE), SimpleNamespace(returncode=0)]
            with (
                patch.object(launcher, "application_lock", return_value=nullcontext()),
                patch.object(launcher, "activate_pending") as activate,
                patch.object(launcher, "resolve_active_release", return_value=release),
                patch.object(launcher, "get_runtime_fingerprint", return_value="runtime"),
                patch.object(launcher.subprocess, "run", side_effect=completed) as run,
            ):
                self.assertEqual(launcher.launch([]), 0)
            self.assertEqual(activate.call_count, 2)
            self.assertEqual(run.call_count, 2)

    def test_manifest_failure_keeps_launching_the_resolved_previous_release(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            release = Path(folder)
            (release / "app.py").write_text("", encoding="utf-8")
            with (
                patch.object(launcher, "application_lock", return_value=nullcontext()),
                patch.object(launcher, "activate_pending", side_effect=ManifestError("invalid pending")),
                patch.object(launcher, "resolve_active_release", return_value=release),
                patch.object(launcher, "get_runtime_fingerprint", return_value="runtime"),
                patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
                patch("sys.stderr"),
            ):
                self.assertEqual(launcher.launch([]), 0)
            run.assert_called_once()


class UpdateWindowPolicyTests(unittest.TestCase):
    def test_management_window_defers_restart_while_another_window_exists(self) -> None:
        class FakeToplevel:
            def __init__(self, exists=True):
                self._exists = exists

            def winfo_exists(self):
                return self._exists

        update = FakeToplevel()
        other = FakeToplevel()
        controller = main_window.MainWindow.__new__(main_window.MainWindow)
        controller.root = SimpleNamespace(winfo_children=lambda: [update, other])
        with patch.object(main_window.tk, "Toplevel", FakeToplevel):
            ready, message = controller.update_restart_readiness(update)
        self.assertFalse(ready)
        self.assertIn("保留", message)

    def test_unknown_update_exception_is_not_displayed_verbatim(self) -> None:
        message = UpdateWindow._error_message(RuntimeError("secret local path"))
        self.assertNotIn("secret local path", message)

    def test_management_window_defers_restart_for_unsaved_invoice_memo(self) -> None:
        class FakeTree:
            def selection(self):
                return ("row",)

            def item(self, _item, _option):
                return ("", "", "", "", "", "", "", "saved memo")

        invoice_window = SimpleNamespace(
            tree=FakeTree(),
            memo_var=SimpleNamespace(get=lambda: "edited memo"),
        )
        controller = main_window.MainWindow.__new__(main_window.MainWindow)
        controller.root = SimpleNamespace(master=invoice_window, winfo_children=lambda: [])
        ready, message = controller.update_restart_readiness(SimpleNamespace())
        self.assertFalse(ready)
        self.assertIn("メモ", message)

    def test_release_identity_matches_current_release(self) -> None:
        self.assertEqual((APP_VERSION, RELEASE_SEQUENCE), ("1.0.8", 8))


if __name__ == "__main__":
    unittest.main()
