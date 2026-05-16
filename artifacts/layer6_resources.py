"""Layer 6 — Resource catalog normalization.

Populates:
  - csd_catalog  from CSD parser output
  - screen_map   from BMS parser output  (already done by bms_parser.py inline)
  - copybook_use catalog with consumer-program lists
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

from parsers.csd.csd_parser import parse_csd_file
from parsers.bms.bms_parser import parse_bms_file


def persist_csd(csd_file: pathlib.Path, con: sqlite3.Connection) -> int:
    """Parse one CSD file and insert rows into csd_catalog."""
    rows = parse_csd_file(csd_file, con)
    return len(rows) if rows else 0


def persist_bms(bms_file: pathlib.Path, con: sqlite3.Connection) -> int:
    """Parse one BMS file and insert rows into screen_map."""
    rows = parse_bms_file(bms_file, con)
    return len(rows) if rows else 0


def build_copybook_catalog(con: sqlite3.Connection) -> dict[str, list[str]]:
    """Return {copybook_name: [program_name, ...]} for every copybook_use row."""
    rows = con.execute(
        "SELECT cu.copybook_name, n.name AS program_name "
        "FROM copybook_use cu "
        "JOIN nodes n ON n.uuid = cu.program_uuid "
        "ORDER BY cu.copybook_name, n.name"
    ).fetchall()
    catalog: dict[str, list[str]] = {}
    for cpy_name, prog_name in rows:
        catalog.setdefault(cpy_name, []).append(prog_name)
    return catalog


def emit_layer6_artifact(con: sqlite3.Connection, output_dir: pathlib.Path) -> dict:
    """Write layer6 summary JSON artifact."""
    csd_rows = [dict(r) for r in con.execute("SELECT * FROM csd_catalog").fetchall()]
    screen_rows = [dict(r) for r in con.execute("SELECT * FROM screen_map").fetchall()]
    copybook_catalog = build_copybook_catalog(con)

    artifact = {
        "csd_definitions": csd_rows,
        "screen_maps": screen_rows,
        "copybook_consumers": copybook_catalog,
    }
    out = output_dir / "layer6_resources.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    return artifact
