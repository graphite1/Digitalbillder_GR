"""Manual recovery CLI; the fixed ``launcher.py`` is the normal update path."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from updater import (
    TRUSTED_PUBLIC_KEYS,
    UpdateError,
    activate_pending,
    application_lock,
    resolve_active_release,
)
from launcher import run_release_healthcheck

def main() -> int:
    parser = argparse.ArgumentParser(description="準備済み更新を安全に適用します")
    parser.add_argument("--install-root", type=Path, default=ROOT)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", type=Path, required=True)
    parser.add_argument("--resolve-only", action="store_true")
    args = parser.parse_args()
    if not TRUSTED_PUBLIC_KEYS:
        print("署名検証用公開鍵が設定されていません。", file=sys.stderr)
        return 2
    try:
        with application_lock(args.install_root):
            if not args.resolve_only:
                activate_pending(
                    args.install_root,
                    TRUSTED_PUBLIC_KEYS,
                    data_dir=args.data_dir,
                    launch_healthcheck=lambda release, version: run_release_healthcheck(
                        release,
                        version,
                        data_dir=args.data_dir,
                        python_executable=args.python_executable,
                        install_root=args.install_root,
                    ),
                )
            print(resolve_active_release(args.install_root, TRUSTED_PUBLIC_KEYS))
            return 0
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
