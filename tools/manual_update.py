"""Manual-download entrypoint; use the selected installation's trusted updater."""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True


def installation(path: Path) -> tuple[Path, Path, Path]:
    root = path.expanduser().resolve()
    required = ("launcher.py", "app.py", "updater/__init__.py", "invoice_manager/version.py", ".venv/Scripts/python.exe")
    if not all((root / name).is_file() for name in required):
        raise ValueError("更新機能を導入済みのアプリフォルダーを選んでください。解凍した手動用ZIPのフォルダーは対象ではありません。")
    configured = os.environ.get("DIGITALBUILDER_DATA_DIR", "").strip()
    data = Path(configured).expanduser().resolve() if configured else root / "data"
    if not (data / "app.db").is_file():
        raise ValueError("既存の台帳を確認できません。元のアプリフォルダーとデータ保存先を確認してください。")
    return root, data, root / ".venv/Scripts/python.exe"


def bundled_manifest(bundle: Path):
    from updater import DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS, get_runtime_fingerprint
    from updater.security import verify_release_envelope
    path = bundle / "update.manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("署名付き更新情報が見つからないか、サイズが不正です。ZIPをすべて解凍してください。")
    return verify_release_envelope(path.read_bytes(), TRUSTED_PUBLIC_KEYS,
        base_url=DEFAULT_UPDATE_BASE_URL, runtime_fingerprint=get_runtime_fingerprint())


def _sequence(active: Path, root: Path) -> int:
    if active != root:
        return int(json.loads((active / "version.json").read_text(encoding="utf-8"))["sequence"])
    # Read the fixed baseline identity without importing a cached release module.
    tree = ast.parse((root / "invoice_manager/version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RELEASE_SEQUENCE" for t in node.targets):
            value = ast.literal_eval(node.value)
            if type(value) is int and value >= 0:
                return value
    raise ValueError("現在のアプリの配布番号を確認できません。")


def prepare_update(root: Path, bundle: Path) -> bool:
    """Stage only. The fixed launcher alone activates and backs up the database."""
    from updater import TRUSTED_PUBLIC_KEYS, application_lock, resolve_active_release, stage_update
    manifest = bundled_manifest(bundle)
    archive = bundle / "update.zip"
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != manifest.archive.size:
        raise ValueError("更新ZIPが見つからないか、署名付き更新情報とサイズが一致しません。")
    expected_url = f"{manifest.base_url}/api/releases/{manifest.sequence}/download"

    def local_archive(request, *, timeout):
        if request.full_url != expected_url or request.get_method() != "GET":
            raise ValueError("想定外の更新ファイル要求です。")
        with archive.open("rb") as file:
            content = file.read(manifest.archive.size + 1)
        stream = io.BytesIO(content)
        stream.headers = {"Content-Length": str(manifest.archive.size)}
        stream.geturl = lambda: expected_url
        return stream

    with application_lock(root):
        active = resolve_active_release(root, TRUSTED_PUBLIC_KEYS)
        if (root / ".updates/pending.json").exists():
            raise ValueError("別の更新が準備済みです。元の起動.batから起動して適用した後、もう一度お試しください。")
        if _sequence(active, root) >= manifest.sequence:
            return False
        stage_update(manifest, root, opener=local_archive, progress=print)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="手動用ZIPから既存のDigitalbuilder GRを更新して起動します。")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    import tkinter as tk
    from tkinter import filedialog, messagebox
    window = tk.Tk()
    window.withdraw()
    try:
        selected = args.install_root
        if selected is None:
            folder = filedialog.askdirectory(parent=window, title="現在使っているDigitalbuilder GRのフォルダーを選択（起動.batがある場所）", mustexist=True)
            if not folder:
                return 0
            selected = Path(folder)
        root, data, python = installation(selected)
        if Path(sys.executable).resolve() != python.resolve():
            if args.worker:
                raise ValueError("選択したアプリのPython環境で実行できませんでした。")
            return subprocess.run([str(python), "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()), "--install-root", str(root), "--worker"], check=False).returncode
        # Import trust configuration and verification from the explicitly selected installation.
        sys.path.insert(0, str(root))
        bundle = Path(__file__).resolve().parent
        manifest = bundled_manifest(bundle)
        if not messagebox.askokcancel("Digitalbuilder GR 手動更新", f"配布版: v{manifest.version}\n\nアプリ: {root}\n台帳: {data}\n\n開いているアプリを閉じてください。更新ファイルを検証し、バックアップを取ってから起動します。既に同じ版か新しい版なら、その版を起動します。", parent=window):
            return 0
        prepare_update(root, bundle)
        environment = os.environ.copy()
        environment["DIGITALBUILDER_DATA_DIR"] = str(data)
        environment["DIGITALBUILDER_INSTALL_ROOT"] = str(root)
        return subprocess.run([str(python), "-B", "-X", "utf8", str(root / "launcher.py")], cwd=root, env=environment, check=False).returncode
    except Exception as exc:
        messagebox.showerror("手動更新を開始できません", str(exc), parent=window)
        return 1
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
