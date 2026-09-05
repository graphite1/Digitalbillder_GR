from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from tools import build_windows_setup as builder
from updater.security import b64url_encode


class SetupBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='digitalbuilder-setup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        keys = patch.object(builder, 'TRUSTED_PUBLIC_KEYS', {'test-key': b64url_encode(public)})
        keys.start(); self.addCleanup(keys.stop)
        self.archive = self.root / 'Digitalbuilder_GR-1.0.4-windows-x64-r1.zip'
        self.archive.write_bytes(b'verified archive contents')
        self.manifest = self.root / 'manifest.json'
        self.payload = {'schema': 1, 'product': 'Digitalbuilder_GR', 'kind': 'windows-portable',
            'platform': 'windows-x64', 'version': '1.0.4', 'sequence': 3, 'build': 1,
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            'archive': {'filename': self.archive.name, 'size': self.archive.stat().st_size,
                        'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}}

    def sign(self):
        raw = json.dumps(self.payload).encode()
        self.manifest.write_text(json.dumps({'key_id': 'test-key', 'payload': b64url_encode(raw),
            'signature': b64url_encode(self.key.sign(raw))}), encoding='utf8')

    def test_config_pins_verified_origin_sequence_and_archive(self):
        self.sign()
        result = builder.read_distribution(self.manifest, self.archive)
        config = builder.setup_config(result)
        self.assertIn('/api/installers/3/download', config)
        self.assertIn(result['archive']['sha256'], config)
        self.assertNotIn(str(self.root), config)

    def test_altered_archive_rejected_even_when_same_size(self):
        self.sign()
        self.archive.write_bytes(b'X' * self.archive.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            builder.read_distribution(self.manifest, self.archive)

    def test_tampered_manifest_rejected(self):
        self.sign()
        e = json.loads(self.manifest.read_text())
        e['signature'] = b64url_encode(bytes(64))
        self.manifest.write_text(json.dumps(e), encoding='utf8')
        with self.assertRaises(InvalidSignature):
            builder.read_distribution(self.manifest, self.archive)

    def test_expired_or_wrong_product_not_compiled(self):
        for change in ({'expires_at': '2000-01-01T00:00:00Z'}, {'product': 'Other'}):
            with self.subTest(change=change):
                original = dict(self.payload)
                self.payload.update(change); self.sign()
                with self.assertRaises(ValueError):
                    builder.read_distribution(self.manifest, self.archive)
                self.payload = original


if __name__ == '__main__':
    unittest.main()
