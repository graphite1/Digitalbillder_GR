"""Offline smoke test for an installed Windows distribution.

This module deliberately does not import the application.  It is run by the
bundled interpreter (``runtime/python.exe -B``) inside the guest VM and only
uses synthetic data under ``installRoot/test-data``.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


def _result(status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status, "detail": detail}
    value.update(extra)
    return value


def _run(label: str, action: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = action()
        if isinstance(value, dict) and value.get("status") in {"pass", "fail", "skip"}:
            return value
        return _result("pass", str(value) if value is not None else "")
    except Exception as exc:  # one failed component must not hide other results
        return _result("fail", f"{type(exc).__name__}: {exc}")


def runtime_check(install_root: Path) -> dict[str, Any]:
    expected = (install_root / "runtime" / "python.exe").resolve()
    actual = Path(sys.executable).resolve()
    # Path comparison is case-insensitive on Windows, while resolve() is safe
    # for the POSIX test runner used by CI.
    same = os.path.normcase(str(expected)) == os.path.normcase(str(actual))
    return _result(
        "pass" if same else "fail",
        "同梱runtime/python.exeで実行中" if same else "同梱runtime/python.exe以外で実行されています",
        expected=str(expected), actual=str(actual), python=sys.version,
    )


def layout_check(install_root: Path) -> dict[str, Any]:
    required = [install_root / "launcher.py", install_root / "runtime" / "python.exe"]
    missing = [str(p.relative_to(install_root)) for p in required if not p.is_file()]
    # A release may be at the root or selected under .updates/releases; this
    # is informational because the smoke test must not load application code.
    source = (install_root / "app.py").is_file() or any(
        (install_root / ".updates" / "releases").glob("*/app.py")
    ) if (install_root / ".updates" / "releases").is_dir() else (install_root / "app.py").is_file()
    if not source:
        missing.append("app.py (root or .updates/releases/*)")
    return _result("pass" if not missing else "fail", "導入構造を確認" if not missing else "不足: " + ", ".join(missing), required=[str(p) for p in required], source_present=source)


def imports_check() -> dict[str, Any]:
    names = ("sqlite3", "fitz", "tkinter", "PIL", "keyring", "openpyxl", "tkinterdnd2", "playwright", "cryptography")
    versions: dict[str, str | None] = {}
    failures: list[str] = []
    for name in names:
        try:
            module = importlib.import_module(name)
            versions[name] = str(getattr(module, "__version__", "available"))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return _result("pass" if not failures else "fail", "必要部品をimport" if not failures else "; ".join(failures), versions=versions)


def pdf_check(work: Path) -> dict[str, Any]:
    import fitz  # type: ignore
    from PIL import Image  # type: ignore

    pdf = work / "synthetic.pdf"
    rendered = work / "synthetic-render.png"
    doc = fitz.open()
    page = doc.new_page(width=240, height=120)
    page.insert_text((20, 60), "Digitalbuilder guest smoke", fontsize=12)
    doc.save(str(pdf))
    doc.close()
    with fitz.open(str(pdf)) as opened:
        if opened.page_count != 1:
            raise RuntimeError("PDF page count mismatch")
        pix = opened[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        pix.save(str(rendered))
    with Image.open(rendered) as image:
        size = image.size
        if size[0] <= 0 or size[1] <= 0:
            raise RuntimeError("rendered image is empty")
    return _result("pass", "架空PDF生成・render・画像読込", pdf_bytes=pdf.stat().st_size, image_size=size)


def tkinter_check() -> dict[str, Any]:
    import tkinter as tk

    root = tk.Tk()
    try:
        root.withdraw()
        label = tk.Label(root, text="guest smoke")
        label.pack()
        root.update_idletasks()
        root.update()
        return _result("pass", "Tk window create/update/destroy")
    finally:
        root.destroy()


def sqlite_check(work: Path) -> dict[str, Any]:
    database = work / "synthetic.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("create table sample (id integer primary key, value text not null)")
        connection.execute("insert into sample(value) values (?)", ("guest-only",))
        connection.commit()
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("select value from sample where id = 1").fetchone()
    if row != ("guest-only",):
        raise RuntimeError(f"reopen mismatch: {row!r}")
    return _result("pass", "架空SQLiteデータを保存・close・reopen照合")


def run_checks(install_root: Path) -> dict[str, Any]:
    install_root = install_root.resolve()
    test_data = install_root / "test-data"
    test_data.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="guest-smoke-", dir=str(test_data)))
    try:
        checks = {
            "guest_runtime": runtime_check(install_root),
            "installed_layout": layout_check(install_root),
            "imports": imports_check(),
            "pdf_render": _run("pdf_render", lambda: pdf_check(work)),
            "tk_window": _run("tk_window", tkinter_check),
            "sqlite_reopen": _run("sqlite_reopen", lambda: sqlite_check(work)),
            "network": _result("pass", "ネットワーク処理を実装せず、外部接続なし"),
        }
        return {
            "schema": 1,
            "test": "guest-offline-smoke",
            "install_root": str(install_root),
            "runtime": {"executable": sys.executable, "version": sys.version, "implementation": sys.implementation.name},
            "checks": checks,
            "scope": {"application_started": False, "auto_update": False, "real_ledger": False, "credentials": False},
            "untested": ["Windows Sandbox/Hyper-V isolation itself", "Digital Billder connection and invoice retrieval", "application startup and auto-update", "real ledger and Windows Credential Manager"],
            "overall": "pass" if all(v["status"] == "pass" for v in checks.values()) else "fail",
        }
    finally:
        # Leave no synthetic data in the installed data directory after the run.
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline guest smoke test")
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.install_root
    output = args.output
    if not root.is_absolute() or not output.is_absolute():
        parser.error("--install-root と --output は絶対パスで指定してください")
    if not root.is_dir():
        parser.error(f"install root not found: {root}")
    # Running from the host Python would make a guest result misleading.
    guard = runtime_check(root)
    if guard["status"] != "pass":
        result = {
            "schema": 1,
            "test": "guest-offline-smoke",
            "install_root": str(root.resolve()),
            "runtime": {"executable": sys.executable, "version": sys.version, "implementation": sys.implementation.name},
            "checks": {"guest_runtime": guard},
            "scope": {"application_started": False, "auto_update": False, "real_ledger": False, "credentials": False},
            "untested": ["All component checks skipped because bundled runtime guard failed"],
            "overall": "fail",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2
    result = run_checks(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
