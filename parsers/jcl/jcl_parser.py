"""JCL parser — regex-based structural extractor.

Extracts: JOB card, EXEC steps (program/proc), DD statements (DSN, DISP),
and builds: jcl_job, jcl_dd, jcl_dependency, parse_coverage rows.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from storage.db import init_db, transaction
from storage.uuid_gen import make_named_uuid, make_uuid

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


def _norm_path(p: pathlib.Path) -> str:
    try:
        return str(p.resolve().relative_to(_PROJECT_ROOT.resolve()))
    except ValueError:
        return str(p)

_JOB_RE   = re.compile(r"^//([A-Z0-9@#$]{1,8})\s+JOB\b", re.IGNORECASE)
_EXEC_RE  = re.compile(r"^//([A-Z0-9@#$]{0,8})\s+EXEC\s+(PGM=|PROC=)?([A-Z0-9@#$.-]+)", re.IGNORECASE)
_DD_RE    = re.compile(r"^//([A-Z0-9@#$]{0,8})\s+DD\b", re.IGNORECASE)
_DSN_RE   = re.compile(r"DSN=([A-Z0-9.@#$&\(\)-]+)", re.IGNORECASE)
_DISP_RE  = re.compile(r"DISP=\(?([A-Z,]+)\)?", re.IGNORECASE)
_CONT_RE  = re.compile(r"^//\s{16,}")  # continuation lines
_COMMENT_RE = re.compile(r"^//\*")


def parse_jcl_file(jcl_file: pathlib.Path, db_path: pathlib.Path) -> dict:
    """Parse a JCL file and persist to jcl_job, jcl_dd, jcl_dependency tables."""
    init_db(db_path)
    text = jcl_file.read_text(errors="replace")
    lines = _join_continuations(text.splitlines())

    jobs: list[dict] = []
    errors: list[str] = []

    current_job: dict | None = None
    current_step: dict | None = None
    current_dd: dict | None = None

    with transaction() as con:
        for lineno, line in enumerate(lines, 1):
            if _COMMENT_RE.match(line) or line.startswith("//*"):
                continue

            m_job = _JOB_RE.match(line)
            m_exec = _EXEC_RE.match(line)
            m_dd = _DD_RE.match(line)

            if m_job:
                job_name = m_job.group(1).upper()
                job_uuid = make_named_uuid("JCL_JOB", job_name)
                current_job = {"name": job_name, "uuid": job_uuid, "steps": []}
                jobs.append(current_job)
                _upsert_node(con, job_uuid, "JclJob", str(jcl_file), lineno, lineno, job_name)
                current_step = None
                current_dd = None

            elif m_exec and current_job:
                step_name = m_exec.group(1).upper() or f"STEP{lineno}"
                pgm_or_proc = m_exec.group(2) or ""
                target = m_exec.group(3).upper()
                step_uuid = make_named_uuid("JCL_STEP", f"{current_job['name']}.{step_name}")
                is_pgm = pgm_or_proc.upper().startswith("PGM")
                current_step = {
                    "name": step_name, "uuid": step_uuid,
                    "program": target if is_pgm else None,
                    "proc": target if not is_pgm else None,
                }
                current_job["steps"].append(current_step)
                _upsert_node(con, step_uuid, "JclStep", str(jcl_file), lineno, lineno, step_name)
                con.execute(
                    """
                    INSERT OR IGNORE INTO jcl_job
                        (job_uuid, job_name, step_uuid, step_name, program, proc)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (current_job["uuid"], current_job["name"], step_uuid, step_name,
                     current_step["program"], current_step["proc"]),
                )

            elif m_dd and current_step:
                dd_name = m_dd.group(1).upper() or "DD"
                dsn_m = _DSN_RE.search(line)
                disp_m = _DISP_RE.search(line)
                dsn = dsn_m.group(1).upper() if dsn_m else None
                disp = disp_m.group(1).upper() if disp_m else "OLD"
                con.execute(
                    """
                    INSERT OR IGNORE INTO jcl_dd
                        (step_uuid, dd_name, dataset, disposition)
                    VALUES (?,?,?,?)
                    """,
                    (current_step["uuid"], dd_name, dsn, disp),
                )
                if dsn:
                    current_dd = {"dd": dd_name, "dsn": dsn, "disp": disp,
                                  "job": current_job["name"]}

        # Build job dependency edges from DD dataset reuse
        _build_job_deps(con)

        # Coverage
        con.execute(
            """
            INSERT OR REPLACE INTO parse_coverage
                (source_file, source_type, status, parse_errors, error_messages)
            VALUES (?,?,?,?,?)
            """,
            (_norm_path(jcl_file), "JCL", "OK" if not errors else "PARSER_ERROR",
             len(errors), json.dumps(errors[:5])),
        )

    return {"file": str(jcl_file), "jobs": len(jobs), "status": "OK"}


def _build_job_deps(con: sqlite3.Connection) -> None:
    """Identify datasets produced by one job and consumed by another."""
    # Produced: DISP=NEW or DISP=(NEW,...) or DISP=MOD
    producers = con.execute(
        """
        SELECT jj.job_name, d.dataset
        FROM jcl_dd d
        JOIN jcl_job jj ON jj.step_uuid = d.step_uuid
        WHERE d.dataset IS NOT NULL
          AND UPPER(d.disposition) LIKE 'NEW%'
           OR UPPER(d.disposition) LIKE 'MOD%'
        """
    ).fetchall()

    consumers = con.execute(
        """
        SELECT jj.job_name, d.dataset
        FROM jcl_dd d
        JOIN jcl_job jj ON jj.step_uuid = d.step_uuid
        WHERE d.dataset IS NOT NULL
          AND (UPPER(d.disposition) LIKE 'OLD%' OR UPPER(d.disposition) LIKE 'SHR%')
        """
    ).fetchall()

    prod_map: dict[str, str] = {r["dataset"]: r["job_name"] for r in producers}
    for row in consumers:
        dsn = row["dataset"]
        if dsn in prod_map and prod_map[dsn] != row["job_name"]:
            con.execute(
                """
                INSERT OR IGNORE INTO jcl_dependency
                    (producer_job, consumer_job, dataset)
                VALUES (?,?,?)
                """,
                (prod_map[dsn], row["job_name"], dsn),
            )


def _upsert_node(con: sqlite3.Connection, uuid: str, kind: str, source: str,
                 start: int, end: int, name: str) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO nodes (uuid, kind, source_file, start_line, end_line, name)
        VALUES (?,?,?,?,?,?)
        """,
        (uuid, kind, source, start, end, name),
    )


def _join_continuations(lines: list[str]) -> list[str]:
    """Join JCL continuation lines (column 16+ continuation marker)."""
    result = []
    for line in lines:
        if _CONT_RE.match(line) and result:
            result[-1] = result[-1].rstrip() + " " + line.strip()
        else:
            result.append(line)
    return result
