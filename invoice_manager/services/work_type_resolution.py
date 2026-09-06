"""Resolve project codes using confirmed history, then the local D-code rule."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from invoice_manager import db


@dataclass(frozen=True)
class CanonicalWorkType:
    code: str
    name: str
    confirmed: bool = True


class WorkTypeResolutionError(ValueError):
    """The input cannot identify one project work type safely."""


def load_confirmed_work_types(project_id: int) -> tuple[CanonicalWorkType, ...]:
    """Read active archived codes without fetching history or changing its schema."""
    if not db.DB_PATH.exists():
        return ()
    with db.get_connection() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not {"projects", "historical_archived_invoices", "historical_archived_allocations"} <= tables:
            return ()
        rows = connection.execute(
            """
            SELECT a.work_type_code, a.work_type_name
            FROM historical_archived_allocations AS a
            JOIN historical_archived_invoices AS i ON i.id = a.historical_invoice_id
            JOIN projects AS p ON p.project_code = i.project_code
            WHERE p.id = ? AND i.source = 'digital_billder'
              AND i.status = 'archived' AND i.is_active = 1
            ORDER BY i.invoice_date DESC, i.id DESC, a.line_number DESC
            """,
            (int(project_id),),
        ).fetchall()
    by_code: dict[str, CanonicalWorkType] = {}
    for row in rows:
        code = str(row["work_type_code"])
        by_code.setdefault(code, CanonicalWorkType(code, str(row["work_type_name"])))
    return tuple(by_code[code] for code in sorted(by_code))


def load_work_type_choices(project_id: int) -> tuple[CanonicalWorkType, ...]:
    """Prefer actual codes; otherwise supplement named local codes with D + 3 digits."""
    confirmed = load_confirmed_work_types(project_id)
    if not db.DB_PATH.exists():
        return confirmed
    with db.get_connection() as connection:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'work_type_codes'"
        ).fetchone() is None:
            return confirmed
        rows = connection.execute(
            "SELECT code, name FROM work_type_codes WHERE project_id = ? ORDER BY code",
            (int(project_id),),
        ).fetchall()
    actual_numbers = {
        item.code[-3:] for item in confirmed
        if re.fullmatch(r"(?:[A-Za-z]+)?[0-9]{3}", item.code)
    }
    choices = {item.code: item for item in confirmed}
    # Add numeric rows first so an existing D-code row keeps its own manual name.
    local_rows = sorted(rows, key=lambda row: (bool(re.fullmatch(r"D[0-9]{3}", row["code"])), row["code"]))
    for row in local_rows:
        code, name = str(row["code"]), str(row["name"])
        if not re.fullmatch(r"D?[0-9]{3}", code) or not name.strip():
            continue
        number = code[-3:]
        if number not in actual_numbers:
            choices[f"D{number}"] = CanonicalWorkType(f"D{number}", name, confirmed=False)
    return tuple(choices[code] for code in sorted(choices))


def resolve_from_catalog(value: str, catalog: Iterable[CanonicalWorkType]) -> CanonicalWorkType:
    """Accept an exact official code or an unambiguous three-digit abbreviation."""
    raw = str(value or "").strip()
    if not raw:
        raise WorkTypeResolutionError("工種コードを入力してください。")
    by_code = {item.code: item for item in catalog}
    normalized_digits = unicodedata.normalize("NFKC", raw)
    if re.fullmatch(r"[0-9]{3}", normalized_digits):
        candidates = [
            item for code, item in by_code.items()
            if code == normalized_digits
            or (re.fullmatch(r"[A-Za-z]+[0-9]{3}", code) and code[-3:] == normalized_digits)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            codes = "、".join(sorted(item.code for item in candidates))
            raise WorkTypeResolutionError(
                f"工種コード {normalized_digits} に対応する正式コードが複数あります（{codes}）。"
                "正式コードを指定してください。"
            )
    elif raw in by_code:
        return by_code[raw]
    raise WorkTypeResolutionError(
        f"工種コード {raw} は、この工事の工種マスタまたはDigital Billder保管済み実績で確認できません。"
        "基本ルールで使う工種は、先に工種コードマスタへ3桁の数字と工種名を登録してください。"
    )


def resolve_work_type_code(project_id: int, value: str) -> CanonicalWorkType:
    return resolve_from_catalog(value, load_work_type_choices(project_id))
