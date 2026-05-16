"""UUID stability test — same source must produce the same UUIDs across runs."""

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

PROJECT = pathlib.Path(__file__).parent.parent
LAYER1_DIR = PROJECT / "output" / "layer1"
INGEST = PROJECT / "pipeline" / "ingest.py"
CBL_DIR = PROJECT / "external" / "carddemo" / "app" / "cbl"
CPY_DIR = PROJECT / "external" / "carddemo" / "app" / "cpy"


def test_uuid_stability_from_same_raw():
    """UUIDs produced by normalize() are stable across two calls on the same input."""
    sys.path.insert(0, str(PROJECT))
    from parsers.cobol.ast_normalizer import normalize

    raw = {
        "file": "COSGN00C.cbl",
        "compilation_units": [
            {
                "name": "COSGN00C",
                "start_line": 1,
                "end_line": 500,
                "data_items": [
                    {"level": 1, "name": "WS-COMM-AREA", "pic": None,
                     "usage": None, "sign": None, "occurs": None,
                     "redefines": None, "value": None,
                     "start_line": 50, "end_line": 50},
                ],
                "paragraphs": [
                    {
                        "name": "MAIN-PARA",
                        "start_line": 200,
                        "end_line": 220,
                        "statements": [
                            {"kind": "MOVE", "text": "MOVE LOW-VALUES TO WS-COMM-AREA",
                             "start_line": 201},
                        ],
                    }
                ],
                "call_statements": [],
                "exec_cics": [],
                "exec_sql": [],
                "file_control": [],
            }
        ],
        "parse_errors": [],
        "preprocess_errors": [],
    }

    nodes_run1 = normalize(raw)
    nodes_run2 = normalize(raw)

    uuids_run1 = {n["uuid"] for n in nodes_run1}
    uuids_run2 = {n["uuid"] for n in nodes_run2}

    assert uuids_run1 == uuids_run2, "UUID mismatch between two normalize() calls on same input"
    assert len(uuids_run1) == len(nodes_run1), "Duplicate UUIDs within a single run"


def test_uuid_gen_deterministic():
    """uuid5 key must always produce the same UUID for the same inputs."""
    sys.path.insert(0, str(PROJECT))
    from storage.uuid_gen import make_uuid

    uid1 = make_uuid("COSGN00C.cbl", 10, 0, 15, 0, "Paragraph", "MAIN-LOGIC")
    uid2 = make_uuid("COSGN00C.cbl", 10, 0, 15, 0, "Paragraph", "MAIN-LOGIC")
    assert uid1 == uid2

    uid3 = make_uuid("COSGN00C.cbl", 10, 0, 15, 0, "Paragraph", "OTHER-LOGIC")
    assert uid1 != uid3


def test_uuid_gen_changes_with_line():
    """Different lines must produce different UUIDs."""
    sys.path.insert(0, str(PROJECT))
    from storage.uuid_gen import make_uuid

    uid1 = make_uuid("X.cbl", 1, 0, 5, 0, "Statement", "")
    uid2 = make_uuid("X.cbl", 2, 0, 5, 0, "Statement", "")
    assert uid1 != uid2
