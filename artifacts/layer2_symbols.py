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
    """Populate data_items, conditions_88, and copybook_use tables."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return
    prog_uuid = prog_node["uuid"]

    data_items = _persist_data_items(nodes, prog_uuid, con)
    _persist_copybook_use(copy_statements, prog_uuid, con)
    _update_copybook_origins(con, prog_uuid)

    artifact = {
        "layer": 2,
        "program": program_name,
        "data_item_count": len(data_items),
        "data_items": data_items,
    }
    (out / f"{program_name}.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False)
    )


def _build_data_item(node: dict, prog_uuid: str) -> dict:
    p = node.get("payload", {})
    ct = p.get("canonical_type", {})
    item = {
        "uuid": node["uuid"],
        "program_uuid": prog_uuid,
        "name": node["name"],
        "level": p.get("level", -1),
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
        "copybook_origin": None,
        "start_line": node["start_line"],
        "end_line": node["end_line"],
    }
    occurs_raw = p.get("occurs")
    if occurs_raw:
        _parse_occurs(occurs_raw, item)
    return item


def _persist_data_items(
    nodes: list[dict],
    prog_uuid: str,
    con: sqlite3.Connection,
) -> list[dict]:
    data_items: list[dict] = []
    last_non88_uuid: str | None = None

    for node in nodes:
        if node["kind"] != "DataItem":
            continue
        item = _build_data_item(node, prog_uuid)
        data_items.append(item)
        _upsert_data_item(con, item)

        level = item["level"]
        if level == 88 and last_non88_uuid:
            p = node.get("payload", {})
            _upsert_condition_88(con, node["uuid"], last_non88_uuid,
                                 node["name"], p.get("value_raw"))
        else:
            last_non88_uuid = node["uuid"]
    return data_items


def _persist_copybook_use(
    copy_statements: list,
    prog_uuid: str,
    con: sqlite3.Connection,
) -> None:
    for cs in copy_statements:
        cpy_name = _copybook_name(cs)
        replacing = (
            [{"from": r.from_text, "to": r.to_text} for r in cs.replacing]
            if hasattr(cs, "replacing") else []
        )
        cpy_line = cs.source_line if hasattr(cs, "source_line") else None
        con.execute(
            "INSERT INTO copybook_use (program_uuid, copybook_name, replacing_json, line) VALUES (?,?,?,?)",
            (prog_uuid, cpy_name, json.dumps(replacing), cpy_line),
        )


def _update_copybook_origins(con: sqlite3.Connection, prog_uuid: str) -> None:
    """Set copybook_origin on data_items using name-based catalog matching.

    Looks up each copybook used by the program from copybook_catalog (which
    stores item_names_json), then marks any data_item whose name appears in
    that list.  Falls back to line-range heuristics when the catalog has no
    name list.
    """
    try:
        rows = con.execute(
            "SELECT copybook_name, line FROM copybook_use WHERE program_uuid=? ORDER BY line",
            (prog_uuid,),
        ).fetchall()
    except Exception:
        return

    if not rows:
        return

    for row in rows:
        cpy_name = row["copybook_name"] if hasattr(row, "keys") else row[0]
        cpy_line = row["line"] if hasattr(row, "keys") else row[1]
        _assign_origin_from_catalog(con, prog_uuid, cpy_name, cpy_line)


def _assign_origin_from_catalog(
    con: sqlite3.Connection,
    prog_uuid: str,
    cpy_name: str,
    cpy_line: int | None,
) -> None:
    """Assign copybook_origin using catalog item names, with line-range fallback."""
    # Primary: use item_names_json from catalog if available
    try:
        cat = con.execute(
            "SELECT item_names_json FROM copybook_catalog WHERE UPPER(name)=UPPER(?)",
            (cpy_name,),
        ).fetchone()
    except Exception:
        cat = None

    if cat and cat["item_names_json"]:
        import json as _json
        names = _json.loads(cat["item_names_json"])
        if names:
            placeholders = ",".join("?" * len(names))
            try:
                con.execute(
                    f"""
                    UPDATE data_items SET copybook_origin=?
                    WHERE program_uuid=? AND copybook_origin IS NULL
                    AND UPPER(name) IN ({placeholders})
                    """,
                    [cpy_name, prog_uuid, *[n.upper() for n in names]],
                )
            except Exception:
                pass
            return

    # Fallback: use file line count to compute expanded line range
    if cpy_line is None:
        return
    try:
        src = con.execute(
            "SELECT source_file FROM copybook_catalog WHERE UPPER(name)=UPPER(?)",
            (cpy_name,),
        ).fetchone()
        if src and src["source_file"]:
            n_lines = len(pathlib.Path(src["source_file"]).read_text(errors="replace").splitlines())
            con.execute(
                """
                UPDATE data_items SET copybook_origin=?
                WHERE program_uuid=? AND copybook_origin IS NULL
                AND start_line > ? AND start_line <= ?
                """,
                (cpy_name, prog_uuid, cpy_line, cpy_line + n_lines),
            )
    except Exception:
        pass


def _copybook_name(cs) -> str:
    if hasattr(cs, "copybook_name"):
        return cs.copybook_name
    if isinstance(cs, dict):
        return cs.get("copybook_name", "")
    return str(cs)


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
