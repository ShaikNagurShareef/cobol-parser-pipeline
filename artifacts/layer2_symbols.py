"""Layer 2 artifact builder — symbol table, data dictionary, type system."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer2"


def persist(
    nodes: list[dict[str, Any]],
    copy_statements: list[dict],
    program_name: str,
    con: sqlite3.Connection,
    output_dir: pathlib.Path | None = None,
) -> None:
    """Populate data_items, conditions_88, and copybook_use tables.

    Args:
        nodes:           Typed AST nodes from ast_normalizer.
        copy_statements: CopyStatement list from preprocessor.
        program_name:    Used for the JSON artifact filename.
        con:             Open SQLite connection.
        output_dir:      Directory for Layer 2 JSON artifacts.
    """
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Find the program node
    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return
    prog_uuid = prog_node["uuid"]

    data_items: list[dict] = []
    last_non88_uuid: str | None = None  # G1: track parent for 88-level items

    for node in nodes:
        if node["kind"] != "DataItem":
            continue
        p = node.get("payload", {})
        ct = p.get("canonical_type", {})
        level = p.get("level", -1)
        item = {
            "uuid": node["uuid"],
            "program_uuid": prog_uuid,
            "name": node["name"],
            "level": level,
            "pic": p.get("pic"),
            "usage": p.get("usage"),
            "sign": p.get("sign"),
            "occurs_min": None,
            "occurs_max": None,
            "occurs_odo": None,
            "redefines": p.get("redefines"),
            "value_raw": p.get("value_raw"),
            "canonical_kind": ct.get("kind"),
            "precision": ct.get("precision"),
            "scale": ct.get("scale"),
            "signed": int(ct.get("signed", False)) if "signed" in ct else None,
            "length": ct.get("length"),
            "copybook_origin": None,  # filled by preprocessor provenance later
            "start_line": node["start_line"],
            "end_line": node["end_line"],
        }

        # OCCURS parsing
        occurs_raw = p.get("occurs")
        if occurs_raw:
            _parse_occurs(occurs_raw, item)

        data_items.append(item)
        _upsert_data_item(con, item)

        # G1: populate conditions_88 for level-88 condition names
        if level == 88 and last_non88_uuid:
            _upsert_condition_88(con, node["uuid"], last_non88_uuid,
                                 node["name"], p.get("value_raw"))
        else:
            last_non88_uuid = node["uuid"]

    # Copybook usage
    for cs in copy_statements:
        con.execute(
            """
            INSERT INTO copybook_use (program_uuid, copybook_name, replacing_json, line)
            VALUES (?,?,?,?)
            """,
            (
                prog_uuid,
                cs.get("copybook_name", cs) if isinstance(cs, dict) else str(cs),
                json.dumps(
                    [{"from": r.from_text, "to": r.to_text}
                     for r in cs.replacing]
                    if hasattr(cs, "replacing") else []
                ),
                cs.source_line if hasattr(cs, "source_line") else None,
            ),
        )

    # JSON artifact
    artifact = {
        "layer": 2,
        "program": program_name,
        "data_item_count": len(data_items),
        "data_items": data_items,
    }
    (out / f"{program_name}.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False)
    )


def _upsert_condition_88(
    con: sqlite3.Connection,
    uuid: str,
    parent_uuid: str,
    name: str,
    value_raw: str | None,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO conditions_88 (uuid, parent_uuid, name, value_raw) VALUES (?,?,?,?)",
        (uuid, parent_uuid, name, value_raw),
    )


def _upsert_data_item(con: sqlite3.Connection, item: dict) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO data_items
            (uuid, program_uuid, name, level, pic, usage, sign,
             occurs_min, occurs_max, occurs_odo, redefines, value_raw,
             canonical_kind, precision, scale, signed, length,
             copybook_origin, start_line, end_line)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            item["uuid"], item["program_uuid"], item["name"], item["level"],
            item["pic"], item["usage"], item["sign"],
            item["occurs_min"], item["occurs_max"], item["occurs_odo"],
            item["redefines"], item["value_raw"],
            item["canonical_kind"], item["precision"], item["scale"],
            item["signed"], item["length"],
            item["copybook_origin"], item["start_line"], item["end_line"],
        ),
    )


def _parse_occurs(occurs_raw: str, item: dict) -> None:
    """Extract OCCURS min/max and DEPENDING ON variable from raw text."""
    import re
    # OCCURS n TIMES or OCCURS m TO n TIMES
    m = re.search(r"OCCURS\s+(\d+)\s+TO\s+(\d+)", occurs_raw, re.IGNORECASE)
    if m:
        item["occurs_min"] = int(m.group(1))
        item["occurs_max"] = int(m.group(2))
    else:
        m2 = re.search(r"OCCURS\s+(\d+)", occurs_raw, re.IGNORECASE)
        if m2:
            item["occurs_min"] = int(m2.group(1))
            item["occurs_max"] = int(m2.group(1))

    m3 = re.search(r"DEPENDING\s+ON\s+([A-Z0-9-]+)", occurs_raw, re.IGNORECASE)
    if m3:
        item["occurs_odo"] = m3.group(1)
