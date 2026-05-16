"""Layer 4 — Inter-program graphs.

Builds, across all programs in the corpus:
  - call_graph:         static CALL + EXEC CICS LINK/XCTL edges
  - file_io:           program → file → operation
  - transaction_flow:  CICS XCTL/LINK/RETURN chains
  - copybook_use:      already written by layer2; re-resolves callee UUIDs

Designed to run AFTER all programs have been ingested (requires full nodes
table to resolve callee UUIDs from program names).
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Any

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer4"


def persist_program(
    nodes: list[dict[str, Any]],
    program_name: str,
    con: sqlite3.Connection,
) -> None:
    """Populate inter-program tables for one already-ingested program."""
    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return
    prog_uuid = prog_node["uuid"]
    source_file = prog_node["source_file"]
    payload = prog_node.get("payload", {})

    # ── Call graph from explicit CALL statements ──
    for cs in payload.get("call_statements", []):
        callee_name = cs.get("callee", "").strip().upper()
        if not callee_name:
            continue
        is_literal = cs.get("literal", False)
        call_type = "CALL_LITERAL" if is_literal else "CALL_DYNAMIC"
        callee_uuid = _resolve_program_uuid(con, callee_name) if is_literal else None
        site_uuid = _make_site_uuid(source_file, cs.get("start_line", 0), callee_name)
        con.execute(
            """
            INSERT INTO call_graph
                (caller_uuid, callee_name, callee_uuid, call_site_uuid, call_type, is_resolved)
            VALUES (?,?,?,?,?,?)
            """,
            (prog_uuid, callee_name, callee_uuid, site_uuid,
             call_type, int(callee_uuid is not None)),
        )

    # ── EXEC CICS LINK / XCTL ──
    for ec in payload.get("exec_cics", []):
        verb = (ec.get("verb") or "").upper()
        if verb not in ("LINK", "XCTL", "RETURN", "SEND", "RECEIVE"):
            pass  # still record all
        text = ec.get("text", "")
        target = _extract_cics_program(text)
        callee_uuid = _resolve_program_uuid(con, target) if target else None

        if verb in ("LINK", "XCTL"):
            call_type = f"CICS_{verb}"
            con.execute(
                """
                INSERT INTO call_graph
                    (caller_uuid, callee_name, callee_uuid, call_site_uuid, call_type, is_resolved)
                VALUES (?,?,?,?,?,?)
                """,
                (prog_uuid, target or "", callee_uuid, None, call_type,
                 int(callee_uuid is not None)),
            )

        # Transaction flow
        trans_id = _extract_cics_transid(text)
        con.execute(
            """
            INSERT INTO transaction_flow
                (from_uuid, to_uuid, to_program, verb, trans_id, line)
            VALUES (?,?,?,?,?,?)
            """,
            (prog_uuid, callee_uuid, target, verb, trans_id, ec.get("start_line")),
        )

    # ── File I/O from statement scan ──
    paragraphs = [n for n in nodes if n["kind"] == "Paragraph"]
    stmts = [n for n in nodes if n["kind"].startswith("Stmt_")]
    file_map = _build_file_map(prog_node)

    for stmt in stmts:
        kind = stmt["kind"]
        payload_s = stmt.get("payload", {})
        text = payload_s.get("text", "")

        op = None
        if kind == "Stmt_READ":
            op = "READ"
        elif kind == "Stmt_WRITE":
            op = "WRITE"
        elif kind == "Stmt_REWRITE":
            op = "REWRITE"
        elif kind == "Stmt_DELETE":
            op = "DELETE"
        elif kind == "Stmt_EXEC_CICS":
            verb = (payload_s.get("verb") or "").upper()
            if verb in ("READ", "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT", "ENDBR"):
                op = verb
                file_name = _extract_cics_file(text)
                if file_name:
                    con.execute(
                        """
                        INSERT INTO file_io
                            (program_uuid, file_name, logical_name, operation, node_uuid, line)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (prog_uuid, file_name, file_name, op,
                         stmt["uuid"], stmt.get("start_line")),
                    )
                continue

        if op:
            file_name = _guess_file_from_stmt(text, file_map)
            if file_name:
                con.execute(
                    """
                    INSERT INTO file_io
                        (program_uuid, file_name, logical_name, operation, node_uuid, line)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (prog_uuid, file_name, file_name, op,
                     stmt["uuid"], stmt.get("start_line")),
                )

    # ── DB2 access from EXEC SQL ──
    for es in prog_node.get("payload", {}).get("exec_sql", []):
        text = es.get("text", "")
        op = es.get("operation", "UNKNOWN")
        tables = _extract_sql_tables(text, op)
        for table in tables:
            con.execute(
                """
                INSERT INTO db_io (program_uuid, table_name, columns, operation, line)
                VALUES (?,?,?,?,?)
                """,
                (prog_uuid, table, json.dumps([]), op, es.get("start_line")),
            )


def resolve_callees(con: sqlite3.Connection) -> int:
    """Second pass: resolve previously unresolved callee UUIDs.

    Returns number of newly resolved edges.
    """
    rows = con.execute(
        "SELECT id, callee_name FROM call_graph WHERE is_resolved=0"
    ).fetchall()
    resolved = 0
    for row in rows:
        uid = _resolve_program_uuid(con, row["callee_name"])
        if uid:
            con.execute(
                "UPDATE call_graph SET callee_uuid=?, is_resolved=1 WHERE id=?",
                (uid, row["id"]),
            )
            resolved += 1
    return resolved


def emit_artifact(con: sqlite3.Connection, output_dir: pathlib.Path | None = None) -> None:
    """Write a summary JSON artifact for Layer 4."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    cg = con.execute("SELECT COUNT(*) FROM call_graph").fetchone()[0]
    fio = con.execute("SELECT COUNT(*) FROM file_io").fetchone()[0]
    tf = con.execute("SELECT COUNT(*) FROM transaction_flow").fetchone()[0]
    artifact = {
        "layer": 4,
        "call_graph_edges": cg,
        "file_io_edges": fio,
        "transaction_flow_edges": tf,
    }
    (out / "summary.json").write_text(json.dumps(artifact, indent=2))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_program_uuid(con: sqlite3.Connection, name: str) -> str | None:
    row = con.execute(
        "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
        (name,),
    ).fetchone()
    return row["uuid"] if row else None


def _make_site_uuid(source_file: str, line: int, name: str) -> str:
    from storage.uuid_gen import make_uuid
    return make_uuid(source_file, line, 0, line, 0, "CallSite", name)


def _extract_cics_program(text: str) -> str | None:
    m = re.search(r"PROGRAM\s*\(([^)]+)\)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"").upper()
    return None


def _extract_cics_transid(text: str) -> str | None:
    m = re.search(r"TRANSID\s*\(([^)]+)\)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"").upper()
    return None


def _extract_cics_file(text: str) -> str | None:
    m = re.search(r"FILE\s*\(([^)]+)\)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"").upper()
    return None


def _build_file_map(prog_node: dict) -> dict[str, str]:
    """Return logical_name → dataset_name from file_control."""
    result = {}
    for fc in prog_node.get("payload", {}).get("file_control", []):
        name = (fc.get("name") or "").upper()
        assign = (fc.get("assign_to") or "").upper()
        if name:
            result[name] = assign or name
    return result


def _guess_file_from_stmt(text: str, file_map: dict[str, str]) -> str | None:
    """Try to match a file logical name from statement text."""
    text_u = text.upper()
    for logical in file_map:
        if logical in text_u:
            return file_map[logical]
    return None


def _extract_sql_tables(text: str, op: str) -> list[str]:
    text_u = text.upper()
    tables = []
    if op == "SELECT":
        m = re.search(r"\bFROM\s+([A-Z][A-Z0-9_#@$.]*)", text_u)
        if m:
            tables.append(m.group(1))
    elif op in ("INSERT",):
        m = re.search(r"\bINTO\s+([A-Z][A-Z0-9_#@$.]*)", text_u)
        if m:
            tables.append(m.group(1))
    elif op in ("UPDATE", "DELETE"):
        m = re.search(r"\b(?:UPDATE|FROM)\s+([A-Z][A-Z0-9_#@$.]*)", text_u)
        if m:
            tables.append(m.group(1))
    elif op == "DECLARE":
        m = re.search(r"DECLARE\s+\w+\s+CURSOR\s+FOR\s+SELECT\s+.*?\s+FROM\s+([A-Z][A-Z0-9_#@$.]*)", text_u)
        if m:
            tables.append(m.group(1))
    return tables
