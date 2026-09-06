from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import updater
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from updater.errors import DownloadError, ManifestError

import tools.manual_update as manual_update
from tests.test_updater import BASE_URL, RUNTIME, public_key, release_zip, signed_envelope


class ManualUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name) / "installation"
        self.root.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        with closing(sqlite3.connect(self.data / "app.db")) as connection:
            connection.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
        for relative in ("launcher.py", "app.py", "updater/__init__.py", "invoice_manager/version.py", ".venv/Scripts/python.exe"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith("version.py"):
                path.write_text("RELEASE_SEQUENCE = 0\n", encoding="utf-8")
            else:
                path.write_bytes(b"fixed")
        self.bundle = Path(self.temp.name) / "bundle"
        self.bundle.mkdir()
        self.private_key = Ed25519PrivateKey.generate()
        self.keys = {"test-key": public_key(self.private_key)}
        self.archive = release_zip(version="1.0.1", sequence=1)
        (self.bundle / "update.zip").write_bytes(self.archive)
        (self.bundle / "update.manifest.json").write_bytes(
            signed_envelope(self.private_key, self.archive, version="1.0.1", sequence=1)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _trusted(self):
        return patch.object(updater, "DEFAULT_UPDATE_BASE_URL", BASE_URL), patch.object(
            updater, "TRUSTED_PUBLIC_KEYS", self.keys
        ), patch.object(updater, "get_runtime_fingerprint", return_value=RUNTIME)

    def _prepare(self) -> bool:
        with self._trusted()[0], self._trusted()[1], self._trusted()[2]:
            return manual_update.prepare_update(self.root, self.bundle)

    def test_installation_rejects_missing_fixed_files_and_database(self) -> None:
        with self.assertRaises(ValueError):
            manual_update.installation(self.root / "missing")
        (self.data / "app.db").unlink()
        with self.assertRaisesRegex(ValueError, "既存の台帳"):
            manual_update.installation(self.root)

    def test_prepare_stages_through_existing_trusted_updater(self) -> None:
        with patch.object(updater, "stage_update", wraps=updater.stage_update) as stage, self._trusted()[0], self._trusted()[1], self._trusted()[2]:
            self.assertTrue(manual_update.prepare_update(self.root, self.bundle))
        stage.assert_called_once()
        pending = json.loads((self.root / ".updates/pending.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["sequence"], 1)
        self.assertEqual(pending["archive_sha256"], hashlib.sha256(self.archive).hexdigest())

    def test_wrong_local_archive_hash_leaves_no_pending(self) -> None:
        damaged = bytearray(self.archive)
        damaged[0] ^= 1
        (self.bundle / "update.zip").write_bytes(damaged)
        with self.assertRaises(DownloadError):
            self._prepare()
        self.assertFalse((self.root / ".updates/pending.json").exists())
        releases = self.root / ".updates/releases"
        self.assertTrue(releases.is_dir())
        self.assertEqual(list(releases.iterdir()), [])

    def test_invalid_manifest_signature_leaves_no_pending(self) -> None:
        envelope = json.loads((self.bundle / "update.manifest.json").read_text(encoding="utf-8"))
        envelope["signature"] = "A" + envelope["signature"][1:]
        (self.bundle / "update.manifest.json").write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "署名"):
            self._prepare()
        self.assertFalse((self.root / ".updates/pending.json").exists())

    def test_same_or_older_sequence_does_not_downgrade(self) -> None:
        self.assertTrue(self._prepare())  # sequence 1 is newer than baseline 0
        (self.root / ".updates/pending.json").unlink()
        (self.root / "invoice_manager/version.py").write_text("RELEASE_SEQUENCE = 1\n", encoding="utf-8")
        self.assertFalse(self._prepare())
        (self.root / "invoice_manager/version.py").write_text("RELEASE_SEQUENCE = 2\n", encoding="utf-8")
        self.assertFalse(self._prepare())
        self.assertFalse((self.root / ".updates/pending.json").exists())

    def test_existing_pending_is_preserved(self) -> None:
        pending = b'{"sentinel":true}'
        (self.root / ".updates").mkdir()
        (self.root / ".updates/pending.json").write_bytes(pending)
        with self.assertRaisesRegex(ValueError, "準備済み"):
            self._prepare()
        self.assertEqual((self.root / ".updates/pending.json").read_bytes(), pending)

    def test_running_application_lock_blocks_staging(self) -> None:
        with updater.application_lock(self.root):
            with self.assertRaises(updater.UpdateBusyError):
                self._prepare()
        self.assertFalse((self.root / ".updates/pending.json").exists())


if __name__ == "__main__":
    unittest.main()
