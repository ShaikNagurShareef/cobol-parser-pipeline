"""Convert the compact JSON from CobolCstExporter into a typed AST.

The typed AST is a list of node dicts, each with:
  uuid, kind, name, source_file, start_line, end_line,
  start_col, end_col, parent_uuid, payload

Nodes are flat; parent_uuid establishes the tree structure.
All UUIDs are deterministic (uuid5).
"""

from __future__ import annotations

import re
from typing import Any

from storage.uuid_gen import make_uuid


# ─── Type lowering ───────────────────────────────────────────────────────────

_PIC_DECIMAL_RE = re.compile(
    r"S?9\((\d+)\)(?:V9\((\d+)\)|V(9+))?|S?9+(V9+)?",
    re.IGNORECASE,
)
_PIC_ALPHA_RE = re.compile(r"X+\((\d+)\)|X+", re.IGNORECASE)
_PIC_ALPHANUM_RE = re.compile(r"A+\((\d+)\)|A+", re.IGNORECASE)


def lower_type(pic: str | None, usage: str | None) -> dict[str, Any]:
    """Lower a PIC/USAGE pair into a canonical type descriptor."""
    if not pic:
        return {"kind": "group"}

    pic_u = pic.upper().replace(" ", "")
    usage_u = (usage or "DISPLAY").upper()

    # Signed flag
    signed = pic_u.startswith("S")

    # Numeric
    m = _PIC_DECIMAL_RE.match(pic_u.lstrip("S"))
    if m:
        if m.group(1):
            int_digits = int(m.group(1))
            frac_digits = int(m.group(2) or 0) if m.group(2) else len(m.group(3) or "")
        else:
            int_part = re.sub(r"[^9]", "", pic_u.split("V")[0])
            int_digits = len(int_part)
            frac_part = pic_u.split("V")[1] if "V" in pic_u else ""
            frac_digits = len(re.sub(r"[^9]", "", frac_part))

        precision = int_digits + frac_digits
        scale = frac_digits

        if usage_u in ("COMP-3", "PACKED-DECIMAL"):
            return {"kind": "decimal", "precision": precision, "scale": scale, "signed": signed}
        if usage_u in ("COMP", "COMP-4", "BINARY"):
            bits = max(16, 8 * ((precision.bit_length() + 7) // 8)) if precision > 4 else 16
            return {"kind": "binary", "bits": bits, "signed": signed}
        # DISPLAY / zoned decimal
        return {"kind": "zoned", "digits": precision, "scale": scale, "signed": signed}

    # Alpha-numeric
    m2 = _PIC_ALPHA_RE.match(pic_u)
    if m2:
        length = int(m2.group(1)) if m2.group(1) else len(pic_u)
        return {"kind": "alpha", "length": length}

    m3 = _PIC_ALPHANUM_RE.match(pic_u)
    if m3:
        length = int(m3.group(1)) if m3.group(1) else len(pic_u)
        return {"kind": "alpha", "length": length}

    return {"kind": "unknown", "pic": pic}


# ─── Main normalizer ─────────────────────────────────────────────────────────

def normalize(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the raw exporter JSON into a flat list of typed AST nodes.

    Returns nodes ordered: Program → DataItems → Paragraphs → Statements.
    """
    source_file = raw.get("file", "")
    nodes: list[dict[str, Any]] = []

    for cu in raw.get("compilation_units", []):
        prog_name = cu.get("name", "UNKNOWN")
        prog_uuid = make_uuid(
            source_file, cu.get("start_line", 0), 0,
            cu.get("end_line", 0), 0, "Program", prog_name,
        )

        prog_node = {
            "uuid": prog_uuid,
            "kind": "Program",
            "name": prog_name,
            "source_file": source_file,
            "start_line": cu.get("start_line"),
            "end_line": cu.get("end_line"),
            "start_col": 0,
            "end_col": 0,
            "parent_uuid": None,
            "payload": {
                "parse_errors": raw.get("parse_errors", []),
                "preprocess_errors": raw.get("preprocess_errors", []),
                "file_control": cu.get("file_control", []),
                "exec_cics": cu.get("exec_cics", []),
                "exec_sql": cu.get("exec_sql", []),
                "call_statements": cu.get("call_statements", []),
            },
        }
        nodes.append(prog_node)

        # ── Data items ──
        for item in cu.get("data_items", []):
            item_uuid = make_uuid(
                source_file,
                item.get("start_line", 0), 0,
                item.get("end_line", 0), 0,
                "DataItem", item.get("name", "FILLER"),
            )
            canonical_type = lower_type(item.get("pic"), item.get("usage"))
            nodes.append({
                "uuid": item_uuid,
                "kind": "DataItem",
                "name": item.get("name", "FILLER"),
                "source_file": source_file,
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "start_col": 0,
                "end_col": 0,
                "parent_uuid": prog_uuid,
                "payload": {
                    "level": item.get("level"),
                    "pic": item.get("pic"),
                    "usage": item.get("usage"),
                    "sign": item.get("sign"),
                    "redefines": item.get("redefines"),
                    "occurs": item.get("occurs"),
                    "occurs_depending_on": item.get("occurs_depending_on", False),
                    "value_raw": item.get("value"),
                    "canonical_type": canonical_type,
                },
            })

        # ── Paragraphs ──
        for para in cu.get("paragraphs", []):
            para_name = para.get("name", "UNNAMED")
            para_uuid = make_uuid(
                source_file,
                para.get("start_line", 0), 0,
                para.get("end_line", 0), 0,
                "Paragraph", para_name,
            )
            nodes.append({
                "uuid": para_uuid,
                "kind": "Paragraph",
                "name": para_name,
                "source_file": source_file,
                "start_line": para.get("start_line"),
                "end_line": para.get("end_line"),
                "start_col": 0,
                "end_col": 0,
                "parent_uuid": prog_uuid,
                "payload": {"statement_count": len(para.get("statements", []))},
            })

            # ── Statements ──
            for stmt in para.get("statements", []):
                stmt_uuid = make_uuid(
                    source_file,
                    stmt.get("start_line", 0), 0,
                    stmt.get("start_line", 0), 0,
                    f"Stmt_{stmt.get('kind','OTHER')}", "",
                )
                nodes.append({
                    "uuid": stmt_uuid,
                    "kind": f"Stmt_{stmt.get('kind', 'OTHER')}",
                    "name": stmt.get("target") or stmt.get("callee") or "",
                    "source_file": source_file,
                    "start_line": stmt.get("start_line"),
                    "end_line": stmt.get("start_line"),
                    "start_col": 0,
                    "end_col": 0,
                    "parent_uuid": para_uuid,
                    "payload": {k: v for k, v in stmt.items()
                                if k not in ("start_line",)},
                })

    return nodes
