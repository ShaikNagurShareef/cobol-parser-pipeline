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

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

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

# Active pipeline process (for cancel support)
_pipeline_proc: asyncio.subprocess.Process | None = None


def _con():
    return get_connection(DEFAULT_DB)


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _db_exists() -> bool:
    return DEFAULT_DB.exists() and DEFAULT_DB.stat().st_size > 0


def _get_prog_uuid(con, program_name: str) -> str | None:
    """Return the best program UUID for the given name.

    When duplicate nodes exist (absolute vs relative path from two pipeline
    phases), prefer the one that has the most associated graph data (CFG edges).
    """
    rows = con.execute(
        "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
        (program_name,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]["uuid"]
    # Multiple rows: prefer the one with the most graph data
    best_uuid = rows[0]["uuid"]
    best_score = -1
    for row in rows:
        uuid = row["uuid"]
        cfg_count = con.execute(
            "SELECT COUNT(*) FROM control_flow WHERE from_uuid IN "
            "(SELECT uuid FROM nodes WHERE parent_uuid=?)",
            (uuid,),
        ).fetchone()[0]
        if cfg_count > best_score:
            best_score = cfg_count
            best_uuid = uuid
    return best_uuid


# ── Settings & Model selection ─────────────────────────────────────────────────

@app.get("/settings", tags=["Settings"])
def get_settings():
    """Return current runtime configuration (no secret key values)."""
    return {
        "llm_provider":    os.environ.get("LLM_PROVIDER", "openai"),
        "openai_model":    os.environ.get("OPENAI_MODEL", "gpt-4o"),
        "gemini_model":    os.environ.get("GEMINI_MODEL", "gemini-1.5-pro"),
        "pipeline_workers": int(os.environ.get("PIPELINE_WORKERS", "4")),
        "openai_key_set":  bool(os.environ.get("OPENAI_API_KEY")),
        "gemini_key_set":  bool(os.environ.get("GEMINI_API_KEY")),
    }


@app.post("/settings", tags=["Settings"])
def update_settings(body: dict):
    """Update runtime model/provider selection (persists to .env file)."""
    env_path = PROJECT_ROOT / ".env"
    env_lines = env_path.read_text().splitlines() if env_path.exists() else []

    updates = {}
    if "llm_provider" in body:
        updates["LLM_PROVIDER"] = str(body["llm_provider"])
        os.environ["LLM_PROVIDER"] = updates["LLM_PROVIDER"]
    if "openai_model" in body:
        updates["OPENAI_MODEL"] = str(body["openai_model"])
        os.environ["OPENAI_MODEL"] = updates["OPENAI_MODEL"]
    if "gemini_model" in body:
        updates["GEMINI_MODEL"] = str(body["gemini_model"])
        os.environ["GEMINI_MODEL"] = updates["GEMINI_MODEL"]
    if "openai_api_key" in body and body["openai_api_key"]:
        updates["OPENAI_API_KEY"] = str(body["openai_api_key"])
        os.environ["OPENAI_API_KEY"] = updates["OPENAI_API_KEY"]
    if "gemini_api_key" in body and body["gemini_api_key"]:
        updates["GEMINI_API_KEY"] = str(body["gemini_api_key"])
        os.environ["GEMINI_API_KEY"] = updates["GEMINI_API_KEY"]

    # Patch the .env file
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in env_lines:
        stripped = line.strip()
        matched = False
        for key, val in updates.items():
            if stripped.startswith(key + "=") or stripped.startswith(f"# {key}="):
                new_lines.append(f"{key}={val}")
                updated_keys.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)
    # Append any keys not already in the file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines) + "\n")

    return {"ok": True, "updated": list(updates.keys())}


@app.get("/models", tags=["Settings"])
async def list_models():
    """Fetch available models from the configured LLM provider."""
    import httpx

    provider = os.environ.get("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return {"provider": "openai", "models": _default_openai_models(), "error": "OPENAI_API_KEY not set — showing defaults"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                r.raise_for_status()
                data = r.json()
                # Filter to chat-capable models, sorted by id
                models = sorted(
                    [m["id"] for m in data.get("data", [])
                     if any(tag in m["id"] for tag in ("gpt-4", "gpt-3.5", "o1", "o3", "o4"))],
                )
                return {"provider": "openai", "models": models or _default_openai_models()}
        except Exception as exc:
            return {"provider": "openai", "models": _default_openai_models(), "error": str(exc)}

    elif provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return {"provider": "gemini", "models": _default_gemini_models(), "error": "GEMINI_API_KEY not set — showing defaults"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                )
                r.raise_for_status()
                data = r.json()
                models = sorted([
                    m["name"].replace("models/", "")
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                    and "gemini" in m.get("name", "")
                ])
                return {"provider": "gemini", "models": models or _default_gemini_models()}
        except Exception as exc:
            return {"provider": "gemini", "models": _default_gemini_models(), "error": str(exc)}

    return {"provider": provider, "models": [], "error": f"Unknown provider: {provider}"}


def _default_openai_models() -> list[str]:
    return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o1", "o3-mini", "o4-mini"]


def _default_gemini_models() -> list[str]:
    return ["gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro"]


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
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        uuid = _get_prog_uuid(con, program_name)
        if not uuid:
            raise HTTPException(404, f"Program '{program_name}' not found")
        row = con.execute("SELECT * FROM nodes WHERE uuid=?", (uuid,)).fetchone()
    d = _row_to_dict(row)
    d["payload"] = json.loads(d.get("payload_json") or "{}")
    return d


@app.get("/programs/{program_name}/detail", tags=["Programs"])
def get_program_detail(program_name: str):
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        uuid = _get_prog_uuid(con, program_name)
        if not uuid:
            raise HTTPException(404, f"Program '{program_name}' not found")
        prog = con.execute("SELECT * FROM nodes WHERE uuid=?", (uuid,)).fetchone()

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


# ── AST / Visualization endpoints ─────────────────────────────────────────────

@app.get("/programs/{program_name}/ast", tags=["Visualization"])
def get_program_ast(program_name: str):
    """Return AST as a nested tree suitable for d3/tree rendering."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        prog = con.execute(
            "SELECT * FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (program_name,),
        ).fetchone()
        if not prog:
            raise HTTPException(404, f"Program '{program_name}' not found")
        prog_uuid = prog["uuid"]

        # Fetch all nodes in the program subtree
        all_nodes = _rows_to_list(con.execute(
            """
            WITH RECURSIVE subtree(uuid) AS (
                SELECT ? UNION ALL
                SELECT n.uuid FROM nodes n JOIN subtree s ON n.parent_uuid = s.uuid
            )
            SELECT n.uuid, n.kind, n.name, n.start_line, n.end_line, n.parent_uuid
            FROM nodes n JOIN subtree s ON n.uuid = s.uuid
            ORDER BY n.start_line
            """,
            (prog_uuid,),
        ).fetchall())

    # Build tree
    by_uuid = {n["uuid"]: {**n, "children": []} for n in all_nodes}
    root = None
    for node in all_nodes:
        if node["parent_uuid"] and node["parent_uuid"] in by_uuid:
            by_uuid[node["parent_uuid"]]["children"].append(by_uuid[node["uuid"]])
        elif node["uuid"] == prog_uuid:
            root = by_uuid[node["uuid"]]
    return {"root": root} if root else {"root": None}


@app.get("/programs/{program_name}/cfg", tags=["Visualization"])
def get_program_cfg(program_name: str):
    """Return control-flow graph as nodes + edges (Mermaid-compatible)."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        prog_uuid = _get_prog_uuid(con, program_name)
        if not prog_uuid:
            raise HTTPException(404, f"Program '{program_name}' not found")

        paras = _rows_to_list(con.execute(
            "SELECT uuid, name, start_line FROM nodes WHERE parent_uuid=? AND kind='Paragraph' ORDER BY start_line",
            (prog_uuid,),
        ).fetchall())
        para_uuids = [p["uuid"] for p in paras]
        para_map = {p["uuid"]: p["name"] or p["uuid"][:8] for p in paras}

        edges = []
        if para_uuids:
            ph = ",".join("?" * len(para_uuids))
            edges = _rows_to_list(con.execute(
                f"SELECT from_uuid, to_uuid, edge_type FROM control_flow WHERE from_uuid IN ({ph})",
                para_uuids,
            ).fetchall())

    def _safe_id(name: str) -> str:
        """Convert paragraph name to a valid Mermaid node ID."""
        import re as _re
        safe = _re.sub(r"[^A-Za-z0-9_]", "_", name or "UNKNOWN")
        if safe and safe[0].isdigit():
            safe = "P_" + safe
        return safe

    # Build Mermaid flowchart
    lines = ["flowchart TD"]
    for p in paras:
        safe_name = _safe_id(p["name"] or p["uuid"][:8])
        display = p["name"] or p["uuid"][:8]
        lines.append(f'  {safe_name}["{display} L{p["start_line"]}"]')
    for e in edges:
        frm = _safe_id(para_map.get(e["from_uuid"], e["from_uuid"][:8]))
        to = _safe_id(para_map.get(e["to_uuid"], e["to_uuid"][:8]))
        label = e["edge_type"] or ""
        lines.append(f'  {frm} -->|"{label}"| {to}')

    return {
        "nodes": paras,
        "edges": edges,
        "mermaid": "\n".join(lines),
        "para_count": len(paras),
        "edge_count": len(edges),
    }


@app.get("/programs/{program_name}/symbol-table", tags=["Visualization"])
def get_symbol_table(program_name: str, search: str = Query("", description="Filter by name")):
    """Return full symbol table / data dictionary for a program."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        prog_uuid = _get_prog_uuid(con, program_name)
        if not prog_uuid:
            raise HTTPException(404, f"Program '{program_name}' not found")

        like = f"%{search.upper()}%" if search else "%"
        items = _rows_to_list(con.execute(
            """
            SELECT d.uuid, d.name, d.level, d.pic, d.usage, d.sign,
                   d.occurs_min, d.occurs_max, d.redefines, d.value_raw,
                   d.canonical_kind, d.precision, d.scale, d.signed, d.length,
                   d.copybook_origin, d.start_line,
                   (SELECT COUNT(*) FROM def_use du WHERE du.data_item_uuid=d.uuid AND du.op='READ')  AS reads,
                   (SELECT COUNT(*) FROM def_use du WHERE du.data_item_uuid=d.uuid AND du.op='WRITE') AS writes
            FROM data_items d
            WHERE d.program_uuid=? AND UPPER(d.name) LIKE ?
            ORDER BY d.start_line, d.level
            """,
            (prog_uuid, like),
        ).fetchall())

        # Fetch 88-level conditions grouped by parent
        conditions = _rows_to_list(con.execute(
            """
            SELECT c.uuid, c.parent_uuid, c.name, c.value_raw
            FROM conditions_88 c
            JOIN data_items d ON d.uuid = c.parent_uuid
            WHERE d.program_uuid=?
            """,
            (prog_uuid,),
        ).fetchall())

    cond_by_parent: dict[str, list] = {}
    for c in conditions:
        cond_by_parent.setdefault(c["parent_uuid"], []).append(c)

    for item in items:
        item["conditions_88"] = cond_by_parent.get(item["uuid"], [])

    return {"program": program_name, "items": items, "total": len(items)}


@app.get("/programs/{program_name}/complexity", tags=["Visualization"])
def get_complexity(program_name: str):
    """Return complexity metrics for each paragraph."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        prog_uuid = _get_prog_uuid(con, program_name)
        if not prog_uuid:
            raise HTTPException(404, f"Program '{program_name}' not found")
        rows = _rows_to_list(con.execute(
            """
            SELECT cm.cyclomatic, cm.statement_count AS loc,
                   cm.nesting_depth, cm.fan_in, cm.fan_out,
                   n.name, n.start_line
            FROM complexity_metrics cm
            JOIN nodes n ON n.uuid = cm.para_uuid
            WHERE cm.program_uuid=?
            ORDER BY cm.cyclomatic DESC
            """,
            (prog_uuid,),
        ).fetchall())
    return {"program": program_name, "paragraphs": rows}


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


# ── Layer summary (pipeline explorer) ────────────────────────────────────────

@app.get("/layers/summary", tags=["Visualization"])
def get_layers_summary():
    """Return per-layer artifact counts for the Pipeline Layer Explorer UI."""
    if not _db_exists():
        return {}
    with _con() as con:
        def _int(q, *p):
            return con.execute(q, p).fetchone()[0] or 0

        # Layer 1
        programs    = _int("SELECT COUNT(DISTINCT name) FROM nodes WHERE kind='Program'")
        paragraphs  = _int("SELECT COUNT(*) FROM nodes WHERE kind='Paragraph'")
        statements  = _int("SELECT COUNT(*) FROM nodes WHERE kind LIKE 'Stmt_%'")

        # Layer 2
        data_items   = _int("SELECT COUNT(*) FROM data_items")
        cond_88      = _int("SELECT COUNT(*) FROM conditions_88")
        copybook_refs = _int("SELECT COUNT(*) FROM copybook_use")

        # Layer 3
        cfg_total    = _int("SELECT COUNT(*) FROM control_flow")
        cfg_branch   = _int("SELECT COUNT(*) FROM control_flow WHERE edge_type LIKE 'BRANCH_%'")
        cfg_perform  = _int("SELECT COUNT(*) FROM control_flow WHERE edge_type='PERFORM'")
        cfg_fallthru = _int("SELECT COUNT(*) FROM control_flow WHERE edge_type='FALLTHROUGH'")
        du_total     = _int("SELECT COUNT(*) FROM def_use")
        du_writes    = _int("SELECT COUNT(*) FROM def_use WHERE op='WRITE'")

        # Layer 4
        call_total    = _int("SELECT COUNT(*) FROM call_graph")
        call_resolved = _int("SELECT COUNT(*) FROM call_graph WHERE is_resolved=1")
        file_io_total = _int("SELECT COUNT(*) FROM file_io")
        tx_flow_total = _int("SELECT COUNT(*) FROM transaction_flow")
        jcl_bindings  = _int("SELECT COUNT(*) FROM jcl_program_binding") if \
            con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jcl_program_binding'").fetchone() else 0

        # Layer 5
        br_total     = _int("SELECT COUNT(*) FROM business_rules")
        br_if        = _int("SELECT COUNT(*) FROM business_rules WHERE kind='IF'")
        br_eval      = _int("SELECT COUNT(*) FROM business_rules WHERE kind='EVALUATE_WHEN'")
        arith_specs  = _int("SELECT COUNT(*) FROM arithmetic_specs")

        # Layer 6
        bms_maps  = _int("SELECT COUNT(DISTINCT map_name) FROM screen_map")
        csd_count = _int("SELECT COUNT(*) FROM csd_catalog")

        # Layer 7
        cov_rows  = con.execute("SELECT status FROM parse_coverage").fetchall()
        cov_total = len(cov_rows)
        cov_ok    = sum(1 for r in cov_rows if r["status"] == "OK")
        risk_high = _int("SELECT COUNT(*) FROM risk_register WHERE severity='HIGH'")
        risk_med  = _int("SELECT COUNT(*) FROM risk_register WHERE severity='MEDIUM'")
        risk_low  = _int("SELECT COUNT(*) FROM risk_register WHERE severity='LOW'")

    return {
        "layer1": {"programs": programs, "paragraphs": paragraphs, "statements": statements},
        "layer2": {"data_items": data_items, "conditions_88": cond_88, "copybook_refs": copybook_refs},
        "layer3": {
            "cfg_edges": cfg_total, "branch_edges": cfg_branch,
            "perform_edges": cfg_perform, "fallthru_edges": cfg_fallthru,
            "def_use_entries": du_total, "def_use_writes": du_writes,
        },
        "layer4": {
            "call_edges": call_total, "resolved": call_resolved,
            "resolved_pct": round(100 * call_resolved / max(call_total, 1)),
            "file_io": file_io_total, "tx_flow": tx_flow_total,
            "jcl_bindings": jcl_bindings,
        },
        "layer5": {"business_rules": br_total, "if_rules": br_if,
                   "evaluate_rules": br_eval, "arith_specs": arith_specs},
        "layer6": {"bms_maps": bms_maps, "csd_entries": csd_count},
        "layer7": {
            "coverage_pct": round(100 * cov_ok / max(cov_total, 1), 1),
            "ok_files": cov_ok, "total_files": cov_total,
            "risk_high": risk_high, "risk_medium": risk_med, "risk_low": risk_low,
        },
    }


# ── JCL program bindings ──────────────────────────────────────────────────────

@app.get("/jcl/bindings", tags=["JCL"])
def get_jcl_bindings(limit: int = Query(100)):
    """Return JCL DD → COBOL logical file bindings (G3)."""
    if not _db_exists():
        return []
    with _con() as con:
        tbl = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jcl_program_binding'"
        ).fetchone()
        if not tbl:
            return []
        rows = con.execute(
            "SELECT * FROM jcl_program_binding ORDER BY job_name, step_name LIMIT ?",
            (limit,),
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
    safe = name.replace("/", "").replace("..", "")
    paths = [
        PROJECT_ROOT / "output" / "diagrams" / f"{safe}.mmd",
        PROJECT_ROOT / "artifacts" / "diagrams" / f"{safe}.mmd",
    ]
    for p in paths:
        if p.exists():
            return {"name": safe, "content": p.read_text()}
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
    raise HTTPException(404, f"Diagram '{name}' not found.")


# ── LLM spec generation ───────────────────────────────────────────────────────

@app.post("/generate-spec", tags=["LLM"])
def generate_spec_endpoint(body: dict):
    uuid  = body.get("uuid", "")
    scope = body.get("scope", "paragraph")
    model = body.get("model")  # optional model override from UI
    if not uuid:
        raise HTTPException(400, "uuid required")
    # Forward model override to env so llm layer picks it up
    if model:
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        if provider == "openai":
            os.environ["OPENAI_MODEL"] = model
        elif provider == "gemini":
            os.environ["GEMINI_MODEL"] = model
    try:
        from llm.langgraph_agent import generate_spec_for
        spec = generate_spec_for(uuid, scope=scope)
        return {"spec": spec, "uuid": uuid, "scope": scope}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Modernization report (batch spec generation) ──────────────────────────────

@app.post("/generate-modernization-report", tags=["LLM"])
async def generate_modernization_report(body: dict = {}):
    """Generate one holistic application modernization report (SSE stream)."""
    if not _db_exists():
        raise HTTPException(400, "No pipeline database found — run the pipeline first.")

    use_llm = body.get("use_llm", False)

    async def _stream():
        import asyncio
        from llm.modernization_report import generate_holistic_report
        try:
            yield f"data: {json.dumps({'event': 'start', 'message': 'Building holistic modernization report…'})}\n\n"
            await asyncio.sleep(0)
            with _con() as con:
                result = generate_holistic_report(con, use_llm=use_llm)
            yield f"data: {json.dumps(result)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'event': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/specs", tags=["LLM"])
def list_specs():
    """List generated program spec files."""
    specs_dir = PROJECT_ROOT / "output" / "specs"
    if not specs_dir.exists():
        return {"specs": [], "combined_report": None}
    files = sorted(specs_dir.glob("*.md"))
    combined = specs_dir / "MODERNIZATION_REPORT.md"
    return {
        "specs": [{"name": f.stem, "size_kb": round(f.stat().st_size / 1024, 1)} for f in files
                  if f.name != "MODERNIZATION_REPORT.md"],
        "combined_report": str(combined) if combined.exists() else None,
        "combined_size_kb": round(combined.stat().st_size / 1024, 1) if combined.exists() else 0,
    }


@app.get("/specs/{program_name}", tags=["LLM"])
def get_spec(program_name: str):
    """Retrieve a generated program spec as Markdown text."""
    spec_file = PROJECT_ROOT / "output" / "specs" / f"{program_name}.md"
    if not spec_file.exists():
        raise HTTPException(404, f"Spec not found for {program_name}. Run the modernization report first.")
    return {"program": program_name, "markdown": spec_file.read_text(encoding="utf-8")}


# ── Java emit ─────────────────────────────────────────────────────────────────

@app.get("/emit-java/{program_name}", tags=["Java Emit"])
def emit_java_endpoint(program_name: str):
    ir_path    = PROJECT_ROOT / "output" / "ir" / f"{program_name.upper()}.ir.json"
    layer1_path = PROJECT_ROOT / "output" / "layer1" / f"{program_name.upper()}.json"
    try:
        if ir_path.exists():
            from ir.java_emitter import emit_java_from_file
            java_src = emit_java_from_file(ir_path)
        elif layer1_path.exists():
            import json as _json
            from ir.canonical_ir import lower_program
            from ir.java_emitter import emit_java
            layer1_data = _json.loads(layer1_path.read_text())
            # layer1 JSON wraps nodes under "nodes" key
            node_list = layer1_data.get("nodes", layer1_data) if isinstance(layer1_data, dict) else layer1_data
            ir = lower_program(node_list, program_name.upper())
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
    global _pipeline_proc
    corpus  = body.get("corpus", str(PROJECT_ROOT / "external/carddemo/app/cbl"))
    db_path = body.get("db",     str(DEFAULT_DB))

    async def event_stream() -> AsyncGenerator[str, None]:
        global _pipeline_proc

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
            _pipeline_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
            )
            async for raw in _pipeline_proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    yield fmt(line, "log")
            await _pipeline_proc.wait()
            if _pipeline_proc.returncode == 0:
                yield fmt("Pipeline completed successfully.", "success")
            elif _pipeline_proc.returncode == -15:
                yield fmt("Pipeline was cancelled.", "error")
            else:
                yield fmt(f"Pipeline exited with code {_pipeline_proc.returncode}", "error")
        except Exception as exc:
            yield fmt(f"ERROR: {exc}", "error")
        finally:
            _pipeline_proc = None

        yield fmt("DONE", "done")

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/pipeline/cancel", tags=["Pipeline"])
async def cancel_pipeline():
    """Terminate the running pipeline process."""
    global _pipeline_proc
    if _pipeline_proc and _pipeline_proc.returncode is None:
        _pipeline_proc.terminate()
        return {"ok": True, "msg": "Pipeline cancelled"}
    return {"ok": False, "msg": "No pipeline running"}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Utility"])
def health():
    db_ready = _db_exists()
    pipeline_running = _pipeline_proc is not None and _pipeline_proc.returncode is None
    return {
        "status": "ok",
        "db": str(DEFAULT_DB),
        "db_ready": db_ready,
        "pipeline_running": pipeline_running,
    }


# ── Source code viewer ────────────────────────────────────────────────────────

@app.get("/programs/{program_name}/source", tags=["Programs"])
def get_program_source(program_name: str):
    """Return the raw COBOL source code for a program."""
    if not _db_exists():
        raise HTTPException(503, "Pipeline not yet run")
    with _con() as con:
        row = con.execute(
            "SELECT source_file FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (program_name,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Program '{program_name}' not found")
    source_file = pathlib.Path(row["source_file"])
    if not source_file.exists():
        # Try relative to project root
        source_file = PROJECT_ROOT / row["source_file"]
    if not source_file.exists():
        raise HTTPException(404, f"Source file not found: {row['source_file']}")
    try:
        content = source_file.read_text(errors="replace")
        lines = content.splitlines()
        return {
            "program": program_name,
            "source_file": str(source_file),
            "content": content,
            "line_count": len(lines),
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Layer drill-down endpoints ────────────────────────────────────────────────

@app.get("/layers/1/programs", tags=["Layer Explorer"])
def layer1_programs(limit: int = Query(50)):
    """Layer 1: List programs with paragraph and statement counts."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            """SELECT n.name, n.source_file, n.start_line, n.end_line,
                      COUNT(DISTINCT p.uuid) AS para_count,
                      COUNT(DISTINCT s.uuid) AS stmt_count
               FROM nodes n
               LEFT JOIN nodes p ON p.parent_uuid=n.uuid AND p.kind='Paragraph'
               LEFT JOIN nodes s ON s.parent_uuid=p.uuid AND s.kind LIKE 'Stmt_%'
               WHERE n.kind='Program'
               GROUP BY n.uuid ORDER BY n.name LIMIT ?""",
            (limit,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/2/data-items", tags=["Layer Explorer"])
def layer2_data_items(limit: int = Query(100), program: str = Query("")):
    """Layer 2: Browse data items and 88-level conditions."""
    if not _db_exists():
        return []
    with _con() as con:
        if program:
            prog_uuid = _get_prog_uuid(con, program)
            if not prog_uuid:
                return []
            rows = con.execute(
                """SELECT d.name, d.level, d.pic, d.usage, d.canonical_kind,
                          d.precision, d.scale, n.name AS program_name
                   FROM data_items d JOIN nodes n ON n.uuid=d.program_uuid
                   WHERE d.program_uuid=? ORDER BY d.start_line LIMIT ?""",
                (prog_uuid, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT d.name, d.level, d.pic, d.usage, d.canonical_kind,
                          d.precision, d.scale, n.name AS program_name
                   FROM data_items d JOIN nodes n ON n.uuid=d.program_uuid
                   ORDER BY n.name, d.start_line LIMIT ?""",
                (limit,),
            ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/3/cfg-edges", tags=["Layer Explorer"])
def layer3_cfg_edges(limit: int = Query(100), program: str = Query("")):
    """Layer 3: Browse CFG edges with paragraph names."""
    if not _db_exists():
        return []
    with _con() as con:
        if program:
            prog_uuid = _get_prog_uuid(con, program)
            if not prog_uuid:
                return []
            para_uuids = [r["uuid"] for r in con.execute(
                "SELECT uuid FROM nodes WHERE parent_uuid=? AND kind='Paragraph'",
                (prog_uuid,)
            ).fetchall()]
            if not para_uuids:
                return []
            ph = ",".join("?" * len(para_uuids))
            rows = con.execute(
                f"""SELECT cf.edge_type, n1.name AS from_para, n2.name AS to_para,
                           prog.name AS program_name
                    FROM control_flow cf
                    JOIN nodes n1 ON n1.uuid=cf.from_uuid
                    JOIN nodes n2 ON n2.uuid=cf.to_uuid
                    JOIN nodes prog ON prog.uuid=n1.parent_uuid
                    WHERE cf.from_uuid IN ({ph})
                    ORDER BY n1.name, cf.edge_type LIMIT ?""",
                para_uuids + [limit],
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT cf.edge_type, n1.name AS from_para, n2.name AS to_para,
                          prog.name AS program_name
                   FROM control_flow cf
                   JOIN nodes n1 ON n1.uuid=cf.from_uuid
                   JOIN nodes n2 ON n2.uuid=cf.to_uuid
                   JOIN nodes prog ON prog.uuid=n1.parent_uuid
                   ORDER BY prog.name, n1.name LIMIT ?""",
                (limit,),
            ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/4/call-graph", tags=["Layer Explorer"])
def layer4_call_graph(limit: int = Query(100)):
    """Layer 4: Browse call graph edges."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            """SELECT cg.callee_name, cg.call_type, cg.is_resolved,
                      n1.name AS caller_name
               FROM call_graph cg JOIN nodes n1 ON n1.uuid=cg.caller_uuid
               ORDER BY n1.name, cg.callee_name LIMIT ?""",
            (limit,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/5/business-rules", tags=["Layer Explorer"])
def layer5_business_rules(limit: int = Query(100), program: str = Query("")):
    """Layer 5: Browse business rules."""
    if not _db_exists():
        return []
    with _con() as con:
        if program:
            prog_uuid = _get_prog_uuid(con, program)
            if not prog_uuid:
                return []
            rows = con.execute(
                """SELECT br.kind, br.predicate_raw, br.predicate_resolved,
                          br.then_summary, br.else_summary, br.line,
                          n.name AS program_name
                   FROM business_rules br JOIN nodes n ON n.uuid=br.program_uuid
                   WHERE br.program_uuid=? ORDER BY br.line LIMIT ?""",
                (prog_uuid, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT br.kind, br.predicate_raw, br.predicate_resolved,
                          br.then_summary, br.else_summary, br.line,
                          n.name AS program_name
                   FROM business_rules br JOIN nodes n ON n.uuid=br.program_uuid
                   ORDER BY n.name, br.line LIMIT ?""",
                (limit,),
            ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/6/bms-maps", tags=["Layer Explorer"])
def layer6_bms_maps(limit: int = Query(100)):
    """Layer 6: Browse BMS screen maps and fields."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            """SELECT map_name, mapset_name, field_name, position_row, position_col,
                      length, attributes
               FROM screen_map ORDER BY map_name, position_row, position_col LIMIT ?""",
            (limit,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/6/csd", tags=["Layer Explorer"])
def layer6_csd(limit: int = Query(100)):
    """Layer 6: Browse CSD catalog entries."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM csd_catalog ORDER BY resource_type, name LIMIT ?",
            (limit,),
        ).fetchall()
    return _rows_to_list(rows)


@app.get("/layers/7/risks", tags=["Layer Explorer"])
def layer7_risks(severity: str = Query(""), limit: int = Query(200)):
    """Layer 7: Browse risk register."""
    if not _db_exists():
        return []
    with _con() as con:
        if severity:
            rows = con.execute(
                """SELECT rr.*, n.name AS program_name FROM risk_register rr
                   LEFT JOIN nodes n ON n.uuid=rr.program_uuid
                   WHERE rr.severity=? ORDER BY rr.kind LIMIT ?""",
                (severity.upper(), limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT rr.*, n.name AS program_name FROM risk_register rr
                   LEFT JOIN nodes n ON n.uuid=rr.program_uuid
                   ORDER BY CASE rr.severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                   rr.kind LIMIT ?""",
                (limit,),
            ).fetchall()
    return _rows_to_list(rows)


@app.get("/jcl/jobs", tags=["JCL"])
def list_jcl_jobs(limit: int = Query(100)):
    """List all JCL jobs with step counts."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            """SELECT job_name, COUNT(DISTINCT step_name) AS step_count,
                      COUNT(DISTINCT program) AS program_count
               FROM jcl_job WHERE job_name IS NOT NULL
               GROUP BY job_name ORDER BY job_name LIMIT ?""",
            (limit,),
        ).fetchall()
    return _rows_to_list(rows)


# ── Serve UI (must be last) ───────────────────────────────────────────────────
# Prefer the Vite-built dist/ output; fall back to raw ui/ for development.
_UI_DIST = UI_DIR / "dist"
_SERVE_DIR = _UI_DIST if _UI_DIST.exists() else UI_DIR

if _SERVE_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_SERVE_DIR), html=True), name="ui")
