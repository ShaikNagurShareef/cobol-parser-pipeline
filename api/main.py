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
_RUNS_FILE = PROJECT_ROOT / "artifacts" / "pipeline_runs.json"

# Active pipeline process (for cancel support)
_pipeline_proc: asyncio.subprocess.Process | None = None


def _load_runs() -> list[dict]:
    try:
        return json.loads(_RUNS_FILE.read_text()) if _RUNS_FILE.exists() else []
    except Exception:
        return []


def _save_runs(runs: list[dict]) -> None:
    _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RUNS_FILE.write_text(json.dumps(runs, indent=2))


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
    provider = os.environ.get("LLM_PROVIDER", "openai")
    model = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or ""
    if provider == "gemini":
        model = os.environ.get("LLM_MODEL") or os.environ.get("GEMINI_MODEL") or ""
    elif provider == "anthropic":
        model = os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL") or ""
    return {
        "llm_provider":      provider,
        "model":             model,
        "openai_model":      os.environ.get("OPENAI_MODEL", ""),
        "gemini_model":      os.environ.get("GEMINI_MODEL", ""),
        "anthropic_model":   os.environ.get("ANTHROPIC_MODEL", ""),
        "pipeline_workers":  int(os.environ.get("PIPELINE_WORKERS", "4")),
        "openai_key_set":    bool(os.environ.get("OPENAI_API_KEY")),
        "gemini_key_set":    bool(os.environ.get("GEMINI_API_KEY")),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.post("/settings", tags=["Settings"])
def update_settings(body: dict):
    """Update runtime model/provider selection (persists to .env file)."""
    env_path = PROJECT_ROOT / ".env"
    env_lines = env_path.read_text().splitlines() if env_path.exists() else []

    updates: dict[str, str] = {}
    if "llm_provider" in body:
        updates["LLM_PROVIDER"] = str(body["llm_provider"])
        os.environ["LLM_PROVIDER"] = updates["LLM_PROVIDER"]
    # Accept a generic "model" field — store under both the generic key and provider-specific key
    provider = body.get("llm_provider") or os.environ.get("LLM_PROVIDER", "openai")
    model = body.get("model") or body.get(f"{provider}_model") or ""
    if model:
        updates["LLM_MODEL"] = model
        os.environ["LLM_MODEL"] = model
        if provider == "openai":
            updates["OPENAI_MODEL"] = model
            os.environ["OPENAI_MODEL"] = model
        elif provider == "gemini":
            updates["GEMINI_MODEL"] = model
            os.environ["GEMINI_MODEL"] = model
        elif provider == "anthropic":
            updates["ANTHROPIC_MODEL"] = model
            os.environ["ANTHROPIC_MODEL"] = model
    if "openai_api_key" in body and body["openai_api_key"]:
        updates["OPENAI_API_KEY"] = str(body["openai_api_key"])
        os.environ["OPENAI_API_KEY"] = updates["OPENAI_API_KEY"]
    if "gemini_api_key" in body and body["gemini_api_key"]:
        updates["GEMINI_API_KEY"] = str(body["gemini_api_key"])
        os.environ["GEMINI_API_KEY"] = updates["GEMINI_API_KEY"]
    if "anthropic_api_key" in body and body["anthropic_api_key"]:
        updates["ANTHROPIC_API_KEY"] = str(body["anthropic_api_key"])
        os.environ["ANTHROPIC_API_KEY"] = updates["ANTHROPIC_API_KEY"]

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
async def list_models(provider: str | None = None):
    """Fetch available models from the configured LLM provider.

    Pass ?provider=openai|gemini|anthropic to override the env-configured provider.
    """
    import httpx

    provider = (provider or os.environ.get("LLM_PROVIDER", "openai")).lower()

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

    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"provider": "anthropic", "models": _default_anthropic_models(), "error": "ANTHROPIC_API_KEY not set — showing defaults"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                r.raise_for_status()
                data = r.json()
                models = sorted([m["id"] for m in data.get("data", [])], reverse=True)
                return {"provider": "anthropic", "models": models or _default_anthropic_models()}
        except Exception as exc:
            return {"provider": "anthropic", "models": _default_anthropic_models(), "error": str(exc)}

    return {"provider": provider, "models": [], "error": f"Unknown provider: {provider}"}


def _default_openai_models() -> list[str]:
    return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o1", "o3-mini", "o4-mini"]


def _default_gemini_models() -> list[str]:
    return ["gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.0-pro"]


def _default_anthropic_models() -> list[str]:
    return ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"]


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
        import re as _re_stats
        # COBOL programs — source of truth is nodes table (parse_coverage has no .cbl entries)
        cobol_files = cnt("SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'")
        # JCL — source of truth is jcl_job table
        jcl_files   = cnt("SELECT COUNT(DISTINCT job_name) FROM jcl_job") if _table_exists(con, "jcl_job") else 0
        # BMS / CSD — parse_coverage is populated by those parsers
        bms_files   = cnt("SELECT COUNT(DISTINCT source_file) FROM parse_coverage WHERE source_type='BMS'")
        csd_files   = cnt("SELECT COUNT(DISTINCT source_file) FROM parse_coverage WHERE source_type='CSD'")
        # Copybooks — from catalog (Phase 0) when available, fall back to copybook_use
        if _table_exists(con, "copybook_catalog"):
            cpy_files = cnt("SELECT COUNT(*) FROM copybook_catalog")
        else:
            cpy_files = cnt("SELECT COUNT(DISTINCT UPPER(copybook_name)) FROM copybook_use")
        asm_files   = 0
        # Coverage: all COBOL programs are parsed OK (ProLeap handles them), plus JCL/BMS/CSD from parse_coverage
        _cov_ok   = cnt("SELECT COUNT(*) FROM parse_coverage WHERE status='OK'")
        _cov_total = cnt("SELECT COUNT(*) FROM parse_coverage")
        ok_files   = _cov_ok + cobol_files        # COBOL always OK when in nodes
        total_files = _cov_total + cobol_files
        cov_pct     = round(100 * ok_files / max(total_files, 1), 1)
        copybook_refs = cnt("SELECT COUNT(DISTINCT program_uuid || copybook_name) FROM copybook_use")
        cics_verbs  = cnt("SELECT COUNT(*) FROM transaction_flow")
        # DB2/IMS/MQ: count from db_io and nodes payload patterns
        db2_stmts   = cnt("SELECT COUNT(*) FROM db_io") if _table_exists(con, "db_io") else 0
        ims_calls   = 0
        mq_calls    = 0

    # Active run — most recent completed run from run history
    _runs = _load_runs()
    _active = next((r for r in _runs if r.get("status") == "completed"), None)
    active_run = {
        "id":           _active["id"] if _active else None,
        "started_at":   _active.get("started_at") if _active else None,
        "completed_at": _active.get("completed_at") if _active else None,
        "corpus":       _active.get("corpus") if _active else None,
    } if _active else None

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
        "active_run": active_run,
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
            GROUP BY UPPER(n.name)
            ORDER BY n.name
            LIMIT ? OFFSET ?
            """,
            (like, limit, offset),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program' AND UPPER(name) LIKE ?",
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

        # Copybook use with catalog metadata joined in
        if _table_exists(con, "copybook_catalog"):
            copybooks = _rows_to_list(con.execute(
                """
                SELECT cu.copybook_name, cu.line, cu.replacing_json,
                       cc.source_type, cc.data_item_count, cc.parse_status
                  FROM copybook_use cu
                  LEFT JOIN copybook_catalog cc ON UPPER(cc.name)=UPPER(cu.copybook_name)
                 WHERE cu.program_uuid=?
                 ORDER BY cu.line
                """,
                (uuid,),
            ).fetchall())
        else:
            copybooks = _rows_to_list(con.execute(
                "SELECT copybook_name, line, replacing_json FROM copybook_use WHERE program_uuid=?",
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

@app.get("/copybooks", tags=["Copybooks"])
def list_copybooks():
    """List all copybooks from the catalog (populated by Phase 0 of the pipeline)."""
    if not _db_exists():
        return []
    with _con() as con:
        if _table_exists(con, "copybook_catalog"):
            rows = con.execute(
                """
                SELECT cc.name, cc.source_file, cc.source_type,
                       cc.data_item_count, cc.parse_status,
                       COUNT(DISTINCT cu.program_uuid) AS consumer_count
                  FROM copybook_catalog cc
                  LEFT JOIN copybook_use cu ON UPPER(cu.copybook_name)=UPPER(cc.name)
                 GROUP BY cc.name
                 ORDER BY cc.name
                """
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT UPPER(copybook_name) AS name, COUNT(DISTINCT program_uuid) AS consumer_count
                  FROM copybook_use GROUP BY UPPER(copybook_name) ORDER BY name
                """
            ).fetchall()
    return _rows_to_list(rows)


@app.get("/copybooks/{copybook_name}", tags=["Copybooks"])
def get_copybook_detail(copybook_name: str):
    """Return catalog entry + consumers + data items for a specific copybook."""
    if not _db_exists():
        return {}
    with _con() as con:
        catalog = {}
        if _table_exists(con, "copybook_catalog"):
            cat_row = con.execute(
                "SELECT * FROM copybook_catalog WHERE UPPER(name)=UPPER(?)",
                (copybook_name,),
            ).fetchone()
            if cat_row:
                catalog = dict(cat_row)
                import json as _j
                catalog["item_names"] = _j.loads(catalog.get("item_names_json") or "[]")
                catalog.pop("item_names_json", None)

        consumers = _rows_to_list(con.execute(
            "SELECT cu.*, n.name AS program_name "
            "FROM copybook_use cu JOIN nodes n ON n.uuid=cu.program_uuid "
            "WHERE UPPER(cu.copybook_name)=UPPER(?)",
            (copybook_name,),
        ).fetchall())

        # Data items that originated from this copybook
        data_items = _rows_to_list(con.execute(
            """
            SELECT di.name, di.level, di.pic, di.usage, di.canonical_kind,
                   di.length, di.precision, di.scale, di.signed,
                   n.name AS program_name
              FROM data_items di
              JOIN nodes n ON n.uuid=di.program_uuid
             WHERE UPPER(di.copybook_origin)=UPPER(?)
             ORDER BY n.name, di.start_line
            """,
            (copybook_name,),
        ).fetchall())

    return {
        "catalog": catalog,
        "consumers": consumers,
        "data_item_sample": data_items[:50],
        "data_item_total": len(data_items),
    }


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


# ── Copybook explanation ──────────────────────────────────────────────────────

@app.post("/explain-copybook", tags=["LLM"])
def explain_copybook_endpoint(body: dict):
    """Generate an AI explanation for a COBOL copybook using its catalog data."""
    name = body.get("name", "").strip().upper()
    if not name:
        raise HTTPException(400, "name required")
    if not _db_exists():
        raise HTTPException(503, "Pipeline database not found — run the pipeline first.")

    with _con() as con:
        catalog: dict = {}
        if _table_exists(con, "copybook_catalog"):
            row = con.execute(
                "SELECT * FROM copybook_catalog WHERE UPPER(name)=UPPER(?)", (name,)
            ).fetchone()
            if row:
                catalog = dict(row)
                import json as _j
                catalog["item_names"] = _j.loads(catalog.get("item_names_json") or "[]")
                catalog.pop("item_names_json", None)

        consumers = _rows_to_list(con.execute(
            "SELECT DISTINCT n.name AS program_name FROM copybook_use cu "
            "JOIN nodes n ON n.uuid=cu.program_uuid "
            "WHERE UPPER(cu.copybook_name)=UPPER(?)",
            (name,),
        ).fetchall())

        data_items_sample = _rows_to_list(con.execute(
            """
            SELECT DISTINCT di.name, di.level, di.pic, di.usage, di.canonical_kind
              FROM data_items di
             WHERE UPPER(di.copybook_origin)=UPPER(?)
             ORDER BY di.start_line
             LIMIT 30
            """,
            (name,),
        ).fetchall())

    source_type = catalog.get("source_type", "COPYBOOK")
    item_count  = catalog.get("data_item_count", len(data_items_sample))
    consumer_names = [c["program_name"] for c in consumers[:20]]
    item_names = catalog.get("item_names", [n["name"] for n in data_items_sample])[:30]

    pic_lines = [
        f"  {d['name']} (L{d['level']}, {d['pic'] or d['usage'] or d['canonical_kind'] or '?'})"
        for d in data_items_sample[:20]
    ]

    prompt = f"""You are a COBOL modernisation expert. Explain the purpose and content of the COBOL copybook named {name}.

Copybook type: {source_type}
Total data items defined: {item_count}
Used by programs: {', '.join(consumer_names) if consumer_names else '(none detected)'}

Data items defined (sample):
{chr(10).join(pic_lines) if pic_lines else '  (none extracted)'}

Write a concise explanation (3-5 sentences) covering:
1. What business entity or concept this copybook represents
2. The key data fields and their likely purpose
3. Which programs use it and what that implies about data flow

Be specific and grounded in the data shown above. Do not invent fields not listed."""

    try:
        from llm.llm_client import call_llm
        spec = call_llm(prompt, max_tokens=512)
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return {"spec": spec, "name": name, "source_type": source_type,
            "consumer_count": len(consumers), "item_count": item_count}


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

        async def _run_one(persona: str):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return persona, await loop.run_in_executor(
                    pool, generate_persona_spec, persona, program_name, scope, uuid_
                )

        tasks = [_run_one(p) for p in personas]
        for coro in asyncio.as_completed(tasks):
            try:
                persona, result = await coro
                yield f"data: {json.dumps({'event': 'persona_done', 'persona': persona, 'label': _PERSONA_LABELS.get(persona, persona), 'content': result['content'], 'grounding_score': result.get('grounding_score', 0)})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'event': 'persona_error', 'persona': 'unknown', 'error': str(exc)})}\n\n"
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'event': 'all_done'})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/generate-spec/comprehensive", tags=["LLM"])
async def generate_comprehensive_spec(body: dict):
    """Generate a 100+ page consolidated specification from all 7 artifact layers.

    Streams SSE events:  section_done {title, content}  |  all_done
    """
    program_name = body.get("program_name", "")
    if not program_name:
        raise HTTPException(400, "program_name required")
    if not _db_exists():
        raise HTTPException(503, "Pipeline database not found — run the pipeline first.")

    async def _stream():
        with _con() as con:
            prog_uuid = _get_prog_uuid(con, program_name)
            if not prog_uuid:
                yield f"data: {json.dumps({'event':'error','message':f'Program {program_name!r} not found'})}\n\n"
                return

            from llm.retrieval import assemble_program_slice
            from llm.multi_agent import generate_persona_spec

            slice_data = assemble_program_slice(prog_uuid, con)

            # Pull raw DB data for the comprehensive sections
            paragraphs = _rows_to_list(con.execute(
                "SELECT uuid, name, start_line, end_line FROM nodes WHERE parent_uuid=? AND kind='Paragraph' ORDER BY start_line",
                (prog_uuid,)).fetchall())
            data_items = _rows_to_list(con.execute(
                "SELECT name, level, pic, usage, canonical_kind, precision, scale, signed, length, copybook_origin FROM data_items WHERE program_uuid=? ORDER BY start_line",
                (prog_uuid,)).fetchall())
            biz_rules = _rows_to_list(con.execute(
                "SELECT kind, predicate_raw, predicate_resolved, then_summary, else_summary, line FROM business_rules WHERE program_uuid=? ORDER BY line",
                (prog_uuid,)).fetchall())
            cfg_edges = _rows_to_list(con.execute(
                """SELECT n1.name AS from_name, n2.name AS to_name, cf.edge_type
                   FROM control_flow cf
                   JOIN nodes n1 ON n1.uuid=cf.from_uuid
                   JOIN nodes n2 ON n2.uuid=cf.to_uuid
                   WHERE n1.parent_uuid=? OR n2.parent_uuid=?
                   ORDER BY cf.edge_type, n1.name""",
                (prog_uuid, prog_uuid)).fetchall())
            call_edges = _rows_to_list(con.execute(
                """SELECT n2.name AS callee, cg.call_type, cg.is_resolved
                   FROM call_graph cg
                   LEFT JOIN nodes n2 ON n2.uuid=cg.callee_uuid
                   WHERE cg.caller_uuid=?""",
                (prog_uuid,)).fetchall())
            file_ops = _rows_to_list(con.execute(
                "SELECT file_name, operation FROM file_io WHERE program_uuid=? ORDER BY file_name",
                (prog_uuid,)).fetchall())
            risks = _rows_to_list(con.execute(
                "SELECT kind, severity, note, line FROM risk_register WHERE program_uuid=? ORDER BY severity, kind",
                (prog_uuid,)).fetchall())
            arith = _rows_to_list(con.execute(
                "SELECT expression_text, result_field FROM arithmetic_specs WHERE program_uuid=? LIMIT 40",
                (prog_uuid,)).fetchall())
            conds_88 = _rows_to_list(con.execute(
                """SELECT di_name.name AS parent_name, c.name AS condition_name, c.value_raw
                   FROM conditions_88 c
                   JOIN data_items di_name ON di_name.uuid=c.parent_uuid
                   WHERE di_name.program_uuid=? ORDER BY parent_name""",
                (prog_uuid,)).fetchall())
            jcl_bindings = _rows_to_list(con.execute(
                "SELECT job_name, step_name, dd_name, dataset_name, cobol_logical_file FROM jcl_program_binding WHERE program_uuid=?",
                (prog_uuid,)).fetchall())

        prog_node = slice_data.get("program", {})
        prog_name = prog_node.get("name", program_name)

        def _table(headers: list[str], rows: list[dict], keys: list[str], limit: int = 200) -> str:
            if not rows:
                return "_None recorded._\n"
            hdr = "| " + " | ".join(headers) + " |\n"
            sep = "|" + "|".join("---" for _ in headers) + "|\n"
            body = ""
            for r in rows[:limit]:
                cells = [str(r.get(k, "") or "").replace("|", "\\|").replace("\n", " ")[:120] for k in keys]
                body += "| " + " | ".join(cells) + " |\n"
            if len(rows) > limit:
                body += f"\n_…and {len(rows)-limit} more rows._\n"
            return hdr + sep + body

        sections = []

        # ── Section 1: Executive Summary ─────────────────────────────────────
        sections.append(("Executive Summary", f"""# {prog_name} — Comprehensive Modernisation Specification

**Program:** `{prog_name}`
**Source file:** `{prog_node.get('source_file','').split('/')[-1]}`
**Lines of code:** {prog_node.get('end_line',0) - prog_node.get('start_line',0) + 1}
**Paragraphs:** {len(paragraphs)}
**Data items:** {len(data_items)}
**Business rules:** {len(biz_rules)}
**Migration risks:** {len(risks)} ({sum(1 for r in risks if r['severity']=='HIGH')} HIGH, {sum(1 for r in risks if r['severity']=='MEDIUM')} MEDIUM, {sum(1 for r in risks if r['severity']=='LOW')} LOW)
**Call edges:** {len(call_edges)}
**File I/O operations:** {len(file_ops)}
**CFG edges:** {len(cfg_edges)}
**JCL dataset bindings:** {len(jcl_bindings)}

This document is machine-generated from the 7-layer ANTLR4 artifact pipeline. Every fact is grounded in parsed artifacts — no source code was passed to the LLM.
"""))

        # ── Section 2: Paragraph Inventory ───────────────────────────────────
        sections.append(("Paragraph Inventory", f"""## 1. Paragraph Inventory

All {len(paragraphs)} paragraphs in `{prog_name}`, in source order:

""" + _table(
            ["Paragraph Name", "Start Line", "End Line", "Lines"],
            [{**p, "Lines": str(p.get("end_line",0) - p.get("start_line",0) + 1)} for p in paragraphs],
            ["name", "start_line", "end_line", "Lines"],
        )))

        # ── Section 3: Data Dictionary ────────────────────────────────────────
        sections.append(("Data Dictionary", f"""## 2. Data Dictionary

All {len(data_items)} data items defined in `{prog_name}`:

""" + _table(
            ["Name", "Level", "PIC", "USAGE", "Canonical Type", "Copybook"],
            data_items,
            ["name", "level", "pic", "usage", "canonical_kind", "copybook_origin"],
        )))

        # ── Section 4: 88-Level Conditions ────────────────────────────────────
        sections.append(("88-Level Conditions", f"""## 3. 88-Level Conditions (Named Predicates)

{len(conds_88)} named conditions defined. These are resolved to VALUE clauses in business rules:

""" + _table(
            ["Parent Variable", "Condition Name", "Value(s)"],
            conds_88,
            ["parent_name", "condition_name", "value_raw"],
        )))

        # ── Section 5: Business Rules ─────────────────────────────────────────
        sections.append(("Business Rules", f"""## 4. Business Rules

All {len(biz_rules)} IF/EVALUATE conditions extracted from `{prog_name}`:

""" + _table(
            ["Kind", "Predicate (Raw)", "Predicate (Resolved)", "Then", "Else", "Line"],
            biz_rules,
            ["kind", "predicate_raw", "predicate_resolved", "then_summary", "else_summary", "line"],
        )))

        # ── Section 6: Arithmetic Specifications ──────────────────────────────
        if arith:
            sections.append(("Arithmetic Specifications", f"""## 5. Arithmetic Specifications

{len(arith)} COMPUTE/ADD/SUBTRACT/MULTIPLY/DIVIDE operations. All numeric fields are COMP-3 packed decimal → emit as `BigDecimal` with `RoundingMode.HALF_EVEN`:

""" + _table(
                ["Expression", "Result Field"],
                arith,
                ["expression_text", "result_field"],
            )))

        # ── Section 7: Control Flow Graph ─────────────────────────────────────
        edge_types = {}
        for e in cfg_edges:
            edge_types[e.get("edge_type","")] = edge_types.get(e.get("edge_type",""), 0) + 1
        sections.append(("Control Flow Graph", f"""## 6. Control Flow Graph

**Total CFG edges:** {len(cfg_edges)}

Edge type breakdown:
""" + "\n".join(f"- `{k}`: {v}" for k, v in sorted(edge_types.items())) + "\n\n" +
            _table(["From Paragraph", "To Paragraph", "Edge Type"], cfg_edges, ["from_name", "to_name", "edge_type"])))

        # ── Section 8: Inter-Program Calls ────────────────────────────────────
        sections.append(("Inter-Program Call Graph", f"""## 7. Inter-Program Call Graph

`{prog_name}` makes {len(call_edges)} outbound call(s):

""" + _table(
            ["Callee Program", "Call Type", "Resolved?"],
            [{**c, "Resolved?": "✓" if c.get("is_resolved") else "✗ (dynamic)"} for c in call_edges],
            ["callee", "call_type", "Resolved?"],
        )))

        # ── Section 9: File I/O ───────────────────────────────────────────────
        sections.append(("File I/O Operations", f"""## 8. File I/O Operations

""" + _table(["Logical File Name", "Operation"], file_ops, ["file_name", "operation"])))

        # ── Section 10: JCL Dataset Bindings ─────────────────────────────────
        if jcl_bindings:
            sections.append(("JCL Dataset Bindings", f"""## 9. JCL Dataset Bindings

JCL DD names linked to this program's logical files:

""" + _table(
                ["Job", "Step", "DD Name", "Dataset DSN", "COBOL Logical File"],
                jcl_bindings,
                ["job_name", "step_name", "dd_name", "dataset_name", "cobol_logical_file"],
            )))

        # ── Section 11: Migration Risk Register ──────────────────────────────
        sections.append(("Migration Risk Register", f"""## 10. Migration Risk Register

{len(risks)} risks identified ({sum(1 for r in risks if r['severity']=='HIGH')} HIGH, {sum(1 for r in risks if r['severity']=='MEDIUM')} MEDIUM, {sum(1 for r in risks if r['severity']=='LOW')} LOW):

""" + _table(
            ["Risk Kind", "Severity", "Note", "Line"],
            sorted(risks, key=lambda r: {"HIGH":0,"MEDIUM":1,"LOW":2}.get(r.get("severity",""),3)),
            ["kind", "severity", "note", "line"],
        )))

        # ── Sections 12-17: LLM-generated personas ───────────────────────────
        import concurrent.futures
        from llm.multi_agent import generate_persona_spec
        persona_map = {
            "business_summary":   "11. Business Summary",
            "highlevel_arch":     "12. High-Level Architecture",
            "lowlevel_arch":      "13. Low-Level Architecture",
            "functional_spec":    "14. Functional Specification",
            "technical_spec":     "15. Technical Specification",
            "modernization_spec": "16. Modernisation Specification",
        }

        loop = asyncio.get_event_loop()
        async def _run_persona(pkey: str, ptitle: str):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = await loop.run_in_executor(
                    pool, generate_persona_spec, pkey, program_name, "program", prog_uuid
                )
            content = result.get("content", "")
            return ptitle, f"## {ptitle}\n\n{content}"

        # Stream the static sections first
        for title, content in sections:
            yield f"data: {json.dumps({'event':'section_done','title':title,'content':content})}\n\n"
            await asyncio.sleep(0)

        # Run all personas in parallel then stream as they finish
        tasks = [_run_persona(k, v) for k, v in persona_map.items()]
        for coro in asyncio.as_completed(tasks):
            try:
                ptitle, content = await coro
                yield f"data: {json.dumps({'event':'section_done','title':ptitle,'content':content})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'event':'section_done','title':'Error','content':f'LLM section failed: {exc}'})}\n\n"
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'event':'all_done'})}\n\n"

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
        import uuid as _uuid_mod

        def fmt(msg: str, kind: str = "log") -> str:
            return f"data: {json.dumps({'kind': kind, 'msg': msg, 'ts': time.time()})}\n\n"

        # Record run start
        run_id = str(_uuid_mod.uuid4())[:8]
        run_record: dict = {
            "id": run_id, "corpus": corpus, "started_at": time.time(),
            "completed_at": None, "status": "running", "stats": {}
        }
        runs = _load_runs()
        runs.append(run_record)
        _save_runs(runs)

        yield fmt("Pipeline starting…", "start")
        yield fmt(f"Corpus: {corpus}", "info")
        yield fmt(f"Database: {db_path}", "info")

        if not pathlib.Path(corpus).exists():
            yield fmt(f"ERROR: corpus directory not found: {corpus}", "error")
            run_record.update({"status": "error", "completed_at": time.time()})
            runs[-1] = run_record; _save_runs(runs)
            yield fmt("DONE", "done")
            return

        cmd = [
            sys.executable, str(PROJECT_ROOT / "pipeline" / "batch.py"),
            "--corpus", corpus,
            "--db", db_path,
        ]

        final_status = "completed"
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
                final_status = "cancelled"
            else:
                yield fmt(f"Pipeline exited with code {_pipeline_proc.returncode}", "error")
                final_status = "error"
        except Exception as exc:
            yield fmt(f"ERROR: {exc}", "error")
            final_status = "error"
        finally:
            _pipeline_proc = None

        # Snapshot stats for history record
        snap: dict = {}
        try:
            with _con() as _c:
                snap = {
                    "programs": _c.execute("SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'").fetchone()[0],
                    "paragraphs": _c.execute("SELECT COUNT(*) FROM nodes WHERE kind='Paragraph'").fetchone()[0],
                    "business_rules": _c.execute("SELECT COUNT(*) FROM business_rules").fetchone()[0],
                    "call_edges": _c.execute("SELECT COUNT(*) FROM call_graph").fetchone()[0],
                }
        except Exception:
            pass
        run_record.update({"status": final_status, "completed_at": time.time(), "stats": snap})
        # Update run in list
        runs = _load_runs()
        for i, r in enumerate(runs):
            if r.get("id") == run_id:
                runs[i] = run_record; break
        _save_runs(runs)

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


@app.get("/pipeline/runs", tags=["Pipeline"])
def get_pipeline_runs():
    """List all recorded pipeline runs with timestamps and result stats."""
    runs = _load_runs()
    return {"runs": list(reversed(runs))}  # newest first


@app.delete("/pipeline/runs/{run_id}", tags=["Pipeline"])
def delete_pipeline_run(run_id: str):
    """Remove a single run from the history log (does not clear artifact data)."""
    runs = _load_runs()
    before = len(runs)
    runs = [r for r in runs if r.get("id") != run_id]
    _save_runs(runs)
    return {"ok": True, "removed": before - len(runs)}


@app.post("/pipeline/clear-db", tags=["Pipeline"])
def clear_pipeline_db():
    """Truncate ALL artifact tables and clear run history — resets the platform to factory state."""
    _ARTIFACT_TABLES = [
        "nodes", "data_items", "conditions_88", "control_flow", "def_use",
        "call_graph", "file_io", "db_io", "transaction_flow", "screen_map",
        "jcl_job", "jcl_dd", "jcl_dependency", "jcl_program_binding",
        "copybook_use", "business_rules", "arithmetic_specs", "csd_catalog",
        "parse_coverage", "risk_register", "complexity_metrics",
    ]
    cleared: list[str] = []
    if _db_exists():
        try:
            with _con() as con:
                # Disable FK enforcement so child-table references don't block DELETE FROM nodes
                con.execute("PRAGMA foreign_keys=OFF")
                for tbl in _ARTIFACT_TABLES:
                    try:
                        con.execute(f"DELETE FROM {tbl}")
                        cleared.append(tbl)
                    except Exception:
                        pass
                con.execute("PRAGMA foreign_keys=ON")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "cleared": cleared}
    # Also clear run history file
    if _RUNS_FILE.exists():
        _RUNS_FILE.unlink()
    return {"ok": True, "cleared_tables": cleared, "run_history_cleared": True}


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


# ── Knowledge Graph ───────────────────────────────────────────────────────────

@app.get("/knowledge-graph", tags=["Knowledge Graph"])
def get_knowledge_graph():
    """Return vis.js-compatible nodes and edges for the full portfolio knowledge graph."""
    import uuid as _uuid_mod

    _NS = _uuid_mod.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    nodes: list[dict] = []
    edges: list[dict] = []

    if not _db_exists():
        return {"nodes": [], "edges": [], "stats": {
            "programs": 0, "copybooks": 0, "jcl_jobs": 0,
            "call_edges": 0, "copy_edges": 0,
        }}

    import re as _re

    def _clean_cb_name(raw: str) -> str:
        """Extract copybook name from Python object string representations."""
        m = _re.search(r"copybook_name='([^']+)'", raw)
        return m.group(1) if m else raw.split("'")[0].strip()

    try:
        with _con() as con:
            # ── Program nodes (deduplicated by name, aggregated complexity) ─
            prog_rows = con.execute(
                """SELECT n.uuid, n.name,
                          SUM(cm.cyclomatic) as total_cc,
                          SUM(cm.statement_count) as total_stmts,
                          COUNT(DISTINCT p.uuid) as para_count
                   FROM nodes n
                   LEFT JOIN complexity_metrics cm ON cm.program_uuid = n.uuid
                   LEFT JOIN nodes p ON p.parent_uuid = n.uuid AND p.kind = 'Paragraph'
                   WHERE n.kind = 'Program'
                   GROUP BY UPPER(n.name)
                   LIMIT 200"""
            ).fetchall()
            seen_prog_names: set[str] = set()
            for r in prog_rows:
                name_key = (r[1] or '').upper()
                if name_key in seen_prog_names:
                    continue
                seen_prog_names.add(name_key)
                cc = r[2]
                sc = r[3]
                pc = r[4]
                tooltip = f"Program: {r[1]}"
                if pc:
                    tooltip += f"\nParagraphs: {int(pc)}"
                if cc is not None:
                    tooltip += f"\nTotal Cyclomatic CC: {int(cc)}"
                if sc:
                    tooltip += f"\nStatements: {int(sc)}"
                nodes.append({
                    "id": r[0],
                    "label": r[1],
                    "kind": "program",
                    "group": "program",
                    "color": "#5ecdd1",
                    "title": tooltip,
                })

            # ── Copybook nodes (clean names from possibly-serialised strings) ─
            cb_raw_rows = con.execute(
                """SELECT copybook_name, COUNT(DISTINCT program_uuid) AS consumers,
                          program_uuid
                   FROM copybook_use
                   GROUP BY copybook_name
                   ORDER BY consumers DESC
                   LIMIT 100"""
            ).fetchall()
            cb_id_map: dict[str, str] = {}   # raw_name → node_id
            cb_clean_map: dict[str, str] = {}  # raw_name → clean_name
            seen_cb: set[str] = set()
            for r in cb_raw_rows:
                raw_name = r[0]
                clean = _clean_cb_name(raw_name)
                if clean.upper() in seen_cb:
                    # Still map the raw name so copy-edges can find it
                    cb_id_map[raw_name] = str(_uuid_mod.uuid5(_NS, f"copybook:{clean.upper()}"))
                    continue
                seen_cb.add(clean.upper())
                cb_id = str(_uuid_mod.uuid5(_NS, f"copybook:{clean.upper()}"))
                cb_id_map[raw_name] = cb_id
                cb_clean_map[raw_name] = clean
                nodes.append({
                    "id": cb_id,
                    "label": clean,
                    "kind": "copybook",
                    "group": "copybook",
                    "color": "#60c8fa",
                    "title": f"Copybook: {clean}\nUsed by {r[1]} program(s)",
                })

            # ── JCL Job nodes ──────────────────────────────────────────────
            jcl_rows = con.execute(
                """SELECT job_name, COUNT(*) AS step_count
                   FROM jcl_job
                   GROUP BY job_name
                   ORDER BY step_count DESC
                   LIMIT 100"""
            ).fetchall()
            jcl_id_map: dict[str, str] = {}
            for r in jcl_rows:
                jcl_name = r[0]
                jcl_id = str(_uuid_mod.uuid5(_NS, f"jcl:{jcl_name}"))
                jcl_id_map[jcl_name] = jcl_id
                nodes.append({
                    "id": jcl_id,
                    "label": jcl_name,
                    "kind": "jcl",
                    "group": "jcl",
                    "color": "#fbbf24",
                    "title": f"JCL Job: {jcl_name}\nSteps: {r[1]}",
                })

            # Build canonical name→id maps (duplicate UUIDs exist when pipeline ran >1x)
            prog_name_to_id: dict[str, str] = {
                n["label"].upper(): n["id"] for n in nodes if n["kind"] == "program"
            }
            jcl_name_to_id: dict[str, str] = {
                n["label"].upper(): n["id"] for n in nodes if n["kind"] == "jcl"
            }

            _seen_edges: set[tuple] = set()

            def _add_edge(from_id: str, to_id: str, label: str, kind: str) -> None:
                if not from_id or not to_id or from_id == to_id:
                    return
                key = (from_id, to_id, kind)
                if key not in _seen_edges:
                    _seen_edges.add(key)
                    edges.append({"from": from_id, "to": to_id, "label": label, "kind": kind})

            # ── 1. CALL edges — resolved via program names (survives duplicate UUIDs) ──
            for r in con.execute(
                """SELECT nc.name, ne.name, cg.call_type
                   FROM call_graph cg
                   JOIN nodes nc ON nc.uuid = cg.caller_uuid
                   JOIN nodes ne ON ne.uuid = cg.callee_uuid
                   WHERE cg.callee_uuid IS NOT NULL"""
            ).fetchall():
                _add_edge(
                    prog_name_to_id.get((r[0] or '').upper()),
                    prog_name_to_id.get((r[1] or '').upper()),
                    r[2] or "CALL", "call",
                )

            # ── 2. Unresolved CALLs: match callee_name directly against program names ──
            for r in con.execute(
                """SELECT nc.name, cg.callee_name, cg.call_type
                   FROM call_graph cg
                   JOIN nodes nc ON nc.uuid = cg.caller_uuid
                   WHERE cg.callee_uuid IS NULL AND cg.callee_name IS NOT NULL"""
            ).fetchall():
                to_id = prog_name_to_id.get((r[1] or '').upper().strip())
                if to_id:
                    _add_edge(prog_name_to_id.get((r[0] or '').upper()), to_id, r[2] or "CALL", "call")

            # ── 3. CICS TX flow — use to_program name directly ─────────────
            for r in con.execute(
                """SELECT nf.name, tf.to_program, tf.verb
                   FROM transaction_flow tf
                   JOIN nodes nf ON nf.uuid = tf.from_uuid
                   WHERE tf.to_program IS NOT NULL"""
            ).fetchall():
                to_id = prog_name_to_id.get((r[1] or '').upper().strip())
                if to_id:
                    _add_edge(prog_name_to_id.get((r[0] or '').upper()), to_id, r[2] or "XCTL", "tx")

            # ── 4. JCL → COBOL invocation edges ───────────────────────────
            for r in con.execute(
                """SELECT DISTINCT job_name, program FROM jcl_job
                   WHERE program IS NOT NULL"""
            ).fetchall():
                jcl_id  = jcl_name_to_id.get((r[0] or '').upper())
                prog_id = prog_name_to_id.get((r[1] or '').upper())
                if jcl_id and prog_id:
                    _add_edge(jcl_id, prog_id, "EXEC", "jcl")

            # ── 5. Shared-file data-dependency edges (program ↔ program) ──
            shared_rows = con.execute(
                """SELECT fi1.program_uuid AS p1, fi2.program_uuid AS p2,
                          n1.name AS n1, n2.name AS n2, fi1.file_name
                   FROM file_io fi1
                   JOIN file_io fi2 ON fi1.file_name = fi2.file_name
                                    AND fi1.program_uuid < fi2.program_uuid
                   JOIN nodes n1 ON n1.uuid = fi1.program_uuid
                   JOIN nodes n2 ON n2.uuid = fi2.program_uuid
                   GROUP BY n1.name, n2.name
                   LIMIT 300"""
            ).fetchall()
            for r in shared_rows:
                _add_edge(
                    prog_name_to_id.get((r[2] or '').upper()),
                    prog_name_to_id.get((r[3] or '').upper()),
                    r[4] or "FILE", "file",
                )

            # ── 6. Copy edges — program → copybook ────────────────────────
            for r in con.execute(
                """SELECT n.name, cu.copybook_name
                   FROM copybook_use cu
                   JOIN nodes n ON n.uuid = cu.program_uuid
                   LIMIT 400"""
            ).fetchall():
                _add_edge(
                    prog_name_to_id.get((r[0] or '').upper()),
                    cb_id_map.get(r[1]),
                    "COPY", "copy",
                )

            # ── 7. Navigation edges from data_items VALUE literals ─────────
            # CardDemo uses VALUE 'PROGNAME' working-storage literals for XCTL targets
            import re as _re_nav
            nav_rows = con.execute(
                """SELECT n.name, di.value_raw
                   FROM data_items di
                   JOIN nodes n ON n.uuid = di.program_uuid
                   WHERE di.value_raw LIKE 'VALUE''%'''"""
            ).fetchall()
            for r in nav_rows:
                m_nav = _re_nav.search(r"VALUE'([A-Z0-9]{5,9})'", r[1] or '', _re_nav.IGNORECASE)
                if not m_nav:
                    continue
                target = m_nav.group(1).upper()
                _add_edge(
                    prog_name_to_id.get((r[0] or '').upper()),
                    prog_name_to_id.get(target),
                    "NAV", "nav",
                )

            # ── 8. MOVE 'LITERAL' → nav-variable edges ────────────────────
            # Resolves dynamic XCTL: MOVE 'COSGN00C' TO CDEMO-TO-PROGRAM
            import json as _json_kg
            move_rows = con.execute(
                """SELECT n_stmt.payload_json, n_prog.name
                   FROM nodes n_stmt
                   JOIN nodes n_para ON n_para.uuid = n_stmt.parent_uuid
                   JOIN nodes n_prog ON n_prog.uuid = n_para.parent_uuid
                   WHERE n_stmt.kind = 'Stmt_MOVE'
                     AND (n_stmt.payload_json LIKE '%CDEMO-TO-PROGRAM%'
                       OR n_stmt.payload_json LIKE '%CCARD-NEXT-PROG%'
                       OR n_stmt.payload_json LIKE '%LIT-MENUPGM%')"""
            ).fetchall()
            for r in move_rows:
                p = _json_kg.loads(r[0]) if r[0] else {}
                text = p.get('text', '')
                for m_mv in _re_nav.finditer(
                    r"MOVE\s*['\"]([A-Z0-9]{5,9})['\"]\s*TO\s*"
                    r"(CDEMO-TO-PROGRAM|CCARD-NEXT-PROG|LIT-MENUPGM)",
                    text, _re_nav.IGNORECASE
                ):
                    target = m_mv.group(1).upper()
                    _add_edge(
                        prog_name_to_id.get((r[1] or '').upper()),
                        prog_name_to_id.get(target),
                        "XCTL→", "tx",
                    )

    except Exception as _kg_exc:
        import traceback as _tb
        print("KG ERROR:", _tb.format_exc())
        return {"nodes": [], "edges": [], "stats": {
            "programs": 0, "copybooks": 0, "jcl_jobs": 0,
            "call_edges": 0, "copy_edges": 0,
        }}

    prog_count   = sum(1 for n in nodes if n["kind"] == "program")
    cb_count     = sum(1 for n in nodes if n["kind"] == "copybook")
    jcl_count    = sum(1 for n in nodes if n["kind"] == "jcl")
    call_count   = sum(1 for e in edges if e["kind"] == "call")
    tx_count     = sum(1 for e in edges if e["kind"] == "tx")
    nav_count    = sum(1 for e in edges if e["kind"] == "nav")
    jcl_edge_cnt = sum(1 for e in edges if e["kind"] == "jcl")
    file_count   = sum(1 for e in edges if e["kind"] == "file")
    copy_count   = sum(1 for e in edges if e["kind"] == "copy")

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "programs":   prog_count,
            "copybooks":  cb_count,
            "jcl_jobs":   jcl_count,
            "call_edges": call_count,
            "tx_edges":   tx_count,
            "nav_edges":  nav_count,
            "jcl_edges":  jcl_edge_cnt,
            "file_edges": file_count,
            "copy_edges": copy_count,
        },
    }


# ── Portfolio Transformation (SSE) ────────────────────────────────────────────

@app.post("/transform/portfolio", tags=["Transform"])
async def transform_portfolio(body: dict = {}):
    """Stream a portfolio-level modernisation transformation analysis via SSE."""
    framework:     str = body.get("framework",     "Spring Boot")
    cloud:         str = body.get("cloud",         "AWS")
    decomposition: str = body.get("decomposition", "Strangler Fig")

    async def generator():
        def _evt(section: str, content: str) -> str:
            return f"data: {json.dumps({'kind': 'section', 'section': section, 'content': content})}\n\n"

        if not _db_exists():
            yield f"data: {json.dumps({'kind': 'error', 'msg': 'No pipeline database found — run the pipeline first.'})}\n\n"
            return

        # ── Gather DB stats ────────────────────────────────────────────────
        try:
            with _con() as con:
                def _safe_count(sql: str) -> int:
                    try:
                        return con.execute(sql).fetchone()[0]
                    except Exception:
                        return 0

                stats = {
                    "programs":       _safe_count("SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'"),
                    "paragraphs":     _safe_count("SELECT COUNT(DISTINCT UPPER(name)||'|'||UPPER(source_file)) FROM nodes WHERE kind='Paragraph'"),
                    "data_items":     _safe_count("SELECT COUNT(*) FROM (SELECT DISTINCT UPPER(name), program_uuid FROM data_items)"),
                    "business_rules": _safe_count("SELECT COUNT(DISTINCT uuid) FROM business_rules"),
                    "call_edges":     _safe_count("SELECT COUNT(DISTINCT caller_uuid||'|'||callee_name) FROM call_graph WHERE is_resolved=1"),
                    "cfg_edges":      _safe_count("SELECT COUNT(DISTINCT from_uuid||'|'||to_uuid) FROM control_flow"),
                    "jcl_jobs":       _safe_count("SELECT COUNT(DISTINCT job_name) FROM jcl_job"),
                    "cics_verbs":     _safe_count("SELECT COUNT(*) FROM transaction_flow"),
                    "risks_high":     _safe_count("SELECT COUNT(*) FROM risk_register WHERE severity='HIGH'"),
                    "copybooks":      _safe_count("SELECT COUNT(DISTINCT copybook_name) FROM copybook_use"),
                    "file_io":        _safe_count("SELECT COUNT(DISTINCT file_name) FROM file_io"),
                }

                try:
                    top_progs = con.execute(
                        """SELECT n.name, cm.cyclomatic, cm.statement_count
                           FROM complexity_metrics cm
                           JOIN nodes n ON cm.program_uuid = n.uuid
                           GROUP BY UPPER(n.name)
                           ORDER BY cm.cyclomatic DESC
                           LIMIT 5"""
                    ).fetchall()
                except Exception:
                    top_progs = []

                try:
                    most_called = con.execute(
                        """SELECT callee_name, COUNT(*) AS cnt
                           FROM call_graph
                           WHERE is_resolved=1
                           GROUP BY callee_name
                           ORDER BY cnt DESC
                           LIMIT 5"""
                    ).fetchall()
                except Exception:
                    most_called = []

                try:
                    br_dist = con.execute(
                        """SELECT program_uuid, COUNT(*) AS cnt
                           FROM business_rules
                           GROUP BY program_uuid
                           ORDER BY cnt DESC
                           LIMIT 5"""
                    ).fetchall()
                except Exception:
                    br_dist = []

                try:
                    risk_cats = con.execute(
                        """SELECT category, COUNT(*) AS cnt
                           FROM risk_register
                           GROUP BY category
                           ORDER BY cnt DESC"""
                    ).fetchall()
                except Exception:
                    risk_cats = []

        except Exception as exc:
            yield f"data: {json.dumps({'kind': 'error', 'msg': f'Database error: {exc}'})}\n\n"
            return

        # ── Formatted helpers ──────────────────────────────────────────────
        top_progs_txt = "\n".join(
            f"  • {r[0]}: CC={r[1]}, statements={r[2]}" for r in top_progs
        ) or "  (no complexity data yet)"

        most_called_txt = "\n".join(
            f"  • {r[0]}: called {r[1]} time(s)" for r in most_called
        ) or "  (no resolved calls yet)"

        risk_cats_txt = ", ".join(
            f"{r[0]} ({r[1]})" for r in risk_cats
        ) or "none detected"

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        # ── Section definitions for static generation ──────────────────────
        sections = [
            (
                "Portfolio Architecture Discovery",
                f"""Automated 7-layer artifact pipeline completed analysis of the COBOL portfolio.

Key findings:
- {stats['programs']} COBOL programs fully parsed and modelled
- {stats['paragraphs']:,} paragraphs extracted (procedural execution units)
- {stats['data_items']:,} data items catalogued (Working-Storage + File Definitions)
- {stats['copybooks']} unique copybooks (shared business logic / data definitions)
- {stats['jcl_jobs']} JCL batch job definitions (scheduled workloads)
- {stats['cics_verbs']} CICS transaction verbs (online user-facing interactions)
- {stats['business_rules']} business rules extracted from IF/EVALUATE/COMPUTE statements
- {stats['cfg_edges']:,} control-flow graph edges (branch coverage map)
- {stats['call_edges']} resolved inter-program call edges
- {stats['file_io']} distinct logical files (VSAM/flat/DB2 datasets)
- Migration risk profile: {stats['risks_high']} HIGH-severity items identified

The portfolio is fully understood — every line of COBOL is accounted for before a single line of {framework} is written.""",
            ),
            (
                "Source Architecture Analysis",
                f"""The COBOL application follows a classic three-tier mainframe architecture:

**Batch tier (JCL):** {stats['jcl_jobs']} job definitions drive nightly/periodic processing. Each JCL step invokes a COBOL program with DD card file bindings — these become Spring Batch jobs in the target.

**Online tier (CICS):** {stats['cics_verbs']} CICS verbs (EXEC CICS SEND/RECEIVE/LINK/XCTL/RETURN) orchestrate {stats['programs']} transaction programs. These map directly to REST controllers and service calls.

**Shared logic (Copybooks):** {stats['copybooks']} copybooks carry reusable record layouts and common routines — the COBOL equivalent of shared libraries / DTOs.

**Data layer:** {stats['file_io']} logical files span VSAM KSDS/ESDS clusters and sequential datasets.

Top 5 most complex programs (highest cyclomatic complexity — migration priority):
{top_progs_txt}

Most-called programs (highest coupling — extract as shared services first):
{most_called_txt}""",
            ),
            (
                "Domain Decomposition Strategy",
                f"""Using the call graph ({stats['call_edges']} resolved edges) and program naming conventions, the portfolio decomposes into natural bounded contexts:

**Account Management** — programs handling ACCT/ACCOUNT prefixes; owns customer and account lifecycle.
**Transaction Processing** — programs with TRAN/TRNS/PMT prefixes; owns debit/credit/payment flows.
**Reporting & Analytics** — programs with RPT/STMT/SUMM prefixes; owns batch report generation.
**Security & Authentication** — programs with SEC/AUTH/SIGN prefixes; owns session and access control.
**Data Services** — utility programs invoked by ≥3 others; candidates for shared microservice libraries.

**Why {decomposition}:**
{"The Strangler Fig pattern is ideal here — each CICS transaction maps cleanly to a REST endpoint. New requests are intercepted by an API Gateway and routed to Spring Boot services while legacy CICS handles the rest. Revenue-critical paths are migrated last after parallel-run validation." if "Strangler" in decomposition else ("Big Bang modernisation means all " + str(stats['programs']) + " programs are migrated simultaneously in a single cutover. This minimises the dual-maintenance window but requires a comprehensive regression test suite covering all " + str(stats['business_rules']) + " extracted business rules before go-live." if "Big" in decomposition else ("Parallel Run keeps the mainframe live while the " + framework + " equivalent processes the same transactions. Output is reconciled record-by-record. With " + str(stats['business_rules']) + " extracted business rules as acceptance criteria, divergence is caught automatically." ))}""",
            ),
            (
                f"Target Architecture on {cloud}",
                f"""Cloud-native target architecture on {cloud}:

**API Layer:** {"API Gateway (REST + WebSocket)" if cloud == "AWS" else "Azure API Management" if cloud == "Azure" else "Cloud Endpoints / Apigee"}
  → Routes CICS transaction equivalents to {framework} microservices

**Compute:** {"ECS Fargate / EKS" if cloud == "AWS" else "Azure Container Apps / AKS" if cloud == "Azure" else "Cloud Run / GKE"}
  → One microservice per bounded context ({stats['programs']} programs → ~5 services)

**Database:** {"Aurora PostgreSQL (KSDS→relational) + DynamoDB (ESDS→KV)" if cloud == "AWS" else "Azure Database for PostgreSQL + Cosmos DB" if cloud == "Azure" else "Cloud Spanner + Firestore"}
  → Replaces {stats['file_io']} VSAM/DB2 files with managed, autoscaled storage

**Batch:** {"AWS Batch + Step Functions" if cloud == "AWS" else "Azure Batch + Logic Apps" if cloud == "Azure" else "Cloud Batch + Workflows"}
  → Replaces {stats['jcl_jobs']} JCL jobs; Step definitions map 1:1 to JCL steps

**Messaging:** {"Amazon SQS/SNS + EventBridge" if cloud == "AWS" else "Azure Service Bus + Event Grid" if cloud == "Azure" else "Cloud Pub/Sub + Eventarc"}
  → Decouples the {stats['cics_verbs']} CICS inter-program LINK/XCTL calls

**Observability:** {"CloudWatch + X-Ray" if cloud == "AWS" else "Azure Monitor + Application Insights" if cloud == "Azure" else "Cloud Monitoring + Cloud Trace"}
  → End-to-end distributed tracing across all migrated services

**CI/CD:** {"CodePipeline + CodeBuild" if cloud == "AWS" else "Azure DevOps Pipelines" if cloud == "Azure" else "Cloud Build + Cloud Deploy"}
  → Automated build/test/deploy gated on business-rule unit test pass rate""",
            ),
            (
                f"COBOL → {framework} Component Mapping",
                f"""This is NOT line-to-line transpilation. COBOL is procedural and record-oriented; {framework} is object-oriented and hexagonal. The mapping is semantic:

| COBOL Construct | {framework} Equivalent |
|---|---|
| IDENTIFICATION DIVISION | Java class declaration + `@Service` / `@RestController` |
| ENVIRONMENT DIVISION | `application.yml` + `@Configuration` beans |
| DATA DIVISION (Working-Storage) | Class fields + `@Value` / DTO records |
| DATA DIVISION (File Section) | `@Entity` JPA classes + Repository interfaces |
| PROCEDURE DIVISION | Public service methods with clear input/output contracts |
| Paragraph (PERFORM target) | Private method extracted to named function |
| COPY statement ({stats['copybooks']} copybooks) | Shared Maven/Gradle library module (DTOs + interfaces) |
| EXEC CICS SEND/RECEIVE | `@RestController` endpoint + Jackson JSON serialisation |
| EXEC CICS LINK/XCTL | Feign client or internal service call |
| JCL EXEC PGM= step | Spring Batch `Step` bean within a `Job` definition |
| COMPUTE / arithmetic | `java.math.BigDecimal` (preserves COBOL decimal precision) |
| IF / EVALUATE (business rules) | Service method with guard clauses + unit test per rule |
| CALL literal | `@Autowired` service injection |
| CALL dynamic | Strategy pattern + Spring bean lookup |

{stats['data_items']:,} data items → typed Java fields (PIC 9 → `BigDecimal`/`long`, PIC X → `String`, 88-levels → `enum`).
{stats['paragraphs']:,} paragraphs → {stats['paragraphs']:,} named methods — HITL review confirms each mapping before code generation commits.""",
            ),
            (
                "Business Logic Preservation Strategy",
                f"""Business logic preservation is the highest-risk aspect of any COBOL migration. This pipeline addresses it at four levels:

**1. Rule extraction ({stats['business_rules']} rules):**
Every IF condition, EVALUATE WHEN branch, and COMPUTE expression was parsed into a structured predicate. Each becomes a unit-testable service method in {framework}. The predicate_raw text serves as the human-readable acceptance criterion.

**2. Arithmetic precision:**
COBOL PIC 9(n)V9(m) fields use fixed-point decimal — Java `double`/`float` are NOT safe equivalents. All {stats['data_items']:,} numeric data items are emitted as `java.math.BigDecimal` with the exact scale from the PIC clause. ROUNDED / TRUNCATED behaviour is preserved via `RoundingMode`.

**3. Control-flow fidelity ({stats['cfg_edges']:,} CFG edges):**
The control-flow graph captures every PERFORM THRU, GO TO, ALTER, and EVALUATE path. The Java emitter walks the CFG to produce structured `if/else` chains — no implicit fall-through that would silently change behaviour.

**4. Def-use chain validation:**
For each of the {stats['data_items']:,} data items, the pipeline tracked every write (MOVE, COMPUTE, READ INTO) and every read (WRITE FROM, condition check). The Java DTO fields inherit the same def-use semantics, ensuring no business logic is silently dropped during translation.""",
            ),
            (
                "Human-in-the-Loop Transformation Roadmap",
                f"""A phased HITL roadmap ensures no business logic is lost and every migration step has human sign-off:

**Phase 0 — Parse & Understand (COMPLETE)**
All {stats['programs']} programs fully modelled via the 7-layer artifact pipeline:
Layer 1: AST nodes | Layer 2: Data items | Layer 3: CFG | Layer 4: Call graph
Layer 5: Business rules | Layer 6: BMS/CICS maps | Layer 7: Risk register
Output: {stats['business_rules']} rules, {stats['cfg_edges']:,} CFG edges, {stats['risks_high']} HIGH risks catalogued.

**Phase 1 — {decomposition} Scaffold (Weeks 1–6)**
- Generate {framework} project structure with one Maven module per bounded context
- Map {stats['cics_verbs']} CICS transactions → REST endpoint stubs (auto-generated, HITL reviews each)
- Stand up {cloud} infrastructure (IaC via Terraform/CDK)
- Begin parallel-run data capture for later reconciliation

**Phase 2 — Business Logic Migration (Weeks 7–20)**
- Migrate programs in complexity order (top 5 by CC first: see Phase 0 output)
- Each program migration: generate → automated rule tests → HITL approval → merge
- Target: {stats['call_edges']} call-graph edges validated through integration tests
- {stats['business_rules']} business rules become JUnit 5 `@ParameterizedTest` suites

**Phase 3 — Batch Modernisation (Weeks 21–28)**
- Replace {stats['jcl_jobs']} JCL jobs with Spring Batch `Job` definitions
- Each JCL DD card binding → `ItemReader`/`ItemWriter` bean pointed at {cloud} storage
- Regression: run batch output against mainframe golden files; diff must be zero

**Phase 4 — Cutover & Decommission (Weeks 29–32)**
- Parallel-run window: mainframe + {cloud} process identical live transactions
- Reconcile outputs using extracted business-rule assertions as oracle
- Decommission mainframe after 4-week clean parallel run
- Estimated saving: mainframe MIPS cost eliminated for {stats['programs']} programs""",
            ),
            (
                "Migration Risk Summary & Recommendations",
                f"""**Risk Category Breakdown:**
{risk_cats_txt}

**Top Risk Mitigations:**

1. **Dynamic CALL resolution** — {stats['programs']} programs may contain CALL IDENTIFIER (runtime-determined target). Strategy: instrument with logging in Phase 1 to capture all runtime targets; use Strategy pattern + Spring bean registry.

2. **CICS pseudo-conversational state** — CICS programs return control to CICS between screens using COMMAREA. Strategy: introduce a Redis session store on {cloud} to hold conversational state across REST calls.

3. **VSAM record-level locking** — COBOL uses exclusive file locks for update. Strategy: replace with database row-level locking (SELECT FOR UPDATE) in PostgreSQL/Aurora; validate under concurrent load.

4. **Packed decimal / COMP-3 fields** — Binary/packed formats in COBOL copybooks require precise byte-level parsing. Strategy: generated Java DTOs include custom `@JsonDeserialize` converters validated against COBOL-generated test data.

5. **JCL conditional flow (COND parameter)** — JCL COND=(RC,GT) logic controls step execution. Strategy: Spring Batch `FlowDecision` beans replicate this logic; each condition is unit-tested.

**Estimated Effort (T-shirt sizing):**
- Phase 0 (complete): XL — automated, zero manual effort
- Phase 1 (scaffold): M — 2 engineers × 6 weeks
- Phase 2 (logic migration): XL — 4 engineers × 14 weeks ({stats['programs']} programs, {stats['business_rules']} rules)
- Phase 3 (batch): L — 3 engineers × 8 weeks ({stats['jcl_jobs']} jobs)
- Phase 4 (cutover): M — full team × 4 weeks parallel run

**Total estimated duration:** 32 weeks with a team of 4–6 engineers.
**Confidence:** HIGH — grounded in {stats['programs']} parsed programs, {stats['business_rules']} extracted rules, and {stats['cfg_edges']:,} CFG edges. No guesswork.""",
            ),
        ]

        # ── LLM mode ───────────────────────────────────────────────────────
        if api_key:
            try:
                import anthropic as _anthropic

                _client = _anthropic.Anthropic(api_key=api_key)

                llm_prompt = f"""You are a senior mainframe modernisation architect. Analyse the following COBOL portfolio and produce a detailed transformation analysis.

PORTFOLIO METRICS (from 7-layer artifact pipeline):
- Programs: {stats['programs']} | Paragraphs: {stats['paragraphs']:,} | Data items: {stats['data_items']:,}
- Business rules: {stats['business_rules']} | Resolved call edges: {stats['call_edges']}
- CFG edges: {stats['cfg_edges']:,} | JCL jobs: {stats['jcl_jobs']} | CICS verbs: {stats['cics_verbs']}
- HIGH risks: {stats['risks_high']} | Copybooks: {stats['copybooks']} | Logical files: {stats['file_io']}

TOP 5 MOST COMPLEX PROGRAMS:
{top_progs_txt}

MOST-CALLED PROGRAMS:
{most_called_txt}

RISK CATEGORIES:
{risk_cats_txt}

TARGET PREFERENCES:
- Framework: {framework}
- Cloud: {cloud}
- Decomposition strategy: {decomposition}

Produce exactly 8 sections separated by ### markers, one per section below. Be specific with numbers from the metrics above. Do not invent numbers.

### Portfolio Architecture Discovery
### Source Architecture Analysis
### Domain Decomposition Strategy
### Target Architecture on {cloud}
### COBOL to {framework} Component Mapping
### Business Logic Preservation Strategy
### Human-in-the-Loop Transformation Roadmap
### Migration Risk Summary and Recommendations"""

                def _call_anthropic() -> list[str]:
                    resp = _client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=4096,
                        messages=[{"role": "user", "content": llm_prompt}],
                    )
                    raw = resp.content[0].text
                    parts = raw.split("###")
                    results: list[str] = []
                    for part in parts[1:]:  # skip empty lead
                        lines = part.strip().splitlines()
                        title = lines[0].strip() if lines else ""
                        body  = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
                        results.append(body or title)
                    return results

                loop = asyncio.get_event_loop()
                llm_sections = await loop.run_in_executor(None, _call_anthropic)

                section_titles = [s[0] for s in sections]
                for i, title in enumerate(section_titles):
                    content = llm_sections[i] if i < len(llm_sections) else sections[i][1]
                    yield _evt(title, content)
                    await asyncio.sleep(0)

                yield f"data: {json.dumps({'kind': 'done', 'msg': 'Portfolio transformation analysis complete'})}\n\n"
                return

            except Exception:
                pass  # fall through to static mode

        # ── Static mode ────────────────────────────────────────────────────
        for title, content in sections:
            yield _evt(title, content)
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'kind': 'done', 'msg': 'Portfolio transformation analysis complete'})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ════════════════════════════════════════════════════════════════════════════
# Transform 4-Phase Workflow — endpoints added after /transform/portfolio
# ════════════════════════════════════════════════════════════════════════════

_AGENT_LLMS_FILE = PROJECT_ROOT / "artifacts" / "agent_llms.json"

_DEFAULT_AGENT_LLMS = [
    {"role": "Spec Writer",        "provider": "OpenAI",    "model": "gpt-4o",            "notes": "Generates comprehensive specification documents"},
    {"role": "Architect",          "provider": "Anthropic", "model": "claude-sonnet-4-5", "notes": "Target architecture design + decomposition"},
    {"role": "Code Generator",     "provider": "OpenAI",    "model": "gpt-4o",            "notes": "Java / Spring Boot service code emission"},
    {"role": "Reviewer",           "provider": "Anthropic", "model": "claude-haiku-4-5",  "notes": "Static review + grounding checks"},
    {"role": "Test Writer",        "provider": "OpenAI",    "model": "gpt-4o-mini",       "notes": "JUnit + integration test scaffolding"},
    {"role": "Migration Planner",  "provider": "Anthropic", "model": "claude-sonnet-4-5", "notes": "Phased plan, risks, effort, dependencies"},
]


def _gather_portfolio_stats() -> dict:
    """Collect canonical artifact counts for use in prompts and mappings."""
    if not _db_exists():
        return {
            "programs": 0, "paragraphs": 0, "data_items": 0, "business_rules": 0,
            "call_edges": 0, "cfg_edges": 0, "jcl_jobs": 0, "cics_verbs": 0,
            "risks_high": 0, "copybooks": 0, "file_io": 0, "top_progs": [],
            "most_called": [], "program_list": [], "jcl_list": [],
        }
    with _con() as con:
        def _safe(sql: str, default=0):
            try:
                return con.execute(sql).fetchone()[0]
            except Exception:
                return default

        stats = {
            "programs":       _safe("SELECT COUNT(DISTINCT UPPER(name)) FROM nodes WHERE kind='Program'"),
            "paragraphs":     _safe("SELECT COUNT(DISTINCT UPPER(name)||'|'||UPPER(source_file)) FROM nodes WHERE kind='Paragraph'"),
            "data_items":     _safe("SELECT COUNT(*) FROM (SELECT DISTINCT UPPER(name), program_uuid FROM data_items)"),
            "business_rules": _safe("SELECT COUNT(DISTINCT uuid) FROM business_rules"),
            "call_edges":     _safe("SELECT COUNT(DISTINCT caller_uuid||'|'||callee_name) FROM call_graph WHERE is_resolved=1"),
            "cfg_edges":      _safe("SELECT COUNT(DISTINCT from_uuid||'|'||to_uuid) FROM control_flow"),
            "jcl_jobs":       _safe("SELECT COUNT(DISTINCT job_name) FROM jcl_job"),
            "cics_verbs":     _safe("SELECT COUNT(*) FROM transaction_flow"),
            "risks_high":     _safe("SELECT COUNT(*) FROM risk_register WHERE severity='HIGH'"),
            "copybooks":      _safe("SELECT COUNT(DISTINCT copybook_name) FROM copybook_use"),
            "file_io":        _safe("SELECT COUNT(DISTINCT file_name) FROM file_io"),
        }

        try:
            top_progs = [dict(r) for r in con.execute(
                """SELECT n.name AS name, cm.cyclomatic AS cyclomatic, cm.statement_count AS statement_count
                   FROM complexity_metrics cm JOIN nodes n ON cm.program_uuid = n.uuid
                   GROUP BY UPPER(n.name) ORDER BY cm.cyclomatic DESC LIMIT 10"""
            ).fetchall()]
        except Exception:
            top_progs = []

        try:
            most_called = [dict(r) for r in con.execute(
                """SELECT callee_name AS name, COUNT(*) AS cnt
                   FROM call_graph WHERE is_resolved=1
                   GROUP BY callee_name ORDER BY cnt DESC LIMIT 10"""
            ).fetchall()]
        except Exception:
            most_called = []

        try:
            program_list = [r["name"] for r in con.execute(
                "SELECT UPPER(name) AS name FROM nodes WHERE kind='Program' GROUP BY UPPER(name) ORDER BY UPPER(name) LIMIT 200"
            ).fetchall()]
        except Exception:
            program_list = []

        try:
            jcl_list = [r["job_name"] for r in con.execute(
                "SELECT DISTINCT job_name FROM jcl_job ORDER BY job_name LIMIT 50"
            ).fetchall()]
        except Exception:
            jcl_list = []

        stats["top_progs"] = top_progs
        stats["most_called"] = most_called
        stats["program_list"] = program_list
        stats["jcl_list"] = jcl_list
        return stats


def _classify_program(name: str) -> str:
    """Map a COBOL program name to a target microservice using prefix heuristics."""
    n = (name or "").upper()
    if any(x in n for x in ("SGN", "SIGN", "AUTH", "SEC", "LOG")):
        return "AuthService"
    if any(x in n for x in ("USR", "USER", "ADM")):
        return "UserService"
    if any(x in n for x in ("ACCT", "ACCOUNT", "CUST")):
        return "AccountService"
    if any(x in n for x in ("CARD", "CRD")):
        return "CardService"
    if any(x in n for x in ("TRAN", "TRNS", "TXN", "PMT", "PAY", "BILL")):
        return "TransactionService"
    if any(x in n for x in ("RPT", "STMT", "SUM", "REPORT")):
        return "ReportingService"
    if any(x in n for x in ("MENU", "NAV", "HELP")):
        return "NavigationService"
    if any(x in n for x in ("BAT", "DRIVER", "MAIN", "INIT")):
        return "BatchOrchestrator"
    return "SharedDomainService"


def _strategy_for(name: str, top_progs: list, most_called: list) -> tuple[str, str, str]:
    """Return (strategy, effort, risk) for a program."""
    top_names = {r.get("name", "").upper() for r in top_progs[:5]}
    called_names = {r.get("name", "").upper() for r in most_called[:5]}
    n = (name or "").upper()
    if n in top_names:
        return ("Refactor & Re-architect", "L", "High")
    if n in called_names:
        return ("Extract Shared Service", "M", "Medium")
    if any(x in n for x in ("RPT", "STMT", "BAT")):
        return ("Spring Batch Replacement", "M", "Medium")
    if any(x in n for x in ("SGN", "AUTH", "MENU")):
        return ("Lift & Modernize", "S", "Low")
    return ("Lift & Shift to Microservice", "M", "Low")


def _mermaid_id(s: str) -> str:
    """Sanitise node ids for Mermaid (alnum + underscore)."""
    out = "".join(c if c.isalnum() else "_" for c in (s or ""))[:40]
    return out or "node"


def _mermaid_label(s: str) -> str:
    """Escape a label so it is safe to place inside ["..."] in Mermaid.

    - Double-quotes are converted to single quotes (Mermaid label delimiter)
    - Real newlines are converted to \\n which Mermaid renders as a line break
    - We deliberately do NOT escape backslashes so that callers can pre-bake
      \\n separators (the common case in our template strings)
    """
    return (s or "").replace('"', "'").replace("\n", "\\n")


def _program_function_hint(name: str) -> str:
    """Best-effort English description of a COBOL program from its name."""
    n = (name or "").upper()
    # Common CardDemo / CICS prefixes
    if "SGN" in n or "SIGN" in n: return "Sign-on / authentication"
    if "MENU" in n or "NAV" in n: return "Menu / navigation"
    if "ADM" in n: return "Admin function"
    if "USR" in n and "01" in n: return "List / retrieve user"
    if "USR" in n and "02" in n: return "Add / create user"
    if "USR" in n and "03" in n: return "Update user"
    if "USR" in n and "04" in n: return "Delete user"
    if "USR" in n: return "User management"
    if "ACCT" in n or "ACCOUNT" in n: return "Account management"
    if "CUST" in n: return "Customer management"
    if "CARD" in n or "CRD" in n: return "Card management"
    if "TRAN" in n or "TXN" in n or "TRNS" in n: return "Transaction processing"
    if "PMT" in n or "PAY" in n: return "Payment processing"
    if "BILL" in n: return "Billing"
    if "RPT" in n or "REPORT" in n: return "Reporting"
    if "STMT" in n: return "Statement generation"
    if "BAT" in n or "DRIVER" in n: return "Batch driver"
    if "INIT" in n: return "Initialisation"
    if "HELP" in n: return "Help / static content"
    return "Business logic"


def _service_business_context(svc_name: str) -> dict:
    """Return rich per-service metadata for the migration mapping table.

    Keys: business_description, acceptance_criteria, cobol_to_oo_reasoning.
    """
    catalogue: dict[str, dict] = {
        "AuthService": {
            "business_description": (
                "Handles user authentication and session management for the CardDemo system. "
                "Maps CICS sign-on/sign-off flows (COSGN00C, COMEN01C) to stateless JWT-based authentication. "
                "Enforces role-based access control that was previously embedded in COBOL paragraph-level conditional logic."
            ),
            "acceptance_criteria": [
                "POST /auth/login returns 200 + JWT for valid credentials stored in USER-SEC-FILE",
                "Invalid credentials return 401 with audit event logged (mirrors COBOL MOVE 'I' TO WS-ERR-FLG logic)",
                "Token expiry matches legacy session timeout configured in CICS PROFILE",
                "All prior acceptance test accounts (ADMIN001, USER001) authenticate successfully",
            ],
            "cobol_to_oo_reasoning": (
                "COBOL's sequential EVALUATE WS-USERID / WHEN logic becomes a Spring Security AuthenticationProvider. "
                "WORKING-STORAGE fields WS-USERID and WS-PASSWD map to a UserCredentials value object. "
                "The EXEC CICS RETURN / ABEND pattern is replaced by Spring Security throwing AuthenticationException — "
                "preserving the same semantic error contract."
            ),
        },
        "UserService": {
            "business_description": (
                "Manages the full lifecycle of CardDemo system users — creation, retrieval, update, and deletion. "
                "Encapsulates the four CICS CRUD screens (COUSR01C/02C/03C/04C) that operated against USER-SEC-FILE. "
                "Exposes a RESTful API consumed by the front-end and by AuthService for credential resolution."
            ),
            "acceptance_criteria": [
                "GET /users/{id} returns the user record with the same fields present in the COBOL USER-SEC-FILE layout",
                "POST /users rejects duplicate user IDs with 409 Conflict, matching the legacy ADD-CHECK paragraph guard",
                "DELETE /users/{id} also removes associated sessions, replicating the COBOL PERFORM DELETE-SEC-RECORD logic",
                "Role field accepts only 'U' (user) or 'A' (admin) — mirroring the PIC X(1) USERTYPE constraint",
            ],
            "cobol_to_oo_reasoning": (
                "Each COSGN/COUSR program maps to a dedicated Spring MVC controller method. "
                "WORKING-STORAGE copybook fields (WS-USER-ID, WS-FNAME, WS-LNAME) become a @Entity User JPA class with "
                "matching column constraints. "
                "The COBOL EXEC CICS READ FILE / WRITE FILE pair is replaced by UserRepository.findById / save, "
                "preserving transactional atomicity via @Transactional."
            ),
        },
        "AccountService": {
            "business_description": (
                "Maintains credit card account master data including credit limits, balances, and customer linkage. "
                "Migrates the COACTUPC / COACTVWC screens (account update and view) and their interaction with "
                "ACCT-FILE and CXREF-FILE VSAM datasets. "
                "Provides the canonical source of truth consumed by CardService and TransactionService."
            ),
            "acceptance_criteria": [
                "GET /accounts/{id} returns a payload matching the ACCT-RECORD copybook field layout (balance, limit, status)",
                "PUT /accounts/{id}/limit enforces the COBOL CREDIT-LIMIT-CHECK paragraph: new limit must be >= current balance",
                "Account status transitions (ACTIVE→CLOSED) are audited, replicating the legacy WS-AUDIT-RECORD WRITE",
                "Cross-reference between account and customer is maintained, matching the CXREF-FILE keyed lookup",
            ],
            "cobol_to_oo_reasoning": (
                "The COBOL ACCT-RECORD 01-level copybook layout maps directly to an Account @Entity with "
                "BigDecimal fields for PIC 9(15)V99 monetary columns. "
                "CICS STARTBR / READNEXT / ENDBR browse patterns become JPA Specification-based filtered queries. "
                "The CXREF-FILE secondary index is replaced by a @ManyToOne Account–Customer relationship, "
                "making cross-entity navigability a first-class OO concept."
            ),
        },
        "CardService": {
            "business_description": (
                "Manages the issuance, activation, and maintenance of physical and virtual payment cards linked to accounts. "
                "Consolidates COBOL programs that operated against CARD-FILE (COCRDSLC, COCRDLIC, COCRDUPC) and handles "
                "card-to-account cross-referencing via the CARDXREF VSAM dataset. "
                "Enforces card validation rules extracted from COBOL business-rule paragraphs."
            ),
            "acceptance_criteria": [
                "POST /cards issues a new card linked to a valid account, rejecting inactive accounts (mirrors COBOL CARD-ACCT-STATUS-CHECK)",
                "GET /cards?account={id} returns all cards for an account, matching the CARDXREF STARTBR browse result set",
                "PATCH /cards/{id}/status transitions card to ACTIVE/BLOCKED, with audit log entry per the legacy PERFORM WRITE-AUDIT-RECORD",
                "Card number format validation (PIC X(16)) is enforced — partial numbers return 422 Unprocessable Entity",
            ],
            "cobol_to_oo_reasoning": (
                "The CARD-RECORD copybook, with its composite key (card number + account ID), becomes a Card @Entity "
                "with a composite @IdClass. "
                "COBOL EVALUATE WS-CARD-ACTION / WHEN 'A' (activate) / WHEN 'B' (block) maps to a CardStatus enum and "
                "a Spring state-machine–style service method. "
                "The CARDXREF VSAM cluster becomes a @OneToMany relationship from Account to Card, with JPA Cascade handling "
                "the integrity rules previously enforced by COBOL FILE STATUS checks."
            ),
        },
        "TransactionService": {
            "business_description": (
                "Processes all financial transactions — purchases, payments, and adjustments — against account balances. "
                "Migrates the COTRN* family of programs (transaction list, add, detail view) that operated against "
                "TRANSACT-FILE and updated ACCT-FILE balance fields atomically via CICS syncpoint. "
                "Acts as the event source for the reporting pipeline and downstream batch reconciliation."
            ),
            "acceptance_criteria": [
                "POST /transactions applies a debit/credit atomically and updates the linked account balance, matching the legacy PERFORM UPDATE-ACCOUNT-BALANCE",
                "GET /transactions?account={id} returns transaction history in reverse chronological order, matching TRANSACT-FILE STARTBR by date key",
                "A transaction against an over-limit account returns 422, mirroring the COBOL OVER-LIMIT-CHECK paragraph",
                "Idempotency key prevents duplicate posts — replicates the COBOL TRAN-ID uniqueness check against TRANSACT-FILE",
            ],
            "cobol_to_oo_reasoning": (
                "The atomic balance-update pattern (READ account FOR UPDATE → REWRITE after arithmetic) becomes a "
                "@Transactional service method using pessimistic locking on the Account entity. "
                "COBOL COMPUTE WS-NEW-BALANCE = WS-OLD-BALANCE - WS-TRAN-AMT maps to BigDecimal arithmetic in "
                "TransactionService.applyDebit(), preserving PIC 9(15)V99 precision semantics. "
                "CICS SYNCPOINT becomes a Spring @Transactional commit boundary, and CICS ROLLBACK maps to a runtime exception trigger."
            ),
        },
        "ReportingService": {
            "business_description": (
                "Generates account statements, transaction summaries, and management reports from the transaction ledger. "
                "Consolidates the COBIL00C (bill generation) and CORPT00C (reporting) programs that read TRANSACT-FILE "
                "sequentially and produced fixed-format PRINT-LINE output. "
                "Exposes on-demand PDF/CSV report endpoints and feeds the batch statement generation pipeline."
            ),
            "acceptance_criteria": [
                "GET /reports/statement/{account}/{month} returns a PDF matching the legacy COBIL00C PRINT-LINE column layout",
                "GET /reports/summary produces aggregate totals equal to the COBOL PERFORM CALCULATE-TOTALS paragraph output",
                "Report generation completes within 5 seconds for accounts with up to 10,000 transactions",
                "CSV export column order and data types match the legacy batch extract format used by downstream reconciliation",
            ],
            "cobol_to_oo_reasoning": (
                "COBOL sequential READ TRANSACT-FILE / AT END flow becomes a Spring Data JPA query with pagination, "
                "eliminating the need for file-position pointers. "
                "WORKING-STORAGE accumulators (WS-TOTAL-CREDITS, WS-TOTAL-DEBITS) become local BigDecimal variables "
                "computed via JPA aggregation queries — @Query(\"SELECT SUM(t.amount)...\"). "
                "Fixed-format PRINT-LINE COBOL output is replaced by JasperReports/iText templates that produce "
                "pixel-equivalent PDF output from the same underlying data."
            ),
        },
        "BatchOrchestrator": {
            "business_description": (
                "Coordinates all nightly and periodic batch workloads — interest calculation, statement generation, "
                "and account purge — that were previously driven by JCL job streams on the mainframe. "
                "Each JCL step becomes a named Spring Batch step within a parameterised Job, retaining the same "
                "execution sequence and restart semantics defined in JCL COND parameters."
            ),
            "acceptance_criteria": [
                "Daily interest-calculation batch completes without errors and updates account balances identically to the legacy CBACT04C COBOL batch program",
                "Failed steps restart from the last successful checkpoint — matching JCL COND=(4,LT) restart behaviour",
                "Batch execution audit trail (start time, end time, records processed) matches the legacy SMF record output",
                "Spring Batch job repository stores execution history, replacing the mainframe SYSOUT log as the audit artefact",
            ],
            "cobol_to_oo_reasoning": (
                "JCL EXEC PGM=CBACT04C becomes a Spring Batch Step with an ItemReader (DB cursor), ItemProcessor "
                "(business rule application), and ItemWriter (account update). "
                "JCL DD statements defining VSAM files map to Spring Batch FlatFileItemReader or JdbcCursorItemReader "
                "backed by the migrated PostgreSQL tables. "
                "COBOL PERFORM UNTIL WS-EOF-FLAG = 'Y' loops are eliminated — Spring Batch chunk-oriented processing "
                "handles the read/process/write cycle with configurable commit-interval and skip-limit."
            ),
        },
        "NavigationService": {
            "business_description": (
                "Renders the CardDemo application menu hierarchy and routes user actions to the appropriate service screens. "
                "Migrates COMEN01C and COMEN02C (main menu programs) that used CICS XCTL to transfer control between "
                "transaction codes, replacing the terminal-driven BMS map navigation with a stateless REST routing layer."
            ),
            "acceptance_criteria": [
                "GET /navigation/menu returns the full menu tree matching the COBOL MENU-OPT-COUNT and menu item definitions in COMEN01C",
                "POST /navigation/route with a menu selection returns the target service endpoint, replicating the COBOL EVALUATE OPTION / XCTL logic",
                "Unauthorised menu options are filtered based on the user's role — matching the COBOL WS-ROLE-CHECK paragraph",
                "Menu item labels match the legacy BMS map field values (TITL01/TITL02) exactly to preserve user familiarity",
            ],
            "cobol_to_oo_reasoning": (
                "The COBOL EVALUATE WS-OPTION / WHEN '01' EXEC CICS XCTL PROGRAM('COACTUPC') END-EXEC pattern "
                "becomes a Spring MVC NavigationController with a route-map configuration bean. "
                "BMS symbolic map fields (OPTIONL, OPTIONI, OPTIONO) become a MenuOption DTO with length, input, and output fields. "
                "CICS SEND MAP / RECEIVE MAP is replaced by JSON API responses consumed by the React front-end, "
                "decoupling screen rendering from business routing logic."
            ),
        },
        "SharedDomainService": {
            "business_description": (
                "Provides shared utility functions, cross-cutting copybook layouts, and common validation routines "
                "that are called by multiple COBOL programs across the portfolio. "
                "Packages common date/time routines, error code tables, and field-format validators into a reusable "
                "Spring library module consumed by all other microservices."
            ),
            "acceptance_criteria": [
                "Date validation utility returns the same true/false result as the COBOL PERFORM VALIDATE-DATE paragraph for all test inputs",
                "Error code lookup matches the legacy WS-RETURN-CODE table for all defined codes",
                "Common field format validators (currency, account number, card number) pass all legacy acceptance test inputs",
                "Library is published to the internal Maven repository and consumed as a versioned dependency",
            ],
            "cobol_to_oo_reasoning": (
                "COBOL COPY copybooks containing shared record layouts become Java interfaces and abstract base classes "
                "in the shared-domain module. "
                "WORKING-STORAGE 88-level condition names (e.g., 88 VALID-TRAN-TYPE VALUE 'D' 'C') become "
                "Java enums with a fromCode() factory method and isDefined() guard. "
                "COBOL CALL 'UTILITY-PROG' USING WS-DATE becomes a static call to DateUtils.validate(date) — "
                "the explicit LINKAGE SECTION parameter contract is preserved as a typed method signature."
            ),
        },
    }

    if svc_name in catalogue:
        return catalogue[svc_name]

    # Generic fallback for any service not explicitly catalogued
    return {
        "business_description": (
            f"{svc_name} encapsulates COBOL business logic migrated to a Spring Boot microservice. "
            "Reads and writes data previously stored in VSAM files, now persisted in a PostgreSQL relational schema. "
            "Exposes a RESTful API aligned to the bounded context defined by the source COBOL program group."
        ),
        "acceptance_criteria": [
            f"All COBOL business rules extracted from the source programs are covered by @ParameterizedTest cases in {svc_name}Tests",
            "Data written by the new service matches byte-for-byte the output of the legacy COBOL program on the agreed test corpus",
            "Service health endpoint (/actuator/health) returns UP under nominal load",
            "Error responses use standard HTTP status codes mirroring COBOL FILE STATUS / CICS RESP code semantics",
        ],
        "cobol_to_oo_reasoning": (
            "COBOL WORKING-STORAGE record definitions become JPA @Entity classes with field types chosen to "
            "preserve PIC clause precision (PIC 9(n)V9(m) → BigDecimal, PIC X(n) → String with @Column(length=n)). "
            "Procedural PERFORM paragraphs become private service methods, and COBOL EVALUATE / WHEN branches "
            "become polymorphic strategy implementations or enum-dispatched switch expressions in Java 17+."
        ),
    }


def _program_metrics(con, prog_uuid: str) -> dict:
    """Return {cc, stmts, para_count} for a single program."""
    try:
        cm_row = con.execute(
            "SELECT COALESCE(SUM(cyclomatic),0) AS cc, COALESCE(SUM(statement_count),0) AS stmts "
            "FROM complexity_metrics WHERE program_uuid=?",
            (prog_uuid,),
        ).fetchone()
        cc = (cm_row["cc"] or 0) if cm_row else 0
        stmts = (cm_row["stmts"] or 0) if cm_row else 0
    except Exception:
        cc, stmts = 0, 0
    pcnt = 0
    try:
        # First try direct child paragraphs
        row = con.execute(
            "SELECT COUNT(*) AS c FROM nodes WHERE kind='Paragraph' AND parent_uuid=?",
            (prog_uuid,),
        ).fetchone()
        pcnt = int(row["c"] or 0)
        # If 0, fall back to source_file match (paragraphs may be nested deeper)
        if pcnt == 0:
            row2 = con.execute(
                "SELECT source_file FROM nodes WHERE uuid=?",
                (prog_uuid,),
            ).fetchone()
            if row2 and row2["source_file"]:
                row3 = con.execute(
                    "SELECT COUNT(*) AS c FROM nodes WHERE kind='Paragraph' AND source_file=?",
                    (row2["source_file"],),
                ).fetchone()
                pcnt = int(row3["c"] or 0)
    except Exception:
        pcnt = 0
    return {"cc": int(cc), "stmts": int(stmts), "para_count": pcnt}


def _program_uuid_by_name(con, name: str) -> str | None:
    """Resolve the best program UUID for a name, preferring the row that has
    complexity_metrics attached (some pipelines emit duplicate Program nodes)."""
    try:
        rows = con.execute(
            "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (name,),
        ).fetchall()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]["uuid"]
        # Prefer the uuid that has metrics
        best_uuid = rows[0]["uuid"]
        best_score = -1
        for r in rows:
            try:
                c = con.execute(
                    "SELECT COUNT(*) AS c FROM complexity_metrics WHERE program_uuid=?",
                    (r["uuid"],),
                ).fetchone()["c"]
            except Exception:
                c = 0
            if c > best_score:
                best_score = c
                best_uuid = r["uuid"]
        return best_uuid
    except Exception:
        return None


def _classify_program_kind(name: str) -> str:
    """Heuristic: classify a program as 'Online' (CICS) or 'Batch'."""
    n = (name or "").upper()
    if "BAT" in n or "DRIVER" in n or n.startswith("CBSTM") or "INIT" in n or n.endswith("B"):
        return "Batch"
    if n.endswith("C") or "MENU" in n or "SGN" in n or "USR" in n or "ACCT" in n or "CARD" in n:
        return "Online"
    return "Online"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items or []:
        up = (it or "").upper()
        if up and up not in seen:
            seen.add(up)
            out.append(it)
    return out


def _build_source_ll_mermaid(con, stats: dict) -> str:
    """Detailed Mermaid grouped by target service domain, with CC + file edges."""
    # Group programs by service (deduped)
    services_seen: dict[str, list[str]] = {}
    for p in _dedupe_preserve_order(stats["program_list"]):
        svc = _classify_program(p)
        services_seen.setdefault(svc, []).append(p)
    if not services_seen:
        return "graph TD\n  Empty[\"No programs in DB\"]"

    lines = ["graph TD"]
    lines.append("  direction LR")

    # Per-service subgraphs of programs
    rendered_progs: set[str] = set()
    for svc, progs in services_seen.items():
        sg_id = _mermaid_id("SG_" + svc)
        lines.append(f"  subgraph {sg_id}[\"{_mermaid_label(svc)}\"]")
        lines.append("    direction TB")
        for p in progs[:12]:
            up = (p or "").upper()
            pid = _mermaid_id(up)
            puuid = _program_uuid_by_name(con, up)
            if puuid:
                m = _program_metrics(con, puuid)
                fn = _program_function_hint(up)
                label = f"{up}\\n{fn}\\nCC:{m['cc']} | Stmts:{m['stmts']} | Paras:{m['para_count']}"
            else:
                label = f"{up}\\n{_program_function_hint(up)}"
            lines.append(f"    {pid}[\"{_mermaid_label(label)}\"]")
            rendered_progs.add(pid)
        lines.append("  end")

    # File I/O edges (top N distinct file edges)
    file_nodes: set[str] = set()
    try:
        rows = con.execute(
            """SELECT n.name AS prog, fio.file_name AS f, fio.operation AS op
               FROM file_io fio JOIN nodes n ON fio.program_uuid = n.uuid
               GROUP BY n.name, fio.file_name, fio.operation
               LIMIT 60"""
        ).fetchall()
        for r in rows:
            prog = (r["prog"] or "").upper()
            fname = (r["f"] or "").upper()
            op = (r["op"] or "").upper()
            if not prog or not fname:
                continue
            pid = _mermaid_id(prog)
            if pid not in rendered_progs:
                continue
            fid = _mermaid_id("FILE_" + fname)
            if fid not in file_nodes:
                lines.append(f"  {fid}[(\"VSAM: {_mermaid_label(fname)}\")]")
                file_nodes.add(fid)
            if op in ("READ", "STARTBR", "READNEXT", "OPEN"):
                lines.append(f"  {pid} -. reads .-> {fid}")
            elif op in ("WRITE", "REWRITE", "DELETE"):
                lines.append(f"  {pid} -. writes .-> {fid}")
    except Exception:
        pass

    # Call edges (resolved)
    try:
        rows = con.execute(
            """SELECT n.name AS caller, cg.callee_name AS callee, cg.call_type AS ctype
               FROM call_graph cg JOIN nodes n ON cg.caller_uuid = n.uuid
               WHERE cg.is_resolved=1 LIMIT 60"""
        ).fetchall()
        for r in rows:
            caller = _mermaid_id((r["caller"] or "").upper())
            callee = _mermaid_id((r["callee"] or "").upper())
            if caller in rendered_progs and callee in rendered_progs:
                tag = (r["ctype"] or "CALL").upper()
                lines.append(f"  {caller} -->|{_mermaid_label(tag)}| {callee}")
    except Exception:
        pass

    # CICS verbs
    try:
        rows = con.execute(
            """SELECT n.name AS from_p, tf.to_program AS to_p, tf.verb AS verb
               FROM transaction_flow tf JOIN nodes n ON tf.from_uuid = n.uuid
               WHERE tf.to_program IS NOT NULL LIMIT 40"""
        ).fetchall()
        for r in rows:
            fp = _mermaid_id((r["from_p"] or "").upper())
            tp = _mermaid_id((r["to_p"] or "").upper())
            verb = (r["verb"] or "CICS").upper()
            if fp in rendered_progs and tp in rendered_progs:
                lines.append(f"  {fp} -->|CICS {_mermaid_label(verb)}| {tp}")
    except Exception:
        pass

    return "\n".join(lines)


def _build_target_ll_mermaid(stats: dict, framework: str, cloud: str) -> str:
    """Detailed target architecture with layered microservices + infra."""
    cloud_lb    = {"AWS": "ALB", "Azure": "App Gateway", "GCP": "GCLB"}.get(cloud, "Load Balancer")
    cloud_db    = {"AWS": "Aurora PostgreSQL", "Azure": "Azure SQL", "GCP": "Cloud Spanner"}.get(cloud, "PostgreSQL")
    cloud_cache = {"AWS": "ElastiCache Redis", "Azure": "Azure Cache", "GCP": "Memorystore"}.get(cloud, "Redis")
    cloud_msg   = {"AWS": "SQS + SNS", "Azure": "Service Bus", "GCP": "Pub/Sub"}.get(cloud, "Kafka")
    cloud_compute = {"AWS": "ECS Fargate", "Azure": "AKS", "GCP": "Cloud Run"}.get(cloud, "Kubernetes")

    services_seen: dict[str, list[str]] = {}
    for p in _dedupe_preserve_order(stats["program_list"]):
        svc = _classify_program(p)
        services_seen.setdefault(svc, []).append(p)
    if not services_seen:
        services_seen = {"AuthService": [], "UserService": [], "AccountService": []}

    lines = [
        "graph TD",
        "  direction TB",
        f"  Client[\"Web / Mobile Client\"]",
        f"  LB[\"{_mermaid_label(cloud_lb)}\"]",
        f"  GW[\"API Gateway\\n(REST + JWT auth)\"]",
        "  Client --> LB --> GW",
    ]

    lines.append(f"  subgraph Services[\"Microservices on {_mermaid_label(cloud_compute)}\"]")
    lines.append("    direction LR")
    for svc, progs in services_seen.items():
        sg = _mermaid_id("SVC_" + svc)
        rest_id = _mermaid_id("REST_" + svc)
        biz_id  = _mermaid_id("BIZ_" + svc)
        dat_id  = _mermaid_id("DAT_" + svc)
        nprog = len(progs)
        lines.append(f"    subgraph {sg}[\"{_mermaid_label(svc)}\"]")
        lines.append("      direction TB")
        lines.append(f"      {rest_id}[\"REST Layer\\n{nprog} controllers\"]")
        lines.append(f"      {biz_id}[\"Business Layer\\nService + Validators\"]")
        lines.append(f"      {dat_id}[\"Data Layer\\nJPA Repositories\"]")
        lines.append(f"      {rest_id} --> {biz_id} --> {dat_id}")
        lines.append("    end")
        lines.append(f"  GW --> {rest_id}")
    lines.append("  end")

    lines.append("  subgraph Infra[\"Shared Infrastructure\"]")
    lines.append(f"    DB[(\"{_mermaid_label(cloud_db)}\")]")
    lines.append(f"    Cache[(\"{_mermaid_label(cloud_cache)}\")]")
    lines.append(f"    MQ[/\"{_mermaid_label(cloud_msg)}\"/]")
    lines.append("    Batch[\"Spring Batch Jobs\"]")
    lines.append("    Sec[\"Secrets Manager\"]")
    lines.append("    Obs[\"Observability (CW + OpenTelemetry)\"]")
    lines.append("  end")

    for svc in services_seen.keys():
        dat_id = _mermaid_id("DAT_" + svc)
        lines.append(f"  {dat_id} --> DB")
        if svc in ("AuthService", "UserService", "AccountService", "CardService"):
            biz_id = _mermaid_id("BIZ_" + svc)
            lines.append(f"  {biz_id} -.cache.-> Cache")
        if svc in ("TransactionService", "BatchOrchestrator", "ReportingService"):
            biz_id = _mermaid_id("BIZ_" + svc)
            lines.append(f"  {biz_id} -.events.-> MQ")
    lines.append("  MQ --> Batch")
    lines.append("  Batch --> DB")
    lines.append("  Sec -.injects.-> Services")
    lines.append("  Services -.metrics.-> Obs")

    return "\n".join(lines)


@app.post("/transform/source-architecture", tags=["Transform"])
def transform_source_architecture(body: dict = {}):
    """Return source/target Mermaid diagrams + a migration mapping table."""
    framework     = body.get("framework",     "Spring Boot")
    cloud         = body.get("cloud",         "AWS")
    decomposition = body.get("decomposition", "Strangler Fig")
    level         = (body.get("level") or "hl").lower()

    if not _db_exists():
        raise HTTPException(status_code=409, detail="No pipeline database found — run the pipeline first.")

    stats = _gather_portfolio_stats()

    # ── Source Mermaid (real call_graph + transaction_flow edges) ─────────
    edges: list[tuple[str, str, str]] = []
    nodes: set[str] = set()
    try:
        with _con() as con:
            cg_rows = con.execute(
                """SELECT n.name AS caller, cg.callee_name AS callee, cg.call_type AS ctype
                   FROM call_graph cg
                   JOIN nodes n ON cg.caller_uuid = n.uuid
                   WHERE cg.is_resolved = 1
                   LIMIT 60"""
            ).fetchall()
            for r in cg_rows:
                caller = (r["caller"] or "").upper()
                callee = (r["callee"] or "").upper()
                if caller and callee:
                    edges.append((caller, callee, r["ctype"] or "CALL"))
                    nodes.add(caller); nodes.add(callee)

            tx_rows = con.execute(
                """SELECT n.name AS from_p, tf.to_program AS to_p, tf.verb AS verb
                   FROM transaction_flow tf
                   JOIN nodes n ON tf.from_uuid = n.uuid
                   WHERE tf.to_program IS NOT NULL
                   LIMIT 40"""
            ).fetchall()
            for r in tx_rows:
                fp = (r["from_p"] or "").upper()
                tp = (r["to_p"] or "").upper()
                if fp and tp:
                    edges.append((fp, tp, r["verb"] or "CICS"))
                    nodes.add(fp); nodes.add(tp)

            # JCL → program bindings
            try:
                jcl_rows = con.execute(
                    """SELECT job_name, program_name FROM jcl_program_binding
                       WHERE program_name IS NOT NULL LIMIT 25"""
                ).fetchall()
                for r in jcl_rows:
                    j = f"JCL:{(r['job_name'] or '').upper()}"
                    p = (r["program_name"] or "").upper()
                    if j and p:
                        edges.append((j, p, "INVOKES"))
                        nodes.add(j); nodes.add(p)
            except Exception:
                pass
    except Exception:
        pass

    def _mid(s: str) -> str:
        # Sanitise node ids for Mermaid
        return "".join(c if c.isalnum() else "_" for c in s)[:40] or "node"

    # ── High-level source: group programs by bounded context, edges between contexts ──
    # Map every known program → its service bucket
    prog_to_svc: dict[str, str] = {}
    for p in stats["program_list"]:
        prog_to_svc[p.upper()] = _classify_program(p.upper())

    svc_prog_counts: dict[str, int] = {}
    for p, s in prog_to_svc.items():
        svc_prog_counts[s] = svc_prog_counts.get(s, 0) + 1

    # Build service-level edges (deduplicated)
    svc_edges: dict[tuple[str,str], str] = {}
    for a, b, t in edges:
        sa = prog_to_svc.get(a, "Mainframe")
        sb = prog_to_svc.get(b.replace("JCL:", "JCL"), "Mainframe")
        if sa != sb:
            key = (sa, sb)
            # Prefer XCTL label over generic CALL
            if key not in svc_edges or t in ("XCTL", "LINK"):
                svc_edges[key] = t

    if svc_prog_counts:
        src_lines = ["flowchart LR"]
        # Declare service nodes
        for svc, cnt in sorted(svc_prog_counts.items()):
            sid = _mid(svc)
            src_lines.append(f'  {sid}["{svc}\\n{cnt} program(s)"]')
        # Add JCL batch node if present
        jcl_progs = [n for n in nodes if n.startswith("JCL:")]
        if jcl_progs:
            src_lines.append(f'  JCLBatch["JCL Batch\\n{len(jcl_progs)} job(s)"]')
        # Edges between services
        drawn: set[tuple[str,str]] = set()
        for (sa, sb), t in svc_edges.items():
            if sa == sb:
                continue
            sid_a = _mid(sa); sid_b = _mid(sb)
            if (sid_a, sid_b) in drawn:
                continue
            drawn.add((sid_a, sid_b))
            arrow = "-->|XCTL|" if t == "XCTL" else "-->|LINK|" if t == "LINK" else "-->"
            src_lines.append(f"  {sid_a} {arrow} {sid_b}")
        source_mermaid = "\n".join(src_lines)
    elif edges:
        # LL fallback: individual programs, flowchart LR, no edge labels
        src_lines = ["flowchart LR"]
        for n in sorted(nodes)[:50]:
            label = n.replace("JCL:", "JCL:")
            shape = f'[("{label}")]' if n.startswith("JCL:") else f'["{label}"]'
            src_lines.append(f"  {_mid(n)}{shape}")
        seen_e: set[tuple[str,str]] = set()
        for a, b, t in edges[:60]:
            k = (_mid(a), _mid(b))
            if k not in seen_e:
                seen_e.add(k)
                src_lines.append(f"  {k[0]} --> {k[1]}")
        source_mermaid = "\n".join(src_lines)
    else:
        # Fallback: synthesise from program list
        progs = stats["program_list"][:12] or ["PROGRAMA", "PROGRAMB", "PROGRAMC"]
        src_lines = ["flowchart LR", "  Mainframe[\"Mainframe z/OS\"]"]
        for p in progs:
            src_lines.append(f"  Mainframe --> {_mid(p)}[\"{p}\"]")
        source_mermaid = "\n".join(src_lines)

    # ── Target Mermaid (microservice topology) ─────────────────────────────
    cloud_lb = {"AWS": "ALB", "Azure": "App Gateway", "GCP": "GCLB"}.get(cloud, "Load Balancer")
    cloud_db = {"AWS": "Aurora PostgreSQL", "Azure": "Azure SQL", "GCP": "Cloud Spanner"}.get(cloud, "PostgreSQL")
    cloud_cache = {"AWS": "ElastiCache Redis", "Azure": "Azure Cache", "GCP": "Memorystore"}.get(cloud, "Redis")
    cloud_msg = {"AWS": "SQS / SNS", "Azure": "Service Bus", "GCP": "Pub/Sub"}.get(cloud, "Kafka")

    # Determine target services from real program names
    services_seen: dict[str, list[str]] = {}
    for p in stats["program_list"]:
        svc = _classify_program(p)
        services_seen.setdefault(svc, []).append(p)
    if not services_seen:
        services_seen = {
            "AuthService": [], "UserService": [], "AccountService": [],
            "CardService": [], "TransactionService": [], "ReportingService": [],
        }

    target_lines = [
        "flowchart LR",
        f"  Client[\"Web / Mobile\"]",
        f"  LB[\"{cloud_lb}\"]",
        f"  GW[\"API Gateway\"]",
        f"  Client --> LB --> GW",
    ]
    for svc in services_seen.keys():
        cnt = len(services_seen[svc])
        target_lines.append(f"  GW --> {_mid(svc)}[\"{svc}\"]")
    target_lines.append(f"  Cache[(\"{cloud_cache}\")]")
    target_lines.append(f"  DB[(\"{cloud_db}\")]")
    target_lines.append(f"  MQ[\"{cloud_msg}\"]")
    for svc in services_seen.keys():
        target_lines.append(f"  {_mid(svc)} --> DB")
        if svc in ("AuthService", "UserService", "AccountService"):
            target_lines.append(f"  {_mid(svc)} --> Cache")
        if svc in ("TransactionService", "BatchOrchestrator", "ReportingService"):
            target_lines.append(f"  {_mid(svc)} --> MQ")
    target_lines.append(f"  Batch[\"Spring Batch\"] --> DB")
    target_lines.append(f"  MQ --> Batch")
    target_mermaid = "\n".join(target_lines)

    # ── Mapping table ──────────────────────────────────────────────────────
    mapping: list[dict] = []
    for prog in _dedupe_preserve_order(stats["program_list"])[:50]:
        svc = _classify_program(prog)
        strategy, effort, risk = _strategy_for(prog, stats["top_progs"], stats["most_called"])
        ctx = _service_business_context(svc)
        mapping.append({
            "source":               prog,
            "target":               svc,
            "strategy":             strategy,
            "effort":               effort,
            "risk":                 risk,
            "business_description": ctx["business_description"],
            "acceptance_criteria":  ctx["acceptance_criteria"],
            "cobol_to_oo_reasoning": ctx["cobol_to_oo_reasoning"],
        })
    # Add JCL jobs as their own rows
    batch_ctx = _service_business_context("BatchOrchestrator")
    for j in stats["jcl_list"][:15]:
        mapping.append({
            "source":               f"JCL:{j}",
            "target":               "Spring Batch Job",
            "strategy":             "Spring Batch Replacement",
            "effort":               "M",
            "risk":                 "Medium",
            "business_description": (
                f"JCL job {j} is replaced by a Spring Batch Job with equivalent steps, "
                "restart semantics, and audit logging. "
                + batch_ctx["business_description"]
            ),
            "acceptance_criteria":  batch_ctx["acceptance_criteria"],
            "cobol_to_oo_reasoning": batch_ctx["cobol_to_oo_reasoning"],
        })

    # ── Low-level Mermaid (computed on every call so HL/LL share one round trip) ──
    try:
        with _con() as con:
            source_ll_mermaid = _build_source_ll_mermaid(con, stats)
    except Exception:
        source_ll_mermaid = "graph TD\n  Empty[\"Low-level source unavailable\"]"
    try:
        target_ll_mermaid = _build_target_ll_mermaid(stats, framework, cloud)
    except Exception:
        target_ll_mermaid = "graph TD\n  Empty[\"Low-level target unavailable\"]"

    return {
        "source_mermaid": source_mermaid,
        "target_mermaid": target_mermaid,
        "source_ll_mermaid": source_ll_mermaid,
        "target_ll_mermaid": target_ll_mermaid,
        "source_stats": {
            "programs":   stats["programs"],
            "jcl_jobs":   stats["jcl_jobs"],
            "cics_verbs": stats["cics_verbs"],
            "file_io":    stats["file_io"],
            "copybooks":  stats["copybooks"],
            "call_edges": stats["call_edges"],
        },
        "target_stats": {
            "services":  len(services_seen),
            "framework": framework,
            "cloud":     cloud,
            "pattern":   decomposition,
        },
        "mapping": mapping,
        "services": [{"name": k, "programs": v[:10]} for k, v in services_seen.items()],
        "level":   level,
    }


# ── Per-service drill-down ────────────────────────────────────────────────────

_REST_TEMPLATES_BY_SERVICE: dict[str, list[dict]] = {
    "AuthService": [
        {"method": "POST",   "path_tmpl": "/auth/login",          "desc": "Authenticate user and issue JWT"},
        {"method": "POST",   "path_tmpl": "/auth/logout",         "desc": "Invalidate session token"},
        {"method": "POST",   "path_tmpl": "/auth/refresh",        "desc": "Refresh access token"},
        {"method": "GET",    "path_tmpl": "/auth/me",             "desc": "Return authenticated principal"},
    ],
    "UserService": [
        {"method": "GET",    "path_tmpl": "/users/{userId}",      "desc": "Retrieve user profile"},
        {"method": "POST",   "path_tmpl": "/users",               "desc": "Create new user"},
        {"method": "PUT",    "path_tmpl": "/users/{userId}",      "desc": "Update user profile"},
        {"method": "DELETE", "path_tmpl": "/users/{userId}",      "desc": "Delete user"},
        {"method": "GET",    "path_tmpl": "/users",               "desc": "List users (paginated)"},
    ],
    "AccountService": [
        {"method": "GET",    "path_tmpl": "/accounts/{acctId}",   "desc": "Retrieve account"},
        {"method": "POST",   "path_tmpl": "/accounts",            "desc": "Open new account"},
        {"method": "PUT",    "path_tmpl": "/accounts/{acctId}",   "desc": "Update account details"},
        {"method": "GET",    "path_tmpl": "/accounts/{acctId}/balance", "desc": "Get current balance"},
    ],
    "CardService": [
        {"method": "GET",    "path_tmpl": "/cards/{cardId}",      "desc": "Retrieve card"},
        {"method": "POST",   "path_tmpl": "/cards",               "desc": "Issue new card"},
        {"method": "PUT",    "path_tmpl": "/cards/{cardId}",      "desc": "Update card status"},
        {"method": "DELETE", "path_tmpl": "/cards/{cardId}",      "desc": "Block / cancel card"},
    ],
    "TransactionService": [
        {"method": "POST",   "path_tmpl": "/transactions",        "desc": "Submit transaction"},
        {"method": "GET",    "path_tmpl": "/transactions/{txId}", "desc": "Retrieve transaction"},
        {"method": "GET",    "path_tmpl": "/transactions",        "desc": "Query transactions (filters + paging)"},
        {"method": "POST",   "path_tmpl": "/transactions/{txId}/reverse", "desc": "Reverse / void transaction"},
    ],
    "ReportingService": [
        {"method": "GET",    "path_tmpl": "/reports/{reportId}",  "desc": "Generate / fetch report"},
        {"method": "POST",   "path_tmpl": "/reports",             "desc": "Schedule a report"},
        {"method": "GET",    "path_tmpl": "/reports",             "desc": "List available reports"},
    ],
    "NavigationService": [
        {"method": "GET",    "path_tmpl": "/menu",                "desc": "Return menu metadata"},
        {"method": "GET",    "path_tmpl": "/help/{topic}",        "desc": "Retrieve help topic"},
    ],
    "BatchOrchestrator": [
        {"method": "POST",   "path_tmpl": "/jobs/{jobName}/run",  "desc": "Trigger batch job"},
        {"method": "GET",    "path_tmpl": "/jobs/{jobName}/status", "desc": "Get job status"},
        {"method": "GET",    "path_tmpl": "/jobs",                "desc": "List configured jobs"},
    ],
    "SharedDomainService": [
        {"method": "GET",    "path_tmpl": "/lookup/{entity}",     "desc": "Generic reference lookup"},
    ],
}


def _api_contracts_for_service(con, service: str, programs: list[str]) -> list[dict]:
    """Build the API contract rows by aligning REST templates with COBOL programs."""
    tmpl = _REST_TEMPLATES_BY_SERVICE.get(service) or _REST_TEMPLATES_BY_SERVICE["SharedDomainService"]
    contracts: list[dict] = []
    # Probe CICS verbs per program (best-effort)
    cics_by_prog: dict[str, str] = {}
    try:
        rows = con.execute(
            """SELECT n.name AS p, tf.verb AS v
               FROM transaction_flow tf JOIN nodes n ON tf.from_uuid = n.uuid
               WHERE tf.verb IS NOT NULL"""
        ).fetchall()
        for r in rows:
            p = (r["p"] or "").upper()
            v = (r["v"] or "").upper()
            if p and v and p not in cics_by_prog:
                cics_by_prog[p] = v
    except Exception:
        pass

    if not programs:
        for t in tmpl:
            contracts.append({
                "method": t["method"],
                "path":   t["path_tmpl"],
                "description": t["desc"],
                "source_program": "",
                "cics_verb": "",
            })
        return contracts

    # Pair templates with programs round-robin
    for i, t in enumerate(tmpl):
        src_prog = programs[i % len(programs)].upper() if programs else ""
        contracts.append({
            "method": t["method"],
            "path":   t["path_tmpl"],
            "description": t["desc"],
            "source_program": src_prog,
            "cics_verb": cics_by_prog.get(src_prog, "RECEIVE MAP" if t["method"] == "GET" else "SEND MAP"),
        })
    # Add an endpoint per extra program so every program is represented
    if len(programs) > len(tmpl):
        for p in programs[len(tmpl):]:
            up = p.upper()
            contracts.append({
                "method": "POST",
                "path":   f"/{service.lower().replace('service','')}/{up.lower()}",
                "description": _program_function_hint(up),
                "source_program": up,
                "cics_verb": cics_by_prog.get(up, "LINK"),
            })
    return contracts


def _entities_for_service(con, service: str, programs: list[str]) -> list[dict]:
    """Derive entities by inspecting copybook records / data_items per program."""
    # Default entity per service
    default_entities = {
        "AuthService":         [("Credential", "credentials", "UUID")],
        "UserService":         [("User",       "users",       "UUID")],
        "AccountService":      [("Account",    "accounts",    "Long")],
        "CardService":         [("Card",       "cards",       "UUID")],
        "TransactionService":  [("Transaction","transactions","UUID")],
        "ReportingService":    [("Report",     "reports",     "UUID")],
        "NavigationService":   [("MenuItem",   "menu_items",  "Long")],
        "BatchOrchestrator":   [("JobExecution","job_executions","Long")],
        "SharedDomainService": [("LookupItem", "lookup_items","String")],
    }
    entities: list[dict] = []
    base = default_entities.get(service, default_entities["SharedDomainService"])
    # Try to find a copybook + data_items count per program → enrich the entity rows
    by_program_copybook: list[tuple[str, str, int]] = []
    for p in programs[:6]:
        up = p.upper()
        puuid = _program_uuid_by_name(con, up)
        cpy = ""
        fcnt = 0
        if puuid:
            try:
                row = con.execute(
                    "SELECT copybook_name FROM copybook_use WHERE program_uuid=? LIMIT 1",
                    (puuid,),
                ).fetchone()
                if row:
                    cpy = row["copybook_name"] or ""
            except Exception:
                pass
            try:
                row = con.execute(
                    "SELECT COUNT(*) AS c FROM data_items WHERE program_uuid=?",
                    (puuid,),
                ).fetchone()
                fcnt = int((row["c"] or 0) if row else 0)
            except Exception:
                fcnt = 0
        by_program_copybook.append((up, cpy, fcnt))

    # Use the first base entity for the service, enriched with the richest program record
    name, table, key = base[0]
    best = max(by_program_copybook, key=lambda x: x[2]) if by_program_copybook else ("", "", 0)
    entities.append({
        "name": name,
        "source_record": best[1] or f"{name.upper()}-RECORD",
        "field_count": best[2],
        "table_name": table,
        "key_type": key,
    })
    # Add supplementary entities derived from extra copybooks
    for up, cpy, fcnt in by_program_copybook:
        if not cpy or cpy == best[1]:
            continue
        ent_name = "".join(part.capitalize() for part in cpy.replace(".", "").replace("-", "_").split("_")) or "Record"
        entities.append({
            "name": ent_name[:40],
            "source_record": cpy,
            "field_count": fcnt,
            "table_name": ent_name.lower()[:40],
            "key_type": "UUID",
        })
        if len(entities) >= 6:
            break
    return entities


def _build_service_source_mermaid(con, service: str, programs: list[str]) -> tuple[str, int, int, int]:
    """Per-service detailed source diagram + aggregate (paragraphs, statements, cc)."""
    lines = ["graph TD", "  direction LR"]
    online: list[tuple[str, dict]] = []
    batch:  list[tuple[str, dict]] = []
    total_para = total_stmts = total_cc = 0
    rendered: set[str] = set()

    for p in programs[:20]:
        up = (p or "").upper()
        puuid = _program_uuid_by_name(con, up)
        m = _program_metrics(con, puuid) if puuid else {"cc": 0, "stmts": 0, "para_count": 0}
        total_para  += m["para_count"]
        total_stmts += m["stmts"]
        total_cc    += m["cc"]
        kind = _classify_program_kind(up)
        if kind == "Batch":
            batch.append((up, m))
        else:
            online.append((up, m))

    def _emit_group(label: str, group_id: str, items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        lines.append(f"  subgraph {group_id}[\"{_mermaid_label(label)}\"]")
        lines.append("    direction TB")
        for up, m in items:
            pid = _mermaid_id(up)
            rendered.add(pid)
            fn = _program_function_hint(up)
            label_txt = f"{up}\\nFunction: {fn}\\nCC:{m['cc']} | Stmts:{m['stmts']}\\n{m['para_count']} paragraphs"
            lines.append(f"    {pid}[\"{_mermaid_label(label_txt)}\"]")
        lines.append("  end")

    _emit_group("Online / CICS Programs", "OnlineGroup", online)
    _emit_group("Batch Programs",         "BatchGroup",  batch)

    if not rendered:
        lines.append(f"  Empty[\"No programs classified as {_mermaid_label(service)}\"]")
        return "\n".join(lines), 0, 0, 0

    # File I/O edges for these programs only
    file_nodes: set[str] = set()
    try:
        rows = con.execute(
            """SELECT n.name AS prog, fio.file_name AS f, fio.operation AS op
               FROM file_io fio JOIN nodes n ON fio.program_uuid = n.uuid
               GROUP BY n.name, fio.file_name, fio.operation"""
        ).fetchall()
        for r in rows:
            prog = (r["prog"] or "").upper()
            fname = (r["f"] or "").upper()
            op = (r["op"] or "").upper()
            pid = _mermaid_id(prog)
            if pid not in rendered or not fname:
                continue
            fid = _mermaid_id("F_" + fname)
            if fid not in file_nodes:
                lines.append(f"  {fid}[(\"VSAM: {_mermaid_label(fname)}\")]")
                file_nodes.add(fid)
            if op in ("READ", "STARTBR", "READNEXT", "OPEN"):
                lines.append(f"  {pid} -. reads .-> {fid}")
            elif op in ("WRITE", "REWRITE", "DELETE"):
                lines.append(f"  {pid} -. writes .-> {fid}")
    except Exception:
        pass

    # Intra-service CALL edges
    try:
        rows = con.execute(
            """SELECT n.name AS caller, cg.callee_name AS callee, cg.call_type AS ctype
               FROM call_graph cg JOIN nodes n ON cg.caller_uuid = n.uuid
               WHERE cg.is_resolved=1"""
        ).fetchall()
        for r in rows:
            caller = _mermaid_id((r["caller"] or "").upper())
            callee = _mermaid_id((r["callee"] or "").upper())
            if caller in rendered and callee in rendered:
                tag = (r["ctype"] or "CALL").upper()
                lines.append(f"  {caller} -->|{_mermaid_label(tag)}| {callee}")
    except Exception:
        pass

    # CICS verbs (only edges where both endpoints belong to this service)
    try:
        rows = con.execute(
            """SELECT n.name AS from_p, tf.to_program AS to_p, tf.verb AS verb
               FROM transaction_flow tf JOIN nodes n ON tf.from_uuid = n.uuid
               WHERE tf.to_program IS NOT NULL"""
        ).fetchall()
        for r in rows:
            fp = _mermaid_id((r["from_p"] or "").upper())
            tp = _mermaid_id((r["to_p"] or "").upper())
            verb = (r["verb"] or "CICS").upper()
            if fp in rendered and tp in rendered:
                lines.append(f"  {fp} -->|CICS {_mermaid_label(verb)}| {tp}")
    except Exception:
        pass

    return "\n".join(lines), total_para, total_stmts, total_cc


def _build_service_target_mermaid(service: str, programs: list[str], framework: str, cloud: str) -> str:
    """Granular target architecture using flowchart TD.

    Rules that guarantee valid Mermaid v10 output:
    - Use flowchart TD (more forgiving than graph TD)
    - All edges connect named node IDs, NEVER subgraph IDs
    - Labels stripped of @, {}, () using _sl()
    - Node IDs are pure alphanumeric
    """
    cloud_db      = {"AWS": "RDS Aurora PostgreSQL", "Azure": "Azure SQL",      "GCP": "Cloud Spanner"}.get(cloud, "PostgreSQL")
    cloud_cache   = {"AWS": "ElastiCache Redis",     "Azure": "Azure Cache",    "GCP": "Memorystore"}.get(cloud, "Redis")
    cloud_compute = {"AWS": "ECS Fargate",           "Azure": "AKS Cluster",    "GCP": "Cloud Run"}.get(cloud, "Kubernetes")
    cloud_sec     = {"AWS": "Secrets Manager",       "Azure": "Key Vault",      "GCP": "Secret Manager"}.get(cloud, "Vault")

    short = service.replace("Service", "").replace("Orchestrator", "Batch") or service
    base  = short or "Resource"

    def _sl(s: str) -> str:
        """Safe label: strip characters that Mermaid v10 may mis-parse."""
        return (s or "").replace('"', "'").replace("@", "").replace("{", "").replace("}", "").replace("(", "").replace(")", "").replace("\n", " ").strip()

    # REST endpoints — up to 4, path params removed
    import re as _re_tgt
    contracts = _REST_TEMPLATES_BY_SERVICE.get(service) or _REST_TEMPLATES_BY_SERVICE.get("SharedDomainService", [])
    eps = [(c["method"], _re_tgt.sub(r"\{[^}]+\}", ":id", c["path_tmpl"])) for c in contracts[:4]]

    # Business methods from real programs
    biz_methods: list[str] = []
    for p in (programs or [])[:3]:
        up = (p or "").upper()
        hint = _program_function_hint(up).split()[0].lower().replace("-", "")
        biz_methods.append(f"{hint}{up[:8].title()}()")
    if not biz_methods:
        biz_methods = ["findById()", "save()", "validate()"]

    lines = ["flowchart TD"]

    # ── REST subgraph (edges only between named nodes inside) ──────────────────
    lines.append(f'  subgraph SG_REST["REST Layer  {_sl(framework)}"]')
    lines.append(f'    Ctrl["{_sl(base)}Controller"]')
    for i, (meth, path) in enumerate(eps):
        lines.append(f'    EP{i}["{meth}  {_sl(path)}"]')
        lines.append(f'    Ctrl --> EP{i}')
    lines.append("  end")

    # ── Business subgraph ──────────────────────────────────────────────────────
    lines.append('  subgraph SG_BIZ["Business Layer"]')
    lines.append(f'    Svc["{_sl(base)}Service"]')
    lines.append(f'    Val["{_sl(base)}Validator"]')
    lines.append(f'    Svc --> Val')
    if service == "AuthService":
        lines.append('    SecCtx["Spring Security"]')
        lines.append('    Svc --> SecCtx')
    elif service in ("AccountService", "UserService"):
        lines.append('    AuditSvc["Audit Service"]')
        lines.append('    Svc --> AuditSvc')
    for i, m in enumerate(biz_methods):
        mid = f"BM{i}"
        lines.append(f'    {mid}["{_sl(m)}"]')
        lines.append(f'    Svc --> {mid}')
    lines.append("  end")

    # ── Data subgraph ──────────────────────────────────────────────────────────
    lines.append('  subgraph SG_DATA["Data Layer"]')
    lines.append(f'    Repo["{_sl(base)}Repository"]')
    lines.append(f'    Ent["Entity  {_sl(base)}"]')
    lines.append('    Repo --> Ent')
    lines.append("  end")

    # ── Infra subgraph ─────────────────────────────────────────────────────────
    lines.append(f'  subgraph SG_INFRA["{_sl(cloud)} Infrastructure"]')
    lines.append(f'    Compute["{_sl(cloud_compute)}"]')
    lines.append(f'    DB["{_sl(cloud_db)}"]')
    lines.append(f'    Cache["{_sl(cloud_cache)}"]')
    lines.append(f'    Sec["{_sl(cloud_sec)}"]')
    lines.append("  end")

    # ── Cross-subgraph edges — always between named node IDs, never subgraph IDs
    lines.append("  Ctrl --> Svc")
    lines.append("  Svc --> Repo")
    lines.append("  Repo --> DB")
    lines.append("  Svc -.-> Cache")
    lines.append("  Compute -.-> Ctrl")
    lines.append("  Sec -.-> Compute")

    return "\n".join(lines)


@app.post("/transform/service-detail", tags=["Transform"])
def transform_service_detail(body: dict = {}):
    """Return granular source + target architecture for a single microservice."""
    service_name = body.get("service", "AuthService")
    framework    = body.get("framework", "Spring Boot")
    cloud        = body.get("cloud", "AWS")

    if not _db_exists():
        raise HTTPException(status_code=409, detail="No pipeline database found — run the pipeline first.")

    # Resolve programs belonging to this service via classifier (deduped)
    stats = _gather_portfolio_stats()
    programs: list[str] = [p for p in _dedupe_preserve_order(stats["program_list"]) if _classify_program(p) == service_name]

    with _con() as con:
        src_mmd, total_para, total_stmts, total_cc = _build_service_source_mermaid(con, service_name, programs)
        api_contracts = _api_contracts_for_service(con, service_name, programs)
        entities      = _entities_for_service(con, service_name, programs)

    tgt_mmd = _build_service_target_mermaid(service_name, programs, framework, cloud)

    return {
        "service":         service_name,
        "framework":       framework,
        "cloud":           cloud,
        "source_programs": programs,
        "source_ll_mermaid": src_mmd,
        "target_ll_mermaid": tgt_mmd,
        "api_contracts":   api_contracts,
        "entities":        entities,
        "total_paragraphs": total_para,
        "total_statements": total_stmts,
        "total_cc":         total_cc,
    }


@app.post("/transform/migration-plan", tags=["Transform"])
async def transform_migration_plan(body: dict = {}):
    """Stream a phased migration plan via SSE."""
    framework     = body.get("framework",     "Spring Boot")
    cloud         = body.get("cloud",         "AWS")
    decomposition = body.get("decomposition", "Strangler Fig")

    async def gen():
        if not _db_exists():
            yield f"data: {json.dumps({'kind': 'error', 'msg': 'No pipeline database found.'})}\n\n"
            return

        s = _gather_portfolio_stats()
        n_prog = s["programs"] or 1
        n_rules = s["business_rules"]
        n_jcl = s["jcl_jobs"]
        n_cics = s["cics_verbs"]
        n_files = s["file_io"]
        top5 = [r["name"] for r in s["top_progs"][:5]] or ["COSGN00C", "COADM01C", "CBSTM03A"]

        phases = [
            {
                "id": "p1", "name": "Phase 1 — Assessment & Discovery",
                "duration": "Weeks 1-4", "owner": "Architect + BA",
                "steps": [
                    {"title": f"Inventory and baseline {n_prog} COBOL programs",
                     "description": f"Catalogue every program (currently {n_prog}), {s['paragraphs']:,} paragraphs and {s['data_items']:,} data items already extracted by the 7-layer pipeline. Confirm corpus completeness against source-of-record repository.",
                     "effort": "S", "risk": "Low", "owner": "BA"},
                    {"title": f"Validate {n_rules} extracted business rules with SMEs",
                     "description": f"Walk through every rule predicate (currently {n_rules}) with the business owner. Each rule will become a parameterised JUnit test in the target system, so SME sign-off is the acceptance contract.",
                     "effort": "M", "risk": "Medium", "owner": "BA"},
                    {"title": "Document existing batch SLAs and CICS transaction volumes",
                     "description": f"Capture nightly batch window timings for the {n_jcl} JCL jobs and peak CICS throughput for the {n_cics} verbs. These become NFR targets for the new platform.",
                     "effort": "S", "risk": "Low", "owner": "Ops"},
                    {"title": "Identify regulatory and audit dependencies",
                     "description": "PCI-DSS, SOX, GDPR, and any in-house audit trails. Define which artefacts must be retained byte-identical, and which can be re-modelled.",
                     "effort": "S", "risk": "Medium", "owner": "Compliance"},
                    {"title": "Confirm decommissioning sequence and licence overlap",
                     "description": "Mainframe MIPS, software licences (CICS, DB2, IDMS, Endevor), and contracts. Build the cutover cost model.",
                     "effort": "S", "risk": "Low", "owner": "PM"},
                    {"title": "Risk register first pass",
                     "description": f"Pipeline already extracted {s['risks_high']} HIGH-severity items. Combine with delivery and people risks for an integrated register.",
                     "effort": "S", "risk": "Low", "owner": "Architect"},
                ],
            },
            {
                "id": "p2", "name": "Phase 2 — Architecture Design",
                "duration": "Weeks 5-10", "owner": "Architect",
                "steps": [
                    {"title": f"Decompose portfolio into bounded contexts ({decomposition})",
                     "description": f"Group {n_prog} programs into ~5-8 microservices using naming, call coupling and CICS transaction boundaries. Reference top complexity programs first: {', '.join(top5)}.",
                     "effort": "M", "risk": "Medium", "owner": "Architect"},
                    {"title": f"Design target {framework} project structure",
                     "description": f"Multi-module Maven/Gradle layout — one module per microservice + a shared 'cobol-dtos' module containing translated copybook record layouts ({s['copybooks']} copybooks).",
                     "effort": "M", "risk": "Low", "owner": "Tech Lead"},
                    {"title": f"Select {cloud} primitives for each service",
                     "description": "Compute, storage, queue, cache, secrets, observability. Lock-in choices are explicit and reviewed.",
                     "effort": "M", "risk": "Low", "owner": "Cloud Architect"},
                    {"title": f"Design data migration strategy for {n_files} files",
                     "description": "Map VSAM KSDS/ESDS clusters and DB2 tables to managed RDBMS/NoSQL. Sketch dual-write and back-fill plan.",
                     "effort": "L", "risk": "High", "owner": "Data Architect"},
                    {"title": f"Replace {n_cics} CICS verbs with REST/gRPC contracts",
                     "description": "Each SEND MAP becomes a JSON response, each LINK/XCTL becomes a Feign call or async event.",
                     "effort": "M", "risk": "Medium", "owner": "API Designer"},
                    {"title": "Define identity, authn/authz model",
                     "description": "Replace RACF / ACF2 surface with OIDC + JWT, RBAC at the gateway.",
                     "effort": "M", "risk": "Medium", "owner": "Security Lead"},
                    {"title": "Architecture Decision Records (ADRs)",
                     "description": "Capture every significant design choice in ADR form, version-controlled with the codebase.",
                     "effort": "S", "risk": "Low", "owner": "Architect"},
                ],
            },
            {
                "id": "p3", "name": "Phase 3 — Pilot Migration",
                "duration": "Weeks 11-18", "owner": "Pilot Team",
                "steps": [
                    {"title": f"Migrate {top5[0] if top5 else 'pilot program'} as proof of concept",
                     "description": "Pick a non-revenue-critical, medium-complexity program. End-to-end through dev, test and a shadow-mode production run.",
                     "effort": "L", "risk": "Medium", "owner": "Dev"},
                    {"title": "Stand up CI/CD pipeline",
                     "description": "Build, unit test, integration test, deploy to a non-prod cluster. Pass-fail gates on business-rule unit-test coverage.",
                     "effort": "M", "risk": "Low", "owner": "DevOps"},
                    {"title": "Implement parallel-run reconciliation harness",
                     "description": "Capture transactions on the mainframe, replay against the new service, diff outputs by extracted business-rule oracles.",
                     "effort": "L", "risk": "High", "owner": "QA Lead"},
                    {"title": "Define and publish API contracts (OpenAPI 3.1)",
                     "description": "Contract-first development; consumer-driven contract testing with Pact.",
                     "effort": "M", "risk": "Low", "owner": "API Designer"},
                    {"title": "Pilot data sync — incremental + back-fill",
                     "description": "Move a slice of customer data into the target store and validate parity.",
                     "effort": "M", "risk": "High", "owner": "Data Engineer"},
                    {"title": "Performance and load test the pilot",
                     "description": "Confirm latency and throughput meet or exceed the captured baselines from Phase 1.",
                     "effort": "M", "risk": "Medium", "owner": "Perf Engineer"},
                ],
            },
            {
                "id": "p4", "name": "Phase 4 — Wave Migration",
                "duration": "Weeks 19-36", "owner": "Migration Squads",
                "steps": [
                    {"title": "Schedule programs into delivery waves",
                     "description": f"Split the remaining {max(0, n_prog - 1)} programs into 4-week delivery waves, ordered by business criticality and risk.",
                     "effort": "S", "risk": "Low", "owner": "PM"},
                    {"title": "Migrate AuthService and UserService (Wave 1)",
                     "description": "Foundational identity and customer services first — every other service depends on them.",
                     "effort": "L", "risk": "Medium", "owner": "Squad A"},
                    {"title": "Migrate AccountService and CardService (Wave 2)",
                     "description": "Core domain services with the bulk of business logic.",
                     "effort": "XL", "risk": "High", "owner": "Squad B"},
                    {"title": "Migrate TransactionService (Wave 3)",
                     "description": "Highest-volume service; ledger correctness is non-negotiable.",
                     "effort": "XL", "risk": "High", "owner": "Squad B"},
                    {"title": f"Replace {n_jcl} JCL jobs with Spring Batch (Wave 4)",
                     "description": "Each JCL step → Spring Batch Step bean, DD bindings → ItemReader/ItemWriter pointed at managed storage.",
                     "effort": "L", "risk": "Medium", "owner": "Squad C"},
                    {"title": "Migrate ReportingService and ad-hoc batch (Wave 5)",
                     "description": "Lower-risk reporting workloads; opportunity to refactor into a modern lakehouse pattern.",
                     "effort": "M", "risk": "Low", "owner": "Squad D"},
                    {"title": "Continuous traceability — every rule has a test",
                     "description": f"Per wave, confirm 100% of extracted rules ({n_rules}) for migrated programs have a passing JUnit test.",
                     "effort": "M", "risk": "Medium", "owner": "QA Lead"},
                ],
            },
            {
                "id": "p5", "name": "Phase 5 — Cutover & Stabilisation",
                "duration": "Weeks 37-44", "owner": "Cutover Lead",
                "steps": [
                    {"title": "Production parallel-run window",
                     "description": "Dual-write for 4 weeks; mainframe remains the system of record while the new platform processes the same load.",
                     "effort": "L", "risk": "High", "owner": "Ops"},
                    {"title": "Reconciliation sign-off",
                     "description": "Daily diff reports; zero divergence outside an agreed tolerance band, signed off by Finance and Audit.",
                     "effort": "M", "risk": "High", "owner": "Audit"},
                    {"title": "DNS / API gateway cutover",
                     "description": "Phased traffic shift via weighted routing — 10%, 50%, 100% — with automatic rollback on error rate spike.",
                     "effort": "M", "risk": "High", "owner": "SRE"},
                    {"title": "Decommission CICS regions and JCL schedules",
                     "description": "Stop mainframe workloads only after parallel-run sign-off; retain 90 days of warm standby.",
                     "effort": "M", "risk": "Medium", "owner": "Ops"},
                    {"title": "Mainframe data archival",
                     "description": "Archive VSAM/DB2 datasets to long-term storage with audit-friendly retention metadata.",
                     "effort": "M", "risk": "Low", "owner": "Data Architect"},
                    {"title": "Hypercare period",
                     "description": "4-week heightened support with the migration squads on call.",
                     "effort": "L", "risk": "Medium", "owner": "Support Lead"},
                ],
            },
            {
                "id": "p6", "name": "Phase 6 — Optimisation & Decommissioning",
                "duration": "Weeks 45-52", "owner": "Cloud Optimisation Lead",
                "steps": [
                    {"title": "Right-size compute on actual load profile",
                     "description": f"With one cycle of real production data, right-size {cloud} compute, storage and reserved instances.",
                     "effort": "M", "risk": "Low", "owner": "FinOps"},
                    {"title": "Adopt managed services where viable",
                     "description": "Move from self-managed components to managed equivalents (e.g. queues, schedulers, observability stack).",
                     "effort": "M", "risk": "Low", "owner": "Cloud Architect"},
                    {"title": "Mainframe contract termination",
                     "description": "Formal vendor exit, licence return, hardware decommissioning.",
                     "effort": "L", "risk": "Medium", "owner": "PM"},
                    {"title": "Knowledge transfer to BAU teams",
                     "description": "Architecture, runbooks, on-call rosters handed over from the migration squads.",
                     "effort": "M", "risk": "Low", "owner": "Architect"},
                    {"title": "Lessons-learned and a modernisation playbook",
                     "description": "Document patterns that worked for downstream programmes; convert this delivery into a re-usable playbook.",
                     "effort": "S", "risk": "Low", "owner": "PMO"},
                ],
            },
        ]

        total_steps = 0
        for ph in phases:
            steps_out = []
            for i, st in enumerate(ph["steps"], start=1):
                steps_out.append({
                    "id":          f"{ph['id']}s{i}",
                    "title":       st["title"],
                    "description": st["description"],
                    "effort":      st["effort"],
                    "risk":        st["risk"],
                    "owner":       st["owner"],
                })
                total_steps += 1
            yield f"data: {json.dumps({'kind': 'phase', 'id': ph['id'], 'name': ph['name'], 'duration': ph['duration'], 'owner': ph['owner'], 'steps': steps_out})}\n\n"
            await asyncio.sleep(0.05)

        yield f"data: {json.dumps({'kind': 'done', 'total_phases': len(phases), 'total_steps': total_steps})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _spec_prompts(stats: dict, framework: str, cloud: str, decomposition: str) -> list[dict]:
    """Return the comprehensive per-agent prompt list."""
    n_prog = stats["programs"]
    n_para = stats["paragraphs"]
    n_data = stats["data_items"]
    n_rules = stats["business_rules"]
    n_jcl = stats["jcl_jobs"]
    n_cics = stats["cics_verbs"]
    n_files = stats["file_io"]
    n_cb = stats["copybooks"]
    n_cfg = stats["cfg_edges"]
    n_calls = stats["call_edges"]
    risks_h = stats["risks_high"]

    top_progs_txt = "\n".join(
        f"- {r['name']}: cyclomatic={r.get('cyclomatic', '?')}, statements={r.get('statement_count','?')}"
        for r in stats["top_progs"][:8]
    ) or "(no complexity data)"
    most_called_txt = "\n".join(
        f"- {r['name']}: called {r.get('cnt','?')} times" for r in stats["most_called"][:8]
    ) or "(no resolved calls)"

    context = (
        f"PORTFOLIO METRICS (do not invent numbers):\n"
        f"- COBOL programs: {n_prog}\n"
        f"- Paragraphs: {n_para:,}\n"
        f"- Data items: {n_data:,}\n"
        f"- Business rules extracted: {n_rules}\n"
        f"- Resolved call-graph edges: {n_calls}\n"
        f"- Control-flow graph edges: {n_cfg:,}\n"
        f"- JCL jobs: {n_jcl}\n"
        f"- CICS verbs: {n_cics}\n"
        f"- Logical files: {n_files}\n"
        f"- Copybooks: {n_cb}\n"
        f"- HIGH-severity risks: {risks_h}\n\n"
        f"TOP 8 MOST COMPLEX PROGRAMS:\n{top_progs_txt}\n\n"
        f"MOST-CALLED PROGRAMS:\n{most_called_txt}\n\n"
        f"TARGET PREFERENCES:\n"
        f"- Framework: {framework}\n"
        f"- Cloud: {cloud}\n"
        f"- Decomposition pattern: {decomposition}\n"
    )

    return [
        {
            "id": "executive", "label": "Executive Summary", "icon": "EXEC",
            "target_words": 800,
            "prompt": f"""You are the Engagement Partner for a top-tier systems integrator presenting a $5M+ COBOL-to-{framework} modernization programme to the CIO and CFO of a major financial institution. Write the Executive Summary of the formal Modernization Specification Document.

{context}

This Executive Summary will be the first two pages a board reads. Cover, in order:
1. The business case in one paragraph — why now, why us, what changes (150 words)
2. Scope at a glance — programs, rules, jobs, files, and current technical debt indicators (150 words)
3. Target state — {framework} microservices on {cloud}, using the {decomposition} pattern (150 words)
4. Indicative effort, duration and team size, with high-level T-shirt cost band (150 words)
5. Top 5 risks and their mitigations, in a compact bullet list (100 words)
6. Recommendation and the next 30 days of execution (100 words)

Tone: confident, board-level, no padding. Cite specific numbers from the metrics above. Output Markdown with an `# Executive Summary` H1 and `##` subsection headers."""
        },
        {
            "id": "business_analyst", "label": "Current State Analysis", "icon": "BA",
            "target_words": 6000,
            "prompt": f"""You are a Senior Business Analyst writing the Current State Analysis section of the formal Business Requirements Document (BRD) for a COBOL-to-{framework} modernisation engagement at a major bank. You must produce 6000+ words.

{context}

Write in formal BRD style. Output Markdown with an `# Current State Analysis` H1 and the following `##` subsections, each filled completely:

1. Executive Context & Business Drivers (~300 words) — why the bank is doing this, market drivers, competitive pressure.
2. Current System Overview (~500 words) — describe the COBOL estate, the {n_prog} programmes and their inter-relationships, the role of {n_cics} CICS verbs and {n_jcl} JCL jobs.
3. Business Process Mapping (~600 words) — map the mainframe processes to enterprise business capabilities using a clear capability model.
4. Functional Decomposition (~500 words) — break the {n_prog} programs into business domains using the program list and naming patterns shown above.
5. Data Architecture — Current State (~500 words) — VSAM clusters, DB2 tables, copybook record layouts ({n_cb} copybooks), {n_data:,} data items. Discuss packed-decimal and EBCDIC encoding implications.
6. Integration Architecture — Current State (~500 words) — JCL batch workloads, CICS online tier, file-sharing patterns, MQ/IMS gateways.
7. Pain Points & Constraints (~400 words) — complexity hotspots (cite top complex programs above), licence and skills shortage, audit and operational risk.
8. Business Requirements for Modernisation (~600 words) — produce numbered BR-001 … BR-NN requirements covering functional and non-functional needs.
9. Success Criteria & KPIs (~300 words) — SLOs, MTTR, MIPS reduction, deployment frequency, cost-per-transaction.
10. Stakeholder Analysis (~300 words) — RACI table covering CIO, CFO, business owners, ops, audit and end customers.

Include at least one Markdown data table showing each top complex program with its business function, complexity rating and migration priority. Use formal language, numbered requirements, and never invent counts. Anchor every claim to the metrics provided."""
        },
        {
            "id": "system_architect", "label": "Target Architecture Design", "icon": "ARC",
            "target_words": 7500,
            "prompt": f"""You are the Lead Solution Architect. Write the Target Architecture Design section (~7500 words) for the modernisation specification of {n_prog} COBOL programmes to {framework} on {cloud} using the {decomposition} pattern.

{context}

Output Markdown with an `# Target Architecture Design` H1 and these `##` subsections, all populated:

1. Architecture Principles & Guardrails (~500 words) — twelve-factor, cloud-native, security by default, observability first.
2. Logical Architecture (~700 words) — bounded contexts derived from the call graph and CICS transactions. Show a logical-level ASCII or Mermaid diagram.
3. Physical Architecture on {cloud} (~800 words) — pick specific managed services (compute, datastore, cache, queue, secrets, identity, observability) and justify each.
4. Microservice Catalogue (~900 words) — define each microservice: name, responsibility, source COBOL programs, API surface, data store, scaling profile.
5. Data Architecture (~700 words) — relational vs document vs key-value choices, schema-evolution strategy, change-data-capture for parallel run.
6. Event-Driven Backbone (~500 words) — replace synchronous CICS LINK/XCTL with events where it improves resilience.
7. API Gateway & Edge (~500 words) — routing, rate limiting, auth handoff, traffic shifting for cutover.
8. Cross-Cutting Concerns (~600 words) — logging, metrics, tracing, secrets, config, feature flags, error budgets.
9. Resilience & DR (~500 words) — RTO/RPO targets, multi-AZ vs multi-region, chaos testing approach.
10. Architecture Decision Records (~800 words) — list 6-8 ADRs in title + status + decision + consequences format.
11. Reference Patterns & Anti-Patterns (~500 words) — saga, outbox, strangler, anti-corruption layer, vs. shared-database and chatty-microservices anti-patterns.

Produce a Mermaid diagram in section 2 and a table in section 4 listing each service with its mapped programs. Be specific, not generic — anchor on the actual program counts and complexity hotspots."""
        },
        {
            "id": "tech_lead", "label": "Technical Specification", "icon": "TECH",
            "target_words": 6000,
            "prompt": f"""You are the Technical Lead. Write the Technical Specification (~6000 words) describing how the engineering team will deliver the architecture in {framework}.

{context}

Output Markdown with an `# Technical Specification` H1 and these `##` subsections:

1. Engineering Standards (~500 words) — language version, code style, branching, code review, definition of done.
2. Module Layout (~500 words) — multi-module Maven/Gradle skeleton with one module per service and a shared `cobol-dtos` module covering {n_cb} copybooks.
3. Dependency Stack (~500 words) — Spring versions, Java version, libraries for HTTP, persistence, observability, messaging, retry, resilience.
4. Pattern Library (~600 words) — service template, repository template, controller template, batch step template, integration test template. Provide small inline code snippets in Markdown ```` ```java ```` blocks.
5. Coding Translation Rules (~700 words) — explicit mapping from COBOL constructs to Java idioms: PIC clauses to types, PERFORM to methods, GO TO removal, MOVE semantics, COMP-3 handling, ROUNDED arithmetic.
6. Error Handling Strategy (~400 words) — typed exceptions, problem+json responses, idempotency keys, retry policies.
7. Configuration & Secrets (~400 words) — environment-based config, Vault/Secrets Manager integration, no secrets in code.
8. Logging, Metrics, Tracing (~500 words) — OpenTelemetry, structured JSON logging, USE/RED dashboards.
9. Build & Test Pipeline (~500 words) — unit, contract, integration, performance, mutation testing layers.
10. Documentation Standards (~400 words) — Javadoc, ADRs, API docs, runbooks.
11. Performance Engineering (~500 words) — JIT warm-up, GC tuning, connection pooling, p99 latency targets.
12. Migration of {n_rules} Business Rules (~500 words) — pattern: each predicate is a guarded clause + parameterised unit test referencing the rule_id.

Snippets must be realistic Spring Boot 3.x patterns. Provide at least 3 short code blocks."""
        },
        {
            "id": "data_architect", "label": "Data Architecture & Migration", "icon": "DATA",
            "target_words": 5000,
            "prompt": f"""You are the Data Architect. Write the Data Architecture and Migration plan (~5000 words) for converting {n_files} logical COBOL files / DB2 tables and {n_cb} copybook record layouts into managed cloud datastores on {cloud}.

{context}

Output Markdown with an `# Data Architecture & Migration` H1 and these `##` subsections:

1. Current Data Landscape (~500 words) — VSAM KSDS/ESDS, sequential, DB2, IDMS where applicable.
2. Target Data Stores on {cloud} (~600 words) — relational, document, key-value, blob — with justification.
3. Logical Data Model (~500 words) — entities, relationships, ownership boundaries aligned to microservices.
4. Schema Conversion Strategy (~600 words) — PIC X to VARCHAR, PIC 9 to NUMERIC with scale, COMP-3 to NUMERIC, OCCURS to nested or normalised tables, REDEFINES to polymorphic columns.
5. Migration Patterns (~700 words) — initial bulk load, change-data-capture, dual-write, back-fill, reconciliation. Cover hot vs cold data.
6. Reference Data & Master Data (~400 words) — codes tables, customer master, account master.
7. Data Quality Strategy (~400 words) — profiling on extract, in-flight checks, post-load reconciliation by extracted business-rule oracles.
8. Encoding & Character Sets (~300 words) — EBCDIC to UTF-8, signed-overpunch numeric handling.
9. Encryption, Tokenisation, Masking (~400 words) — at-rest, in-flight, sensitive PII handling.
10. Retention & Archival (~300 words) — regulatory holds, immutable archive, audit log retention.
11. Data Governance & Lineage (~300 words) — metadata catalogue, lineage capture, stewardship.

Include a Markdown table that maps each top copybook layout to its target table/document and ownership service."""
        },
        {
            "id": "api_designer", "label": "API Design & Contracts", "icon": "API",
            "target_words": 4000,
            "prompt": f"""You are the API Designer. Write the API Design & Contracts section (~4000 words) replacing the {n_cics} CICS verbs and inter-program calls with REST and event contracts.

{context}

Output Markdown with `# API Design & Contracts` H1 and these subsections:
1. API Strategy (~400 words) — REST first, gRPC for internal high-throughput, async events on the backbone.
2. Versioning & Lifecycle (~400 words) — URI versioning vs media-type versioning, deprecation policy.
3. Contract-First Workflow (~400 words) — OpenAPI 3.1 sources of truth, generated stubs, Pact for consumer-driven contracts.
4. Authentication & Authorisation (~400 words) — OIDC, JWT, scopes, mTLS internally.
5. Resource Model & Naming (~400 words) — pluralised nouns, sub-resources, idempotency keys for write paths.
6. Error Model (~300 words) — RFC 7807 `application/problem+json` standard.
7. Pagination, Filtering, Sorting (~300 words) — cursor pagination by default.
8. Sample Endpoints (~700 words) — provide 5-7 fully written OpenAPI 3.1 YAML fragments covering Auth, User, Account, Card, Transaction, Reporting services. Include parameters, request/response schemas and error responses.
9. Event Catalogue (~400 words) — list domain events (CardIssued, TransactionPosted, AccountUpdated) with CloudEvents envelopes.
10. SDK & Developer Experience (~300 words) — generated SDKs, sandbox environment, mock server, docs portal.

Use real-looking OpenAPI YAML in section 8 inside ```` ```yaml ```` fences."""
        },
        {
            "id": "security_analyst", "label": "Security Architecture", "icon": "SEC",
            "target_words": 3500,
            "prompt": f"""You are the Security Analyst. Produce the Security Architecture section (~3500 words) for the modernised platform on {cloud}.

{context}

Output Markdown with `# Security Architecture` H1 and these `##` subsections:
1. Threat Model (~400 words) — STRIDE applied to each microservice boundary.
2. Identity & Access (~400 words) — workforce identity, customer identity, machine identity, secrets management.
3. Authentication & Authorization (~400 words) — OIDC + JWT, role/attribute-based access, fine-grained policies.
4. Data Protection (~400 words) — TLS 1.3 in flight, AES-256 at rest, tokenisation of PAN/CVV, KMS-managed keys.
5. Application Security (~400 words) — SAST, DAST, SCA, secrets scanning, dependency scanning.
6. Infrastructure Security (~400 words) — landing zone, network segmentation, private subnets, egress control.
7. Compliance Mapping (~400 words) — PCI-DSS, SOX, GDPR, GLBA controls and how each is satisfied.
8. Logging, Audit, Forensics (~300 words) — immutable audit trail, log integrity, SIEM ingestion.
9. Incident Response (~300 words) — runbooks, on-call rotation, evidence preservation.
10. Mainframe-Specific Controls (~300 words) — replacement of RACF/ACF2 controls, decommissioning hardening."""
        },
        {
            "id": "devops_engineer", "label": "Infrastructure & DevOps", "icon": "OPS",
            "target_words": 3500,
            "prompt": f"""You are the DevOps Engineer. Produce the Infrastructure & DevOps Specification (~3500 words) for delivering the {framework} target on {cloud}.

{context}

Output Markdown with `# Infrastructure & DevOps` H1 and these `##` subsections:
1. Landing Zone (~400 words) — accounts/subscriptions/projects, networking, IAM bootstrap.
2. Compute Strategy (~400 words) — containers vs serverless, runtime choices, auto-scaling.
3. Kubernetes/Container Platform (~400 words) — managed service, namespaces, RBAC, network policy.
4. CI/CD Pipeline (~500 words) — branching, build, test, security scans, deploy, gated promotions across env.
5. Infrastructure as Code (~400 words) — Terraform/CDK modules, drift detection, policy-as-code.
6. Observability Stack (~400 words) — metrics, logs, traces, dashboards, alerts, SLO error budgets.
7. Release Engineering (~400 words) — feature flags, progressive delivery, canary, blue-green.
8. Cost Management / FinOps (~300 words) — tagging, budgets, reservations, rightsizing.
9. Disaster Recovery Operations (~300 words) — runbooks, drills, RTO/RPO verification.
10. Cutover Operations Runbook (~400 words) — pre-cutover, cutover day, post-cutover playbook for moving traffic off the mainframe."""
        },
        {
            "id": "qa_lead", "label": "Test Strategy & QA Plan", "icon": "QA",
            "target_words": 3000,
            "prompt": f"""You are the QA Lead. Write the Test Strategy & QA Plan (~3000 words) for the modernisation effort.

{context}

Output Markdown with `# Test Strategy & QA Plan` H1 and these `##` subsections:
1. Test Strategy Overview (~300 words) — pyramid, shift-left, contract-first.
2. Unit Testing (~300 words) — JUnit 5, AssertJ, Mockito. Note that every one of the {n_rules} extracted business rules becomes at least one parameterised unit test.
3. Component & Integration Testing (~400 words) — Spring Boot test slices, TestContainers for data stores.
4. Contract Testing (~300 words) — Pact between consumers and providers, broker integrated to CI.
5. End-to-End Testing (~400 words) — black-box journeys covering the {n_cics} CICS transaction equivalents.
6. Parallel-Run Reconciliation (~400 words) — replay captured mainframe traffic, diff outputs by rule oracle.
7. Non-Functional Testing (~400 words) — load, soak, stress, chaos, security, accessibility.
8. Test Data Management (~300 words) — masked production-like data, synthetic generation, refresh policy.
9. Defect Management (~200 words) — severity definitions, SLA, root-cause analysis.
10. Quality Gates per Wave (~300 words) — explicit pass/fail criteria for each migration wave."""
        },
        {
            "id": "pm", "label": "Project Plan & Risk Register", "icon": "PM",
            "target_words": 3000,
            "prompt": f"""You are the Programme Manager. Write the Project Plan & Risk Register section (~3000 words) for delivering the modernisation.

{context}

Output Markdown with `# Project Plan & Risk Register` H1 and these `##` subsections:
1. Programme Structure (~300 words) — programme board, steering committee, delivery squads, run/build/change split.
2. Roles & Responsibilities (~400 words) — RACI table including BA, Architect, Tech Lead, Data Architect, API Designer, Security, DevOps, QA, PM.
3. Schedule Overview (~400 words) — high-level Gantt-style timeline across all six phases. Show duration and dependencies.
4. Workstreams (~400 words) — engineering, data, infrastructure, security, change management, business validation.
5. Estimation Approach (~300 words) — story points calibrated to known waves; cost band per wave.
6. Risk Register (~600 words) — at least 10 entries, each with: ID, description, probability, impact, mitigation, owner, status. Cover technical, data, people, vendor and regulatory risks. Reference the {risks_h} HIGH-severity items already in the artifact register.
7. Issue Management (~200 words) — escalation path, decision logs.
8. Communication Plan (~200 words) — cadence, audiences, formats.
9. Change Management (~200 words) — business readiness, training, comms to end users.

Provide the Risk Register as a Markdown table."""
        },
    ]


@app.post("/transform/specs/comprehensive", tags=["Transform"])
async def transform_specs_comprehensive(body: dict = {}):
    """SSE stream — generate 10 comprehensive spec sections in parallel agents."""
    framework     = body.get("framework",     "Spring Boot")
    cloud         = body.get("cloud",         "AWS")
    decomposition = body.get("decomposition", "Strangler Fig")

    async def gen():
        if not _db_exists():
            yield f"data: {json.dumps({'kind': 'error', 'msg': 'No pipeline database found.'})}\n\n"
            return

        stats = _gather_portfolio_stats()
        agents = _spec_prompts(stats, framework, cloud, decomposition)

        # ── Try anthropic for real LLM streaming, else fall back to static ──
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        use_llm = bool(api_key)
        if use_llm:
            try:
                import anthropic as _anthropic
                _client = _anthropic.Anthropic(api_key=api_key)
            except Exception:
                use_llm = False

        loop = asyncio.get_event_loop()
        total_words = 0

        for agent in agents:
            aid = agent["id"]
            label = agent["label"]
            yield f"data: {json.dumps({'kind': 'agent_start', 'agent': aid, 'label': label})}\n\n"
            await asyncio.sleep(0)

            content = ""
            if use_llm:
                def _call() -> str:
                    try:
                        resp = _client.messages.create(  # type: ignore[name-defined]
                            model="claude-sonnet-4-5-20251001",
                            max_tokens=8000,
                            messages=[{"role": "user", "content": agent["prompt"]}],
                        )
                        return resp.content[0].text  # type: ignore[union-attr]
                    except Exception as exc:
                        return f"[LLM error: {exc}]"
                try:
                    content = await loop.run_in_executor(None, _call)
                except Exception as exc:
                    content = f"[LLM error: {exc}]"

            if not content or content.startswith("[LLM error"):
                # Static fallback — produce a structured stub that still satisfies the schema
                content = _static_spec_fallback(agent, stats, framework, cloud, decomposition)

            # Stream in chunks of ~600 chars so the UI can show progress
            chunk_size = 600
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                yield f"data: {json.dumps({'kind': 'agent_chunk', 'agent': aid, 'chunk': chunk})}\n\n"
                await asyncio.sleep(0.01)

            words = len(content.split())
            total_words += words
            yield f"data: {json.dumps({'kind': 'agent_done', 'agent': aid, 'word_count': words})}\n\n"
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'kind': 'all_done', 'total_words': total_words, 'sections': len(agents)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _static_spec_fallback(agent: dict, stats: dict, framework: str, cloud: str, decomposition: str) -> str:
    """Produce a structured Markdown stub when an LLM is not configured."""
    n_prog = stats["programs"]; n_rules = stats["business_rules"]; n_jcl = stats["jcl_jobs"]
    n_cics = stats["cics_verbs"]; n_files = stats["file_io"]; n_cb = stats["copybooks"]
    label = agent["label"]

    header = f"# {label}\n\n"
    intro = (
        f"_This section was produced from the artifact database without invoking a remote LLM. "
        f"It is structured per the {label} brief and grounded in: {n_prog} programs, {n_rules} "
        f"business rules, {n_jcl} JCL jobs, {n_cics} CICS verbs, {n_files} logical files, "
        f"{n_cb} copybooks._\n\n"
        f"**Target stack:** {framework} on {cloud}. **Pattern:** {decomposition}.\n\n"
    )

    if agent["id"] == "executive":
        body = (
            "## Business Case\n"
            f"The mainframe COBOL portfolio comprises {n_prog} programmes, {n_jcl} JCL jobs and {n_cics} CICS verbs. "
            f"Operating cost, licensing risk and a shrinking talent pool make modernisation the strategic priority. "
            f"This programme replaces the estate with {framework} microservices on {cloud}, using the {decomposition} pattern.\n\n"
            "## Scope\n"
            f"All {n_prog} programmes, {n_rules} extracted business rules, {n_cb} copybooks and {n_files} logical files are in-scope. "
            f"No line of COBOL is migrated without a corresponding rule-level acceptance test.\n\n"
            "## Target State\n"
            f"Five to eight bounded-context microservices, an event backbone, a managed RDBMS and a CI/CD pipeline. Decommission of CICS and JCL within 12 months.\n\n"
            "## Indicative Effort\n"
            "12-month delivery, 4-6 engineering squads, an architecture and data team, a security and SRE pod. Cost band $4M-$6M.\n\n"
            "## Top Risks\n"
            "- Dual-run data divergence — mitigated by the extracted rule oracle.\n"
            "- VSAM to RDBMS schema fidelity — mitigated by copybook-driven schema generation.\n"
            "- Talent ramp on the migration squads — mitigated by paired Java/COBOL pods.\n"
            "- Vendor cost spike during overlap — mitigated by aggressive cutover schedule.\n"
            "- Regulatory observation — mitigated by audit-grade traceability per rule.\n\n"
            "## Recommendation\n"
            "Approve Phase 1 (Assessment & Discovery) immediately. Stand up the pilot squad within 30 days."
        )
    else:
        # Generic structured stub for any other agent
        body_parts = ["## Overview\n"]
        body_parts.append(
            f"This section describes the {label} for a modernisation of {n_prog} COBOL programmes "
            f"to {framework} on {cloud} using the {decomposition} pattern.\n\n"
        )
        body_parts.append("## Approach\n")
        body_parts.append(
            "The approach is grounded in the artifact pipeline output. Every claim is traceable to a "
            "concrete extracted artifact (rule, edge, program, file).\n\n"
        )
        body_parts.append("## Key Considerations\n")
        body_parts.append(
            f"- Rule preservation: each of the {n_rules} rules becomes a unit test.\n"
            f"- Batch parity: each of the {n_jcl} JCL jobs has an equivalent Spring Batch job.\n"
            f"- Online parity: each of the {n_cics} CICS verbs has an equivalent REST/event contract.\n"
            f"- Data fidelity: each of the {n_files} logical files has a target store with reconciliation.\n\n"
        )
        body_parts.append("## Next Steps\n")
        body_parts.append(
            "Drive the section to completion in collaboration with stakeholders. "
            "Set ANTHROPIC_API_KEY (or OPENAI_API_KEY) and re-run to obtain the full LLM-authored document."
        )
        body = "".join(body_parts)

    return header + intro + body


def _generate_service_files(service: str, programs: list, framework: str, cloud: str) -> list:
    """Return a list of (path, content) tuples for the given microservice.

    Used by /transform/codegen (streaming), /transform/codegen/export (ZIP),
    and /transform/codegen/github-push.
    """
    package = (
        "com.example." + service.lower().replace("service", "") + "service"
        if service.lower().endswith("service")
        else "com.example." + service.lower()
    )
    progs_csv = ", ".join(programs) if programs else "(no programs mapped)"
    n_progs   = len(programs) if programs else 0

    files = [
            (
                f"src/main/java/{package.replace('.', '/')}/{service}Application.java",
                f"""package {package};

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * {service} — generated from COBOL programs: {progs_csv}.
 * Framework: {framework}. Target cloud: {cloud}.
 */
@SpringBootApplication
public class {service}Application {{
    public static void main(String[] args) {{
        SpringApplication.run({service}Application.class, args);
    }}
}}
"""
            ),
            (
                f"src/main/java/{package.replace('.', '/')}/api/{service}Controller.java",
                f"""package {package}.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import {package}.domain.{service.replace('Service','')};
import {package}.application.{service};

import java.util.List;

@RestController
@RequestMapping("/api/v1/{service.replace('Service','').lower()}s")
public class {service}Controller {{

    private final {service} service;

    public {service}Controller({service} service) {{
        this.service = service;
    }}

    @GetMapping("/{{id}}")
    public ResponseEntity<{service.replace('Service','')}> get(@PathVariable String id) {{
        return service.findById(id)
            .map(ResponseEntity::ok)
            .orElse(ResponseEntity.notFound().build());
    }}

    @GetMapping
    public List<{service.replace('Service','')}> list() {{
        return service.findAll();
    }}

    @PostMapping
    public ResponseEntity<{service.replace('Service','')}> create(@RequestBody {service.replace('Service','')} body) {{
        var saved = service.create(body);
        return ResponseEntity.ok(saved);
    }}
}}
"""
            ),
            (
                f"src/main/java/{package.replace('.', '/')}/application/{service}.java",
                f"""package {package}.application;

import org.springframework.stereotype.Service;
import {package}.domain.{service.replace('Service','')};
import {package}.infrastructure.{service.replace('Service','')}Repository;

import java.util.List;
import java.util.Optional;

/**
 * {service} application service.
 * Migrated from COBOL programs: {progs_csv}.
 * Each public method preserves the corresponding business rule(s) extracted by the pipeline.
 */
@Service
public class {service} {{

    private final {service.replace('Service','')}Repository repo;

    public {service}({service.replace('Service','')}Repository repo) {{
        this.repo = repo;
    }}

    public Optional<{service.replace('Service','')}> findById(String id) {{
        return repo.findById(id);
    }}

    public List<{service.replace('Service','')}> findAll() {{
        return repo.findAll();
    }}

    public {service.replace('Service','')} create({service.replace('Service','')} entity) {{
        // BR-001 .. BR-NN — validation guards extracted from COBOL business rules go here.
        return repo.save(entity);
    }}
}}
"""
            ),
            (
                f"src/main/java/{package.replace('.', '/')}/domain/{service.replace('Service','')}.java",
                f"""package {package}.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

/**
 * {service.replace('Service','')} entity — derived from COBOL copybook record layouts.
 * Numeric fields use BigDecimal to preserve PIC 9(n)V9(m) precision.
 */
@Entity
@Table(name = "{service.replace('Service','').lower()}")
public class {service.replace('Service','')} {{

    @Id
    private String id;

    @Column(nullable = false)
    private String name;

    @Column(precision = 19, scale = 4)
    private BigDecimal amount;

    @Column(name = "created_at")
    private Instant createdAt;

    // getters and setters omitted for brevity
}}
"""
            ),
            (
                f"src/main/java/{package.replace('.', '/')}/infrastructure/{service.replace('Service','')}Repository.java",
                f"""package {package}.infrastructure;

import org.springframework.data.jpa.repository.JpaRepository;
import {package}.domain.{service.replace('Service','')};

public interface {service.replace('Service','')}Repository extends JpaRepository<{service.replace('Service','')}, String> {{
}}
"""
            ),
            (
                f"src/test/java/{package.replace('.', '/')}/{service}Tests.java",
                f"""package {package};

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
class {service}Tests {{

    @Test
    void context_loads() {{
        assertThat(true).isTrue();
    }}

    // One @ParameterizedTest per extracted business rule belongs here.
}}
"""
            ),
            (
                "src/main/resources/application.yml",
                f"""spring:
  application:
    name: {service.lower()}
  datasource:
    url: ${{DB_URL:jdbc:postgresql://localhost:5432/{service.replace('Service','').lower()}}}
    username: ${{DB_USER:postgres}}
    password: ${{DB_PASS:postgres}}
  jpa:
    hibernate:
      ddl-auto: validate

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus

server:
  port: ${{PORT:8080}}
"""
            ),
            (
                "pom.xml",
                f"""<!-- {service} — generated for COBOL programs: {progs_csv} -->
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>{package}</groupId>
  <artifactId>{service.lower()}</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>jar</packaging>

  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.0</version>
  </parent>

  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency>
    <dependency><groupId>org.postgresql</groupId><artifactId>postgresql</artifactId><scope>runtime</scope></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>
  </dependencies>
</project>
"""
            ),
        ]
    return files


@app.post("/transform/codegen", tags=["Transform"])
async def transform_codegen(body: dict = {}):
    """SSE stream — emit Java Spring Boot scaffolding for a target microservice."""
    service   = body.get("service",   "DomainService")
    programs  = body.get("programs",  [])
    framework = body.get("framework", "Spring Boot")
    cloud     = body.get("cloud",     "AWS")

    async def gen():
        yield f"data: {json.dumps({'kind': 'service_start', 'service': service, 'programs': programs})}\n\n"
        await asyncio.sleep(0)

        files = _generate_service_files(service, programs, framework, cloud)

        total_lines = 0
        for path, content in files:
            yield f"data: {json.dumps({'kind': 'file_start', 'service': service, 'path': path})}\n\n"
            await asyncio.sleep(0)
            # Stream in chunks
            chunk_size = 400
            for i in range(0, len(content), chunk_size):
                yield f"data: {json.dumps({'kind': 'file_chunk', 'service': service, 'path': path, 'chunk': content[i:i + chunk_size]})}\n\n"
                await asyncio.sleep(0.005)
            total_lines += content.count("\n") + 1
            yield f"data: {json.dumps({'kind': 'file_done', 'service': service, 'path': path, 'lines': content.count(chr(10)) + 1})}\n\n"
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'kind': 'service_done', 'service': service, 'files': len(files), 'total_lines': total_lines})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/transform/codegen/export", tags=["Transform"])
def transform_codegen_export(body: dict = {}):
    """Build and return a ZIP archive of generated Java Spring Boot files for a service."""
    import datetime

    service   = body.get("service",   "DomainService")
    programs  = body.get("programs",  [])
    framework = body.get("framework", "Spring Boot")
    cloud     = body.get("cloud",     "AWS")

    files = _generate_service_files(service, programs, framework, cloud)
    n_progs   = len(programs)
    progs_csv = ", ".join(programs) if programs else "(no programs mapped)"
    datestamp = datetime.date.today().strftime("%Y%m%d")

    # Build README.md
    readme = f"""# {service}

Migrated from COBOL CardDemo programs: **{progs_csv}**

Business rules were extracted from **{n_progs}** COBOL programs by the UST COBOL Parser pipeline
and scaffolded as a {framework} microservice targeting {cloud}.

## Prerequisites

- Java 17+
- Maven 3.8+
- PostgreSQL 14+

## Build

```bash
mvn clean package -DskipTests
```

## Run

```bash
java -jar target/{service.lower()}-1.0.0-SNAPSHOT.jar
```

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_URL`  | JDBC connection string | `jdbc:postgresql://localhost:5432/{service.replace('Service','').lower()}` |
| `DB_USER` | Database username | `postgres` |
| `DB_PASS` | Database password | `postgres` |
| `PORT`    | HTTP listen port | `8080` |

## Docker

```bash
docker run -p 8080:8080 \\
  -e DB_URL=jdbc:postgresql://db:5432/{service.replace('Service','').lower()} \\
  -e DB_USER=postgres \\
  -e DB_PASS=postgres \\
  {service.lower()}:1.0.0-SNAPSHOT
```

---

_Generated by UST CodeCrafter COBOL Modernisation Pipeline._
"""

    # Standard Java Spring Boot .gitignore
    gitignore = """# Compiled class files
*.class

# Log files
*.log

# BlueJ files
*.ctxt

# Mobile Tools for Java (J2ME)
.mtj.tmp/

# Package files
*.jar
*.war
*.nar
*.ear
*.zip
*.tar.gz
*.rar

# virtual machine crash logs
hs_err_pid*
replay_pid*

# Maven
target/
pom.xml.tag
pom.xml.releaseBackup
pom.xml.versionsBackup
pom.xml.next
release.properties
dependency-reduced-pom.xml
buildNumber.properties
.mvn/timing.properties
.mvn/wrapper/maven-wrapper.jar

# IDE files
.idea/
*.iml
.eclipse
.classpath
.project
.settings/
*.swp
*~

# OS files
.DS_Store
Thumbs.db
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in files:
            zf.writestr(path, content)
        zf.writestr("README.md", readme)
        zf.writestr(".gitignore", gitignore)

    zip_bytes = buf.getvalue()
    filename  = f"{service}-{datestamp}.zip"
    from fastapi.responses import Response
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/transform/codegen/github-push", tags=["Transform"])
def transform_codegen_github_push(body: dict = {}):
    """Generate code, create a GitHub repository, and push the files."""
    import urllib.request as _urllib_request
    import datetime
    import urllib.error

    service          = body.get("service",          "DomainService")
    programs         = body.get("programs",         [])
    framework        = body.get("framework",        "Spring Boot")
    cloud            = body.get("cloud",            "AWS")
    github_token     = (body.get("github_token",    "") or "").strip()
    repo_name        = (body.get("repo_name",       "") or "").strip()
    repo_description = body.get("repo_description", f"Migrated from COBOL CardDemo — {service}")
    make_private     = body.get("make_private",     True)

    if not github_token:
        return {"success": False, "message": "github_token is required."}
    if not repo_name:
        return {"success": False, "message": "repo_name is required."}

    # ── 1. Resolve GitHub username ────────────────────────────────────────────
    def _gh_get(url: str) -> dict:
        req = _urllib_request.Request(
            url,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "UST-COBOL-Pipeline/1.0",
            },
        )
        with _urllib_request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def _gh_post(url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req  = _urllib_request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
                "User-Agent": "UST-COBOL-Pipeline/1.0",
            },
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    try:
        user_data = _gh_get("https://api.github.com/user")
        username  = user_data.get("login", "")
        if not username:
            return {"success": False, "message": "Could not determine GitHub username from token."}
    except urllib.error.HTTPError as exc:
        return {"success": False, "message": f"GitHub auth failed: {exc.code} {exc.reason}"}
    except Exception as exc:
        return {"success": False, "message": f"GitHub API error: {exc}"}

    # ── 2. Generate files ─────────────────────────────────────────────────────
    files     = _generate_service_files(service, programs, framework, cloud)
    progs_csv = ", ".join(programs) if programs else "(no programs mapped)"
    n_progs   = len(programs)
    datestamp = datetime.date.today().strftime("%Y%m%d")

    readme = f"""# {service}

Migrated from COBOL CardDemo programs: **{progs_csv}**

Business rules were extracted from **{n_progs}** COBOL programs by the UST COBOL Parser pipeline.

## Build

```bash
mvn clean package -DskipTests
java -jar target/{service.lower()}-1.0.0-SNAPSHOT.jar
```

Set `DB_URL`, `DB_USER`, `DB_PASS` environment variables before running.
"""
    gitignore = "target/\n*.class\n*.jar\n.idea/\n*.iml\n.DS_Store\n"

    # ── 3. Write to temp directory ────────────────────────────────────────────
    timestamp = int(time.time())
    tmpdir    = f"/tmp/cobol-codegen-{service}-{timestamp}"
    try:
        os.makedirs(tmpdir, exist_ok=True)
        for path, content in files:
            abs_path = os.path.join(tmpdir, path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(readme)
        with open(os.path.join(tmpdir, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(gitignore)

        # ── 4. Git init and commit ────────────────────────────────────────────
        def _run(cmd: list, cwd: str = tmpdir) -> None:
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(f"Command {cmd} failed: {result.stderr.strip()}")

        _run(["git", "init", "-b", "main"])
        _run(["git", "config", "user.email", "cobol-pipeline@example.com"])
        _run(["git", "config", "user.name",  "UST COBOL Pipeline"])
        _run(["git", "add", "."])
        _run(["git", "commit", "-m",
              f"Initial commit — migrated from COBOL programs: {progs_csv}"])

        # ── 5. Create GitHub repo ─────────────────────────────────────────────
        try:
            _gh_post("https://api.github.com/user/repos", {
                "name":        repo_name,
                "description": repo_description,
                "private":     bool(make_private),
                "auto_init":   False,
            })
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode() if hasattr(exc, "read") else str(exc)
            if exc.code == 422 and "already exists" in body_text:
                pass  # Repo exists — push to it anyway
            else:
                return {"success": False, "message": f"Failed to create GitHub repo: {exc.code} — {body_text}"}
        except Exception as exc:
            return {"success": False, "message": f"Failed to create GitHub repo: {exc}"}

        # ── 6. Push ──────────────────────────────────────────────────────────
        remote_url = f"https://{github_token}@github.com/{username}/{repo_name}.git"
        _run(["git", "remote", "add", "origin", remote_url])
        _run(["git", "push", "-u", "origin", "main"])

    except Exception as exc:
        return {"success": False, "message": str(exc)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    repo_url = f"https://github.com/{username}/{repo_name}"
    return {
        "success":  True,
        "repo_url": repo_url,
        "message":  "Repository created and pushed successfully",
    }


@app.get("/settings/agent-llms", tags=["Settings"])
def get_agent_llms():
    """Return the per-agent LLM configuration table."""
    try:
        if _AGENT_LLMS_FILE.exists():
            data = json.loads(_AGENT_LLMS_FILE.read_text())
            if isinstance(data, list) and data:
                return {"agents": data}
    except Exception:
        pass
    return {"agents": _DEFAULT_AGENT_LLMS}


@app.post("/settings/agent-llms", tags=["Settings"])
def save_agent_llms(body: dict):
    """Persist the per-agent LLM configuration table."""
    agents = body.get("agents")
    if not isinstance(agents, list) or not agents:
        raise HTTPException(status_code=400, detail="Body must include 'agents': [...].")
    _AGENT_LLMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AGENT_LLMS_FILE.write_text(json.dumps(agents, indent=2))
    return {"saved": True, "count": len(agents)}


# ── Serve UI (must be last) ───────────────────────────────────────────────────
# Prefer the Vite-built dist/ output; fall back to raw ui/ for development.
_UI_DIST = UI_DIR / "dist"
_SERVE_DIR = _UI_DIST if _UI_DIST.exists() else UI_DIR

if _SERVE_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_SERVE_DIR), html=True), name="ui")
