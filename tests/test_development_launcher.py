from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import launcher
from invoice_manager import db
from invoice_manager.services.db_backup import create_database_backup


class DevelopmentLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        (self.root / ".git").mkdir()
        (self.root / "app.py").write_text("# Synthetic checkout app; never executed.\n", encoding="utf-8")
        self.data = Path(self.temp.name) / "existing-ledger"
        self.data.mkdir()
        self.database = self.data / "app.db"
        with sqlite3.connect(self.database, factory=db.ClosingConnection) as connection:
            connection.execute("CREATE TABLE ledger (value TEXT)")
            connection.execute("INSERT INTO ledger VALUES ('synthetic original')")
        self.updates = self.root / ".updates"
        self.updates.mkdir()
        (self.updates / "state.json").write_text('{"active":"4-1.0.4","pending":"5-1.0.5"}', encoding="utf-8")
        release = self.updates / "releases" / "4-1.0.4"
        release.mkdir(parents=True)
        (release / "app.py").write_text("# Synthetic signed old app\n", encoding="utf-8")
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(launcher, "__file__", str(self.root / "launcher.py")))
        self.stack.enter_context(patch.dict(os.environ, {launcher.DATA_DIR_ENV: str(self.data)}, clear=False))
        self.stack.enter_context(patch.object(launcher, "get_runtime_fingerprint", return_value="synthetic-runtime"))
        self.lock = self.stack.enter_context(patch.object(launcher, "application_lock", return_value=nullcontext()))
        self.activate = self.stack.enter_context(patch.object(launcher, "activate_pending"))
        self.resolve = self.stack.enter_context(patch.object(launcher, "resolve_active_release", return_value=release))
        self.run = self.stack.enter_context(patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)))
        self.stderr = self.stack.enter_context(patch("sys.stderr"))

    def tearDown(self) -> None:
        self.stack.close()
        self.temp.cleanup()

    def update_tree(self) -> dict[str, bytes]:
        return {str(path.relative_to(self.updates)): path.read_bytes()
                for path in self.updates.rglob("*") if path.is_file()}

    def test_development_uses_checkout_same_data_backup_and_preserves_release_state(self) -> None:
        original_database = self.database.read_bytes()
        original_updates = self.update_tree()
        with patch("invoice_manager.services.db_backup.create_database_backup", wraps=create_database_backup) as backup:
            self.assertEqual(launcher.launch(["--development", "--init-db", "other-argument"]), 0)
        backup.assert_called_once()
        self.assertEqual(backup.call_args.args[0], "before_development")
        self.assertEqual(Path(backup.call_args.kwargs["source_path"]), self.database)
        self.lock.assert_called_once_with(self.root)
        command = self.run.call_args.args[0]
        self.assertEqual(command[4], str(self.root / "app.py"))
        self.assertEqual(command[5:], ["--init-db", "other-argument"])
        self.assertNotIn("--development", command)
        self.assertEqual(self.run.call_args.kwargs["cwd"], self.root)
        environment = self.run.call_args.kwargs["env"]
        self.assertEqual(environment[launcher.DATA_DIR_ENV], str(self.data))
        self.assertEqual(environment[launcher.INSTALL_ROOT_ENV], str(self.root))
        self.assertEqual(environment["DIGITALBUILDER_DEVELOPMENT"], "1")
        self.activate.assert_not_called()
        self.resolve.assert_not_called()
        self.assertEqual(self.update_tree(), original_updates)
        self.assertEqual(self.database.read_bytes(), original_database)
        backups = list((self.data / "backups").glob("*before_development.db"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0], factory=db.ClosingConnection) as connection:
            self.assertEqual(connection.execute("SELECT value FROM ledger").fetchone(), ("synthetic original",))

    def test_backup_failure_prevents_child_launch_and_does_not_touch_pending(self) -> None:
        before = self.update_tree()
        with patch("invoice_manager.services.db_backup.create_database_backup", side_effect=OSError("backup failed")):
            self.assertEqual(launcher.launch(["--development"]), 1)
        self.run.assert_not_called()
        self.activate.assert_not_called()
        self.resolve.assert_not_called()
        self.assertEqual(self.update_tree(), before)

    def test_application_lock_conflict_prevents_backup_and_launch(self) -> None:
        self.lock.side_effect = launcher.UpdateBusyError("already running")
        with patch("invoice_manager.services.db_backup.create_database_backup") as backup:
            self.assertEqual(launcher.launch(["--development"]), 1)
        backup.assert_not_called()
        self.run.assert_not_called()
        self.activate.assert_not_called()
        self.resolve.assert_not_called()

    def test_lock_is_held_during_backup_and_child_execution(self) -> None:
        events = []

        @contextmanager
        def acquired(_root):
            events.append("lock")
            try:
                yield
            finally:
                events.append("unlock")

        def backup(*_args, **_kwargs):
            self.assertEqual(events, ["lock"])
            events.append("backup")

        def run(*_args, **_kwargs):
            self.assertEqual(events, ["lock", "backup"])
            events.append("run")
            return SimpleNamespace(returncode=0)

        self.lock.side_effect = acquired
        self.run.side_effect = run
        with patch("invoice_manager.services.db_backup.create_database_backup", side_effect=backup):
            self.assertEqual(launcher.launch(["--development"]), 0)
        self.assertEqual(events, ["lock", "backup", "run", "unlock"])

    def test_noncheckout_rejects_development_option(self) -> None:
        (self.root / ".git").rmdir()
        with patch("invoice_manager.services.db_backup.create_database_backup") as backup:
            self.assertEqual(launcher.launch(["--development"]), 1)
        self.run.assert_not_called()
        backup.assert_not_called()
        self.activate.assert_not_called()
        self.resolve.assert_not_called()

    def test_git_file_checkout_can_launch(self) -> None:
        (self.root / ".git").rmdir()
        (self.root / ".git").write_text("gitdir: synthetic-linked-worktree", encoding="utf-8")
        self.assertEqual(launcher.launch(["--development"]), 0)
        self.assertEqual(self.run.call_args.args[0][4], str(self.root / "app.py"))

    def test_missing_database_skips_backup_but_preserves_configured_data_path(self) -> None:
        empty_data = Path(self.temp.name) / "new-empty-data"
        with (patch.dict(os.environ, {launcher.DATA_DIR_ENV: str(empty_data)}),
              patch("invoice_manager.services.db_backup.create_database_backup") as backup):
            self.assertEqual(launcher.launch(["--development"]), 0)
        backup.assert_not_called()
        self.assertEqual(self.run.call_args.kwargs["env"][launcher.DATA_DIR_ENV], str(empty_data))
        self.assertFalse(empty_data.exists())

    def test_no_data_override_uses_checkout_data_directory(self) -> None:
        with patch.dict(os.environ, {launcher.DATA_DIR_ENV: ""}):
            self.assertEqual(launcher.launch(["--development"]), 0)
        self.assertEqual(self.run.call_args.kwargs["env"][launcher.DATA_DIR_ENV], str(self.root / "data"))

    def test_normal_launch_still_activates_and_selects_verified_release(self) -> None:
        with (patch("invoice_manager.services.db_backup.create_database_backup") as backup,
              patch.dict(os.environ, {"DIGITALBUILDER_DEVELOPMENT": "1"})):
            self.assertEqual(launcher.launch(["--init-db"]), 0)
        self.activate.assert_called_once()
        self.resolve.assert_called_once()
        backup.assert_not_called()
        self.assertEqual(self.run.call_args.args[0][4], str(self.updates / "releases" / "4-1.0.4" / "app.py"))
        self.assertEqual(self.run.call_args.args[0][5:], ["--init-db"])
        self.assertNotIn("DIGITALBUILDER_DEVELOPMENT", self.run.call_args.kwargs["env"])

    def test_child_launch_error_is_reported_as_failure(self) -> None:
        self.run.side_effect = OSError("child failed")
        self.assertEqual(launcher.launch(["--development"]), 1)
        self.activate.assert_not_called()
        self.resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
