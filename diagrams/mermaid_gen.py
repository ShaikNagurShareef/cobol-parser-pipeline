"""Mermaid diagram generator — SQL → .mmd diagram files."""

from __future__ import annotations

import pathlib
import sqlite3

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "diagrams"


def generate_all_diagrams(con: sqlite3.Connection, output_dir: pathlib.Path | None = None) -> None:
    """Generate all four required Mermaid diagrams from the graph tables."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    _call_graph(con, out)
    _transaction_flow(con, out)
    _jcl_job_chain(con, out)
    _file_io_graph(con, out)


# ─── Call Graph ───────────────────────────────────────────────────────────────

def _call_graph_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """
        SELECT n1.name AS caller, cg.callee_name AS callee, cg.call_type
        FROM call_graph cg
        JOIN nodes n1 ON n1.uuid = cg.caller_uuid
        WHERE cg.callee_name != ''
        LIMIT 150
        """
    ).fetchall()
    lines = ["graph LR"]
    seen: set[tuple] = set()
    for row in rows:
        caller = _safe(row["caller"])
        callee = _safe(row["callee"])
        key = (caller, callee)
        if key in seen:
            continue
        seen.add(key)
        style = "-->" if "LITERAL" in row["call_type"] else "-.->"
        label = f"|{row['call_type']}|" if "CICS" in row["call_type"] else ""
        lines.append(f"    {caller} {style}{label} {callee}")
    return "\n".join(lines)


def _call_graph(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "call_graph.mmd", _call_graph_mmd(con))


# ─── Transaction Flow ─────────────────────────────────────────────────────────

def _tx_flow_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """
        SELECT n1.name AS from_prog, tf.to_program, tf.verb, tf.trans_id
        FROM transaction_flow tf
        JOIN nodes n1 ON n1.uuid = tf.from_uuid
        WHERE tf.to_program IS NOT NULL
        LIMIT 100
        """
    ).fetchall()
    lines = ["stateDiagram-v2"]
    seen: set[tuple] = set()
    for row in rows:
        frm = _safe(row["from_prog"])
        to  = _safe(row["to_program"])
        key = (frm, to, row["verb"])
        if key in seen:
            continue
        seen.add(key)
        label = row["verb"]
        if row["trans_id"]:
            label += f"\\n({row['trans_id']})"
        lines.append(f"    {frm} --> {to} : {label}")
    return "\n".join(lines)


def _transaction_flow(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "transaction_flow.mmd", _tx_flow_mmd(con))


# ─── JCL Job Chain ────────────────────────────────────────────────────────────

def _jcl_chain_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        "SELECT producer_job, consumer_job, dataset FROM jcl_dependency LIMIT 80"
    ).fetchall()
    lines = ["graph TD"]
    seen: set[tuple] = set()
    for row in rows:
        p = _safe(row["producer_job"])
        c = _safe(row["consumer_job"])
        key = (p, c)
        if key in seen:
            continue
        seen.add(key)
        dsn = row["dataset"] or ""
        short_dsn = dsn.split(".")[-1] if "." in dsn else dsn
        lines.append(f"    {p} -->|{_safe(short_dsn)}| {c}")
    if len(lines) == 1:
        lines.append("    Note[No job dependencies found]")
    return "\n".join(lines)


def _jcl_job_chain(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "jcl_job_chain.mmd", _jcl_chain_mmd(con))


# ─── File I/O Graph ───────────────────────────────────────────────────────────

def _file_io_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """
        SELECT n.name AS program, fio.file_name, fio.operation
        FROM file_io fio
        JOIN nodes n ON n.uuid = fio.program_uuid
        LIMIT 120
        """
    ).fetchall()
    lines = ["graph LR"]
    seen: set[tuple] = set()
    for row in rows:
        prog = _safe(row["program"])
        fname = _safe(row["file_name"])
        op = row["operation"] or "IO"
        key = (prog, fname, op)
        if key in seen:
            continue
        seen.add(key)
        arrow = "-->" if op in ("WRITE", "REWRITE", "DELETE") else "<--"
        lines.append(f"    {prog} {arrow}|{op}| {fname}")
    if len(lines) == 1:
        lines.append("    Note[No file I/O found]")
    return "\n".join(lines)


def _file_io_graph(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "file_io_graph.mmd", _file_io_mmd(con))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _write(path: pathlib.Path, content: str) -> None:
    path.write_text(content + "\n", encoding="utf-8")


import re
