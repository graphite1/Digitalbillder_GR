"""Real app/health subprocesses with Git baseline and synthetic release trust.

This is a source-fixture integration test, not a published Windows ZIP gate.
Only transport and the test public-key constant are substituted; the fixed
launcher, activation protocol, candidate app and health command execute intact.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "3a049af7d22f87499eb0ed3cac692667c173c6ba"
TRANSPORT = '''
import io, json, os
from pathlib import Path
from unittest.mock import patch
root = Path(os.environ["DIGITALBUILDER_INSTALL_ROOT"])
files = json.loads(Path(os.environ["DBGR_TEST_RESPONSES"]).read_text())
urls = []
def opener(request, timeout):
    urls.append(request.full_url)
    stream = io.BytesIO(Path(files[request.full_url]).read_bytes())
    stream.headers = {"Content-Length": str(len(stream.getvalue()))}
    stream.geturl = lambda: request.full_url
    return stream
'''


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "GUI health requires a display")
class BridgeSubprocessTests(unittest.TestCase):
    def test_fixed_git_launcher_activates_real_bridge_and_real_next_release(self):
        import hashlib
        import io
        import json
        import shutil
        import sqlite3
        import subprocess
        import tarfile
        import tempfile
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from tools import build_release as builder
        from updater.config import LEGACY_OFFICIAL_UPDATE_BASE_URL, DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS
        from updater.runtime import get_runtime_fingerprint
        from updater.security import b64url_encode

        if not shutil.which("git"):
            self.skipTest("Git source baseline unavailable")
        source = subprocess.run(["git", "archive", BASELINE, "launcher.py", "app.py",
            "invoice_manager", "updater", "assets"], cwd=ROOT, capture_output=True)
        if source.returncode:
            self.skipTest("Git source baseline unavailable")
        with tempfile.TemporaryDirectory(prefix="dbgr-bridge-process-") as folder:
            temporary = Path(folder)
            install = temporary / "installation"
            install.mkdir()
            with tarfile.open(fileobj=io.BytesIO(source.stdout)) as archive:
                archive.extractall(install, filter="data")
            # Ephemeral synthetic trust only. Never call the signing vault.
            key = Ed25519PrivateKey.generate()
            public = b64url_encode(key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw))
            def test_trust(text):
                return text.replace(TRUSTED_PUBLIC_KEYS["release-2026-01"], public)
            fixed_config = install / "updater/config.py"
            fixed_config.write_text(test_trust(fixed_config.read_text(encoding="utf8")), encoding="utf8")
            candidate_config = temporary / "candidate-config.py"
            candidate_config.write_text(test_trust((ROOT / "updater/config.py").read_text(encoding="utf8")), encoding="utf8")
            fixed_hashes = {name: hashlib.sha256((install / name).read_bytes()).hexdigest()
                for name in ("launcher.py", "updater/config.py", "updater/core.py")}
            files = [(candidate_config if name == "updater/config.py" else path, name)
                for path, name in builder._source_files()]
            now = datetime.now(timezone.utc)
            with patch.object(builder, "load_or_create_signing_key", return_value=(key, public)), \
                 patch.object(builder, "_source_files", return_value=files):
                bundles = [builder.build_release(version=version, sequence=sequence,
                    output_dir=temporary / version, key_id="release-2026-01", notes="synthetic integration",
                    published_at=now - timedelta(minutes=1), expires_at=now + timedelta(days=1),
                    runtime_fingerprint=get_runtime_fingerprint()) for version, sequence in
                    (("9.9.1", 901), ("9.9.2", 902))]
            data = temporary / "business-data"
            responses = temporary / "responses.json"
            environment = os.environ.copy()
            environment.update(DIGITALBUILDER_INSTALL_ROOT=str(install),
                DIGITALBUILDER_DATA_DIR=str(data), DBGR_TEST_RESPONSES=str(responses),
                PYTHONDONTWRITEBYTECODE="1")
            environment.pop("PYTHONPATH", None)
            def run(arguments, cwd=install, *, include_stderr=False):
                result = subprocess.run([sys.executable, "-B", *arguments], cwd=cwd,
                    env=environment, capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout + result.stderr if include_stderr else result.stdout
            run([str(install / "app.py"), "--init-db"])
            with sqlite3.connect(data / "app.db") as connection:
                connection.execute("CREATE TABLE synthetic_ledger(value TEXT)")
                connection.execute("INSERT INTO synthetic_ledger VALUES ('preserved')")
                connection.commit()
            settings = data / "custom-settings.json"
            settings.write_bytes(b'{"preserve":true}')
            def response_files(origin, sequence, bundle):
                responses.write_text(json.dumps({origin + "/api/releases/latest": str(bundle[1]),
                    origin + f"/api/releases/{sequence}/download": str(bundle[0])}), encoding="utf8")
            response_files(LEGACY_OFFICIAL_UPDATE_BASE_URL, 901, bundles[0])
            old_stage = TRANSPORT + '''
from updater import TRUSTED_PUBLIC_KEYS, check_for_update, stage_update
from updater.config import DEFAULT_UPDATE_BASE_URL
from invoice_manager.version import RELEASE_SEQUENCE
manifest = check_for_update(DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS,
    current_sequence=RELEASE_SEQUENCE, opener=opener)
stage_update(manifest, root, opener=opener)
print(json.dumps(urls))
'''
            urls = json.loads(run(["-c", old_stage]).strip())
            self.assertEqual(urls, [LEGACY_OFFICIAL_UPDATE_BASE_URL + "/api/releases/latest",
                LEGACY_OFFICIAL_UPDATE_BASE_URL + "/api/releases/901/download"])
            # Run the real candidate health against an intentionally incomplete
            # disposable DB copy. The actual fixed launcher must recover and
            # launch the baseline; the normal business fixture stays untouched.
            broken_data = temporary / "incomplete-data"
            broken_data.mkdir()
            shutil.copy2(data / "app.db", broken_data / "app.db")
            with sqlite3.connect(broken_data / "app.db") as connection:
                connection.execute("DROP TABLE app_settings")
                connection.commit()
            before = hashlib.sha256((data / "app.db").read_bytes()).hexdigest()
            environment["DIGITALBUILDER_DATA_DIR"] = str(broken_data)
            output = run([str(install / "launcher.py"), "--init-db"], include_stderr=True)
            self.assertIn("直前版", output)
            self.assertFalse((install / ".updates/current.json").exists())
            self.assertFalse((install / ".updates/pending.json").exists())
            self.assertEqual(len(list((install / ".updates/failed").glob("901-9.9.1-*.json"))), 1)
            self.assertEqual(hashlib.sha256((data / "app.db").read_bytes()).hexdigest(), before)
            environment["DIGITALBUILDER_DATA_DIR"] = str(data)
            run(["-c", old_stage])
            run([str(install / "launcher.py"), "--init-db"])
            active = json.loads((install / ".updates/current.json").read_text())
            self.assertEqual((active["version"], active["sequence"]), ("9.9.1", 901))
            bridge = install / ".updates" / active["release"]
            response_files(DEFAULT_UPDATE_BASE_URL, 902, bundles[1])
            next_stage = TRANSPORT + '''
from invoice_manager.ui.update_window import UpdateWindow
with patch("updater.core.urlopen", opener):
    manifest = UpdateWindow._default_check_update()
    UpdateWindow._default_stage_release(manifest, None)
print(json.dumps(urls))
'''
            urls = json.loads(run(["-c", next_stage], cwd=bridge).strip())
            self.assertEqual(urls, [DEFAULT_UPDATE_BASE_URL + "/api/releases/latest",
                DEFAULT_UPDATE_BASE_URL + "/api/releases/902/download"])
            run([str(install / "launcher.py"), "--init-db"])
            active = json.loads((install / ".updates/current.json").read_text())
            self.assertEqual((active["version"], active["sequence"]), ("9.9.2", 902))
            for name, expected in fixed_hashes.items():
                self.assertEqual(hashlib.sha256((install / name).read_bytes()).hexdigest(), expected)
            with sqlite3.connect(data / "app.db") as connection:
                self.assertEqual(connection.execute("SELECT value FROM synthetic_ledger").fetchone(), ("preserved",))
            self.assertEqual(settings.read_bytes(), b'{"preserve":true}')
            self.assertEqual(len(list((data / "backups").glob("*.db"))), 2)


if __name__ == "__main__":
    unittest.main()
