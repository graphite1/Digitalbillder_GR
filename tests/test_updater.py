from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from urllib.error import HTTPError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from updater import (
    ActivationError,
    ArchiveError,
    ManifestError,
    UpdateBusyError,
    activate_pending,
    application_lock,
    check_for_update,
    resolve_active_release,
    stage_update,
)
from updater.archive import validate_archive
from updater.security import b64url_encode, verify_release_envelope


RUNTIME = "python=3.12;platform=win32;arch=amd64;deps=test"
BASE_URL = "https://updates.example.test"


class SimulatedPowerLoss(BaseException):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def zip_info(name: str, *, mode: int = 0o100644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = mode << 16
    info.create_system = 3
    return info


def release_zip(version="1.0.1", sequence=1, *, extra=None) -> bytes:
    stream = io.BytesIO()
    version_value = {
        "product": "Digitalbuilder_GR",
        "version": version,
        "sequence": sequence,
        "schema": 1,
        "runtime_fingerprint": RUNTIME,
    }
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(zip_info("app.py"), b"print('safe release')\n")
        archive.writestr(zip_info("version.json"), canonical(version_value))
        archive.writestr(zip_info("updater/__init__.py"), b"")
        if extra:
            for name, value, mode in extra:
                archive.writestr(zip_info(name, mode=mode), value)
    return stream.getvalue()


def signed_envelope(private_key, archive_bytes: bytes, *, mutate_payload=None, version="1.0.1", sequence=1):
    now = datetime.now(timezone.utc)
    payload = {
        "schema": 1,
        "product": "Digitalbuilder_GR",
        "channel": "stable",
        "version": version,
        "sequence": sequence,
        "published_at": (now - timedelta(minutes=1)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "notes": "安全な架空更新",
        "archive": {
            "filename": f"Digitalbuilder_GR-{version}.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "size": len(archive_bytes),
        },
        "compatibility": {"updater": 1, "schema": 1, "runtime_fingerprint": RUNTIME},
    }
    if mutate_payload:
        mutate_payload(payload)
    payload_bytes = canonical(payload)
    envelope = {
        "payload": b64url_encode(payload_bytes),
        "signature": b64url_encode(private_key.sign(payload_bytes)),
        "key_id": "test-key",
    }
    return canonical(envelope)


def public_key(private_key) -> str:
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url_encode(raw)


class FakeResponse:
    def __init__(self, data: bytes, final_url: str | None = None):
        self.data = data
        self.headers = {"Content-Length": str(len(data))}
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.data[:size]

    def geturl(self):
        return self.final_url


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        return FakeResponse(self.responses[request.full_url], request.full_url)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)
        self.private_key = Ed25519PrivateKey.generate()
        self.keys = {"test-key": public_key(self.private_key)}
        self.archive = release_zip()
        self.envelope = signed_envelope(self.private_key, self.archive)

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self):
        return verify_release_envelope(
            self.envelope, self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME
        )

    def test_check_uses_fixed_same_origin_urls_and_rejects_tampering(self):
        opener = FakeOpener({BASE_URL + "/api/releases/latest": self.envelope})
        manifest = check_for_update(
            BASE_URL + "/ignored/path", self.keys, current_sequence=0,
            runtime_fingerprint=RUNTIME, opener=opener,
        )
        self.assertEqual(manifest.version, "1.0.1")
        self.assertEqual(manifest.notes, "安全な架空更新")
        self.assertEqual(opener.urls, [BASE_URL + "/api/releases/latest"])
        self.assertIsNone(check_for_update(
            BASE_URL, self.keys, current_sequence=1, runtime_fingerprint=RUNTIME,
            opener=FakeOpener({BASE_URL + "/api/releases/latest": self.envelope}),
        ))
        damaged = json.loads(self.envelope)
        damaged["signature"] = "A" + damaged["signature"][1:]
        with self.assertRaisesRegex(ManifestError, "署名"):
            verify_release_envelope(
                canonical(damaged), self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME
            )
        with self.assertRaisesRegex(ManifestError, "HTTPS"):
            check_for_update("http://unsafe.test", self.keys, current_sequence=0, opener=lambda *_a, **_k: None)

    def test_only_explicit_no_release_404_is_treated_as_up_to_date(self):
        def no_release(_request, timeout):
            raise HTTPError(
                BASE_URL + "/api/releases/latest", 404, "Not Found",
                {"Content-Type": "application/json"}, io.BytesIO(b'{"error":"no_release"}'),
            )

        self.assertIsNone(check_for_update(
            BASE_URL, self.keys, current_sequence=0,
            runtime_fingerprint=RUNTIME, opener=no_release,
        ))

        def generic_404(_request, timeout):
            raise HTTPError(
                BASE_URL + "/api/releases/latest", 404, "Not Found",
                {"Content-Type": "text/html"}, io.BytesIO(b"missing"),
            )

        from updater import DownloadError
        with self.assertRaisesRegex(DownloadError, "更新情報"):
            check_for_update(
                BASE_URL, self.keys, current_sequence=0,
                runtime_fingerprint=RUNTIME, opener=generic_404,
            )

    def test_manifest_rejects_extra_fields_expiry_and_runtime_mismatch(self):
        extra = signed_envelope(self.private_key, self.archive, mutate_payload=lambda value: value.update({"extra": 1}))
        with self.assertRaisesRegex(ManifestError, "余剰"):
            verify_release_envelope(extra, self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME)
        with self.assertRaisesRegex(ManifestError, "実行環境"):
            verify_release_envelope(self.envelope, self.keys, base_url=BASE_URL, runtime_fingerprint="other")
        expired = signed_envelope(
            self.private_key,
            self.archive,
            mutate_payload=lambda value: value.update({
                "published_at": "2025-01-01T00:00:00Z", "expires_at": "2025-01-31T00:00:00Z"
            }),
        )
        with self.assertRaisesRegex(ManifestError, "有効期限"):
            verify_release_envelope(expired, self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME)

    def test_redirect_to_another_origin_is_rejected_before_reading(self):
        def redirecting(_request, timeout):
            return FakeResponse(self.envelope, "https://other.example.test/api/releases/latest")

        from updater import DownloadError
        with self.assertRaisesRegex(DownloadError, "リダイレクト"):
            check_for_update(
                BASE_URL, self.keys, current_sequence=0,
                runtime_fingerprint=RUNTIME, opener=redirecting,
            )

    def test_archive_rejects_traversal_links_reserved_names_and_allowlist_extras(self):
        cases = (
            ("../escape.py", 0o100644, "パス"),
            ("updater/link.py", 0o120777, "リンク"),
            ("assets/CON.png", 0o100644, "予約名"),
            ("assets/internal-screenshot.png", 0o100644, "allowlist"),
            ("data/app.db", 0o100644, "allowlist"),
        )
        for name, mode, message in cases:
            with self.subTest(name=name):
                path = self.root / (hashlib.sha256(name.encode()).hexdigest() + ".zip")
                path.write_bytes(release_zip(extra=[(name, b"x", mode)]))
                with self.assertRaisesRegex(ArchiveError, message):
                    validate_archive(path)

    def _stage(self):
        manifest = self.manifest()
        opener = FakeOpener({BASE_URL + "/api/releases/1/download": self.archive})
        staged = stage_update(manifest, self.root, opener=opener)
        return manifest, staged

    def test_stage_then_activate_backs_up_database_and_resolves_signed_release(self):
        manifest, staged = self._stage()
        data = self.root / "user-data"
        data.mkdir()
        with closing(sqlite3.connect(data / "app.db")) as connection:
            connection.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO ledger(value) VALUES ('preserved')")
            connection.commit()
        calls = []
        result = activate_pending(
            self.root, self.keys, data_dir=data,
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda path, version: calls.append((path, version)) or True,
        )
        self.assertTrue(result.activated)
        self.assertEqual(calls[0][1], manifest.version)
        self.assertEqual(resolve_active_release(
            self.root, self.keys, runtime_fingerprint=RUNTIME
        ), self.root / ".updates" / staged.release_dir)
        self.assertFalse((self.root / ".updates" / "pending.json").exists())
        self.assertTrue(Path(result.backup_path).is_file())
        with closing(sqlite3.connect(data / "app.db")) as connection:
            self.assertEqual(connection.execute("SELECT value FROM ledger").fetchone()[0], "preserved")

    def test_health_failure_rolls_back_and_quarantines_pending(self):
        self._stage()
        with self.assertRaisesRegex(ActivationError, "直前版"):
            activate_pending(
                self.root, self.keys, data_dir=self.root / "data",
                runtime_fingerprint=RUNTIME,
                launch_healthcheck=lambda _path, _version: False,
            )
        self.assertEqual(resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME), self.root)
        self.assertFalse((self.root / ".updates" / "pending.json").exists())
        self.assertEqual(
            len(list((self.root / ".updates" / "failed").glob("1-1.0.1-*.json"))), 1
        )

    def test_power_loss_boundaries_never_launch_uncommitted_baseline_update(self):
        expectations = {
            "after_journal": 0,
            "after_switch": 0,
            "after_health": 1,
            "after_health_commit": 1,
            "after_pending_delete": 1,
        }
        for boundary, health_calls_before_recovery in expectations.items():
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                root = Path(folder)
                manifest = self.manifest()
                stage_update(
                    manifest, root,
                    opener=FakeOpener({BASE_URL + "/api/releases/1/download": self.archive}),
                )
                health_calls = []

                def lose_power(point):
                    if point == boundary:
                        raise SimulatedPowerLoss(point)

                with self.assertRaises(SimulatedPowerLoss):
                    activate_pending(
                        root, self.keys, data_dir=root / "data",
                        runtime_fingerprint=RUNTIME,
                        launch_healthcheck=lambda path, version: health_calls.append((path, version)) or True,
                        _fault_inject=lose_power,
                    )
                self.assertEqual(len(health_calls), health_calls_before_recovery)

                recovered = activate_pending(
                    root, self.keys, data_dir=root / "data",
                    runtime_fingerprint=RUNTIME,
                    launch_healthcheck=lambda path, version: health_calls.append((path, version)) or True,
                )
                self.assertTrue(recovered.activated)
                expected_total_calls = 2 if boundary == "after_health" else 1
                self.assertEqual(len(health_calls), expected_total_calls)
                self.assertEqual(
                    resolve_active_release(root, self.keys, runtime_fingerprint=RUNTIME),
                    root / ".updates" / "releases" / "1-1.0.1",
                )
                self.assertFalse((root / ".updates" / "activation-journal.json").exists())
                self.assertFalse((root / ".updates" / "pending.json").exists())

    def test_resolve_rolls_back_interrupted_switch_to_verified_previous_release(self):
        _first, first_staged = self._stage()
        activate_pending(
            self.root, self.keys, data_dir=self.root / "data",
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda _path, _version: True,
        )
        second_archive = release_zip(version="1.0.2", sequence=2)
        second_manifest = verify_release_envelope(
            signed_envelope(self.private_key, second_archive, version="1.0.2", sequence=2),
            self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME,
        )
        stage_update(
            second_manifest, self.root,
            opener=FakeOpener({BASE_URL + "/api/releases/2/download": second_archive}),
        )

        with self.assertRaises(SimulatedPowerLoss):
            activate_pending(
                self.root, self.keys, data_dir=self.root / "data",
                runtime_fingerprint=RUNTIME,
                launch_healthcheck=lambda _path, _version: True,
                _fault_inject=lambda point: (
                    (_ for _ in ()).throw(SimulatedPowerLoss(point))
                    if point == "after_switch" else None
                ),
            )

        self.assertEqual(
            resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME),
            self.root / ".updates" / first_staged.release_dir,
        )
        self.assertTrue((self.root / ".updates" / "pending.json").exists())
        activate_pending(
            self.root, self.keys, data_dir=self.root / "data",
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda _path, _version: True,
        )
        self.assertEqual(
            resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME),
            self.root / ".updates" / "releases" / "2-1.0.2",
        )

    def test_tampered_extracted_release_is_never_resolved(self):
        _manifest, staged = self._stage()
        activate_pending(
            self.root, self.keys, data_dir=self.root / "data",
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda _path, _version: True,
        )
        (self.root / ".updates" / staged.release_dir / "app.py").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ArchiveError, "一致しません"):
            resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME)

    def test_failed_second_release_restores_verified_previous_pointer(self):
        _first, first_staged = self._stage()
        activate_pending(
            self.root, self.keys, data_dir=self.root / "data",
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda _path, _version: True,
        )
        second_archive = release_zip(version="1.0.2", sequence=2)
        second_envelope = signed_envelope(
            self.private_key, second_archive, version="1.0.2", sequence=2
        )
        second_manifest = verify_release_envelope(
            second_envelope, self.keys, base_url=BASE_URL, runtime_fingerprint=RUNTIME
        )
        stage_update(
            second_manifest,
            self.root,
            opener=FakeOpener({BASE_URL + "/api/releases/2/download": second_archive}),
        )

        with self.assertRaisesRegex(ActivationError, "直前版"):
            activate_pending(
                self.root, self.keys, data_dir=self.root / "data",
                runtime_fingerprint=RUNTIME,
                launch_healthcheck=lambda _path, _version: False,
            )

        self.assertEqual(
            resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME),
            self.root / ".updates" / first_staged.release_dir,
        )

    def test_expired_pending_is_rejected_but_expired_active_remains_launchable(self):
        _manifest, staged = self._stage()
        activate_pending(
            self.root, self.keys, data_dir=self.root / "data",
            runtime_fingerprint=RUNTIME,
            launch_healthcheck=lambda _path, _version: True,
        )
        expired = signed_envelope(
            self.private_key,
            self.archive,
            mutate_payload=lambda value: value.update({
                "published_at": "2025-01-01T00:00:00Z", "expires_at": "2025-01-31T00:00:00Z"
            }),
        )
        pointer_path = self.root / ".updates" / "current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["envelope"] = json.loads(expired)
        pointer_path.write_bytes(canonical(pointer))
        self.assertEqual(
            resolve_active_release(self.root, self.keys, runtime_fingerprint=RUNTIME),
            self.root / ".updates" / staged.release_dir,
        )

        pending = dict(pointer)
        pending.pop("activated_at")
        pending["staged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (self.root / ".updates" / "pending.json").write_bytes(canonical(pending))
        with self.assertRaisesRegex(ManifestError, "有効期限"):
            activate_pending(
                self.root, self.keys, data_dir=self.root / "data",
                runtime_fingerprint=RUNTIME,
                launch_healthcheck=lambda _path, _version: True,
            )

    def test_application_lock_rejects_second_launcher(self):
        with application_lock(self.root):
            with self.assertRaises(UpdateBusyError):
                with application_lock(self.root):
                    pass

    def test_release_builder_includes_only_code_allowlist_and_signs_output(self):
        import tools.build_release as builder

        output = self.root / "release-output"
        now = datetime.now(timezone.utc)
        with patch.object(
            builder, "load_or_create_signing_key",
            return_value=(self.private_key, self.keys["test-key"]),
        ):
            archive, manifest_path, public_path = builder.build_release(
                version="1.0.1",
                sequence=1,
                output_dir=output,
                key_id="test-key",
                notes="架空release",
                published_at=now,
                expires_at=now + timedelta(days=30),
                runtime_fingerprint=RUNTIME,
            )
        names = {info.filename for info in zipfile.ZipFile(archive).infolist()}
        self.assertIn("app.py", names)
        self.assertIn("version.json", names)
        self.assertIn("updater/__init__.py", names)
        self.assertFalse(any(name.startswith(("data/", ".git/", ".venv/")) for name in names))
        with zipfile.ZipFile(archive) as bundle:
            release_version_module = bundle.read("invoice_manager/version.py").decode("utf-8")
        self.assertIn('APP_VERSION = "1.0.1"', release_version_module)
        self.assertIn("RELEASE_SEQUENCE = 1", release_version_module)
        verified = verify_release_envelope(
            manifest_path.read_bytes(), self.keys, base_url=BASE_URL,
            runtime_fingerprint=RUNTIME, now=now,
        )
        self.assertEqual(verified.archive.sha256, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(json.loads(public_path.read_text(encoding="utf-8"))["public_key"], self.keys["test-key"])


if __name__ == "__main__":
    unittest.main()
