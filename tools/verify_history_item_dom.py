"""Run wholly synthetic item-table DOM checks in an isolated new browser.

Example: unshare -Urn python -B tools/verify_history_item_dom.py --browser /usr/bin/chromium
This does not connect to the user's browser, access credentials, or contact a site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_manager.services.web_invoice_reader import (
    INVOICE_ITEM_TABLES_SCRIPT, InvoiceReadError, parse_invoice_item_tables,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, help="Existing Chromium executable; no download or install.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    devices = {line.split(":", 1)[0].strip()
               for line in Path("/proc/net/dev").read_text().splitlines()[2:]}
    if devices - {"lo"} or Path("/proc/net/route").read_text().splitlines()[1:]:
        raise SystemExit("Run in a network-isolated namespace.")
    fixture = ROOT / "tests/fixtures/invoice_item_scope.html"
    from playwright.sync_api import sync_playwright
    expected = {
        "regular": True, "official_auxiliary": True, "split": True,
        "unknown_two_columns": False, "nonadjacent_heading": False,
        "aria_named_changed_header": False, "no_thead": False,
        "multiple_thead": False, "multiple_header_rows": False,
        "spanning_main_cell": False, "nested_in_main_cell": False,
        "nested_ancestor_only": True,
    }
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
        context = browser.new_context()
        context.route("**/*", lambda route: route.abort())
        page = context.new_page()
        page.set_content(fixture.read_text(), wait_until="domcontentloaded")
        assert page.url == "about:blank", "Test must remain a new synthetic document."
        assert page.locator("section[data-case]").count() == len(expected)
        for name, should_pass in expected.items():
            panel = page.locator(f'section[data-case="{name}"]').get_by_role("tabpanel", name="請求書情報", exact=True)
            tables = panel.get_by_role("table").filter(
                has=page.get_by_role("columnheader", name="項目", exact=True))
            extracted = tables.evaluate_all(INVOICE_ITEM_TABLES_SCRIPT)
            detail = {"case": name, "candidate_tables": tables.count(), "own_header_tables": len(extracted)}
            if name == "official_auxiliary":
                legacy = tables.locator("tbody tr").evaluate_all(
                    "rows => rows.map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText.trim()))")
                assert any(len(row) != 3 or row[2] for row in legacy), "Published shape failure must reproduce."
                detail["published_failure_reproduced"] = True
            try:
                fields = parse_invoice_item_tables(extracted)
                assert fields["請求金額 (税込)"] == "¥1,100"
                actual_pass = True
            except InvoiceReadError as exc:
                actual_pass = False
                detail["error"] = str(exc)
                assert "PRIVATE" not in str(exc), "Diagnostics must not expose fixture private-field sentinels."
            assert actual_pass == should_pass, f"{name}: expected {should_pass}, got {actual_pass}"
            detail["passed"] = True
            results.append(detail)
        context.close()
        browser.close()
    report = {"result": "PASS", "cases": results, "count": len(results),
              "browser": args.browser, "network_devices": sorted(devices),
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "extractor_sha256": hashlib.sha256(INVOICE_ITEM_TABLES_SCRIPT.encode()).hexdigest(),
              "reader_sha256": hashlib.sha256((ROOT/"invoice_manager/services/web_invoice_reader.py").read_bytes()).hexdigest(),
              "real_web_accessed": False, "user_browser_accessed": False}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
