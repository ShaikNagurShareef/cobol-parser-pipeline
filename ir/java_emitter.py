"""Java emitter — walk canonical IR and emit a Java class.

Target bounded context: User Admin (COUSR01C, COUSR02C, COUSR03C).

Type mapping from IR:
  BigDecimal → BigDecimal with MathContext / RoundingMode.HALF_EVEN
  long / int → primitive long / int
  String     → String with max-length Javadoc
  Object     → Map<String, Object> (group item placeholder)
"""

from __future__ import annotations

import json
import pathlib
import re
import textwrap
from typing import Any

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "java"


# ─── Public entry point ───────────────────────────────────────────────────────

def emit_java(ir: dict[str, Any], package: str = "com.ust.carddemo") -> str:
    """Emit a Java class from a canonical IR dict.

    Returns the Java source as a string and also writes it to
    output/java/{ClassName}.java.
    """
    program = ir.get("program", "Unknown")
    class_name = _to_class_name(program)

    lines: list[str] = []
    lines.append(f"package {package};")
    lines.append("")
    lines.extend(_imports(ir))
    lines.append("")
    lines.append(f"/**")
    lines.append(f" * Migrated from COBOL program: {program}")
    lines.append(f" * Source: {ir.get('source_file', '')}")
    lines.append(f" * IR version: {ir.get('ir_version', '?')}")
    lines.append(f" */")
    lines.append(f"public class {class_name} {{")
    lines.append("")

    # Fields
    for field in ir.get("fields", []):
        lines.extend(_emit_field(field))
    lines.append("")

    # Methods (one per paragraph)
    for func in ir.get("functions", []):
        lines.extend(_emit_method(func))
        lines.append("")

    lines.append("}")
    source = "\n".join(lines)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{class_name}.java").write_text(source, encoding="utf-8")
    return source


def emit_java_from_file(ir_path: pathlib.Path, package: str = "com.ust.carddemo") -> str:
    """Load a .ir.json file and emit Java."""
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    return emit_java(ir, package)


# ─── Imports ─────────────────────────────────────────────────────────────────

def _imports(ir: dict) -> list[str]:
    needs_bigdecimal = any(
        f.get("ir_type", {}).get("java_type") == "BigDecimal"
        for f in ir.get("fields", [])
    )
    needs_map = any(
        f.get("ir_type", {}).get("java_type") == "Object"
        for f in ir.get("fields", [])
    )
    result = []
    if needs_bigdecimal:
        result += [
            "import java.math.BigDecimal;",
            "import java.math.MathContext;",
            "import java.math.RoundingMode;",
        ]
    if needs_map:
        result += ["import java.util.Map;", "import java.util.HashMap;"]
    return result


# ─── Field emission ───────────────────────────────────────────────────────────

def _emit_field(field: dict) -> list[str]:
    name = field.get("name", "unknown")
    cobol = field.get("cobol_name", "")
    level = field.get("level", 0)
    pic = field.get("pic") or ""
    ir_type = field.get("ir_type", {})
    java_type = ir_type.get("java_type", "Object")
    occurs = field.get("occurs_max")

    lines: list[str] = []

    # Javadoc
    doc_parts = [f"COBOL: {cobol} (level {level})"]
    if pic:
        doc_parts.append(f"PIC {pic}")
    if java_type == "String" and ir_type.get("maxLen"):
        doc_parts.append(f"maxLen={ir_type['maxLen']}")
    if java_type == "BigDecimal":
        prec = ir_type.get("precision", "?")
        scale = ir_type.get("scale", "?")
        doc_parts.append(f"precision={prec} scale={scale}")
    lines.append(f"    /** {' | '.join(doc_parts)} */")

    # Declaration
    if occurs:
        if java_type == "BigDecimal":
            init = f"new BigDecimal[{occurs}]"
            decl_type = "BigDecimal[]"
        elif java_type in ("int", "long"):
            init = f"new {java_type}[{occurs}]"
            decl_type = f"{java_type}[]"
        elif java_type == "String":
            init = f"new String[{occurs}]"
            decl_type = "String[]"
        else:
            init = f"new Object[{occurs}]"
            decl_type = "Object[]"
        lines.append(f"    private {decl_type} {name} = {init};")
    else:
        init = _field_initializer(ir_type)
        lines.append(f"    private {_java_decl_type(ir_type)} {name} = {init};")

    return lines


def _java_decl_type(ir_type: dict) -> str:
    t = ir_type.get("java_type", "Object")
    if t == "Object":
        return "Map<String, Object>"
    return t


def _field_initializer(ir_type: dict) -> str:
    t = ir_type.get("java_type", "Object")
    if t == "BigDecimal":
        return "BigDecimal.ZERO"
    if t == "long":
        return "0L"
    if t == "int":
        return "0"
    if t == "String":
        max_len = ir_type.get("maxLen", 0)
        return f'""  /* maxLen={max_len} */'
    return "new HashMap<>()"


# ─── Method emission ──────────────────────────────────────────────────────────

def _emit_method(func: dict) -> list[str]:
    name = func.get("name", "unknown")
    cobol = func.get("cobol_name", "")
    start_line = func.get("start_line", 0)
    body_stmts = func.get("body", [])

    lines: list[str] = []
    lines.append(f"    /**")
    lines.append(f"     * Paragraph: {cobol} (source line {start_line})")
    lines.append(f"     */")
    lines.append(f"    public void {name}() {{")

    for stmt in body_stmts:
        for stmt_line in _emit_stmt(stmt, indent=2):
            lines.append(stmt_line)

    if not body_stmts:
        lines.append("        // empty paragraph")

    lines.append("    }")
    return lines


def _emit_stmt(stmt: dict, indent: int = 2) -> list[str]:
    pad = "    " * indent
    ir = stmt.get("ir", "")

    if ir == "Assign":
        target = stmt.get("target", "unknown")
        val = stmt.get("value", {})
        rhs = _emit_expr(val)
        return [f"{pad}{_safe_java_id(target)} = {rhs};"]

    if ir == "Call":
        target = stmt.get("target", "unknown")
        return [f"{pad}{_safe_java_id(target)}();"]

    if ir == "If":
        cond = _cobol_cond_to_java(stmt.get("condition", "/* condition */"))
        lines = [f"{pad}if ({cond}) {{"]
        then_body = stmt.get("then", [])
        else_body = stmt.get("else_", [])
        for s in then_body:
            lines.extend(_emit_stmt(s, indent + 1))
        if not then_body:
            lines.append(f"{pad}    // then branch")
        if else_body:
            lines.append(f"{pad}}} else {{")
            for s in else_body:
                lines.extend(_emit_stmt(s, indent + 1))
        lines.append(f"{pad}}}")
        return lines

    if ir == "Switch":
        text = stmt.get("text", "")
        return _emit_evaluate(text, pad)

    if ir == "ArithOp":
        return _emit_arith(stmt, pad)

    if ir == "FileRead":
        text = stmt.get("text", "")
        m = re.search(r"\bREAD\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        fname = _safe_java_id(m.group(1)) if m else "file"
        into_m = re.search(r"\bINTO\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        into = f", {_safe_java_id(into_m.group(1))}" if into_m else ""
        return [
            f"{pad}{fname}FileStatus = fileIO.read({fname}File{into});",
            f"{pad}if (!\"00\".equals({fname}FileStatus)) {{ /* handle not-found/error */ }}",
        ]

    if ir == "FileWrite":
        text = stmt.get("text", "")
        m = re.search(r"\bWRITE\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        fname = _safe_java_id(m.group(1)) if m else "record"
        from_m = re.search(r"\bFROM\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        from_var = f", {_safe_java_id(from_m.group(1))}" if from_m else ""
        return [f"{pad}{fname}FileStatus = fileIO.write({fname}File{from_var});"]

    if ir == "FileRewrite":
        text = stmt.get("text", "")
        m = re.search(r"\bREWRITE\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        fname = _safe_java_id(m.group(1)) if m else "record"
        from_m = re.search(r"\bFROM\s+([A-Z0-9-]+)", text, re.IGNORECASE)
        from_var = f", {_safe_java_id(from_m.group(1))}" if from_m else ""
        return [f"{pad}{fname}FileStatus = fileIO.rewrite({fname}File{from_var});"]

    if ir == "CicsReturn":
        return [f"{pad}return; // EXEC CICS RETURN"]

    if ir == "CicsCall":
        verb = stmt.get("verb", "LINK")
        prog = stmt.get("program", "?")
        return [f"{pad}cics{verb.capitalize()}(\"{prog}\"); // EXEC CICS {verb}"]

    if ir == "CicsOp":
        verb = stmt.get("verb", "")
        text = stmt.get("text", "")
        return [f"{pad}// CICS {verb}: {_truncate(text, 50)}"]

    if ir == "Return":
        return [f"{pad}return;"]

    if ir == "Initialize":
        text = stmt.get("text", "")
        # Extract variable names after INITIALIZE
        vars_m = re.findall(r"\bINITIALIZE\s+([A-Z0-9-]+(?:\s+[A-Z0-9-]+)*)", text, re.IGNORECASE)
        if vars_m:
            names = vars_m[0].split()
            out = []
            for v in names:
                j = _safe_java_id(v)
                out.append(f"{pad}{j} = ({j} instanceof BigDecimal) ? BigDecimal.ZERO : \"\";  // INITIALIZE {v}")
            return out if out else [f"{pad}// INITIALIZE: {_truncate(text, 60)}"]
        return [f"{pad}// INITIALIZE: {_truncate(text, 60)}"]

    return [f"{pad}// {ir}: {_truncate(str(stmt), 60)}"]


def _emit_arith(stmt: dict, pad: str) -> list[str]:
    """Emit BigDecimal arithmetic for ADD/SUBTRACT/MULTIPLY/DIVIDE ArithOp nodes."""
    op = (stmt.get("op") or stmt.get("kind") or "OP").upper()
    operands = stmt.get("operands", [])
    result = stmt.get("result", "")

    if not result:
        return [f"{pad}// {op}: (no result target parsed)"]

    res = _safe_java_id(result)
    ops = [_safe_java_id(o) for o in operands if o]

    if op == "ADD":
        if ops:
            chain = " + ".join(ops)
            return [f"{pad}{res} = {res}.add(new BigDecimal(\"{chain}\").setScale(2, RoundingMode.HALF_EVEN));"]
        return [f"{pad}// ADD: operands not parsed"]
    elif op == "SUBTRACT":
        if ops:
            chain = ".subtract(".join(f"new BigDecimal(\"{o}\")" for o in ops)
            if len(ops) == 1:
                return [f"{pad}{res} = {res}.subtract({_safe_java_id(ops[0])}.setScale(2, RoundingMode.HALF_EVEN));"]
            return [f"{pad}{res} = {chain}.setScale(2, RoundingMode.HALF_EVEN);"]
        return [f"{pad}// SUBTRACT: operands not parsed"]
    elif op == "MULTIPLY":
        if len(ops) >= 1:
            return [f"{pad}{res} = {res}.multiply({_safe_java_id(ops[0])}).setScale(2, RoundingMode.HALF_EVEN);"]
        return [f"{pad}// MULTIPLY: operands not parsed"]
    elif op == "DIVIDE":
        if len(ops) >= 1:
            return [f"{pad}{res} = {res}.divide({_safe_java_id(ops[0])}, 2, RoundingMode.HALF_EVEN);"]
        return [f"{pad}// DIVIDE: operands not parsed"]
    else:
        return [f"{pad}// {op}: {_truncate(str(stmt), 60)}"]


def _emit_evaluate(text: str, pad: str) -> list[str]:
    """Emit if/else chain from EVALUATE … WHEN … text."""
    lines = []
    # Extract WHEN clauses using simple regex
    subject_m = re.search(r"EVALUATE\s+(.+?)(?=\s+WHEN\s+|\s*$)", text, re.IGNORECASE | re.DOTALL)
    subject = _cobol_cond_to_java((subject_m.group(1) or "TRUE").strip()[:80]) if subject_m else "TRUE"
    whens = re.findall(r"WHEN\s+(.+?)(?=WHEN|END-EVALUATE|$)", text, re.IGNORECASE | re.DOTALL)
    if not whens:
        lines.append(f"{pad}// EVALUATE {subject}: no WHEN clauses parsed")
        return lines
    first = True
    for w in whens:
        w = w.strip()
        if not w:
            continue
        parts = w.split("\n", 1)
        when_val = _cobol_cond_to_java(parts[0].strip()[:60])
        kw = "if" if first else "} else if"
        if when_val.upper() in ("OTHER", "ALSO OTHER"):
            lines.append(f"{pad}}} else {{")
            lines.append(f"{pad}    // WHEN OTHER")
        elif first:
            lines.append(f"{pad}if ({subject}.equals({when_val})) {{")
        else:
            lines.append(f"{pad}}} else if ({subject}.equals({when_val})) {{")
            _ = kw  # used above
        first = False
    lines.append(f"{pad}}}")
    return lines


def _emit_expr(val: dict) -> str:
    ir = val.get("ir", "")
    if ir == "Ref":
        name = val.get("name", "unknown")
        return _safe_java_id(name)
    if ir == "Expr":
        expr = val.get("expression", "")
        return _cobol_expr_to_java(expr)
    if ir == "Literal":
        return repr(val.get("value", ""))
    return "/* unknown expr */"


# ─── Expression / condition translation ──────────────────────────────────────

def _cobol_expr_to_java(expr: str) -> str:
    """Best-effort COBOL arithmetic → Java expression."""
    expr = expr.strip()
    # Replace COBOL ** with Math.pow
    expr = re.sub(
        r"([A-Z0-9a-z\-]+)\s*\*\*\s*([A-Z0-9a-z\-]+)",
        lambda m: f"Math.pow({_safe_java_id(m.group(1))}, {_safe_java_id(m.group(2))})",
        expr,
    )
    # camelCase identifiers
    expr = re.sub(
        r"\b([A-Z][A-Z0-9\-]{2,})\b",
        lambda m: _safe_java_id(m.group(1)),
        expr,
    )
    # COBOL numeric literal: no change needed
    return expr or "/* expression */"


def _cobol_cond_to_java(cond: str) -> str:
    """Best-effort COBOL condition → Java boolean expression."""
    cond = cond.strip()
    cond = re.sub(r"\bAND\b", "&&", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bOR\b", "||", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bNOT\b", "!", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bEQUAL TO\b", "==", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bNOT EQUAL\b", "!=", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bGREATER THAN OR EQUAL TO\b", ">=", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bGREATER THAN\b", ">", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bLESS THAN OR EQUAL TO\b", "<=", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bLESS THAN\b", "<", cond, flags=re.IGNORECASE)
    cond = re.sub(r"\bEQUALS?\b", "==", cond, flags=re.IGNORECASE)
    # camelCase COBOL identifiers
    cond = re.sub(
        r"\b([A-Z][A-Z0-9\-]{2,})\b",
        lambda m: _safe_java_id(m.group(1)),
        cond,
    )
    return cond or "/* condition */"


# ─── Helpers ─────────────────────────────────────────────────────────────────

_JAVA_RESERVED = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while",
}


def _to_class_name(program: str) -> str:
    parts = program.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p)


def _java_name(cobol: str) -> str:
    parts = cobol.lower().split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _safe_java_id(name: str) -> str:
    j = _java_name(name)
    if j in _JAVA_RESERVED:
        j = j + "_"
    return j


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s[:n] + "..." if len(s) > n else s


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Emit Java from canonical IR JSON")
    ap.add_argument("ir_file", help="Path to .ir.json file, or program name to look up")
    ap.add_argument("--package", default="com.ust.carddemo", help="Java package name")
    ap.add_argument("--ir-dir", default="output/ir", help="Directory containing *.ir.json files")
    args = ap.parse_args()

    ir_path = pathlib.Path(args.ir_file)
    if not ir_path.exists():
        # Try looking up by program name
        ir_path = pathlib.Path(args.ir_dir) / f"{args.ir_file}.ir.json"
    if not ir_path.exists():
        print(f"IR file not found: {args.ir_file}", file=sys.stderr)
        sys.exit(1)

    java_src = emit_java_from_file(ir_path, package=args.package)
    print(java_src)
