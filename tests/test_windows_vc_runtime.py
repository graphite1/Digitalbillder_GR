from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import windows_vc_runtime as crt


class WindowsVcRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dbgr-crt-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "official-source"
        self.runtime = self.root / "build-runtime"
        self.source.mkdir()
        self.runtime.mkdir()
        self.metadata = []
        for name in crt.CRT_FILES:
            self.write_pe(self.source / name)
            self.metadata.append({"filename": name, "signature_status": "Valid",
                                  "signer": "Microsoft Corporation",
                                  "signer_subject": "CN=Microsoft Corporation, O=Microsoft Corporation, C=US",
                                  "version": [14, 44, 35211, 0]})
        self.metadata_mock = patch.object(crt, "_read_metadata", return_value=self.metadata).start()
        self.addCleanup(patch.stopall)
        for name in crt.CRT_FILES[1:]:
            (self.runtime / name).write_bytes(b"original Python runtime")

    @staticmethod
    def write_pe(path, machine=0x8664):
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, machine)
        path.write_bytes(data)

    def assert_runtime_unchanged(self):
        self.assertFalse((self.runtime / "msvcp140.dll").exists())
        for name in crt.CRT_FILES[1:]:
            self.assertEqual((self.runtime / name).read_bytes(), b"original Python runtime")

    def test_verified_set_replaces_only_build_crt_and_returns_no_source_paths(self):
        unrelated = self.runtime / "python.exe"
        unrelated.write_bytes(b"keep")
        result = crt.copy_app_local_crt(self.source, self.runtime)
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertEqual(len(result["files"]), 3)
        self.assertNotIn(str(self.root), json.dumps(result))
        for item in result["files"]:
            self.assertEqual(item["sha256"], crt._sha256(self.runtime / item["filename"]))

    def test_wrong_architecture_is_rejected_before_any_copy(self):
        self.write_pe(self.source / crt.CRT_FILES[-1], machine=0x14C)
        with self.assertRaisesRegex(ValueError, "AMD64"):
            crt.copy_app_local_crt(self.source, self.runtime)
        self.assert_runtime_unchanged()
        self.metadata_mock.assert_not_called()

    def test_invalid_signature_or_signer_prevents_all_mutations(self):
        for key, value in (("signature_status", "HashMismatch"), ("signer", "Other Corporation"),
                           ("signer_subject", "CN=Microsoft Corporation, O=Not Microsoft Corporation, C=US")):
            with self.subTest(key=key):
                original = self.metadata[-1][key]
                self.metadata[-1][key] = value
                with self.assertRaisesRegex(ValueError, "Microsoft Corporation signature"):
                    crt.copy_app_local_crt(self.source, self.runtime)
                self.assert_runtime_unchanged()
                self.metadata[-1][key] = original

    def test_official_compatibility_publisher_is_accepted_with_microsoft_organization(self):
        for item in self.metadata:
            item["signer"] = "Microsoft Windows Software Compatibility Publisher"
            item["signer_subject"] = "CN=Microsoft Windows Software Compatibility Publisher, O=Microsoft Corporation, C=US"
        result = crt.copy_app_local_crt(self.source, self.runtime)
        self.assertEqual(len(result["files"]), 3)

    def test_mismatched_and_unsupported_versions_prevent_all_mutations(self):
        for version in ([14, 42, 0, 0], [13, 44, 35211, 0], [True, 0, 0, 0]):
            with self.subTest(version=version):
                self.metadata[-1]["version"] = version
                with self.assertRaisesRegex(ValueError, "version"):
                    crt.copy_app_local_crt(self.source, self.runtime)
                self.assert_runtime_unchanged()

    def test_existing_msvcp_is_never_overwritten(self):
        existing = self.runtime / "msvcp140.dll"
        existing.write_bytes(b"existing installed CRT")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            crt.copy_app_local_crt(self.source, self.runtime)
        self.assertEqual(existing.read_bytes(), b"existing installed CRT")
        self.metadata_mock.assert_not_called()

    def test_source_mutation_during_signature_check_prevents_copy(self):
        def mutate(_):
            with (self.source / crt.CRT_FILES[-1]).open("ab") as stream:
                stream.write(b"changed")
            return self.metadata
        self.metadata_mock.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "changed during validation"):
            crt.copy_app_local_crt(self.source, self.runtime)
        self.assert_runtime_unchanged()

    def test_missing_input_prevents_copy(self):
        (self.source / crt.CRT_FILES[-1]).unlink()
        with self.assertRaises(FileNotFoundError):
            crt.copy_app_local_crt(self.source, self.runtime)
        self.assert_runtime_unchanged()

    def test_same_source_and_destination_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            crt.copy_app_local_crt(self.source, self.source)

    def test_hardlinked_destination_cannot_modify_another_app(self):
        external = self.root / "other-app.dll"
        external.write_bytes(b"other app must survive")
        destination = self.runtime / "vcruntime140.dll"
        destination.unlink()
        os.link(external, destination)
        with self.assertRaisesRegex(ValueError, "unlinked regular file"):
            crt.copy_app_local_crt(self.source, self.runtime)
        self.assertEqual(external.read_bytes(), b"other app must survive")
        self.assertFalse((self.runtime / "msvcp140.dll").exists())

    def test_truncated_or_invalid_pe_rejected(self):
        for payload in (b"MZ", bytes(128)):
            with self.subTest(payload_size=len(payload)):
                (self.source / crt.CRT_FILES[-1]).write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "Invalid VC runtime PE"):
                    crt.copy_app_local_crt(self.source, self.runtime)
                self.assert_runtime_unchanged()


class WindowsVcSignatureInvocationTests(unittest.TestCase):
    def test_paths_are_json_data_and_powershell_modules_are_engine_local(self):
        supplied = Path("C:/source ' $(bad)/msvcp140.dll")
        response = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        with patch.dict(os.environ, {"PSModulePath": "C:/PowerShell7/Modules"}):
            with patch.object(crt.subprocess, "run", return_value=response) as invoke:
                self.assertEqual(crt._read_metadata([supplied]), [])
        args, kwargs = invoke.call_args
        self.assertEqual(json.loads(kwargs["input"]), [str(supplied)])
        self.assertNotIn(str(supplied), " ".join(args[0]))
        self.assertEqual(Path(kwargs["env"]["PSModulePath"]), Path(args[0][0]).parent / "Modules")


if __name__ == "__main__":
    unittest.main()
