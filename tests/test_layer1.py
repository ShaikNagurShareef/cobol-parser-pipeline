"""Tests for Layer 1 AST normalization."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PROJECT = pathlib.Path(__file__).parent.parent


def _sample_raw() -> dict:
    """Minimal raw JSON shape as CobolCstExporter produces."""
    return {
        "file": "TESTPROG.cbl",
        "compilation_units": [
            {
                "name": "TESTPROG",
                "start_line": 1,
                "end_line": 200,
                "data_items": [
                    {
                        "level": 1,
                        "name": "WS-RECORD",
                        "pic": None,
                        "usage": None,
                        "sign": None,
                        "occurs": None,
                        "redefines": None,
                        "value": None,
                        "start_line": 10,
                        "end_line": 10,
                    },
                    {
                        "level": 5,
                        "name": "WS-AMOUNT",
                        "pic": "S9(9)V99",
                        "usage": "COMP-3",
                        "sign": None,
                        "occurs": None,
                        "redefines": None,
                        "value": None,
                        "start_line": 11,
                        "end_line": 11,
                    },
                    {
                        "level": 5,
                        "name": "WS-NAME",
                        "pic": "X(30)",
                        "usage": None,
                        "sign": None,
                        "occurs": None,
                        "redefines": None,
                        "value": None,
                        "start_line": 12,
                        "end_line": 12,
                    },
                ],
                "paragraphs": [
                    {
                        "name": "MAIN-LOGIC",
                        "start_line": 100,
                        "end_line": 120,
                        "statements": [
                            {"kind": "MOVE", "text": "MOVE SPACES TO WS-NAME",
                             "start_line": 101},
                            {"kind": "COMPUTE", "text": "COMPUTE WS-AMOUNT = 0",
                             "start_line": 102},
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


def test_normalize_produces_program_node():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    program_nodes = [n for n in nodes if n["kind"] == "Program"]
    assert len(program_nodes) == 1
    assert program_nodes[0]["name"] == "TESTPROG"


def test_normalize_produces_data_items():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    data_items = [n for n in nodes if n["kind"] == "DataItem"]
    assert len(data_items) == 3
    names = {d["name"] for d in data_items}
    assert "WS-AMOUNT" in names


def test_normalize_type_lowering_decimal():
    from parsers.cobol.ast_normalizer import lower_type
    ct = lower_type("S9(9)V99", "COMP-3")
    assert ct["kind"] == "decimal"
    assert ct["precision"] == 11
    assert ct["scale"] == 2
    assert ct["signed"] is True


def test_normalize_type_lowering_alpha():
    from parsers.cobol.ast_normalizer import lower_type
    ct = lower_type("X(30)", None)
    assert ct["kind"] == "alpha"
    assert ct["length"] == 30


def test_normalize_type_lowering_binary():
    from parsers.cobol.ast_normalizer import lower_type
    ct = lower_type("9(9)", "COMP-4")
    assert ct["kind"] == "binary"


def test_normalize_produces_paragraphs():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    paras = [n for n in nodes if n["kind"] == "Paragraph"]
    assert len(paras) == 1
    assert paras[0]["name"] == "MAIN-LOGIC"


def test_normalize_produces_statements():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    stmts = [n for n in nodes if n["kind"].startswith("Stmt_")]
    assert len(stmts) == 2
    kinds = {s["kind"] for s in stmts}
    assert "Stmt_MOVE" in kinds
    assert "Stmt_COMPUTE" in kinds


def test_uuids_are_unique():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    uuids = [n["uuid"] for n in nodes]
    assert len(uuids) == len(set(uuids)), "Duplicate UUIDs found"


def test_parent_uuid_links():
    from parsers.cobol.ast_normalizer import normalize
    nodes = normalize(_sample_raw())
    node_map = {n["uuid"]: n for n in nodes}
    for node in nodes:
        parent_uuid = node.get("parent_uuid")
        if parent_uuid:
            assert parent_uuid in node_map, (
                f"parent_uuid {parent_uuid} not in nodes"
            )
