"""Offline, synthetic-only Linux QA. Run within an OS network namespace."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import types
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("This evaluated QA harness requires Linux.")
    network_devices = {
        line.split(":", 1)[0].strip()
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]
    }
    routes = Path("/proc/net/route").read_text()
    if network_devices - {"lo"} or routes.splitlines()[1:]:
        raise SystemExit("OS network isolation is required; run with unshare --net.")

    runs = ROOT / "作業補助" / "cloud-qa"
    runs.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    for name in ("home", "config", "cache", "user-data", "tmp", "data", "install", "browsers"):
        (work / name).mkdir()
    os.environ.update({
        "HOME": str(work / "home"), "XDG_CONFIG_HOME": str(work / "config"),
        "XDG_CACHE_HOME": str(work / "cache"), "XDG_DATA_HOME": str(work / "user-data"),
        "TMPDIR": str(work / "tmp"), "DIGITALBUILDER_DATA_DIR": str(work / "data"),
        "DIGITALBUILDER_INSTALL_ROOT": str(work / "install"),
        "PLAYWRIGHT_BROWSERS_PATH": str(work / "browsers"),
        "DIGITALBUILDER_DEVELOPMENT": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    for name in ("DBUS_SESSION_BUS_ADDRESS", "DIGITALBUILDER_UPDATE_HEALTH_FILE", "DIGITALBUILDER_UPDATE_HEALTH_NONCE"):
        os.environ.pop(name, None)
    tempfile.tempdir = str(work / "tmp")
    sys.path.insert(0, str(ROOT))
    # Resolve tests.* helpers to this checkout even if a dependency has a tests package.
    package = types.ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package

    import tkinter as tk
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()  # Fail explicitly; do not hide a DnD error with a Tk fallback.
    try:
        root.withdraw()
        root.update_idletasks()
        root.update()
        gui = {"tk": str(root.tk.call("package", "require", "Tk")),
               "tkdnd": str(root.tk.call("package", "require", "tkdnd")),
               "screen": [root.winfo_screenwidth(), root.winfo_screenheight()]}
    finally:
        root.destroy()

    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    def cases(value):
        for item in value:
            if isinstance(item, unittest.TestSuite):
                yield from cases(item)
            else:
                yield item
    inventory = list(cases(suite))
    for test in inventory:
        if test._testMethodName == "test_release_builder_includes_only_code_allowlist_and_signs_output":
            if not all((ROOT / "assets" / name).is_file() for name in ("DigitalBuileder_GR.ico", "DigitalBuileder_GR.png")):
                method = getattr(type(test), test._testMethodName)
                method.__unittest_skip__ = True
                method.__unittest_skip_why__ = "Application image assets excluded from sparse QA checkout; release packaging not validated"

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    source_files = [ROOT / "app.py", ROOT / "launcher.py", ROOT / "requirements.txt"]
    for directory in ("invoice_manager", "updater", "tests", "tools"):
        source_files.extend((ROOT / directory).rglob("*.py"))
    source_files.extend((ROOT / "tools" / "cloud").glob("*.sh"))
    source_files.extend((ROOT / "tools" / "cloud").glob("*.txt"))
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "source_head": head,
        "working_tree_status": status,
        "source_files_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(set(source_files)) if p.is_file()},
        "python": sys.version, "platform": platform.platform(), "gui": gui,
        "collected": len(inventory), "run": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped) - len(result.expectedFailures),
        "failures": [{"id": t.id(), "traceback": trace} for t, trace in result.failures],
        "errors": [{"id": t.id(), "traceback": trace} for t, trace in result.errors],
        "skips": [{"id": t.id(), "reason": reason} for t, reason in result.skipped],
        "seconds": time.monotonic() - started,
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "network_devices": sorted(network_devices), "network_routes": routes,
        "isolated_work_dir": str(work),
    }
    output = args.output_dir.resolve() if args.output_dir else work / "reports"
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("QA report:", output / "result.json")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
