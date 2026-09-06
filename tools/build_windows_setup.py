"""Build a tiny Windows setup EXE pinned to a verified distribution archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from updater.config import DEFAULT_UPDATE_BASE_URL, TRUSTED_PUBLIC_KEYS
from updater.security import b64url_decode


def compile_executable(sources: list[Path], output: Path) -> None:
    compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise ValueError('Windowsの.NET Framework C#コンパイラーが必要です。')
    if output.exists():
        raise ValueError('既存のEXEは上書きしません。')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
        '/utf8output', '/out:' + str(output),
        '/win32icon:' + str(ROOT / 'assets/DigitalBuileder_GR.ico'),
        '/reference:System.dll', '/reference:System.Core.dll',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
        '/reference:Microsoft.CSharp.dll', *map(str, sources),
    ], capture_output=True, text=True, encoding='utf8', errors='replace', timeout=90)
    if result.returncode or not output.is_file():
        raise ValueError('EXEを作成できません: ' + result.stdout + result.stderr)


def compile_launcher(output: Path) -> None:
    compile_executable([ROOT / 'tools/windows/Launcher.cs'], output)


def read_distribution(envelope_path: Path, archive_path: Path) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if envelope_path.stat().st_size > 100_000:
        raise ValueError('配布情報のサイズが上限を超えています。')
    envelope = json.loads(envelope_path.read_bytes())
    if set(envelope) != {'key_id', 'payload', 'signature'}:
        raise ValueError('署名付き配布情報の形式が不正です。')
    payload = b64url_decode(envelope['payload'], '配布情報')
    key = Ed25519PublicKey.from_public_bytes(b64url_decode(TRUSTED_PUBLIC_KEYS[envelope['key_id']], '公開鍵'))
    key.verify(b64url_decode(envelope['signature'], '署名'), payload)
    m = json.loads(payload)
    if (m.get('schema') != 1 or m.get('product') != 'Digitalbuilder_GR'
            or m.get('kind') != 'windows-portable' or m.get('platform') != 'windows-x64'
            or not re.fullmatch(r'\d+\.\d+\.\d+', m.get('version', ''))
            or type(m.get('sequence')) is not int or m['sequence'] <= 0
            or type(m.get('build')) is not int or m['build'] <= 0):
        raise ValueError('対象のWindows版を確認できません。')
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(m['expires_at'].replace('Z', '+00:00'))
    if expires <= now:
        raise ValueError('配布情報の有効期限が切れています。')
    archive = m['archive']
    expected_name = f"Digitalbuilder_GR-{m['version']}-windows-x64-r{m['build']}.zip"
    if (archive.get('filename') != expected_name or archive_path.name != expected_name
            or type(archive.get('size')) is not int or not 0 < archive['size'] <= 2 * 1024**3
            or not re.fullmatch(r'[0-9a-f]{64}', archive.get('sha256', ''))
            or archive_path.stat().st_size != archive['size']):
        raise ValueError('配布ファイル名またはサイズが署名情報と一致しません。')
    digest = hashlib.sha256()
    with archive_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != archive['sha256']:
        raise ValueError('配布ファイルのSHA-256が署名情報と一致しません。')
    return m


def setup_config(m: dict) -> str:
    fields = {
        'Version': m['version'],
        'ArchiveUrl': DEFAULT_UPDATE_BASE_URL + f"/api/installers/{m['sequence']}/download",
        'ArchiveSha256': m['archive']['sha256'],
        'ArchiveRoot': m['archive']['filename'][:-4],
        'ExpiresAt': m['expires_at'],
    }
    lines = ['internal static class InstallerConfig {']
    for key, value in fields.items():
        lines.append(f' internal const string {key} = {json.dumps(value, ensure_ascii=True)};')
    lines.append(f" internal const long ArchiveSize = {m['archive']['size']}L;")
    lines.append(f" internal const int Sequence = {m['sequence']};")
    return '\n'.join([*lines, '}'])


def build_setup(archive: Path, envelope: Path, output_dir: Path) -> tuple[Path, Path]:
    m = read_distribution(envelope, archive)
    filename = f"Digitalbuilder_GR-Setup-{m['version']}-r{m['build']}.exe"
    exe = output_dir / filename
    metadata = exe.with_suffix('.json')
    if exe.exists() or metadata.exists():
        raise ValueError('既存のセットアップは上書きしません。')
    with tempfile.TemporaryDirectory(prefix='digitalbuilder-setup-build-') as directory:
        config = Path(directory) / 'InstallerConfig.cs'
        config.write_text(setup_config(m), encoding='utf8')
        compile_executable([ROOT / 'tools/windows/Setup.cs', config], exe)
    value = {'schema': 1, 'version': m['version'], 'installer_sequence': m['sequence'],
             'build': m['build'], 'filename': filename, 'size': exe.stat().st_size,
             'sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
             'archive_size': m['archive']['size'], 'archive_sha256': m['archive']['sha256'],
             'expires_at': m['expires_at']}
    metadata.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return exe, metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='署名済みWindows版を取得する小さなセットアップEXEを作成します。')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    for result in build_setup(args.archive, args.manifest, args.output_dir):
        print(result)
