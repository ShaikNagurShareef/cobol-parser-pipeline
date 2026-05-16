"""Layer 3 — Intra-program graphs: CFG and def-use chains.

Walks the typed AST nodes from Layer 1 and populates:
  - control_flow table: paragraph→paragraph edges
  - def_use table: data-item reads and writes per statement
  - complexity_metrics table: cyclomatic complexity per paragraph
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Any

from storage.uuid_gen import make_uuid, make_edge_uuid

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer3"

# Statement kinds that write (define) a variable
_WRITE_KINDS = {
    "Stmt_MOVE", "Stmt_COMPUTE", "Stmt_ADD", "Stmt_SUBTRACT",
    "Stmt_MULTIPLY", "Stmt_DIVIDE", "Stmt_INITIALIZE", "Stmt_READ",
}
# Statement kinds that read a variable
_READ_KINDS = {
    "Stmt_IF", "Stmt_EVALUATE", "Stmt_PERFORM", "Stmt_CALL",
    "Stmt_WRITE", "Stmt_REWRITE", "Stmt_DELETE", "Stmt_COMPUTE",
    "Stmt_ADD", "Stmt_SUBTRACT", "Stmt_MULTIPLY", "Stmt_DIVIDE",
}


def persist(
    nodes: list[dict[str, Any]],
    program_name: str,
    con: sqlite3.Connection,
    output_dir: pathlib.Path | None = None,
) -> None:
    """Build and persist intra-program graphs for one program."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return
    prog_uuid = prog_node["uuid"]
    source_file = prog_node["source_file"]

    paragraphs = [n for n in nodes if n["kind"] == "Paragraph"]
    para_by_name: dict[str, dict] = {p["name"].upper(): p for p in paragraphs}

    data_items = [n for n in nodes if n["kind"] == "DataItem"]
    item_by_name: dict[str, dict] = {d["name"].upper(): d for d in data_items}

    cfg_edges: list[dict] = []
    du_rows: list[dict] = []
    complexity: dict[str, dict] = {}

    for para in paragraphs:
        para_uuid = para["uuid"]
        stmts = [n for n in nodes if n.get("parent_uuid") == para_uuid]

        # Init complexity counter
        cyclomatic = 1
        nesting = 0
        fan_out = 0

        for i, stmt in enumerate(stmts):
            kind = stmt["kind"]
            payload = stmt.get("payload", {})
            stmt_uuid = stmt["uuid"]
            line = stmt.get("start_line", 0)

            # ── CFG edges ──
            if kind == "Stmt_PERFORM":
                target = (payload.get("target") or "").upper()
                thru = _extract_thru(payload.get("text", ""))
                if target and target in para_by_name:
                    target_para = para_by_name[target]
                    _add_cfg(con, para_uuid, target_para["uuid"], "PERFORM", source_file)
                    cfg_edges.append({"from": para["name"], "to": target, "type": "PERFORM"})
                    fan_out += 1
                if thru and thru in para_by_name:
                    _add_cfg(con, para_by_name[target]["uuid"] if target in para_by_name else para_uuid,
                             para_by_name[thru]["uuid"], "PERFORM_THRU", source_file)
                # Loop-back if PERFORM VARYING / UNTIL
                if re.search(r"VARYING|UNTIL|TIMES", payload.get("text", ""), re.IGNORECASE):
                    _add_cfg(con, para_uuid, para_uuid, "LOOP_BACK", source_file)
                    cyclomatic += 1

            elif kind == "Stmt_GOTO":
                target = _first_word_after_goto(payload.get("text", "")).upper()
                if target and target in para_by_name:
                    _add_cfg(con, para_uuid, para_by_name[target]["uuid"], "GOTO", source_file)
                    cfg_edges.append({"from": para["name"], "to": target, "type": "GOTO"})
                fan_out += 1

            elif kind == "Stmt_IF":
                cyclomatic += 1
                nesting = max(nesting, 1)

            elif kind == "Stmt_EVALUATE":
                # Count WHEN clauses (each adds a branch)
                text = payload.get("text", "")
                when_count = len(re.findall(r"\bWHEN\b", text, re.IGNORECASE))
                cyclomatic += max(1, when_count)

            elif kind == "Stmt_EXEC_CICS":
                verb = payload.get("verb", "")
                if verb in ("XCTL", "LINK"):
                    _add_cfg(con, para_uuid, para_uuid, f"CICS_{verb}", source_file)
                elif verb == "RETURN":
                    _add_cfg(con, para_uuid, para_uuid, "CICS_RETURN", source_file)

            # Fall-through to next paragraph
            if i == len(stmts) - 1 and i + 1 < len(paragraphs):
                next_para_idx = paragraphs.index(para) + 1
                if next_para_idx < len(paragraphs):
                    _add_cfg(con, para_uuid, paragraphs[next_para_idx]["uuid"],
                             "FALLTHROUGH", source_file)

            # ── Def-use ──
            text = payload.get("text", "")
            if kind in _WRITE_KINDS:
                for name in _extract_identifiers(text):
                    if name in item_by_name:
                        _add_du(con, item_by_name[name]["uuid"], stmt_uuid, "WRITE", line)
                        du_rows.append({"item": name, "stmt": stmt_uuid, "op": "WRITE"})

            if kind in _READ_KINDS:
                for name in _extract_identifiers(text):
                    if name in item_by_name:
                        _add_du(con, item_by_name[name]["uuid"], stmt_uuid, "READ", line)
                        du_rows.append({"item": name, "stmt": stmt_uuid, "op": "READ"})

        complexity[para_uuid] = {
            "para_uuid": para_uuid,
            "program_uuid": prog_uuid,
            "cyclomatic": cyclomatic,
            "statement_count": len(stmts),
            "nesting_depth": nesting,
            "fan_out": fan_out,
        }

    # Persist complexity
    for row in complexity.values():
        con.execute(
            """
            INSERT OR REPLACE INTO complexity_metrics
                (para_uuid, program_uuid, cyclomatic, statement_count, nesting_depth, fan_out)
            VALUES (?,?,?,?,?,?)
            """,
            (row["para_uuid"], row["program_uuid"], row["cyclomatic"],
             row["statement_count"], row["nesting_depth"], row["fan_out"]),
        )

    # JSON artifact
    artifact = {
        "layer": 3,
        "program": program_name,
        "cfg_edges": cfg_edges,
        "def_use_count": len(du_rows),
    }
    (out / f"{program_name}.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False)
    )


# ─── Helpers ────────────────────────────────────────────────────────────────

def _add_cfg(con: sqlite3.Connection, from_uuid: str, to_uuid: str,
             edge_type: str, source_file: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO control_flow (from_uuid, to_uuid, edge_type) VALUES (?,?,?)",
        (from_uuid, to_uuid, edge_type),
    )


def _add_du(con: sqlite3.Connection, item_uuid: str, stmt_uuid: str,
            op: str, line: int) -> None:
    con.execute(
        "INSERT INTO def_use (data_item_uuid, stmt_uuid, op, line) VALUES (?,?,?,?)",
        (item_uuid, stmt_uuid, op, line),
    )


_IDENT_RE = re.compile(r"\b([A-Z][A-Z0-9-]{2,})\b")


def _extract_identifiers(text: str) -> list[str]:
    """Extract likely COBOL identifiers from statement text."""
    return [m.group(1).upper() for m in _IDENT_RE.finditer(text.upper())]


def _extract_thru(text: str) -> str:
    m = re.search(r"THRU\s+([A-Z0-9-]+)", text, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _first_word_after_goto(text: str) -> str:
    m = re.search(r"GO\s+TO\s+([A-Z0-9-]+)", text, re.IGNORECASE)
    return m.group(1) if m else ""
