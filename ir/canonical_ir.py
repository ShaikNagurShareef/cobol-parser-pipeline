"""Canonical IR — lower typed AST + symbol table into a language-neutral IR.

The IR is a simple JSON-serializable tree of typed expressions.
Key mappings:
  DataItem (decimal)  → IRField(type=BigDecimal, precision, scale, signed)
  DataItem (zoned)    → IRField(type=BigDecimal, precision, scale, signed)
  DataItem (binary)   → IRField(type=long/int)
  DataItem (alpha)    → IRField(type=String, maxLen)
  Paragraph           → IRFunction(name, params, body: [IRStatement])
  PERFORM             → IRCall(target)
  MOVE a TO b         → IRAssign(target=b, value=a)
  COMPUTE x = expr    → IRAssign(target=x, value=IRExpr(expr))
  IF cond             → IRIf(condition, then_body, else_body)
  EVALUATE            → IRSwitch(subject, cases)
  READ / WRITE        → IRFileOp(file, op, record_var)
  EXEC CICS LINK      → IRCicsCall(verb, program, commarea)
  EXEC CICS RETURN    → IRReturn()
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "ir"


def lower_program(nodes: list[dict[str, Any]], program_name: str) -> dict[str, Any]:
    """Lower one program's typed AST to the canonical IR.

    Returns an IR dict with fields, functions, and metadata.
    """
    prog_node = next((n for n in nodes if n["kind"] == "Program"), None)
    if prog_node is None:
        return {}

    fields = _lower_fields(nodes)
    functions = _lower_functions(nodes, fields)

    ir = {
        "ir_version": "1.0",
        "program": program_name,
        "source_file": prog_node["source_file"],
        "fields": fields,
        "functions": functions,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{program_name}.ir.json").write_text(
        json.dumps(ir, indent=2, ensure_ascii=False)
    )
    return ir


def _lower_fields(nodes: list[dict]) -> list[dict]:
    fields = []
    for node in nodes:
        if node["kind"] != "DataItem":
            continue
        p = node.get("payload", {})
        ct = p.get("canonical_type", {})
        kind = ct.get("kind", "unknown")

        ir_type = _map_type(kind, ct)
        fields.append({
            "uuid": node["uuid"],
            "name": _java_name(node["name"]),
            "cobol_name": node["name"],
            "level": p.get("level"),
            "ir_type": ir_type,
            "pic": p.get("pic"),
            "usage": p.get("usage"),
            "redefines": p.get("redefines"),
            "occurs_max": ct.get("occurs_max"),
        })
    return fields


def _map_type(kind: str, ct: dict) -> dict:
    if kind == "decimal":
        return {
            "java_type": "BigDecimal",
            "precision": ct.get("precision"),
            "scale": ct.get("scale"),
            "signed": ct.get("signed", True),
            "rounding_mode": "HALF_EVEN",
        }
    if kind == "zoned":
        prec = ct.get("digits", 9)
        scale = ct.get("scale", 0)
        if prec <= 4:
            return {"java_type": "int", "signed": ct.get("signed", False)}
        if prec <= 9:
            return {"java_type": "long", "signed": ct.get("signed", False)}
        return {
            "java_type": "BigDecimal",
            "precision": prec,
            "scale": scale,
            "signed": ct.get("signed", False),
            "rounding_mode": "HALF_EVEN",
        }
    if kind == "binary":
        bits = ct.get("bits", 32)
        return {"java_type": "long" if bits > 32 else "int",
                "signed": ct.get("signed", True)}
    if kind == "alpha":
        return {"java_type": "String", "maxLen": ct.get("length", 0)}
    if kind == "group":
        return {"java_type": "Object"}
    return {"java_type": "Object"}


def _lower_functions(nodes: list[dict], fields: list[dict]) -> list[dict]:
    field_map = {f["cobol_name"].upper(): f for f in fields}
    functions = []
    paragraphs = [n for n in nodes if n["kind"] == "Paragraph"]
    stmts_by_para = {}
    for n in nodes:
        if n["kind"].startswith("Stmt_") and n.get("parent_uuid"):
            stmts_by_para.setdefault(n["parent_uuid"], []).append(n)

    for para in paragraphs:
        body = _lower_stmts(stmts_by_para.get(para["uuid"], []), field_map)
        functions.append({
            "uuid": para["uuid"],
            "name": _java_method_name(para["name"]),
            "cobol_name": para["name"],
            "start_line": para["start_line"],
            "body": body,
        })
    return functions


def _lower_stmts(stmts: list[dict], field_map: dict) -> list[dict]:
    result = []
    for stmt in stmts:
        kind = stmt["kind"]
        p = stmt.get("payload", {})
        text = p.get("text", "")

        if kind == "Stmt_MOVE":
            src, tgt = _parse_move(text)
            result.append({"ir": "Assign", "target": tgt, "value": {"ir": "Ref", "name": src}})

        elif kind == "Stmt_COMPUTE":
            tgt, expr = _parse_compute(text)
            result.append({"ir": "Assign", "target": tgt,
                           "value": {"ir": "Expr", "expression": expr}})

        elif kind in ("Stmt_ADD", "Stmt_SUBTRACT", "Stmt_MULTIPLY", "Stmt_DIVIDE"):
            result.append(_parse_arith_expr(kind.replace("Stmt_", ""), text))

        elif kind == "Stmt_IF":
            cond = _extract_condition(text)
            result.append({"ir": "If", "condition": cond, "then": [], "else_": []})

        elif kind == "Stmt_EVALUATE":
            result.append({"ir": "Switch", "text": text[:80]})

        elif kind == "Stmt_PERFORM":
            target = p.get("target") or _extract_perform_target(text)
            result.append({"ir": "Call", "target": _java_method_name(target or "UNKNOWN")})

        elif kind == "Stmt_READ":
            result.append({"ir": "FileRead", "text": text[:60]})

        elif kind == "Stmt_WRITE":
            result.append({"ir": "FileWrite", "text": text[:60]})

        elif kind == "Stmt_REWRITE":
            result.append({"ir": "FileRewrite", "text": text[:60]})

        elif kind == "Stmt_EXEC_CICS":
            verb = (p.get("verb") or "").upper()
            if verb == "RETURN":
                result.append({"ir": "CicsReturn"})
            elif verb in ("LINK", "XCTL"):
                prog = re.search(r"PROGRAM\s*\(([^)]+)\)", text, re.IGNORECASE)
                result.append({"ir": "CicsCall", "verb": verb,
                               "program": prog.group(1).strip() if prog else "?"})
            else:
                result.append({"ir": "CicsOp", "verb": verb, "text": text[:60]})

        elif kind == "Stmt_GOBACK" or kind == "Stmt_STOP":
            result.append({"ir": "Return"})

        elif kind == "Stmt_INITIALIZE":
            result.append({"ir": "Initialize", "text": text[:60]})

    return result


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _java_name(cobol: str) -> str:
    parts = cobol.lower().split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _java_method_name(cobol: str) -> str:
    return _java_name(cobol.lower())


def _parse_move(text: str) -> tuple[str, str]:
    m = re.search(r"MOVE\s+(\S+)\s+TO\s+([A-Z0-9-]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), _java_name(m.group(2).strip())
    return "UNKNOWN", "UNKNOWN"


def _parse_compute(text: str) -> tuple[str, str]:
    m = re.search(r"COMPUTE\s+([A-Z0-9-]+)\s*=\s*(.+?)(?:END-COMPUTE|$)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return _java_name(m.group(1).strip()), m.group(2).strip()[:100]
    return "UNKNOWN", text[:60]


def _extract_condition(text: str) -> str:
    m = re.search(r"IF\s+(.+?)(?:\s+THEN|\s+ELSE|END-IF|$)", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip()[:100] if m else text[:60]


def _extract_perform_target(text: str) -> str:
    m = re.search(r"PERFORM\s+([A-Z0-9-]+)", text, re.IGNORECASE)
    return m.group(1) if m else ""


def _parse_arith_expr(op: str, text: str) -> dict:
    """G5: Parse an arithmetic statement into a structured expression tree."""
    t = text.upper()
    node: dict = {"ir": "ArithOp", "op": op.lower()}

    if op == "ADD":
        giving = re.search(r"\bGIVING\s+([A-Z0-9-]+)", t)
        if giving:
            operands = re.findall(r"\bADD\s+(.+?)\s+GIVING\b", t)
            node["operands"] = operands[0].split() if operands else []
            node["result"] = _java_name(giving.group(1))
        else:
            m = re.search(r"\bADD\s+(.+?)\s+TO\s+([A-Z0-9-]+)", t)
            if m:
                node["operands"] = m.group(1).split()
                node["result"] = _java_name(m.group(2))
    elif op == "SUBTRACT":
        giving = re.search(r"\bGIVING\s+([A-Z0-9-]+)", t)
        if giving:
            m = re.search(r"\bSUBTRACT\s+(.+?)\s+FROM\s+(.+?)\s+GIVING\b", t)
            if m:
                node["subtrahend"] = m.group(1).strip()
                node["minuend"] = m.group(2).strip()
                node["result"] = _java_name(giving.group(1))
        else:
            m = re.search(r"\bSUBTRACT\s+(.+?)\s+FROM\s+([A-Z0-9-]+)", t)
            if m:
                node["subtrahend"] = m.group(1).strip()
                node["result"] = _java_name(m.group(2))
    elif op == "MULTIPLY":
        giving = re.search(r"\bGIVING\s+([A-Z0-9-]+)", t)
        m = re.search(r"\bMULTIPLY\s+([A-Z0-9-]+)\s+BY\s+([A-Z0-9-]+)", t)
        if m:
            node["operands"] = [m.group(1), m.group(2)]
            node["result"] = _java_name(giving.group(1) if giving else m.group(2))
    elif op == "DIVIDE":
        giving = re.search(r"\bGIVING\s+([A-Z0-9-]+)", t)
        rem = re.search(r"\bREMAINDER\s+([A-Z0-9-]+)", t)
        m = re.search(r"\bDIVIDE\s+([A-Z0-9-]+)\s+(?:INTO|BY)\s+([A-Z0-9-]+)", t)
        if m:
            node["divisor"] = m.group(1)
            node["dividend"] = m.group(2)
            node["result"] = _java_name(giving.group(1) if giving else m.group(2))
            if rem:
                node["remainder"] = _java_name(rem.group(1))

    if "result" not in node:
        node["raw"] = text[:80]
    return node
