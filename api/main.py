"""FastAPI REST API — all endpoints for the COBOL Pipeline + UI."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import AsyncGenerator

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from storage.db import get_connection, init_db

app = FastAPI(
    title="CardDemo COBOL Pipeline API",
    description="Queryable artifact store for the UST CodeCrafter Championship pipeline.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_DB = pathlib.Path(os.environ.get("PIPELINE_DB", str(PROJECT_ROOT / "artifacts" / "pipeline.db")))
UI_DIR = PROJECT_ROOT / "ui"


def _con():
    return get_connection(DEFAULT_DB)


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _db_exists() -> bool:
    return DEFAULT_DB.exists() and DEFAULT_DB.stat().st_size > 0


# ── Dashboard stats ───────────────────────────────────────────────────────────

@app.get("/stats", tags=["Dashboard"])
def get_stats():
    """Dashboard KPIs: counts across all artifact layers."""
    if not _db_exists():
        return {
            "programs": 0, "paragraphs": 0, "data_items": 0,
            "statements": 0, "business_rules": 0, "call_edges": 0,
            "file_io_ops": 0, "risks": 0, "coverage_pct": 0,
            "ok_files": 0, "total_files": 0,
        }
    with _con() as con:
        def cnt(sql, *args):
            return con.execute(sql, args).fetchone()[0]

        programs    = cnt("SELECT COUNT(*) FROM nodes WHERE kind='Program'")
        paragraphs  = cnt("SELECT COUNT(*) FROM nodes WHERE kind='Paragraph'")
        data_items  = cnt("SELECT COUNT(*) FROM data_items")
        statements  = cnt("SELECT COUNT(*) FROM nodes WHERE kind LIKE 'Stmt_%'")
        biz_rules   = cnt("SELECT COUNT(*) FROM business_rules")
        call_edges  = cnt("SELECT COUNT(*) FROM call_graph")
        file_ops    = cnt("SELECT COUNT(*) FROM file_io")
        risks       = cnt("SELECT COUNT(*) FROM risk_register")
        total_files = cnt("SELECT COUNT(*) FROM parse_coverage")
        ok_files    = cnt("SELECT COUNT(*) FROM parse_coverage WHERE status='OK'")
        cov_pct     = round(100 * ok_files / max(total_files, 1), 1)

    return {
        "programs": programs, "paragraphs": paragraphs,
        "data_items": data_items, "statements": statements,
        "business_rules": biz_rules, "call_edges": call_edges,
        "file_io_ops": file_ops, "risks": risks,
        "coverage_pct": cov_pct, "ok_files": ok_files, "total_files": total_files,
    }


# ── Program list ──────────────────────────────────────────────────────────────

@app.get("/programs", tags=["Programs"])
def list_programs(
    q: str = Query("", description="Filter by name"),
    limit: int = Query(200, le=1000),
    offset: int = Query(0),
):
    """Paginated list of all parsed programs."""
    if not _db_exists():
        return {"items": [], "total": 0}
    with _con() as con:
        like = f"%{q.upper()}%" if q else "%"
        rows = con.execute(
            """
            SELECT n.uuid, n.name, n.source_file, n.start_line, n.end_line,
                   (SELECT COUNT(*) FROM nodes p WHERE p.parent_uuid=n.uuid AND p.kind='Paragraph') AS para_count,
                   (SELECT COUNT(*) FROM data_items d WHERE d.program_uuid=n.uuid) AS item_count,
                   (SELECT COUNT(*) FROM business_rules b WHERE b.program_uuid=n.uuid) AS rule_count,
                   (SELECT COUNT(*) FROM risk_register r WHERE r.program_uuid=n.uuid) AS risk_count
            FROM nodes n
            WHERE n.kind='Program' AND UPPER(n.name) LIKE ?
            ORDER BY n.name
            LIMIT ? OFFSET ?
            """,
            (like, limit, offset),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='Program' AND UPPER(name) LIKE ?",
            (like,),
        ).fetchone()[0]
    return {"items": _rows_to_list(rows), "total": total}


@app.get("/programs/{program_name}", tags=["Programs"])
def get_program(program_name: str):
    """Program metadata + node UUID."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
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


@app.get("/programs/{program_name}/detail", tags=["Programs"])
def get_program_detail(program_name: str):
    """Full program detail: paragraphs, data items, call graph, business rules, risks."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        prog = con.execute(
            "SELECT * FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (program_name,),
        ).fetchone()
        if not prog:
            raise HTTPException(404, f"Program '{program_name}' not found")
        uuid = prog["uuid"]

        paragraphs = _rows_to_list(con.execute(
            "SELECT uuid, name, start_line, end_line FROM nodes "
            "WHERE parent_uuid=? AND kind='Paragraph' ORDER BY start_line",
            (uuid,),
        ).fetchall())

        data_items = _rows_to_list(con.execute(
            "SELECT uuid, name, level, pic, usage, canonical_kind, precision, scale, signed, length "
            "FROM data_items WHERE program_uuid=? ORDER BY name",
            (uuid,),
        ).fetchall())

        call_out = _rows_to_list(con.execute(
            "SELECT callee_name, call_type, is_resolved FROM call_graph WHERE caller_uuid=?",
            (uuid,),
        ).fetchall())

        business_rules = _rows_to_list(con.execute(
            "SELECT uuid, kind, predicate_raw, then_summary, else_summary, line "
            "FROM business_rules WHERE program_uuid=? ORDER BY line",
            (uuid,),
        ).fetchall())

        file_ops = _rows_to_list(con.execute(
            "SELECT file_name, operation, record_copybook FROM file_io WHERE program_uuid=?",
            (uuid,),
        ).fetchall())

        risks = _rows_to_list(con.execute(
            "SELECT kind, severity, note, line FROM risk_register "
            "WHERE program_uuid=? ORDER BY severity",
            (uuid,),
        ).fetchall())

        copybooks = _rows_to_list(con.execute(
            "SELECT copybook_name FROM copybook_use WHERE program_uuid=?",
            (uuid,),
        ).fetchall())

    return {
        "program": _row_to_dict(prog),
        "paragraphs": paragraphs,
        "data_items": data_items,
        "call_graph": call_out,
        "business_rules": business_rules,
        "file_io": file_ops,
        "risks": risks,
        "copybooks": copybooks,
    }


# ── Paragraphs ────────────────────────────────────────────────────────────────

@app.get("/paragraphs/{uuid}", tags=["Paragraphs"])
def get_paragraph(uuid: str):
    with _con() as con:
        para = con.execute("SELECT * FROM nodes WHERE uuid=?", (uuid,)).fetchone()
        if not para:
            raise HTTPException(404, "Paragraph not found")
        stmts = con.execute(
            "SELECT * FROM nodes WHERE parent_uuid=? ORDER BY start_line", (uuid,)
        ).fetchall()
    result = _row_to_dict(para)
    result["payload"] = json.loads(result.get("payload_json") or "{}")
    result["statements"] = _rows_to_list(stmts)
    return result


# ── Data items ────────────────────────────────────────────────────────────────

@app.get("/data-items/{uuid}", tags=["Data Items"])
def get_data_item(uuid: str):
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


# ── Call graph ────────────────────────────────────────────────────────────────

@app.get("/call-graph/{uuid}/callers", tags=["Call Graph"])
def get_callers(uuid: str):
    with _con() as con:
        rows = con.execute(
            "SELECT cg.*, n.name AS caller_name FROM call_graph cg "
            "JOIN nodes n ON n.uuid=cg.caller_uuid WHERE cg.callee_uuid=?",
            (uuid,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/call-graph/{uuid}/callees", tags=["Call Graph"])
def get_callees(uuid: str):
    with _con() as con:
        rows = con.execute(
            "SELECT cg.*, n.name AS callee_resolved_name FROM call_graph cg "
            "LEFT JOIN nodes n ON n.uuid=cg.callee_uuid WHERE cg.caller_uuid=?",
            (uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── Control flow ──────────────────────────────────────────────────────────────

@app.get("/control-flow/{program_uuid}", tags=["Control Flow"])
def get_control_flow(program_uuid: str):
    with _con() as con:
        paras = con.execute(
            "SELECT uuid FROM nodes WHERE parent_uuid=? AND kind='Paragraph'",
            (program_uuid,),
        ).fetchall()
        para_uuids = [r["uuid"] for r in paras]
        if not para_uuids:
            return {"edges": [], "nodes": []}
        ph = ",".join("?" * len(para_uuids))
        rows = con.execute(
            f"SELECT cf.*, n1.name AS from_name, n2.name AS to_name "
            f"FROM control_flow cf "
            f"LEFT JOIN nodes n1 ON n1.uuid=cf.from_uuid "
            f"LEFT JOIN nodes n2 ON n2.uuid=cf.to_uuid "
            f"WHERE cf.from_uuid IN ({ph})",
            para_uuids,
        ).fetchall()
    return {"edges": _rows_to_list(rows), "nodes": [{"uuid": p, "name": ""} for p in para_uuids]}


# ── Def-use ───────────────────────────────────────────────────────────────────

@app.get("/def-use/{data_item_uuid}", tags=["Def-Use"])
def get_def_use(data_item_uuid: str):
    with _con() as con:
        rows = con.execute(
            "SELECT du.*, n.kind AS stmt_kind, n.start_line FROM def_use du "
            "JOIN nodes n ON n.uuid=du.stmt_uuid WHERE du.data_item_uuid=? ORDER BY n.start_line",
            (data_item_uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── Business rules ────────────────────────────────────────────────────────────

@app.get("/business-rules/{program_uuid}", tags=["Business Rules"])
def get_business_rules(program_uuid: str):
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM business_rules WHERE program_uuid=? ORDER BY line",
            (program_uuid,),
        ).fetchall()
    return _rows_to_list(rows)


# ── File I/O ──────────────────────────────────────────────────────────────────

@app.get("/file-access/{program_uuid}", tags=["File I/O"])
def get_file_access(program_uuid: str):
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM file_io WHERE program_uuid=?", (program_uuid,)
        ).fetchall()
    return _rows_to_list(rows)


# ── Transaction flow ──────────────────────────────────────────────────────────

@app.get("/transaction-flow/{trans_id}", tags=["Transaction Flow"])
def get_transaction_flow(trans_id: str):
    with _con() as con:
        rows = con.execute(
            "SELECT tf.*, n.name AS from_program FROM transaction_flow tf "
            "JOIN nodes n ON n.uuid=tf.from_uuid "
            "WHERE tf.trans_id=? OR tf.to_program LIKE ?",
            (trans_id.upper(), f"%{trans_id.upper()}%"),
        ).fetchall()
    return _rows_to_list(rows)


# ── JCL job chain ─────────────────────────────────────────────────────────────

@app.get("/jcl/job-chain/{job_name}", tags=["JCL"])
def get_job_chain(job_name: str):
    job_u = job_name.upper()
    with _con() as con:
        return {
            "job": job_name,
            "upstream":   _rows_to_list(con.execute("SELECT * FROM jcl_dependency WHERE UPPER(consumer_job)=?", (job_u,)).fetchall()),
            "downstream": _rows_to_list(con.execute("SELECT * FROM jcl_dependency WHERE UPPER(producer_job)=?", (job_u,)).fetchall()),
            "steps":      _rows_to_list(con.execute("SELECT * FROM jcl_job WHERE UPPER(job_name)=?", (job_u,)).fetchall()),
        }


# ── Copybooks ─────────────────────────────────────────────────────────────────

@app.get("/copybooks/{copybook_name}/consumers", tags=["Copybooks"])
def get_copybook_consumers(copybook_name: str):
    with _con() as con:
        rows = con.execute(
            "SELECT cu.*, n.name AS program_name FROM copybook_use cu "
            "JOIN nodes n ON n.uuid=cu.program_uuid WHERE UPPER(cu.copybook_name)=UPPER(?)",
            (copybook_name,),
        ).fetchall()
    return _rows_to_list(rows)


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/reports/coverage", tags=["Reports"])
def get_coverage_report():
    if not _db_exists():
        return {"total_files": 0, "ok_files": 0, "coverage_pct": 0, "files": []}
    with _con() as con:
        rows = con.execute("SELECT * FROM parse_coverage ORDER BY source_file").fetchall()
        total = len(rows)
        ok = sum(1 for r in rows if r["status"] == "OK")
    return {"total_files": total, "ok_files": ok,
            "coverage_pct": round(100 * ok / max(total, 1), 1), "files": _rows_to_list(rows)}


@app.get("/reports/risk-register", tags=["Reports"])
def get_risk_register():
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            "SELECT rr.*, n.name AS program_name, n.source_file FROM risk_register rr "
            "LEFT JOIN nodes n ON n.uuid=rr.program_uuid ORDER BY rr.severity, rr.kind"
        ).fetchall()
    return _rows_to_list(rows)


# ── Diagrams ──────────────────────────────────────────────────────────────────

@app.get("/diagrams/{name}", tags=["Diagrams"])
def get_diagram(name: str):
    """Return raw Mermaid diagram text by name (call_graph, transaction_flow, etc.)."""
    safe = name.replace("/", "").replace("..", "")
    paths = [
        PROJECT_ROOT / "output" / "diagrams" / f"{safe}.mmd",
        PROJECT_ROOT / "artifacts" / "diagrams" / f"{safe}.mmd",
    ]
    for p in paths:
        if p.exists():
            return {"name": safe, "content": p.read_text()}
    # Try generating on-the-fly
    try:
        from diagrams.mermaid_gen import _call_graph_mmd, _tx_flow_mmd, _jcl_chain_mmd, _file_io_mmd
        with _con() as con:
            generators = {
                "call_graph":       _call_graph_mmd,
                "transaction_flow": _tx_flow_mmd,
                "jcl_job_chain":    _jcl_chain_mmd,
                "file_io_graph":    _file_io_mmd,
            }
            if safe in generators:
                content = generators[safe](con)
                return {"name": safe, "content": content}
    except Exception:
        pass
    raise HTTPException(404, f"Diagram '{name}' not found. Run ./run.sh --diagrams first.")


# ── LLM spec generation ───────────────────────────────────────────────────────

@app.post("/generate-spec", tags=["LLM"])
def generate_spec_endpoint(body: dict):
    """Generate a grounded specification. Body: {uuid, scope}"""
    uuid  = body.get("uuid", "")
    scope = body.get("scope", "paragraph")
    if not uuid:
        raise HTTPException(400, "uuid required")
    try:
        from llm.langgraph_agent import generate_spec_for
        spec = generate_spec_for(uuid, scope=scope)
        return {"spec": spec, "uuid": uuid, "scope": scope}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Java emit ─────────────────────────────────────────────────────────────────

@app.get("/emit-java/{program_name}", tags=["Java Emit"])
def emit_java_endpoint(program_name: str):
    """Emit Java source for a program from its canonical IR."""
    ir_path = PROJECT_ROOT / "output" / "ir" / f"{program_name.upper()}.ir.json"
    layer1_path = PROJECT_ROOT / "output" / "layer1" / f"{program_name.upper()}.json"

    try:
        if ir_path.exists():
            from ir.java_emitter import emit_java_from_file
            java_src = emit_java_from_file(ir_path)
        elif layer1_path.exists():
            import json as _json
            from ir.canonical_ir import lower_program
            from ir.java_emitter import emit_java
            nodes = _json.loads(layer1_path.read_text())
            ir = lower_program(nodes, program_name.upper())
            java_src = emit_java(ir)
        else:
            raise HTTPException(404, f"No IR found for {program_name}. Run the pipeline first.")
        return {"program": program_name, "java_source": java_src,
                "lines": len(java_src.splitlines())}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Pipeline runner (SSE streaming) ──────────────────────────────────────────

@app.post("/pipeline/run", tags=["Pipeline"])
async def run_pipeline_stream(body: dict = {}):
    """Stream pipeline execution log via Server-Sent Events."""
    corpus  = body.get("corpus", str(PROJECT_ROOT / "external/carddemo/app/cbl"))
    db_path = body.get("db",     str(DEFAULT_DB))

    async def event_stream() -> AsyncGenerator[str, None]:
        def fmt(msg: str, kind: str = "log") -> str:
            return f"data: {json.dumps({'kind': kind, 'msg': msg, 'ts': time.time()})}\n\n"

        yield fmt("Pipeline starting…", "start")
        yield fmt(f"Corpus: {corpus}", "info")
        yield fmt(f"Database: {db_path}", "info")

        if not pathlib.Path(corpus).exists():
            yield fmt(f"ERROR: corpus directory not found: {corpus}", "error")
            yield fmt("DONE", "done")
            return

        cmd = [
            sys.executable, str(PROJECT_ROOT / "pipeline" / "batch.py"),
            "--corpus", corpus,
            "--db", db_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
            )
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    yield fmt(line, "log")
            await proc.wait()
            if proc.returncode == 0:
                yield fmt("Pipeline completed successfully.", "success")
            else:
                yield fmt(f"Pipeline exited with code {proc.returncode}", "error")
        except Exception as exc:
            yield fmt(f"ERROR: {exc}", "error")

        yield fmt("DONE", "done")

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Utility"])
def health():
    db_ready = _db_exists()
    return {"status": "ok", "db": str(DEFAULT_DB), "db_ready": db_ready}


# ── Serve UI (must be last) ───────────────────────────────────────────────────

if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
