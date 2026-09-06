"""Compatibility probes using an immutable local copy of the published 1.0.4.

All databases live in a disposable directory under 作業補助/配布検証. The old
release is read-only: isolated Python processes use -B to suppress bytecode.
This release gate skips when that external published fixture is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_RELEASE = ROOT / ".updates" / "releases" / "4-1.0.4"
HELPERS = ROOT / "作業補助" / "配布検証"


@unittest.skipUnless((OLD_RELEASE / "invoice_manager" / "db.py").is_file(),
                     "Published 1.0.4 fixture is required for this release gate")
class Release105CompatibilityTests(unittest.TestCase):
    def setUp(self):
        HELPERS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="release105-compat-", dir=HELPERS)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = self.directory / "data"
        self.data.mkdir()
        self.old_hashes = {str(path.relative_to(OLD_RELEASE)): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in OLD_RELEASE.rglob("*.py")}
        self.run_code(OLD_RELEASE, '''
from invoice_manager import db, repositories as repo
db.initialize_database()
p = repo.get_or_create_project("P105", "Synthetic release compatibility project")
v = repo.get_or_create_vendor("Synthetic vendor")
c = repo.save_work_type_code(p, "D301", "Synthetic work type")
with db.get_connection() as connection:
    i = connection.execute("""INSERT INTO invoices
        (external_id, project_id, vendor_id, invoice_date, billing_month,
         total_amount, total_amount_excluded, created_at, updated_at)
        VALUES ('11111111-1111-4111-8111-111111111111', ?, ?, '2026-08-09',
                '2026-08', 25252, 22956, '2026-08-09', '2026-08-09')""", (p, v)).lastrowid
repo.save_invoice_allocation(i, c, 22956, "Synthetic preserved memo", 7)
print(json.dumps({"version": "seeded"}))
''')

    def tearDown(self):
        current = {str(path.relative_to(OLD_RELEASE)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in OLD_RELEASE.rglob("*.py")}
        self.assertEqual(current, self.old_hashes, "Published source fixture was changed")

    def run_code(self, source, code):
        environment = os.environ.copy()
        for name in ("DIGITALBUILDER_DEVELOPMENT", "DIGITALBUILDER_UPDATE_HEALTH_FILE", "DIGITALBUILDER_UPDATE_HEALTH_NONCE"):
            environment.pop(name, None)
        environment["DIGITALBUILDER_DATA_DIR"] = str(self.data)
        environment["DIGITALBUILDER_INSTALL_ROOT"] = str(self.directory)
        prefix = "import sys, json, os; sys.path.insert(0, sys.argv[1]); sys.dont_write_bytecode = True\n"
        result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-c", prefix + code, str(source)],
                                cwd=self.directory, env=environment, text=True, encoding="utf-8",
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-6000:] + result.stdout[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def snapshot(self, source=OLD_RELEASE):
        return self.run_code(source, '''
from invoice_manager import db
with db.get_connection() as c:
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    state = {t: {"columns": [r[1] for r in c.execute('PRAGMA table_info("'+t+'")')],
                 "rows": [dict(r) for r in c.execute('SELECT * FROM "'+t+'" ORDER BY rowid')]} for t in tables}
print(json.dumps(state))
''')

    def migrate(self):
        return self.run_code(ROOT, '''
from invoice_manager import db
db.initialize_database()
print(json.dumps({"migrated": True}))
''')

    def test_additive_migration_preserves_all_old_columns_and_rows_and_is_idempotent(self):
        before = self.snapshot()
        self.assertNotIn("tax_rounding_adjustment", before["invoice_allocations"]["columns"])
        self.migrate()
        after = self.snapshot()
        self.assertEqual(set(before), set(after))
        for table, old in before.items():
            expected_columns = old["columns"] + (["tax_rounding_adjustment"] if table == "invoice_allocations" else [])
            self.assertEqual(after[table]["columns"], expected_columns, table)
            projected = [{key: row[key] for key in old["columns"]} for row in after[table]["rows"]]
            self.assertEqual(projected, old["rows"], table)
        self.assertEqual(after["invoice_allocations"]["rows"][0]["tax_rounding_adjustment"], 0)
        self.migrate()
        self.assertEqual(self.snapshot(), after)

    def test_old_code_can_initialize_read_insert_and_update_migrated_unadjusted_rows(self):
        self.migrate()
        result = self.run_code(OLD_RELEASE, '''
from invoice_manager import db, repositories as repo
db.initialize_database()
first = dict(repo.list_invoice_allocations(1)[0])
repo.save_invoice_allocation(1, first["work_type_code_id"], 200, "Old version edit", 3, allocation_id=first["id"])
repo.save_invoice_allocation(1, first["work_type_code_id"], 100, "Old version insert", 4)
print(json.dumps([dict(row) for row in repo.list_invoice_allocations(1)]))
''')
        self.assertEqual([(r["amount_excluded"], r["amount"], r["tax_rounding_adjustment"]) for r in result],
                         [(200, 220, 0), (100, 110, 0)])

    def test_old_and_new_health_checks_are_read_only_and_old_health_works_after_migration(self):
        for source in (ROOT, OLD_RELEASE):
            before = (self.data / "app.db").read_bytes()
            result = self.run_code(source, '''
from pathlib import Path
root = Path(os.environ["DIGITALBUILDER_INSTALL_ROOT"])
health = root / ".updates" / "health"
health.mkdir(parents=True, exist_ok=True)
os.environ["DIGITALBUILDER_UPDATE_HEALTH_FILE"] = str(health / "synthetic.json")
os.environ["DIGITALBUILDER_UPDATE_HEALTH_NONCE"] = "synthetic-release-test"
import app
path, nonce = app._validate_update_health()
print(json.dumps({"nonce": nonce, "ok": True}))
''')
            self.assertTrue(result["ok"])
            self.assertEqual((self.data / "app.db").read_bytes(), before)
            self.migrate()

    def test_old_amount_edit_after_new_rounding_leaves_unsupported_stale_adjustment(self):
        self.migrate()
        adjusted = self.run_code(ROOT, '''
from invoice_manager.services.allocation_rounding import preview_rounding_adjustment, apply_rounding_adjustment
from invoice_manager import repositories as repo
preview = preview_rounding_adjustment(1, 1)
apply_rounding_adjustment(1, 1, preview)
print(json.dumps(dict(repo.list_invoice_allocations(1)[0])))
''')
        self.assertEqual((adjusted["amount"], adjusted["tax_rounding_adjustment"]), (25252, 1))
        old_edited = self.run_code(OLD_RELEASE, '''
from invoice_manager import repositories as repo
row = dict(repo.list_invoice_allocations(1)[0])
repo.save_invoice_allocation(1, row["work_type_code_id"], 100, "Unsupported downgrade amount edit", 7, allocation_id=1)
print(json.dumps(dict(repo.list_invoice_allocations(1)[0])))
''')
        # This is an explicit downgrade limitation, not a claim of full backward write compatibility.
        self.assertEqual((old_edited["amount_excluded"], old_edited["amount"], old_edited["tax_rounding_adjustment"]), (100, 110, 1))
        plan = self.run_code(ROOT, '''
from invoice_manager.services.web_allocation_plan import build_allocation_plan
print(json.dumps({"errors": build_allocation_plan(1).errors}, ensure_ascii=False))
''')
        self.assertIn("保存済みの税込金額と税率計算に差があります。端数の扱いを確認してください。", plan["errors"])


if __name__ == "__main__":
    unittest.main()
