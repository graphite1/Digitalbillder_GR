import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("guest_smoke", ROOT / "tools/windows_test_environment/guest_smoke.py")
guest_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guest_smoke)


class GuestSmokeTests(unittest.TestCase):
    def test_runtime_check_classifies_bundled_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / "runtime" / "python.exe"
            expected.parent.mkdir()
            expected.touch()
            with patch.object(guest_smoke.sys, "executable", str(expected)):
                self.assertEqual(guest_smoke.runtime_check(root)["status"], "pass")
            with patch.object(guest_smoke.sys, "executable", str(root / "host-python.exe")):
                self.assertEqual(guest_smoke.runtime_check(root)["status"], "fail")

    def test_layout_requires_launcher_runtime_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime").mkdir()
            (root / "launcher.py").touch()
            (root / "runtime/python.exe").touch()
            self.assertEqual(guest_smoke.layout_check(root)["status"], "fail")
            (root / "app.py").touch()
            self.assertEqual(guest_smoke.layout_check(root)["status"], "pass")

    def test_main_rejects_relative_paths(self):
        with self.assertRaises(SystemExit) as raised:
            guest_smoke.main(["--install-root", ".", "--output", "result.json"])
        self.assertEqual(raised.exception.code, 2)

    def test_output_is_json_and_scope_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test-data").mkdir()
            with patch.object(guest_smoke, "runtime_check", return_value={"status": "pass"}), patch.object(guest_smoke, "run_checks", return_value={"overall": "pass", "checks": {}}):
                output = root / "out.json"
                self.assertEqual(guest_smoke.main(["--install-root", str(root), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["overall"], "pass")


if __name__ == "__main__":
    unittest.main()
