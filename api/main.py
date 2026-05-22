"""FastAPI REST API — all endpoints for the COBOL Pipeline + UI."""

from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import AsyncGenerator

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
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


def _table_exists(con, table_name: str) -> bool:
    row = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return row[0] > 0


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
    _zero = {
        "programs": 0, "paragraphs": 0, "data_items": 0, "conditions_88": 0,
        "statements": 0, "business_rules": 0, "call_edges": 0, "cfg_edges": 0,
        "file_io_ops": 0, "risks": 0, "coverage_pct": 0, "ok_files": 0, "total_files": 0,
        "cobol_files": 0, "jcl_files": 0, "bms_files": 0, "csd_files": 0,
        "copybook_files": 0, "asm_files": 0, "db2_statements": 0, "ims_calls": 0,
        "mq_calls": 0, "cics_verbs": 0, "copybook_refs": 0,
    }
    if not _db_exists():
        return _zero
    with _con() as con:
        def cnt(sql, *args):
            return con.execute(sql, args).fetchone()[0]

        programs    = cnt("SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'")
        paragraphs  = cnt("SELECT COUNT(*) FROM nodes WHERE kind='Paragraph'")
        data_items  = cnt("SELECT COUNT(*) FROM data_items")
        cond_88     = cnt("SELECT COUNT(*) FROM conditions_88")
        statements  = cnt("SELECT COUNT(*) FROM nodes WHERE kind LIKE 'Stmt_%'")
        biz_rules   = cnt("SELECT COUNT(*) FROM business_rules")
        call_edges  = cnt("SELECT COUNT(*) FROM call_graph")
        cfg_edges   = cnt("SELECT COUNT(*) FROM control_flow")
        file_ops    = cnt("SELECT COUNT(*) FROM file_io")
        risks       = cnt("SELECT COUNT(*) FROM risk_register")
        # Deduplicate by basename — pipeline phases write absolute and relative
        # paths for the same file, so count distinct lowercased basenames.
        _cov_rows = con.execute("SELECT source_file, status FROM parse_coverage").fetchall()
        import os as _os
        _seen_base: dict[str, str] = {}
        for _r in _cov_rows:
            _base = _os.path.basename(_r[0]).lower()
            if _base not in _seen_base:
                _seen_base[_base] = _r[1]
        total_files = len(_seen_base)
        ok_files    = sum(1 for s in _seen_base.values() if s == 'OK')
        cov_pct     = round(100 * ok_files / max(total_files, 1), 1)
        copybook_refs = cnt("SELECT COUNT(DISTINCT program_uuid || copybook_name) FROM copybook_use")
        cics_verbs  = cnt("SELECT COUNT(*) FROM transaction_flow")
        # DB2/IMS/MQ: count from db_io and nodes payload patterns
        db2_stmts   = cnt("SELECT COUNT(*) FROM db_io") if _table_exists(con, "db_io") else 0
        ims_calls   = cnt("SELECT COUNT(*) FROM nodes WHERE kind='Stmt_EXEC_IMS'") if False else 0
        mq_calls    = cnt("SELECT COUNT(*) FROM nodes WHERE kind='Stmt_EXEC_MQ'") if False else 0
        # Per file-type counts — deduplicated by basename
        _by_type: dict[str, set] = {}
        for _r in _cov_rows:
            _base = _os.path.basename(_r[0]).lower()
            _ext  = _os.path.splitext(_base)[1]
            _by_type.setdefault(_ext, set()).add(_base)
        cobol_files = len(_by_type.get('.cbl', set()))
        jcl_files   = len(_by_type.get('.jcl', set()))
        bms_files   = len(_by_type.get('.bms', set()))
        csd_files   = len(_by_type.get('.csd', set()))
        cpy_files   = len(_by_type.get('.cpy', set()))
        asm_files   = len(_by_type.get('.asm', set()) | _by_type.get('.hlasm', set()) | _by_type.get('.s', set()))

    return {
        "programs": programs, "paragraphs": paragraphs,
        "data_items": data_items, "conditions_88": cond_88,
        "statements": statements, "business_rules": biz_rules,
        "call_edges": call_edges, "cfg_edges": cfg_edges,
        "file_io_ops": file_ops, "risks": risks,
        "coverage_pct": cov_pct, "ok_files": ok_files, "total_files": total_files,
        "cobol_files": cobol_files, "jcl_files": jcl_files,
        "bms_files": bms_files, "csd_files": csd_files,
        "copybook_files": cpy_files, "asm_files": asm_files,
        "db2_statements": db2_stmts, "ims_calls": ims_calls, "mq_calls": mq_calls,
        "cics_verbs": cics_verbs, "copybook_refs": copybook_refs,
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


# ── Multi-persona spec generation ─────────────────────────────────────────────

_PERSONA_LABELS = {
    "business_summary":   "Business Summary",
    "highlevel_arch":     "High-Level Architecture",
    "lowlevel_arch":      "Low-Level Architecture",
    "functional_spec":    "Functional Specification",
    "technical_spec":     "Technical Specification",
    "modernization_spec": "Modernisation Specification",
}

@app.post("/generate-spec/personas", tags=["LLM"])
async def generate_spec_personas(body: dict):
    """Run multiple spec personas in parallel and stream results."""
    program_name = body.get("program_name", "")
    scope = body.get("scope", "program")
    uuid_ = body.get("uuid", "")
    personas: list[str] = body.get("personas", list(_PERSONA_LABELS.keys()))
    model = body.get("model")

    if not program_name and not uuid_:
        raise HTTPException(400, "program_name or uuid required")

    if model:
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        if provider == "openai":
            os.environ["OPENAI_MODEL"] = model
        elif provider == "gemini":
            os.environ["GEMINI_MODEL"] = model

    async def _stream():
        import concurrent.futures
        from llm.multi_agent import generate_persona_spec

        yield f"data: {json.dumps({'event': 'start', 'personas': personas, 'total': len(personas)})}\n\n"
        await asyncio.sleep(0)

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(personas), 4)) as pool:
            futures = {
                loop.run_in_executor(pool, generate_persona_spec, persona, program_name, scope, uuid_): persona
                for persona in personas
            }
            for fut in asyncio.as_completed(futures):
                persona = futures[fut]
                try:
                    result = await fut
                    yield f"data: {json.dumps({'event': 'persona_done', 'persona': persona, 'label': _PERSONA_LABELS.get(persona, persona), 'content': result['content'], 'grounding_score': result.get('grounding_score', 0)})}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'event': 'persona_error', 'persona': persona, 'error': str(exc)})}\n\n"
                await asyncio.sleep(0)

        yield f"data: {json.dumps({'event': 'all_done'})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/specs/export/pdf", tags=["LLM"])
def export_spec_pdf(body: dict):
    """Render markdown spec content to a properly styled PDF via WeasyPrint."""
    content = body.get("content", "")
    title   = body.get("title", "COBOL Modernisation Specification")
    if not content:
        raise HTTPException(400, "content required")
    try:
        import markdown as md_lib
        from weasyprint import HTML as WP_HTML, CSS
        html_body = md_lib.markdown(content, extensions=["tables", "fenced_code", "toc"])
        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ margin: 2cm; @top-center {{ content: "{title}"; font-size: 9pt; color: #666; }} @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 9pt; color: #666; }} }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 10pt; line-height: 1.6; color: #1a1a2e; }}
  h1 {{ font-size: 20pt; color: #006e74; border-bottom: 2px solid #006e74; padding-bottom: 6pt; margin-top: 18pt; }}
  h2 {{ font-size: 14pt; color: #0097ab; border-bottom: 1px solid #e0e0e0; padding-bottom: 3pt; margin-top: 14pt; }}
  h3 {{ font-size: 11pt; color: #003c51; margin-top: 10pt; }}
  code {{ background: #f4f4f4; padding: 1pt 4pt; border-radius: 3pt; font-family: 'Courier New', monospace; font-size: 9pt; }}
  pre {{ background: #f4f4f4; padding: 10pt; border-radius: 5pt; border-left: 3pt solid #006e74; overflow: hidden; white-space: pre-wrap; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10pt 0; font-size: 9pt; }}
  th {{ background: #006e74; color: white; padding: 5pt 8pt; text-align: left; }}
  td {{ border: 1pt solid #ddd; padding: 4pt 8pt; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  blockquote {{ border-left: 4pt solid #0097ab; padding-left: 12pt; color: #555; margin: 8pt 0; }}
  .toc {{ background: #f0f8ff; padding: 12pt; border: 1pt solid #cce; border-radius: 5pt; margin: 12pt 0; }}
</style>
</head><body>
<h1 style="font-size:22pt;text-align:center;border:none;color:#003c51;">{title}</h1>
<p style="text-align:center;color:#666;font-size:9pt;">Generated by UST CodeCrafter COBOL Modernisation Pipeline</p>
<hr style="border:1pt solid #006e74;margin:12pt 0;">
{html_body}
</body></html>"""
        from io import BytesIO
        buf = BytesIO()
        WP_HTML(string=full_html).write_pdf(buf)
        buf.seek(0)
        from fastapi.responses import Response
        return Response(
            content=buf.read(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{title.replace(" ", "_")}.pdf"'},
        )
    except ImportError as e:
        raise HTTPException(501, f"PDF export requires weasyprint and markdown: pip install weasyprint markdown — {e}")
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Multi-agent Forward Engineering (HITL Transform) ─────────────────────────

_transform_sessions: dict[str, dict] = {}

TRANSFORM_STEPS = [
    {"id": 0, "name": "Discovery",     "agent": "DiscoveryAgent",     "description": "Analyse all parsed artifacts for the program"},
    {"id": 1, "name": "Specification", "agent": "SpecAgent",          "description": "Generate functional & technical specifications"},
    {"id": 2, "name": "Architecture",  "agent": "ArchitectAgent",     "description": "Design target Java package and class structure"},
    {"id": 3, "name": "Domain Model",  "agent": "DomainAgent",        "description": "Map COBOL data items to Java entities and DTOs"},
    {"id": 4, "name": "Business Logic","agent": "BusinessLogicAgent", "description": "Transform business rules to Java service methods"},
    {"id": 5, "name": "Integration",   "agent": "IntegrationAgent",   "description": "Map file I/O and CICS to repositories and REST clients"},
    {"id": 6, "name": "Tests",         "agent": "TestAgent",          "description": "Generate JUnit 5 test cases from business rules"},
]


@app.post("/transform/sessions", tags=["Transform"])
def create_transform_session(body: dict):
    """Create a new HITL transformation session."""
    import uuid as _uuid
    if not _db_exists():
        raise HTTPException(400, "No pipeline database — run the pipeline first.")
    program_name = body.get("program_name", "")
    framework    = body.get("framework", "Spring Boot")
    auto_mode    = body.get("auto_mode", False)
    if not program_name:
        raise HTTPException(400, "program_name required")
    session_id = str(_uuid.uuid4())[:8]
    _transform_sessions[session_id] = {
        "session_id": session_id,
        "program_name": program_name,
        "framework": framework,
        "auto_mode": auto_mode,
        "status": "pending",
        "current_step": 0,
        "steps": [
            {**s, "status": "pending", "output": None, "rationale": None, "approved": False, "feedback": None}
            for s in TRANSFORM_STEPS
        ],
        "created_at": time.time(),
    }
    return {"session_id": session_id, "program_name": program_name, "framework": framework, "steps": TRANSFORM_STEPS}


@app.get("/transform/sessions/{session_id}", tags=["Transform"])
def get_transform_session(session_id: str):
    if session_id not in _transform_sessions:
        raise HTTPException(404, "Session not found")
    return _transform_sessions[session_id]


@app.post("/transform/sessions/{session_id}/steps/{step_id}/run", tags=["Transform"])
def run_transform_step(session_id: str, step_id: int):
    """Run a single transformation step using the LLM agent."""
    if session_id not in _transform_sessions:
        raise HTTPException(404, "Session not found")
    session = _transform_sessions[session_id]
    if step_id < 0 or step_id >= len(TRANSFORM_STEPS):
        raise HTTPException(400, f"step_id must be 0-{len(TRANSFORM_STEPS)-1}")
    step = session["steps"][step_id]
    if step["approved"]:
        return {"ok": True, "already_approved": True, "output": step["output"]}

    session["status"] = "running"
    step["status"] = "running"
    try:
        from llm.multi_agent import run_transform_step as _run_step
        with _con() as con:
            result = _run_step(
                step_id=step_id,
                program_name=session["program_name"],
                framework=session["framework"],
                previous_steps=session["steps"][:step_id],
                con=con,
            )
        step["output"]    = result["output"]
        step["rationale"] = result.get("rationale", "")
        step["status"]    = "awaiting_approval"
        session["status"] = "awaiting_approval"
        session["current_step"] = step_id
        if session["auto_mode"]:
            step["approved"] = True
            step["status"]   = "approved"
            session["current_step"] = step_id + 1
            if step_id == len(TRANSFORM_STEPS) - 1:
                session["status"] = "complete"
        return {"ok": True, "step_id": step_id, "output": step["output"], "rationale": step["rationale"], "auto_approved": session["auto_mode"]}
    except Exception as exc:
        step["status"]    = "error"
        session["status"] = "error"
        raise HTTPException(500, str(exc))


@app.post("/transform/sessions/{session_id}/steps/{step_id}/approve", tags=["Transform"])
def approve_transform_step(session_id: str, step_id: int, body: dict = {}):
    if session_id not in _transform_sessions:
        raise HTTPException(404, "Session not found")
    session = _transform_sessions[session_id]
    step = session["steps"][step_id]
    step["approved"] = True
    step["status"]   = "approved"
    step["feedback"] = body.get("feedback", "")
    next_step = step_id + 1
    if next_step < len(TRANSFORM_STEPS):
        session["current_step"] = next_step
        session["status"] = "pending"
    else:
        session["status"] = "complete"
    return {"ok": True, "next_step": next_step if next_step < len(TRANSFORM_STEPS) else None}


@app.post("/transform/sessions/{session_id}/steps/{step_id}/reject", tags=["Transform"])
def reject_transform_step(session_id: str, step_id: int, body: dict = {}):
    if session_id not in _transform_sessions:
        raise HTTPException(404, "Session not found")
    session = _transform_sessions[session_id]
    step = session["steps"][step_id]
    step["approved"] = False
    step["status"]   = "rejected"
    step["feedback"] = body.get("feedback", "")
    session["status"] = "pending"
    return {"ok": True, "message": "Step rejected — you may re-run it after updating feedback"}


@app.get("/transform/sessions", tags=["Transform"])
def list_transform_sessions():
    return {"sessions": [{"session_id": s["session_id"], "program_name": s["program_name"], "framework": s["framework"], "status": s["status"]} for s in _transform_sessions.values()]}


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


@app.post("/pipeline/clone-github", tags=["Pipeline"])
async def clone_github_repo(body: dict = {}):
    """Clone a GitHub repository and auto-detect COBOL/JCL/BMS/CSD paths."""
    repo_url: str = body.get("url", "").strip()
    if not repo_url:
        raise HTTPException(status_code=400, detail="url is required")

    # Derive a short name from the URL
    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = PROJECT_ROOT / "external" / repo_name

    async def stream() -> AsyncGenerator[str, None]:
        def fmt(msg: str, kind: str = "log") -> str:
            return f"data: {json.dumps({'kind': kind, 'msg': msg, 'ts': time.time()})}\n\n"

        yield fmt(f"Cloning {repo_url} → external/{repo_name} …", "start")

        if dest.exists():
            yield fmt(f"Destination exists — pulling latest…", "info")
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(dest), "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth=1", repo_url, str(dest),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                yield fmt(line, "log")
        await proc.wait()
        if proc.returncode != 0:
            yield fmt(f"git exited with code {proc.returncode}", "error")
            yield fmt("DONE", "done")
            return

        # Auto-detect COBOL corpus paths
        corpus = _find_first_dir(dest, [
            "app/cbl", "src/cbl", "cobol", "cbl", "src/main/cobol",
        ])
        copybooks = _find_first_dir(dest, [
            "app/cpy", "src/cpy", "copybooks", "cpy",
        ])
        result = {
            "repo": str(dest),
            "corpus": str(corpus) if corpus else "",
            "copybooks": str(copybooks) if copybooks else "",
        }
        yield fmt(f"Detected corpus: {result['corpus'] or '(none found)'}", "info")
        yield fmt(f"Detected copybooks: {result['copybooks'] or '(none found)'}", "info")
        yield fmt(json.dumps(result), "result")
        yield fmt("DONE", "done")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/pipeline/upload-zip", tags=["Pipeline"])
async def upload_zip(file: UploadFile = File(...)):
    """Accept a ZIP archive, extract it to external/, auto-detect COBOL paths."""
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")

    repo_name = pathlib.Path(file.filename).stem
    dest = PROJECT_ROOT / "external" / repo_name

    content = await file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # Determine common prefix to strip
            names = zf.namelist()
            prefix = _common_prefix(names)
            dest.mkdir(parents=True, exist_ok=True)
            for member in zf.infolist():
                rel = member.filename[len(prefix):]
                if not rel:
                    continue
                out_path = dest / rel
                if member.is_dir():
                    out_path.mkdir(parents=True, exist_ok=True)
                else:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(zf.read(member.filename))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Bad ZIP file: {exc}")

    corpus = _find_first_dir(dest, ["app/cbl", "src/cbl", "cobol", "cbl"])
    copybooks = _find_first_dir(dest, ["app/cpy", "src/cpy", "copybooks", "cpy"])
    return {
        "ok": True,
        "repo": str(dest),
        "corpus": str(corpus) if corpus else "",
        "copybooks": str(copybooks) if copybooks else "",
        "files_extracted": len([n for n in zf.namelist() if not n.endswith("/")]) if False else None,
    }


def _find_first_dir(base: pathlib.Path, candidates: list[str]) -> pathlib.Path | None:
    for c in candidates:
        p = base / c
        if p.is_dir():
            return p
    return None


def _common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    parts = names[0].split("/")
    prefix = parts[0] + "/" if len(parts) > 1 else ""
    for n in names[1:]:
        if not n.startswith(prefix):
            return ""
    return prefix


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
    """Layer 6: Browse BMS screen maps and fields (deduplicated)."""
    if not _db_exists():
        return []
    with _con() as con:
        rows = con.execute(
            """SELECT DISTINCT map_name, mapset_name, field_name, position_row, position_col,
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
            """SELECT id, kind AS resource_type, name,
                      group_name, attributes
               FROM csd_catalog ORDER BY kind, name LIMIT ?""",
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


# ── Platform Recommender ─────────────────────────────────────────────────────

_HYPERSCALER_SERVICES = {
    "aws": {
        "compute": "AWS Lambda / ECS Fargate / EKS",
        "database": "Amazon RDS (Aurora) / DynamoDB / Redshift",
        "messaging": "Amazon SQS / SNS / EventBridge / MSK (Kafka)",
        "storage": "Amazon S3 / EFS / EBS",
        "cics_replacement": "AWS Step Functions + API Gateway",
        "batch": "AWS Batch / Step Functions",
        "monitoring": "CloudWatch / X-Ray / AWS Config",
        "ci_cd": "AWS CodePipeline / CodeBuild / CodeDeploy",
        "migration_service": "AWS Mainframe Modernization (M2)",
    },
    "azure": {
        "compute": "Azure Functions / AKS / Container Apps",
        "database": "Azure SQL / Cosmos DB / Synapse Analytics",
        "messaging": "Azure Service Bus / Event Hubs / Event Grid",
        "storage": "Azure Blob Storage / Data Lake",
        "cics_replacement": "Azure Logic Apps + APIM",
        "batch": "Azure Batch / Durable Functions",
        "monitoring": "Azure Monitor / Application Insights",
        "ci_cd": "Azure DevOps / GitHub Actions",
        "migration_service": "Azure Migrate + App Service Migration Assistant",
    },
    "gcp": {
        "compute": "Cloud Run / GKE / Cloud Functions",
        "database": "Cloud SQL / Firestore / BigQuery / Spanner",
        "messaging": "Pub/Sub / Eventarc / Dataflow",
        "storage": "Cloud Storage / Filestore",
        "cics_replacement": "Cloud Endpoints + Workflows",
        "batch": "Cloud Batch / Dataflow",
        "monitoring": "Cloud Monitoring / Cloud Trace / Cloud Logging",
        "ci_cd": "Cloud Build / Cloud Deploy / Artifact Registry",
        "migration_service": "Google Cloud Mainframe Modernization API",
    },
    "on-prem": {
        "compute": "OpenShift / Kubernetes / VMware Tanzu",
        "database": "PostgreSQL / MariaDB / Oracle on-prem",
        "messaging": "Apache Kafka / ActiveMQ / RabbitMQ",
        "storage": "NetApp / Dell EMC / Ceph",
        "cics_replacement": "IBM WebSphere / JBoss / Quarkus REST",
        "batch": "Spring Batch / Quartz Scheduler",
        "monitoring": "Prometheus / Grafana / ELK Stack",
        "ci_cd": "Jenkins / GitLab CI / Nexus",
        "migration_service": "Micro Focus Enterprise / Broadcom CA7",
    },
}


@app.post("/platform/recommend", tags=["Platform Recommender"])
async def platform_recommend(body: dict = {}):
    """Stream a cloud architecture recommendation grounded in COBOL artifacts."""
    hyperscaler: str = body.get("hyperscaler", "aws")
    program_name: str = body.get("program", "")
    runtime: str = body.get("runtime", "microservices")
    data_strategy: str = body.get("data_strategy", "managed-sql")
    priority: str = body.get("priority", "speed")
    scope: str = body.get("scope", "portfolio")

    services = _HYPERSCALER_SERVICES.get(hyperscaler, _HYPERSCALER_SERVICES["aws"])

    async def stream() -> AsyncGenerator[str, None]:
        def fmt(msg: str, kind: str = "chunk") -> str:
            return f"data: {json.dumps({'kind': kind, 'msg': msg})}\n\n"

        if not _db_exists():
            yield fmt("Pipeline has not been run yet. Run the pipeline first.", "error")
            yield fmt("", "done")
            return

        # Gather artifact context
        with _con() as con:
            prog_count = con.execute(
                "SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'"
            ).fetchone()[0]
            data_items = con.execute("SELECT COUNT(*) FROM data_items").fetchone()[0]
            business_rules = con.execute("SELECT COUNT(*) FROM business_rules").fetchone()[0]
            call_edges = con.execute("SELECT COUNT(*) FROM call_graph").fetchone()[0]
            cfg_edges = con.execute("SELECT COUNT(*) FROM control_flow").fetchone()[0]
            risks_high = con.execute(
                "SELECT COUNT(*) FROM risk_register WHERE severity='HIGH'"
            ).fetchone()[0]
            risks_med = con.execute(
                "SELECT COUNT(*) FROM risk_register WHERE severity='MEDIUM'"
            ).fetchone()[0]
            cics_verbs = con.execute("SELECT COUNT(*) FROM transaction_flow").fetchone()[0]
            jcl_jobs = con.execute(
                "SELECT COUNT(DISTINCT job_name) FROM jcl_job"
            ).fetchone()[0]
            file_io = con.execute("SELECT COUNT(DISTINCT file_name) FROM file_io").fetchone()[0]
            top_risks = con.execute(
                """SELECT DISTINCT kind, COUNT(*) AS cnt FROM risk_register
                   GROUP BY kind ORDER BY cnt DESC LIMIT 5"""
            ).fetchall()

            # Program-specific context if requested
            prog_detail = ""
            if program_name and scope == "program":
                prog_uuid = _get_prog_uuid(con, program_name)
                if prog_uuid:
                    paragraphs = con.execute(
                        "SELECT COUNT(*) FROM nodes WHERE kind='Paragraph' AND parent_uuid=?",
                        (prog_uuid,),
                    ).fetchone()[0]
                    prog_rules = con.execute(
                        "SELECT COUNT(*) FROM business_rules WHERE program_uuid=?",
                        (prog_uuid,),
                    ).fetchone()[0]
                    prog_risks = con.execute(
                        "SELECT kind, severity FROM risk_register WHERE program_uuid=? ORDER BY severity LIMIT 5",
                        (prog_uuid,),
                    ).fetchall()
                    prog_detail = (
                        f"\n\nProgram '{program_name}': {paragraphs} paragraphs, "
                        f"{prog_rules} business rules, "
                        f"{len(prog_risks)} risks: {', '.join(f'{r[0]}({r[1]})' for r in prog_risks)}"
                    )

        risk_summary = ", ".join(f"{r[0]}×{r[1]}" for r in top_risks) if top_risks else "none detected"

        from llm.multi_agent import _call_llm

        prompt = f"""You are a cloud architecture expert specialising in mainframe modernisation.

COBOL PORTFOLIO ANALYSIS (from 7-layer artifact pipeline):
- Programs: {prog_count} | Data items: {data_items:,} | Paragraphs: via CFG ({cfg_edges:,} edges)
- Business rules: {business_rules} | Call graph edges: {call_edges}
- CICS transaction verbs: {cics_verbs} | JCL job definitions: {jcl_jobs}
- Logical files (VSAM/flat): {file_io}
- Migration risks: HIGH={risks_high}, MEDIUM={risks_med} — top kinds: {risk_summary}{prog_detail}

USER PREFERENCES:
- Target hyperscaler: {hyperscaler.upper()}
- Target runtime: {runtime}
- Data strategy: {data_strategy}
- Migration priority: {priority}
- Scope: {scope}

AVAILABLE {hyperscaler.upper()} SERVICES:
{json.dumps(services, indent=2)}

Generate a structured Target Platform Architecture Recommendation with these sections:

## 1. Executive Summary
One paragraph — business case for this modernisation approach given the portfolio complexity.

## 2. Recommended Architecture Pattern
Name the pattern (e.g. "Strangler Fig to Microservices", "Lift-Rehost to PaaS", "Event-Driven Decomposition"). Explain why given the artifact data above.

## 3. Target Platform Components
For each layer (Compute, Database, Messaging, Storage, CI/CD, Monitoring), name the specific {hyperscaler.upper()} service and justify it based on the COBOL portfolio characteristics.

## 4. COBOL → Cloud Mapping
Map the mainframe constructs to cloud equivalents:
- CICS transactions → {services['cics_replacement']}
- VSAM files → (data strategy: {data_strategy})
- JCL batch jobs → {services['batch']}
- COBOL programs → (runtime: {runtime})
- Copybooks → shared library / API contracts

## 5. Migration Roadmap (3 Phases)
Phase 1 (0-3 months): What to migrate first given HIGH risk = {risks_high}
Phase 2 (3-9 months): Core business logic decomposition
Phase 3 (9-18 months): Decommission mainframe components

## 6. Risk Mitigation
Address the top detected migration risks: {risk_summary}

## 7. Estimated Effort
T-shirt sizing per phase based on: {prog_count} programs, {business_rules} business rules, {cfg_edges:,} CFG edges.

Be specific, actionable, and grounded in the artifact data provided. Do not give generic advice."""

        try:
            result = _call_llm(prompt)
            yield fmt(result, "result")
        except Exception as exc:
            yield fmt(
                f"LLM not configured ({exc}). Configure an API key in Settings.\n\n"
                + _generate_static_recommendation(hyperscaler, services, prog_count,
                                                   business_rules, risks_high, risks_med,
                                                   cics_verbs, jcl_jobs, runtime, data_strategy),
                "result"
            )
        yield fmt("", "done")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _generate_static_recommendation(
    hyperscaler: str, services: dict, prog_count: int, rules: int,
    high: int, med: int, cics: int, jcl: int, runtime: str, data: str
) -> str:
    """Fallback static recommendation when LLM is unavailable."""
    hs = hyperscaler.upper()
    return f"""## Target Platform Recommendation — {hs}

**Executive Summary**
Your portfolio of {prog_count} COBOL programs with {rules} business rules, {cics} CICS transaction verbs and {jcl} JCL batch jobs represents a significant modernisation opportunity. With {high} HIGH and {med} MEDIUM migration risks, a phased strangler-fig approach targeting {hs} is recommended.

**Recommended Architecture Pattern:** Strangler Fig → Event-Driven Microservices

**Target Platform ({hs}):**
- Compute: {services['compute']}
- Database: {services['database']}
- Messaging: {services['messaging']}
- Storage: {services['storage']}
- CICS → {services['cics_replacement']}
- Batch: {services['batch']}
- Monitoring: {services['monitoring']}
- CI/CD: {services['ci_cd']}

**Phase 1 (0-3 months):** Rehost infrastructure, set up CI/CD, instrument observability
**Phase 2 (3-9 months):** Decompose CICS transactions into REST APIs; migrate VSAM to {services['database'].split('/')[0]}
**Phase 3 (9-18 months):** Refactor JCL batch to {services['batch']}; decommission mainframe

**Note:** Configure an LLM API key in Settings for a fully AI-grounded personalised recommendation."""


# ── Serve UI (must be last) ───────────────────────────────────────────────────
# Prefer the Vite-built dist/ output; fall back to raw ui/ for development.
_UI_DIST = UI_DIR / "dist"
_SERVE_DIR = _UI_DIST if _UI_DIST.exists() else UI_DIR

if _SERVE_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_SERVE_DIR), html=True), name="ui")
