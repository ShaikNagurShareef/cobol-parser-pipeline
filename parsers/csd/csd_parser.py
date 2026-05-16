"""CSD (CICS System Definition) parser.

Extracts DEFINE PROGRAM/TRANSACTION/MAPSET/FILE/LIBRARY entries
and persists to the csd_catalog table.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3

from storage.db import init_db, transaction

_DEFINE_RE = re.compile(
    r"DEFINE\s+(PROGRAM|TRANSACTION|MAPSET|FILE|LIBRARY)\s*\(\s*([A-Z0-9@#$-]+)\s*\)",
    re.IGNORECASE,
)
_GROUP_RE = re.compile(r"GROUP\s*\(\s*([A-Z0-9@#$-]+)\s*\)", re.IGNORECASE)
_KV_RE = re.compile(r"([A-Z]+)\s*\(\s*([^)]+)\s*\)", re.IGNORECASE)


def parse_csd_file(csd_file: pathlib.Path, db_path: pathlib.Path) -> dict:
    """Parse a CSD batch definition file and persist to csd_catalog."""
    init_db(db_path)
    text = _join_continuations(csd_file.read_text(errors="replace").splitlines())
    entries: list[dict] = []

    with transaction() as con:
        for line in text:
            line_s = line.strip()
            if not line_s or line_s.startswith("*"):
                continue

            m = _DEFINE_RE.search(line_s)
            if not m:
                continue

            kind = m.group(1).upper()
            name = m.group(2).upper()
            group_m = _GROUP_RE.search(line_s)
            group = group_m.group(1).upper() if group_m else None

            # Collect all key-value pairs as attributes
            attrs = {}
            for kv in _KV_RE.finditer(line_s):
                k = kv.group(1).upper()
                v = kv.group(2).strip()
                if k not in ("DEFINE", kind.upper()):
                    attrs[k] = v

            entries.append({"kind": kind, "name": name, "group": group, "attrs": attrs})
            con.execute(
                """
                INSERT OR IGNORE INTO csd_catalog (kind, name, group_name, attributes)
                VALUES (?,?,?,?)
                """,
                (kind, name, group, json.dumps(attrs)),
            )

        con.execute(
            """
            INSERT OR REPLACE INTO parse_coverage
                (source_file, source_type, status, parse_errors, error_messages)
            VALUES (?,?,?,?,?)
            """,
            (str(csd_file), "CSD", "OK", 0, "[]"),
        )

    return {"file": str(csd_file), "entries": len(entries), "status": "OK"}


def _join_continuations(lines: list[str]) -> list[str]:
    result: list[str] = []
    buf = ""
    for line in lines:
        stripped = line.strip()
        if stripped.endswith("+"):
            buf += " " + stripped[:-1]
        else:
            buf += " " + stripped
            result.append(buf.strip())
            buf = ""
    if buf.strip():
        result.append(buf.strip())
    return result
