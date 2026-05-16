"""Layer 7 — Parse coverage report and migration risk register."""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Any

from storage.uuid_gen import make_uuid

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer7"


def build_risk_register(
    nodes: list[dict[str, Any]],
    con: sqlite3.Connection,
) -> list[dict]:
    """Scan a program's AST for migration risk patterns.

    Risk categories per §6 Layer 7 of the brief:
      ALTER, GO TO DEPENDING ON, dynamic CALL, OCCURS DEPENDING ON,
      REDEFINES overlap, COMP-3 rounding, EXEC CICS HANDLE CONDITION,
      pseudo-conversational COMMAREA state.
    """
    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return []
    prog_uuid = prog_node["uuid"]
    source_file = prog_node["source_file"]
    risks: list[dict] = []

    def _add(kind: str, node_uuid: str, severity: str, note: str, line: int) -> None:
        risks.append({
            "kind": kind,
            "program_uuid": prog_uuid,
            "node_uuid": node_uuid,
            "severity": severity,
            "note": note,
            "line": line,
        })
        con.execute(
            """
            INSERT INTO risk_register
                (kind, program_uuid, node_uuid, severity, note, line)
            VALUES (?,?,?,?,?,?)
            """,
            (kind, prog_uuid, node_uuid, severity, note, line),
        )

    # DataItem risks
    for node in nodes:
        if node["kind"] != "DataItem":
            continue
        p = node.get("payload", {})
        uuid = node["uuid"]
        line = node.get("start_line", 0) or 0

        # OCCURS DEPENDING ON
        if p.get("occurs_depending_on"):
            _add("ODO", uuid, "MEDIUM",
                 f"OCCURS DEPENDING ON detected on {node['name']} — "
                 "Java equivalent requires dynamic array allocation", line)

        # REDEFINES (possible overlap)
        if p.get("redefines"):
            _add("REDEFINES_OVERLAP", uuid, "MEDIUM",
                 f"{node['name']} REDEFINES {p['redefines']} — "
                 "verify overlapping memory semantics preserved in Java", line)

        # COMP-3 arithmetic
        ct = p.get("canonical_type", {})
        if ct.get("kind") == "decimal" and p.get("usage") in ("COMP-3", None):
            _add("COMP3_ROUNDING", uuid, "LOW",
                 f"{node['name']} is COMP-3 decimal — "
                 "emit as BigDecimal with explicit RoundingMode", line)

    # Statement risks
    for node in nodes:
        kind = node["kind"]
        payload = node.get("payload", {})
        text = payload.get("text", "")
        uuid = node["uuid"]
        line = node.get("start_line", 0) or 0

        # GO TO DEPENDING ON
        if kind == "Stmt_GOTO" and re.search(r"DEPENDING", text, re.IGNORECASE):
            _add("GOTO_DEPENDING", uuid, "HIGH",
                 "GO TO DEPENDING ON — computed branch, hard to forward-engineer", line)

        # Dynamic CALL (CALL identifier, not literal)
        if kind == "Stmt_CALL" and not payload.get("literal", True):
            callee = payload.get("callee", "")
            _add("DYNAMIC_CALL", uuid, "HIGH",
                 f"Dynamic CALL via variable '{callee}' — cannot statically resolve target", line)

        # EXEC CICS HANDLE CONDITION
        if kind == "Stmt_EXEC_CICS":
            verb = (payload.get("verb") or "").upper()
            if verb == "HANDLE":
                _add("HANDLE_CONDITION", uuid, "HIGH",
                     "EXEC CICS HANDLE CONDITION — implicit control flow transfer, "
                     "must be rewritten as explicit exception handling in Java", line)

        # Pseudo-conversational COMMAREA state
        if kind in ("Stmt_EXEC_CICS",):
            if re.search(r"COMMAREA", text, re.IGNORECASE):
                _add("PSEUDO_CONVERSATIONAL", uuid, "MEDIUM",
                     "COMMAREA usage — pseudo-conversational state pattern, "
                     "requires session state management in modern equivalent", line)

    return risks


def coverage_report(con: sqlite3.Connection, output_dir: pathlib.Path | None = None) -> dict:
    """Generate the parse coverage report across the full corpus."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    rows = con.execute(
        "SELECT source_type, status, COUNT(*) as cnt FROM parse_coverage GROUP BY source_type, status"
    ).fetchall()

    by_type: dict[str, dict] = {}
    for row in rows:
        t = row["source_type"]
        if t not in by_type:
            by_type[t] = {"total": 0, "ok": 0, "failures": {}}
        by_type[t]["total"] += row["cnt"]
        if row["status"] == "OK":
            by_type[t]["ok"] += row["cnt"]
        else:
            by_type[t]["failures"][row["status"]] = \
                by_type[t]["failures"].get(row["status"], 0) + row["cnt"]

    overall_total = sum(v["total"] for v in by_type.values())
    overall_ok = sum(v["ok"] for v in by_type.values())
    overall_pct = round(100 * overall_ok / max(overall_total, 1), 1)

    # Risk register summary
    risk_rows = con.execute(
        "SELECT kind, severity, COUNT(*) as cnt FROM risk_register GROUP BY kind, severity"
    ).fetchall()
    risk_summary = [{"kind": r["kind"], "severity": r["severity"], "count": r["cnt"]}
                    for r in risk_rows]

    report = {
        "layer": 7,
        "overall_coverage_pct": overall_pct,
        "total_files": overall_total,
        "ok_files": overall_ok,
        "by_source_type": {
            t: {
                "total": v["total"],
                "ok": v["ok"],
                "pct": round(100 * v["ok"] / max(v["total"], 1), 1),
                "failures": v["failures"],
            }
            for t, v in by_type.items()
        },
        "risk_register_summary": risk_summary,
    }

    (out / "coverage_report.json").write_text(json.dumps(report, indent=2))
    return report
