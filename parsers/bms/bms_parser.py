"""BMS macro parser — extracts DFHMSD/DFHMDI/DFHMDF definitions."""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3

from storage.db import init_db, transaction
from storage.uuid_gen import make_named_uuid

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


def _norm_path(p: pathlib.Path) -> str:
    try:
        return str(p.resolve().relative_to(_PROJECT_ROOT.resolve()))
    except ValueError:
        return str(p)

_MSD_RE  = re.compile(r"DFHMSD\s+TYPE=MAP|DFHMSD\s+TYPE=&SYSPARM", re.IGNORECASE)
_MDI_RE  = re.compile(r"([A-Z0-9]{1,7})\s+DFHMDI\b.*?SIZE=\((\d+),(\d+)\)", re.IGNORECASE | re.DOTALL)
_MDF_RE  = re.compile(r"([A-Z0-9]{1,7})\s+DFHMDF\s+", re.IGNORECASE)
_POS_RE  = re.compile(r"POS=\((\d+),(\d+)\)", re.IGNORECASE)
_LEN_RE  = re.compile(r"LENGTH=(\d+)", re.IGNORECASE)
_ATTRB_RE = re.compile(r"ATTRB=\(?([A-Z,]+)\)?", re.IGNORECASE)
_PIC_RE  = re.compile(r"PICIN='?([^',\s]+)'?", re.IGNORECASE)


def parse_bms_file(bms_file: pathlib.Path, db_path: pathlib.Path) -> dict:
    """Parse a BMS map file and persist to screen_map table."""
    init_db(db_path)
    text = _join_continuations(bms_file.read_text(errors="replace").splitlines())

    maps: list[dict] = []
    current_mapset = bms_file.stem.upper()
    current_map: dict | None = None
    errors: list[str] = []

    with transaction() as con:
        for line in text:
            line_s = line.strip()
            if not line_s or line_s.startswith("*"):
                continue

            m_mdi = _MDI_RE.search(line)
            m_mdf = _MDF_RE.search(line)

            if m_mdi:
                map_name = m_mdi.group(1).upper()
                current_map = {"name": map_name, "mapset": current_mapset, "fields": []}
                maps.append(current_map)

            elif m_mdf and current_map:
                field_name = m_mdf.group(1).upper()
                pos_m = _POS_RE.search(line)
                len_m = _LEN_RE.search(line)
                attrb_m = _ATTRB_RE.search(line)
                pic_m = _PIC_RE.search(line)

                row = int(pos_m.group(1)) if pos_m else 0
                col = int(pos_m.group(2)) if pos_m else 0
                length = int(len_m.group(1)) if len_m else 0
                attrs = attrb_m.group(1) if attrb_m else ""
                pic = pic_m.group(1) if pic_m else None

                current_map["fields"].append({
                    "name": field_name, "row": row, "col": col,
                    "length": length, "attrs": attrs, "pic": pic,
                })
                con.execute(
                    """
                    INSERT OR IGNORE INTO screen_map
                        (map_name, mapset_name, field_name,
                         position_row, position_col, length, attributes, pic)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (current_map["name"], current_mapset, field_name,
                     row, col, length, attrs, pic),
                )

        # Coverage
        con.execute(
            """
            INSERT OR REPLACE INTO parse_coverage
                (source_file, source_type, status, parse_errors, error_messages)
            VALUES (?,?,?,?,?)
            """,
            (_norm_path(bms_file), "BMS", "OK", 0, "[]"),
        )

    return {"file": str(bms_file), "maps": len(maps), "status": "OK"}


def _join_continuations(lines: list[str]) -> list[str]:
    """Join HLASM continuation lines (trailing '-' or ',' means more follows)."""
    result: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        is_cont = (
            stripped.endswith("-")
            or stripped.endswith(",")
            or (stripped.endswith("X") and len(stripped) >= 71)
        )
        # Remove the continuation marker and trailing whitespace
        core = stripped.rstrip("-").rstrip("X").rstrip(",").rstrip()

        if result and result[-1].endswith(" "):
            # Previous line was a continuation — append this content to it
            result[-1] = result[-1].rstrip() + " " + stripped.strip().rstrip("-").rstrip(",").rstrip()
            if is_cont:
                result[-1] += " "  # mark that this line also continues
        elif is_cont:
            result.append(core + " ")
        else:
            result.append(stripped)
    return result
