"""Layer 5 — Business rule catalog, arithmetic specs, data lineage."""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Any

from storage.uuid_gen import make_uuid, make_named_uuid

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer5"


def persist(
    nodes: list[dict[str, Any]],
    program_name: str,
    con: sqlite3.Connection,
    output_dir: pathlib.Path | None = None,
) -> None:
    """Extract and persist business rules and arithmetic specs."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return
    prog_uuid = prog_node["uuid"]
    source_file = prog_node["source_file"]

    data_items = {n["name"].upper(): n["uuid"] for n in nodes if n["kind"] == "DataItem"}
    rules: list[dict] = []
    arith: list[dict] = []

    for para in [n for n in nodes if n["kind"] == "Paragraph"]:
        para_uuid = para["uuid"]
        stmts = [n for n in nodes if n.get("parent_uuid") == para_uuid]

        for stmt in stmts:
            kind = stmt["kind"]
            payload = stmt.get("payload", {})
            text = payload.get("text", "")
            line = stmt.get("start_line", 0)

            # ── IF predicates → business rules ──
            if kind == "Stmt_IF":
                predicate = _extract_if_condition(text)
                resolved = _resolve_predicate(predicate, data_items)
                rule_uuid = make_uuid(source_file, line, 0, line, 0, "BusinessRule", predicate[:40])
                rule = {
                    "uuid": rule_uuid,
                    "program_uuid": prog_uuid,
                    "para_uuid": para_uuid,
                    "kind": "IF",
                    "predicate_raw": predicate,
                    "predicate_resolved": json.dumps(resolved),
                    "then_summary": _summarize_branch(text, "THEN"),
                    "else_summary": _summarize_branch(text, "ELSE"),
                    "node_uuid": stmt["uuid"],
                    "line": line,
                }
                rules.append(rule)
                _upsert_rule(con, rule)

            # ── EVALUATE WHEN → business rules ──
            elif kind == "Stmt_EVALUATE":
                for when_clause in _extract_when_clauses(text):
                    rule_uuid = make_uuid(source_file, line, 0, line, 0,
                                         "BusinessRule_WHEN", when_clause[:40])
                    rule = {
                        "uuid": rule_uuid,
                        "program_uuid": prog_uuid,
                        "para_uuid": para_uuid,
                        "kind": "EVALUATE_WHEN",
                        "predicate_raw": when_clause,
                        "predicate_resolved": json.dumps({"op": "WHEN", "value": when_clause}),
                        "then_summary": "",
                        "else_summary": "",
                        "node_uuid": stmt["uuid"],
                        "line": line,
                    }
                    rules.append(rule)
                    _upsert_rule(con, rule)

            # ── Arithmetic specs ──
            if kind in ("Stmt_COMPUTE", "Stmt_ADD", "Stmt_SUBTRACT",
                        "Stmt_MULTIPLY", "Stmt_DIVIDE"):
                arith_uuid = make_uuid(source_file, line, 0, line, 0, "Arith", text[:40])
                expr = _parse_arithmetic(kind, text)
                arith_row = {
                    "uuid": arith_uuid,
                    "program_uuid": prog_uuid,
                    "stmt_uuid": stmt["uuid"],
                    "kind": kind.replace("Stmt_", ""),
                    "expression_json": json.dumps(expr),
                    "result_var": expr.get("result"),
                    "line": line,
                }
                arith.append(arith_row)
                con.execute(
                    """
                    INSERT OR IGNORE INTO arithmetic_specs
                        (uuid, program_uuid, stmt_uuid, kind, expression_json, result_var, line)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (arith_row["uuid"], arith_row["program_uuid"], arith_row["stmt_uuid"],
                     arith_row["kind"], arith_row["expression_json"],
                     arith_row["result_var"], arith_row["line"]),
                )

    artifact = {
        "layer": 5,
        "program": program_name,
        "business_rules": len(rules),
        "arithmetic_specs": len(arith),
        "rules": rules[:50],  # truncate for file size
    }
    (out / f"{program_name}.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False)
    )


def _upsert_rule(con: sqlite3.Connection, rule: dict) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO business_rules
            (uuid, program_uuid, para_uuid, kind, predicate_raw, predicate_resolved,
             then_summary, else_summary, node_uuid, line)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (rule["uuid"], rule["program_uuid"], rule["para_uuid"], rule["kind"],
         rule["predicate_raw"], rule["predicate_resolved"],
         rule["then_summary"], rule["else_summary"],
         rule["node_uuid"], rule["line"]),
    )


def _extract_if_condition(text: str) -> str:
    m = re.search(r"\bIF\b(.+?)(?:\bTHEN\b|\bELSE\b|$)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    return text[:100]


def _resolve_predicate(predicate: str, item_uuids: dict[str, str]) -> dict:
    """Build a simple resolved predicate dict linking identifiers to UUIDs."""
    tokens = re.findall(r"[A-Z][A-Z0-9-]{2,}", predicate.upper())
    refs = {t: item_uuids[t] for t in tokens if t in item_uuids}
    return {"raw": predicate, "data_item_refs": refs}


def _summarize_branch(text: str, branch: str) -> str:
    if branch == "THEN":
        m = re.search(r"\bTHEN\b(.+?)(?:\bELSE\b|END-IF|$)", text, re.IGNORECASE | re.DOTALL)
    else:
        m = re.search(r"\bELSE\b(.+?)(?:END-IF|$)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:100]
    return ""


def _extract_when_clauses(text: str) -> list[str]:
    return [m.strip()[:100] for m in re.findall(
        r"\bWHEN\b\s+(.+?)(?=\bWHEN\b|\bEND-EVALUATE\b|$)",
        text, re.IGNORECASE | re.DOTALL,
    )]


def _parse_arithmetic(kind: str, text: str) -> dict:
    """Parse arithmetic statement into a simple expression tree."""
    text_u = text.upper()
    result: dict = {"kind": kind.replace("Stmt_", ""), "raw": text[:120]}
    if kind == "Stmt_COMPUTE":
        m = re.search(r"COMPUTE\s+([A-Z0-9-]+)\s*=\s*(.+?)(?:END-COMPUTE|$)",
                      text_u, re.DOTALL)
        if m:
            result["result"] = m.group(1).strip()
            result["expression"] = m.group(2).strip()[:100]
    elif kind == "Stmt_ADD":
        m = re.search(r"ADD\s+(.+?)\s+TO\s+([A-Z0-9-]+)", text_u)
        if m:
            result["operand"] = m.group(1).strip()
            result["result"] = m.group(2).strip()
    elif kind == "Stmt_SUBTRACT":
        m = re.search(r"SUBTRACT\s+(.+?)\s+FROM\s+([A-Z0-9-]+)", text_u)
        if m:
            result["operand"] = m.group(1).strip()
            result["result"] = m.group(2).strip()
    elif kind == "Stmt_MULTIPLY":
        m = re.search(r"MULTIPLY\s+(.+?)\s+BY\s+([A-Z0-9-]+)", text_u)
        if m:
            result["operand1"] = m.group(1).strip()
            result["result"] = m.group(2).strip()
    elif kind == "Stmt_DIVIDE":
        m = re.search(r"DIVIDE\s+(.+?)\s+(?:INTO|BY)\s+([A-Z0-9-]+)", text_u)
        if m:
            result["operand"] = m.group(1).strip()
            result["result"] = m.group(2).strip()
    return result
