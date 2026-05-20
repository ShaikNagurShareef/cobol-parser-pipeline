"""Mermaid diagram generator — SQL → .mmd diagram files."""

from __future__ import annotations

import re
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
        """SELECT n1.name AS caller, cg.callee_name AS callee, cg.call_type
           FROM call_graph cg JOIN nodes n1 ON n1.uuid = cg.caller_uuid
           WHERE cg.callee_name != '' LIMIT 150"""
    ).fetchall()
    lines = ["graph LR"]
    seen: set[tuple] = set()
    nodes_added: set[str] = set()
    for row in rows:
        caller_raw = row["caller"] or "UNKNOWN"
        callee_raw = row["callee"] or "UNKNOWN"
        caller = _safe(caller_raw)
        callee = _safe(callee_raw)
        if caller not in nodes_added:
            lines.append(f'    {caller}["{caller_raw}"]')
            nodes_added.add(caller)
        if callee not in nodes_added:
            lines.append(f'    {callee}["{callee_raw}"]')
            nodes_added.add(callee)
        key = (caller, callee)
        if key in seen:
            continue
        seen.add(key)
        style = "-->" if "LITERAL" in (row["call_type"] or "") else "-.->"
        label = f'|"{row["call_type"]}"|' if "CICS" in (row["call_type"] or "") else ""
        lines.append(f"    {caller} {style}{label} {callee}")
    return "\n".join(lines)


def _call_graph(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "call_graph.mmd", _call_graph_mmd(con))


# ─── Transaction Flow ─────────────────────────────────────────────────────────

def _tx_flow_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """SELECT n1.name AS from_prog, tf.to_program, tf.verb, tf.trans_id
           FROM transaction_flow tf JOIN nodes n1 ON n1.uuid = tf.from_uuid
           WHERE tf.to_program IS NOT NULL LIMIT 100"""
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
        label = row["verb"] or "CALL"
        if row["trans_id"]:
            label += f" ({row['trans_id']})"
        lines.append(f"    {frm} --> {to} : {label}")
    if len(lines) == 1:
        lines.append("    [*] --> NO_DATA : No transaction flow data")
    return "\n".join(lines)


def _transaction_flow(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "transaction_flow.mmd", _tx_flow_mmd(con))


# ─── JCL Job Chain ────────────────────────────────────────────────────────────

def _jcl_chain_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """SELECT DISTINCT j.job_name, j.step_name, j.program
           FROM jcl_job j WHERE j.program IS NOT NULL
           ORDER BY j.job_name, j.step_name LIMIT 80"""
    ).fetchall()
    lines = ["graph TD"]
    seen: set[tuple] = set()
    jobs_added: set[str] = set()
    for row in rows:
        job = _safe(row["job_name"])
        prog = _safe(row["program"])
        if job not in jobs_added:
            lines.append(f'    {job}["{row["job_name"]}"]:::job')
            jobs_added.add(job)
        key = (job, prog)
        if key not in seen:
            seen.add(key)
            lines.append(f'    {job} -->|"{row["step_name"] or "STEP"}"| {prog}["{row["program"]}"]:::prog')
    lines.append("    classDef job fill:#2b2008,stroke:#fbbf24,color:#fbbf24")
    lines.append("    classDef prog fill:#0d2c30,stroke:#5ecdd1,color:#5ecdd1")
    if len(lines) <= 3:
        lines.append('    NOTE["No JCL job data found"]')
    return "\n".join(lines)


def _jcl_job_chain(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "jcl_job_chain.mmd", _jcl_chain_mmd(con))


# ─── File I/O Graph ───────────────────────────────────────────────────────────

def _file_io_mmd(con: sqlite3.Connection) -> str:
    rows = con.execute(
        """SELECT n.name AS program, fio.file_name, fio.operation
           FROM file_io fio JOIN nodes n ON n.uuid = fio.program_uuid
           LIMIT 120"""
    ).fetchall()
    lines = ["graph LR"]
    seen: set[tuple] = set()
    progs_added: set[str] = set()
    files_added: set[str] = set()
    for row in rows:
        prog = _safe(row["program"])
        fname = _safe(row["file_name"])
        op = row["operation"] or "IO"
        # Add nodes with labels if not added yet
        if prog not in progs_added:
            lines.append(f'    {prog}(["{row["program"]}"]):::prog')
            progs_added.add(prog)
        if fname not in files_added:
            lines.append(f'    {fname}[("{row["file_name"]}")]:::file')
            files_added.add(fname)
        key = (prog, fname, op)
        if key not in seen:
            seen.add(key)
            if op in ("WRITE", "REWRITE", "DELETE"):
                lines.append(f'    {prog} -->|"{op}"| {fname}')
            else:
                lines.append(f'    {fname} -->|"{op}"| {prog}')
    lines.append("    classDef prog fill:#0d2c30,stroke:#5ecdd1,color:#5ecdd1")
    lines.append("    classDef file fill:#2b2008,stroke:#fbbf24,color:#fbbf24")
    if len(lines) <= 2:
        lines.append('    NOTE["No file I/O data found"]')
    return "\n".join(lines)


def _file_io_graph(con: sqlite3.Connection, out: pathlib.Path) -> None:
    _write(out / "file_io_graph.mmd", _file_io_mmd(con))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return re.sub(r"\W", "_", name)


def _write(path: pathlib.Path, content: str) -> None:
    path.write_text(content + "\n", encoding="utf-8")
