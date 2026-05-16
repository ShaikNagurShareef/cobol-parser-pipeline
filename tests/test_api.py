"""FastAPI endpoint smoke tests."""

import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PROJECT = pathlib.Path(__file__).parent.parent

# Try to import FastAPI test client; skip all if not installed
try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def client():
    """Create a test client backed by a fresh in-memory database."""
    import sqlite3
    import os

    schema_sql = (PROJECT / "storage" / "schema.sql").read_text()

    # Write to a temp file (SQLite needs a file for WAL)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=OFF")
    con.executescript(schema_sql)
    con.execute("PRAGMA foreign_keys=OFF")

    # Seed minimal data
    prog_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    para_uuid = "bbbbbbbb-0000-0000-0000-000000000001"
    item_uuid = "cccccccc-0000-0000-0000-000000000001"

    con.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (prog_uuid, "Program", "TESTPROG", "TEST.cbl", 1, 200, 0, 0, None,
         '{"name":"TESTPROG"}'),
    )
    con.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (para_uuid, "Paragraph", "MAIN-LOGIC", "TEST.cbl", 10, 30, 0, 0,
         prog_uuid, '{"name":"MAIN-LOGIC"}'),
    )
    con.execute(
        """INSERT INTO data_items
           (uuid, program_uuid, name, level, pic, usage, sign,
            occurs_min, occurs_max, occurs_odo, redefines, value_raw,
            canonical_kind, precision, scale, signed, length,
            copybook_origin, start_line, end_line)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item_uuid, prog_uuid, "WS-AMOUNT", 5, "S9(9)V99", "COMP-3", None,
         None, None, None, None, None,
         "decimal", 11, 2, 1, None, None, 11, 11),
    )
    con.execute(
        "INSERT INTO control_flow (from_uuid, to_uuid, edge_type) VALUES (?,?,?)",
        (prog_uuid, para_uuid, "PERFORM"),
    )
    con.execute(
        """INSERT INTO call_graph
           (caller_uuid, callee_name, callee_uuid, call_site_uuid, call_type, is_resolved)
           VALUES (?,?,?,?,?,?)""",
        (prog_uuid, "EXTPROG", None, None, "CALL_LITERAL", 0),
    )
    con.commit()
    con.close()

    # Patch DB path in api.main before importing
    os.environ["PIPELINE_DB"] = db_path

    from api.main import app
    with TestClient(app) as c:
        yield c

    os.unlink(db_path)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_program_not_found(client):
    r = client.get("/programs/NONEXISTENT")
    assert r.status_code == 404


def test_program_found(client):
    r = client.get("/programs/TESTPROG")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "Program"


def test_paragraphs_uuid(client):
    para_uuid = "bbbbbbbb-0000-0000-0000-000000000001"
    r = client.get(f"/paragraphs/{para_uuid}")
    assert r.status_code == 200


def test_data_items_uuid(client):
    item_uuid = "cccccccc-0000-0000-0000-000000000001"
    r = client.get(f"/data-items/{item_uuid}")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "WS-AMOUNT"


def test_control_flow(client):
    prog_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    r = client.get(f"/control-flow/{prog_uuid}")
    assert r.status_code == 200
    # endpoint returns either a list of edges or a dict with "edges" key
    data = r.json()
    assert isinstance(data, (list, dict))


def test_reports_coverage(client):
    r = client.get("/reports/coverage")
    assert r.status_code == 200


def test_reports_risk_register(client):
    r = client.get("/reports/risk-register")
    assert r.status_code == 200
