"""Artifact retrieval — assemble the UUID-referenced context slice for LLM prompts.

Rule 1: No raw source reaches the LLM.
The retrieval function returns only structured artifact data.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def assemble_paragraph_slice(para_uuid: str, con: sqlite3.Connection) -> dict[str, Any]:
    """Assemble the complete artifact slice for spec generation of a paragraph.

    Returns a dict with:
      - paragraph:   the paragraph node
      - statements:  list of statement nodes
      - data_items:  data dictionary entries for identifiers touched
      - def_use:     def-use chains for those identifiers
      - business_rules: IF/EVALUATE predicates in scope
      - file_io:     file operations performed
      - callers:     programs/paragraphs that PERFORM this paragraph
      - callees:     paragraphs PERFORMed from this paragraph
      - cfg_edges:   control-flow edges from/to this paragraph
    """
    para = _fetch_node(con, para_uuid)
    if not para:
        return {}

    prog_uuid = _best_prog_uuid(con, para["parent_uuid"])

    # Statements
    stmts = _fetch_children(con, para_uuid)

    # Data items: gather all identifiers mentioned in statements
    item_names: set[str] = set()
    for stmt in stmts:
        p = json.loads(stmt["payload_json"] or "{}" if stmt["payload_json"] else "{}")
        text = p.get("text", "") or ""
        item_names.update(_extract_cobol_identifiers(text))

    data_items = []
    if item_names and prog_uuid:
        placeholders = ",".join("?" * len(item_names))
        rows = con.execute(
            f"""
            SELECT * FROM data_items
            WHERE program_uuid=? AND UPPER(name) IN ({placeholders})
            """,
            [prog_uuid] + [n.upper() for n in item_names],
        ).fetchall()
        data_items = [dict(r) for r in rows]

    # Def-use for those items
    du_rows: list[dict] = []
    for item in data_items:
        rows = con.execute(
            """
            SELECT du.*, n.kind AS stmt_kind, n.start_line
            FROM def_use du JOIN nodes n ON n.uuid=du.stmt_uuid
            WHERE du.data_item_uuid=?
            ORDER BY n.start_line
            LIMIT 20
            """,
            (item["uuid"],),
        ).fetchall()
        du_rows.extend([dict(r) for r in rows])

    # Business rules in scope
    br_rows = _fetch_business_rules(con, prog_uuid, para_uuid)

    # File I/O
    fio_rows: list[dict] = []
    if prog_uuid:
        fio_rows = [dict(r) for r in con.execute(
            "SELECT * FROM file_io WHERE program_uuid=?", (prog_uuid,)
        ).fetchall()]

    # CFG callers and callees
    callers = [dict(r) for r in con.execute(
        "SELECT * FROM control_flow WHERE to_uuid=?", (para_uuid,)
    ).fetchall()]
    callees = [dict(r) for r in con.execute(
        "SELECT * FROM control_flow WHERE from_uuid=?", (para_uuid,)
    ).fetchall()]

    # G1: conditions_88 for all data items in scope
    conditions_88: list[dict] = []
    if data_items:
        item_uuids = [item["uuid"] for item in data_items]
        placeholders = ",".join("?" * len(item_uuids))
        rows = con.execute(
            f"""
            SELECT c.uuid, c.name, c.value_raw, d.name AS parent_name
            FROM conditions_88 c JOIN data_items d ON d.uuid=c.parent_uuid
            WHERE c.parent_uuid IN ({placeholders})
            """,
            item_uuids,
        ).fetchall()
        conditions_88 = [dict(r) for r in rows]

    return {
        "paragraph": _node_to_dict(para),
        "statements": [_node_to_dict(s) for s in stmts],
        "data_items": data_items,
        "conditions_88": conditions_88,
        "def_use": du_rows,
        "business_rules": br_rows,
        "file_io": fio_rows,
        "callers": callers,
        "callees": callees,
    }


def assemble_program_slice(prog_uuid: str, con: sqlite3.Connection) -> dict[str, Any]:
    """Assemble a comprehensive program context slice leveraging all 7 layers."""
    prog_uuid = _best_prog_uuid(con, prog_uuid)
    prog = _fetch_node(con, prog_uuid)
    if not prog:
        return {}

    # Layer 1: paragraphs + statement count
    paragraphs = [dict(r) for r in con.execute(
        """SELECT p.uuid, p.name, p.start_line, p.end_line,
                  COUNT(s.uuid) AS stmt_count
           FROM nodes p
           LEFT JOIN nodes s ON s.parent_uuid=p.uuid AND s.kind LIKE 'Stmt_%'
           WHERE p.parent_uuid=? AND p.kind='Paragraph'
           GROUP BY p.uuid ORDER BY p.start_line""",
        (prog_uuid,),
    ).fetchall()]

    # Layer 2: data items + 88-level conditions
    data_items = [dict(r) for r in con.execute(
        "SELECT name, level, pic, usage, canonical_kind, precision, scale, signed FROM data_items "
        "WHERE program_uuid=? ORDER BY start_line",
        (prog_uuid,),
    ).fetchall()]

    conditions_88 = [dict(r) for r in con.execute(
        """SELECT c.name, c.value_raw, d.name AS parent_name
           FROM conditions_88 c JOIN data_items d ON d.uuid=c.parent_uuid
           WHERE d.program_uuid=?""",
        (prog_uuid,),
    ).fetchall()]

    # Layer 3: CFG summary and complexity
    cfg_edge_counts = dict(con.execute(
        """SELECT cf.edge_type, COUNT(*) AS cnt
           FROM control_flow cf
           JOIN nodes n ON n.uuid=cf.from_uuid
           WHERE n.parent_uuid=?
           GROUP BY cf.edge_type""",
        (prog_uuid,),
    ).fetchall() or [])

    complexity = [dict(r) for r in con.execute(
        """SELECT n.name AS paragraph, cm.cyclomatic, cm.statement_count,
                  cm.nesting_depth, cm.fan_out
           FROM complexity_metrics cm JOIN nodes n ON n.uuid=cm.para_uuid
           WHERE cm.program_uuid=? ORDER BY cm.cyclomatic DESC LIMIT 10""",
        (prog_uuid,),
    ).fetchall()]

    # Layer 4: call graph (in + out), file I/O, CICS tx, JCL bindings
    call_out = [dict(r) for r in con.execute(
        "SELECT callee_name, call_type, is_resolved FROM call_graph WHERE caller_uuid=?",
        (prog_uuid,),
    ).fetchall()]

    call_in = [dict(r) for r in con.execute(
        """SELECT n.name AS caller_name, cg.call_type
           FROM call_graph cg JOIN nodes n ON n.uuid=cg.caller_uuid
           WHERE cg.callee_uuid=?""",
        (prog_uuid,),
    ).fetchall()]

    file_io = [dict(r) for r in con.execute(
        "SELECT DISTINCT file_name, operation FROM file_io WHERE program_uuid=?",
        (prog_uuid,),
    ).fetchall()]

    cics_verbs = [dict(r) for r in con.execute(
        """SELECT tf.verb, tf.to_program, tf.trans_id
           FROM transaction_flow tf WHERE tf.from_uuid=?""",
        (prog_uuid,),
    ).fetchall()]

    jcl_bindings = []
    try:
        jcl_bindings = [dict(r) for r in con.execute(
            """SELECT job_name, step_name, dd_name, dataset_name, cobol_logical_file
               FROM jcl_program_binding WHERE program_uuid=?""",
            (prog_uuid,),
        ).fetchall()]
    except Exception:
        pass

    # Layer 5: business rules
    business_rules = [dict(r) for r in con.execute(
        "SELECT kind, predicate_raw, predicate_resolved, then_summary, else_summary, line "
        "FROM business_rules WHERE program_uuid=? ORDER BY line",
        (prog_uuid,),
    ).fetchall()]

    # Layer 6: copybooks used
    copybooks = [dict(r) for r in con.execute(
        "SELECT copybook_name FROM copybook_use WHERE program_uuid=?",
        (prog_uuid,),
    ).fetchall()]

    # Layer 7: risks
    risks = [dict(r) for r in con.execute(
        "SELECT kind, severity, note, line FROM risk_register WHERE program_uuid=? ORDER BY severity",
        (prog_uuid,),
    ).fetchall()]

    prog_d = _node_to_dict(prog)
    return {
        "program": prog_d,
        "source_file": prog_d.get("source_file", ""),
        # Layer 1
        "paragraphs": paragraphs,
        "paragraph_count": len(paragraphs),
        # Layer 2
        "data_items": data_items,
        "data_item_count": len(data_items),
        "conditions_88": conditions_88,
        # Layer 3
        "cfg_edge_summary": cfg_edge_counts,
        "complexity_hotspots": complexity,
        # Layer 4
        "calls_out": call_out,
        "calls_in": call_in,
        "file_io": file_io,
        "cics_interactions": cics_verbs,
        "jcl_bindings": jcl_bindings,
        # Layer 5
        "business_rules": business_rules,
        "business_rule_count": len(business_rules),
        # Layer 6
        "copybook_use": copybooks,
        # Layer 7
        "migration_risks": risks,
        "risk_summary": {
            "HIGH": sum(1 for r in risks if r["severity"] == "HIGH"),
            "MEDIUM": sum(1 for r in risks if r["severity"] == "MEDIUM"),
            "LOW": sum(1 for r in risks if r["severity"] == "LOW"),
        },
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_node(con: sqlite3.Connection, uuid: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM nodes WHERE uuid=?", (uuid,)).fetchone()


def _best_prog_uuid(con: sqlite3.Connection, uuid: str) -> str:
    """Given a program UUID, return the best UUID for that program name.

    Handles the duplicate-node case (absolute vs relative path from two pipeline
    phases) by preferring the UUID with the most CFG edges.
    """
    row = con.execute("SELECT name FROM nodes WHERE uuid=?", (uuid,)).fetchone()
    if not row:
        return uuid
    name = row["name"]
    rows = con.execute(
        "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
        (name,),
    ).fetchall()
    if len(rows) == 1:
        return uuid
    best_uuid, best_score = uuid, -1
    for r in rows:
        cnt = con.execute(
            "SELECT COUNT(*) FROM control_flow WHERE from_uuid IN "
            "(SELECT uuid FROM nodes WHERE parent_uuid=?)",
            (r["uuid"],),
        ).fetchone()[0]
        if cnt > best_score:
            best_score, best_uuid = cnt, r["uuid"]
    return best_uuid


def _fetch_children(con: sqlite3.Connection, parent_uuid: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM nodes WHERE parent_uuid=? ORDER BY start_line",
        (parent_uuid,),
    ).fetchall()


def _fetch_business_rules(
    con: sqlite3.Connection, prog_uuid: str | None, para_uuid: str | None
) -> list[dict]:
    if not prog_uuid:
        return []
    rows = con.execute(
        "SELECT * FROM business_rules WHERE program_uuid=? AND (para_uuid=? OR para_uuid IS NULL) "
        "ORDER BY line LIMIT 30",
        (prog_uuid, para_uuid),
    ).fetchall()
    return [dict(r) for r in rows]


def _node_to_dict(row: sqlite3.Row | None) -> dict:
    if not row:
        return {}
    d = dict(row)
    if d.get("payload_json"):
        try:
            d["payload"] = json.loads(d["payload_json"])
        except Exception:
            d["payload"] = {}
        del d["payload_json"]
    return d


import re

def _extract_cobol_identifiers(text: str) -> set[str]:
    return {m.group(0) for m in re.finditer(r"\b([A-Z][A-Z0-9-]{2,})\b", text.upper())}
