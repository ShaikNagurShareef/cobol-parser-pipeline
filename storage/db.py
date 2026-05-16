"""SQLite session factory with WAL mode and schema initialisation."""

from __future__ import annotations

import os
import pathlib
import sqlite3
from contextlib import contextmanager
from typing import Generator

_DB_PATH: pathlib.Path | None = None
_SCHEMA_SQL = pathlib.Path(__file__).parent / "schema.sql"

_DEFAULT_DB = pathlib.Path(__file__).parent.parent / "artifacts" / "pipeline.db"


def init_db(db_path: str | pathlib.Path) -> None:
    """Create/open the database and apply the schema (idempotent)."""
    global _DB_PATH
    _DB_PATH = pathlib.Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(_DB_PATH)
    con.executescript(_SCHEMA_SQL.read_text())
    con.commit()
    con.close()


def _resolve_path(db_path: str | pathlib.Path | None = None) -> pathlib.Path:
    if db_path:
        return pathlib.Path(db_path)
    if _DB_PATH:
        return _DB_PATH
    # Fall back to env var (useful for tests)
    env = os.environ.get("PIPELINE_DB")
    if env:
        return pathlib.Path(env)
    return _DEFAULT_DB


def get_connection(db_path: str | pathlib.Path | None = None) -> sqlite3.Connection:
    """Return a raw sqlite3 connection with WAL mode active."""
    path = _resolve_path(db_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def transaction(db_path: str | pathlib.Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a connection and commits/rolls-back."""
    con = get_connection(db_path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def upsert_node(con: sqlite3.Connection, node: dict) -> None:
    """Insert or replace a node row."""
    import json
    con.execute(
        """
        INSERT OR REPLACE INTO nodes
            (uuid, kind, name, source_file, start_line, end_line,
             start_col, end_col, parent_uuid, payload_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node["uuid"],
            node["kind"],
            node.get("name"),
            node["source_file"],
            node.get("start_line"),
            node.get("end_line"),
            node.get("start_col", 0),
            node.get("end_col", 0),
            node.get("parent_uuid"),
            json.dumps(node.get("payload"), ensure_ascii=False),
        ),
    )
