"""Deterministic UUID generation for all pipeline artifacts.

Every node gets a uuid5 derived from its canonical identity key, so the
same source file always produces the same UUIDs across pipeline runs.
"""

import uuid

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL


def make_uuid(
    source_file: str,
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    kind: str,
    name: str = "",
) -> str:
    """Return a stable uuid5 string for a source-located node."""
    key = f"{source_file}:{start_line}:{start_col}:{end_line}:{end_col}:{kind}:{name}"
    return str(uuid.uuid5(_NS, key))


def make_named_uuid(scope: str, name: str) -> str:
    """Return a stable uuid5 for a named entity that has no single source position
    (e.g. a copybook entry, a CSD catalog entry, a JCL job).
    """
    key = f"{scope}::{name}"
    return str(uuid.uuid5(_NS, key))


def make_edge_uuid(from_uuid: str, to_uuid: str, edge_type: str) -> str:
    """Return a stable uuid5 for a directed graph edge."""
    key = f"{from_uuid}->{to_uuid}:{edge_type}"
    return str(uuid.uuid5(_NS, key))
