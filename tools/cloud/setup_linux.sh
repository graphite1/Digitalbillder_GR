#!/usr/bin/env bash
# Evaluation workspace only. No browser install, login, updater or signing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3.13}"
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  echo 'This evaluated wheel lock requires Linux x86_64.' >&2; exit 2
fi
"$PYTHON" -c 'import sys, tkinter; assert sys.version_info[:2] == (3, 13), sys.version; print(sys.version); print("Tk", tkinter.TkVersion)'
if [ -n "${BOOTSTRAP_PYTHON:-}" ]; then
  # Explicit workaround for a test image whose Python lacks ensurepip.
  "$PYTHON" -m venv --without-pip .venv
  "$BOOTSTRAP_PYTHON" -m pip --python "$ROOT/.venv/bin/python" install \
    --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes \
    --index-url https://pypi.org/simple -r tools/cloud/requirements-linux-py313.lock.txt
  "$BOOTSTRAP_PYTHON" -m pip --python "$ROOT/.venv/bin/python" check
else
  "$PYTHON" -m venv .venv
  .venv/bin/python -m pip install \
    --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes \
    --index-url https://pypi.org/simple -r tools/cloud/requirements-linux-py313.lock.txt
  .venv/bin/python -m pip check
fi
printf '\nRun tests with OS network isolation and a Tk display; see docs/CLOUD_LINUX_QA.md\n'
