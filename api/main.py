"""FastAPI REST API — all 11 required endpoints from §8 of the brief."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from storage.db import get_connection

app = FastAPI(
    title="CardDemo COBOL Pipeline API",
    description="Queryable artifact store for the UST CodeCrafter Championship pipeline.",
    version="1.0.0",
)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "artifacts" / "pipeline.db"


def _con():
    return get_connection()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ── 1. getProgram ─────────────────────────────────────────────────────────────

@app.get("/programs/{program_name}", tags=["Programs"])
def get_program(program_name: str):
    """Program metadata + node UUID."""
    with _con() as con:
        row = con.execute(
            "SELECT * FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (program_name,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Program '{program_name}' not found")
    d = _row_to_dict(row)
    d["payload"] = json.loads(d.get("payload_json") or "{}")
    return d


# ── 2. getParagraph ───────────────────────────────────────────────────────────

@app.get("/paragraphs/{uuid}", tags=["Paragraphs"])
def get_paragraph(uuid: str):
    """Paragraph AST node + child statements."""
    with _con() as con:
        para = con.execute("SELECT * FROM nodes WHERE uuid=?", (uuid,)).fetchone()
        if not para:
            raise HTTPException(404, "Paragraph not found")
        stmts = con.execute(
            "SELECT * FROM nodes WHERE parent_uuid=? ORDER BY start_line",
            (uuid,),
        ).fetchall()
    result = _row_to_dict(para)
    result["payload"] = json.loads(result.get("payload_json") or "{}")
    result["statements"] = _rows_to_list(stmts)
    return result


# ── 3. getDataItem ────────────────────────────────────────────────────────────

@app.get("/data-items/{uuid}", tags=["Data Items"])
def get_data_item(uuid: str):
    """Data item definition with canonical type and caller count."""
    with _con() as con:
        item = con.execute("SELECT * FROM data_items WHERE uuid=?", (uuid,)).fetchone()
        if not item:
            raise HTTPException(404, "Data item not found")
        reads  = con.execute("SELECT COUNT(*) FROM def_use WHERE data_item_uuid=? AND op='READ'",  (uuid,)).fetchone()[0]
        writes = con.execute("SELECT COUNT(*) FROM def_use WHERE data_item_uuid=? AND op='WRITE'", (uuid,)).fetchone()[0]
    result = _row_to_dict(item)
    result["read_count"] = reads
    result["write_count"] = writes
    return result


# ── 4/5. getCallers / getCallees ──────────────────────────────────────────────

@app.get("/call-graph/{uuid}/callers", tags=["Call Graph"])
def get_callers(uuid: str):
    """Programs that call the given program UUID."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT cg.*, n.name AS caller_name
            FROM call_graph cg
            JOIN nodes n ON n.uuid = cg.caller_uuid
            WHERE cg.callee_uuid = ?
            """,
            (uuid,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/call-graph/{uuid}/callees", tags=["Call Graph"])
def get_callees(uuid: str):
    """Programs called by the given program UUID."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT cg.*, n.name AS callee_resolved_name
            FROM call_graph cg
            LEFT JOIN nodes n ON n.uuid = cg.callee_uuid
            WHERE cg.caller_uuid = ?
            """,
            (uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── 6. getControlFlow ─────────────────────────────────────────────────────────

@app.get("/control-flow/{program_uuid}", tags=["Control Flow"])
def get_control_flow(program_uuid: str):
    """CFG as a list of {from, to, edge_type} edges for the given program."""
    with _con() as con:
        paras = con.execute(
            "SELECT uuid FROM nodes WHERE parent_uuid=? AND kind='Paragraph'",
            (program_uuid,),
        ).fetchall()
        para_uuids = {r["uuid"] for r in paras}
        if not para_uuids:
            return []
        placeholders = ",".join("?" * len(para_uuids))
        rows = con.execute(
            f"""
            SELECT cf.*, n1.name AS from_name, n2.name AS to_name
            FROM control_flow cf
            LEFT JOIN nodes n1 ON n1.uuid = cf.from_uuid
            LEFT JOIN nodes n2 ON n2.uuid = cf.to_uuid
            WHERE cf.from_uuid IN ({placeholders})
            """,
            list(para_uuids),
        ).fetchall()
    return _rows_to_list(rows)


# ── 7. getDefUse ──────────────────────────────────────────────────────────────

@app.get("/def-use/{data_item_uuid}", tags=["Def-Use"])
def get_def_use(data_item_uuid: str):
    """All reads and writes for a data item, with statement context."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT du.*, n.kind AS stmt_kind, n.start_line
            FROM def_use du
            JOIN nodes n ON n.uuid = du.stmt_uuid
            WHERE du.data_item_uuid = ?
            ORDER BY n.start_line
            """,
            (data_item_uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── 8. getBusinessRules ───────────────────────────────────────────────────────

@app.get("/business-rules/{program_uuid}", tags=["Business Rules"])
def get_business_rules(program_uuid: str):
    """Ordered business rule catalog for a program."""
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM business_rules WHERE program_uuid=? ORDER BY line",
            (program_uuid,),
        ).fetchall()
    result = _rows_to_list(rows)
    for r in result:
        if r.get("predicate_resolved"):
            try:
                r["predicate_resolved"] = json.loads(r["predicate_resolved"])
            except Exception:
                pass
    return result


# ── 9. getFileAccesses ────────────────────────────────────────────────────────

@app.get("/file-access/{program_uuid}", tags=["File I/O"])
def get_file_access(program_uuid: str):
    """All file I/O operations for a program."""
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM file_io WHERE program_uuid=? ORDER BY line",
            (program_uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── 10. getTransactionFlow ────────────────────────────────────────────────────

@app.get("/transaction-flow/{trans_id}", tags=["Transaction Flow"])
def get_transaction_flow(trans_id: str):
    """Reachable transaction graph starting from a transaction ID."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT tf.*, n.name AS from_program
            FROM transaction_flow tf
            JOIN nodes n ON n.uuid = tf.from_uuid
            WHERE tf.trans_id = ? OR tf.to_program LIKE ?
            """,
            (trans_id.upper(), f"%{trans_id.upper()}%"),
        ).fetchall()
    return _rows_to_list(rows)


# ── 11. getJobChain ───────────────────────────────────────────────────────────

@app.get("/jcl/job-chain/{job_name}", tags=["JCL"])
def get_job_chain(job_name: str):
    """Upstream and downstream job dependencies via dataset reuse."""
    job_u = job_name.upper()
    with _con() as con:
        upstream = con.execute(
            "SELECT * FROM jcl_dependency WHERE UPPER(consumer_job)=?", (job_u,)
        ).fetchall()
        downstream = con.execute(
            "SELECT * FROM jcl_dependency WHERE UPPER(producer_job)=?", (job_u,)
        ).fetchall()
        steps = con.execute(
            "SELECT * FROM jcl_job WHERE UPPER(job_name)=?", (job_u,)
        ).fetchall()
    return {
        "job": job_name,
        "upstream": _rows_to_list(upstream),
        "downstream": _rows_to_list(downstream),
        "steps": _rows_to_list(steps),
    }


# ── 12. getCopybookConsumers ──────────────────────────────────────────────────

@app.get("/copybooks/{copybook_name}/consumers", tags=["Copybooks"])
def get_copybook_consumers(copybook_name: str):
    """Programs that include this copybook."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT cu.*, n.name AS program_name
            FROM copybook_use cu
            JOIN nodes n ON n.uuid = cu.program_uuid
            WHERE UPPER(cu.copybook_name)=UPPER(?)
            """,
            (copybook_name,),
        ).fetchall()
    return _rows_to_list(rows)


# ── Bonus: coverage report ────────────────────────────────────────────────────

@app.get("/reports/coverage", tags=["Reports"])
def get_coverage_report():
    """Parse coverage summary across the full corpus."""
    with _con() as con:
        rows = con.execute("SELECT * FROM parse_coverage ORDER BY source_file").fetchall()
        total = len(rows)
        ok = sum(1 for r in rows if r["status"] == "OK")
    return {
        "total_files": total,
        "ok_files": ok,
        "coverage_pct": round(100 * ok / max(total, 1), 1),
        "files": _rows_to_list(rows),
    }


@app.get("/reports/risk-register", tags=["Reports"])
def get_risk_register():
    """Full migration risk register."""
    with _con() as con:
        rows = con.execute(
            """
            SELECT rr.*, n.name AS program_name, n.source_file
            FROM risk_register rr
            LEFT JOIN nodes n ON n.uuid = rr.program_uuid
            ORDER BY rr.severity, rr.kind
            """
        ).fetchall()
    return _rows_to_list(rows)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Utility"])
def health():
    return {"status": "ok", "db": str(DEFAULT_DB.resolve())}
