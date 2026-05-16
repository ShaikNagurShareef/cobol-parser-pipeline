"""Layer 1 artifact builder — persist typed AST to JSON and the nodes table."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

from storage.db import upsert_node


OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "layer1"


def persist(
    nodes: list[dict[str, Any]],
    program_name: str,
    con: sqlite3.Connection,
    output_dir: pathlib.Path | None = None,
) -> None:
    """Write nodes to the SQLite spine table and emit a JSON artifact file.

    Args:
        nodes:        Flat list of typed AST nodes from ast_normalizer.
        program_name: Used for naming the output JSON file.
        con:          Open SQLite connection (caller manages transaction).
        output_dir:   Directory for JSON artifacts (defaults to output/layer1/).
    """
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Write to DB
    for node in nodes:
        upsert_node(con, node)

    # Write JSON artifact
    artifact = {
        "layer": 1,
        "program": program_name,
        "node_count": len(nodes),
        "nodes": nodes,
    }
    out_file = out / f"{program_name}.json"
    out_file.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
