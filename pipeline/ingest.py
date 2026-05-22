"""Single-file COBOL ingestion pipeline.

Usage:
    python pipeline/ingest.py <path/to/file.cbl> [--db artifacts/pipeline.db]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

# Allow running from project root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from parsers.cobol.proleap_wrapper import parse_cobol_file
from parsers.cobol.ast_normalizer import normalize
from pipeline.preprocessor import preprocess
from artifacts import layer1_ast, layer2_symbols
from storage.db import init_db, transaction

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "artifacts" / "pipeline.db"

CPY_DIRS = [
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy",
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-bms",
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-stubs",
]


def ingest_file(
    cbl_file: pathlib.Path,
    db_path: pathlib.Path = DEFAULT_DB,
    copybook_dirs: list[pathlib.Path] | None = None,
    verbose: bool = True,
) -> dict:
    """Parse and persist a single COBOL file through Layers 1 and 2.

    Returns a result dict with keys: program, status, counts, errors, elapsed_ms.
    """
    start = time.perf_counter()
    dirs = copybook_dirs or CPY_DIRS
    result: dict = {"program": cbl_file.stem, "file": str(cbl_file)}

    # 1. Run ProLeap Java exporter
    try:
        raw = parse_cobol_file(cbl_file, dirs)
    except Exception as exc:
        result.update({"status": "PREPROCESSOR_FAILURE", "error": str(exc)})
        _record_coverage(db_path, cbl_file, "COBOL", "PREPROCESSOR_FAILURE", [], str(exc))
        return result

    parse_errors = raw.get("parse_errors", [])
    preprocess_errors = raw.get("preprocess_errors", [])
    all_errors = parse_errors + preprocess_errors

    status = "OK" if not parse_errors else "PARSER_ERROR"
    if preprocess_errors and not parse_errors:
        status = "PREPROCESSOR_FAILURE" if not raw.get("compilation_units") else "OK"

    # 2. Python preprocessor (for provenance + copybook tracking)
    try:
        pre_result = preprocess(cbl_file, dirs)
    except Exception:
        pre_result = None

    copy_statements = pre_result.copy_statements if pre_result else []

    # 3. Normalize to typed AST
    nodes = normalize(raw)
    if not nodes:
        result.update({"status": "PARSER_ERROR", "error": "No AST nodes produced"})
        _record_coverage(db_path, cbl_file, "COBOL", "PARSER_ERROR", parse_errors)
        return result

    prog_name = nodes[0]["name"] if nodes else cbl_file.stem

    # 4. Persist Layers 1 and 2
    init_db(db_path)
    with transaction() as con:
        layer1_ast.persist(nodes, prog_name, con)
        layer2_symbols.persist(nodes, copy_statements, prog_name, con)
        _record_coverage_con(con, cbl_file, "COBOL", status, parse_errors)

    elapsed = int((time.perf_counter() - start) * 1000)

    prog_nodes = [n for n in nodes if n["kind"] == "Program"]
    data_items = [n for n in nodes if n["kind"] == "DataItem"]
    paragraphs = [n for n in nodes if n["kind"] == "Paragraph"]

    result.update({
        "status": status,
        "program": prog_name,
        "counts": {
            "programs": len(prog_nodes),
            "data_items": len(data_items),
            "paragraphs": len(paragraphs),
            "nodes_total": len(nodes),
        },
        "errors": all_errors,
        "elapsed_ms": elapsed,
    })

    if verbose:
        print(
            f"  [{status:25s}] {prog_name:30s} "
            f"items={len(data_items):4d} paras={len(paragraphs):3d} "
            f"({elapsed}ms)"
        )

    return result


def _record_coverage(
    db_path: pathlib.Path,
    source_file: pathlib.Path,
    source_type: str,
    status: str,
    errors: list[str],
    error_msg: str = "",
) -> None:
    init_db(db_path)
    with transaction() as con:
        _record_coverage_con(con, source_file, source_type, status, errors, error_msg)


def _norm_source_path(p: pathlib.Path) -> str:
    """Normalize to a project-relative path so absolute and relative insertions
    resolve to the same UNIQUE key regardless of which pipeline phase calls us."""
    try:
        return str(p.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(p)


def _record_coverage_con(
    con,
    source_file: pathlib.Path,
    source_type: str,
    status: str,
    errors: list[str],
    error_msg: str = "",
) -> None:
    msgs = list(errors) + ([error_msg] if error_msg else [])
    con.execute(
        """
        INSERT OR REPLACE INTO parse_coverage
            (source_file, source_type, status, parse_errors, error_messages)
        VALUES (?,?,?,?,?)
        """,
        (
            _norm_source_path(source_file),
            source_type,
            status,
            len(errors),
            json.dumps(msgs[:10]),
        ),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingest a single COBOL file")
    ap.add_argument("file", help="Path to .cbl file")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    args = ap.parse_args()

    result = ingest_file(pathlib.Path(args.file), pathlib.Path(args.db))
    print(json.dumps(result, indent=2))
