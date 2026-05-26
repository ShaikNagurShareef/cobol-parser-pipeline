"""Parse COBOL copybook (.cpy) files through ProLeap by wrapping them
in a minimal host program skeleton.

Produces:
  - A set of data item names defined in the copybook
  - A copybook_catalog row with name, source_file, data_item_count
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


_WRAPPER = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DUMMY-WRAP.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY {name}.
       PROCEDURE DIVISION.
           STOP RUN.
"""

_ALL_CPY_DIRS: list[pathlib.Path] = [
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy",
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-bms",
    PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-stubs",
]


def parse_copybook(
    cpy_file: pathlib.Path,
    copybook_dirs: list[pathlib.Path] | None = None,
) -> dict:
    """Parse a copybook and return a catalog entry dict.

    Returns:
        {name, source_file, data_item_count, data_item_names, parse_status, error_msg}
    """
    from parsers.cobol.proleap_wrapper import parse_cobol_file
    from parsers.cobol.ast_normalizer import normalize

    dirs = copybook_dirs or _ALL_CPY_DIRS
    stem = cpy_file.stem.upper()

    # Write a temp wrapper .cbl so ProLeap can parse the copybook
    with tempfile.NamedTemporaryFile(
        suffix=".cbl", prefix=f"wrap_{stem}_", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(_WRAPPER.format(name=stem))
        tmp_path = pathlib.Path(tmp.name)

    try:
        raw = parse_cobol_file(tmp_path, dirs)
        nodes = normalize(raw)
        data_names = [n["name"] for n in nodes if n["kind"] == "DataItem"]
        return {
            "name": stem,
            "source_file": str(cpy_file),
            "data_item_count": len(data_names),
            "data_item_names": data_names,
            "parse_status": "OK",
            "error_msg": None,
        }
    except Exception as exc:
        return {
            "name": stem,
            "source_file": str(cpy_file),
            "data_item_count": 0,
            "data_item_names": [],
            "parse_status": "ERROR",
            "error_msg": str(exc)[:200],
        }
    finally:
        tmp_path.unlink(missing_ok=True)


def build_copybook_name_index(
    copybook_dirs: list[pathlib.Path] | None = None,
) -> dict[str, str]:
    """Return {data_item_name_upper: copybook_stem_upper} for all copybooks.

    When the same name appears in multiple copybooks, the last one wins
    (order: cpy, cpy-bms, cpy-stubs — least-specific first so stubs lose).
    """
    dirs = copybook_dirs or _ALL_CPY_DIRS
    index: dict[str, str] = {}
    for d in dirs:
        if not d.exists():
            continue
        for cpy in sorted(d.glob("*.cpy")) + sorted(d.glob("*.CPY")):
            entry = parse_copybook(cpy, dirs)
            for name in entry["data_item_names"]:
                index[name.upper()] = cpy.stem.upper()
    return index


def persist_catalog(
    con: sqlite3.Connection,
    copybook_dirs: list[pathlib.Path] | None = None,
    source_type: str = "COPYBOOK",
) -> int:
    """Parse all .cpy files in *copybook_dirs* and upsert into copybook_catalog.

    Returns the number of copybooks successfully parsed.
    """
    dirs = copybook_dirs or _ALL_CPY_DIRS
    ok = 0
    for d in dirs:
        if not d.exists():
            continue
        typ = source_type
        if "bms" in d.name.lower():
            typ = "BMS_COPYBOOK"
        elif "stub" in d.name.lower():
            typ = "STUB"
        for cpy in sorted(d.glob("*.cpy")) + sorted(d.glob("*.CPY")):
            entry = parse_copybook(cpy, dirs)
            import json as _json
            con.execute(
                """
                INSERT OR REPLACE INTO copybook_catalog
                    (name, source_file, source_type, data_item_count, item_names_json, parse_status, error_msg)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    entry["name"],
                    entry["source_file"],
                    typ,
                    entry["data_item_count"],
                    _json.dumps(entry["data_item_names"]),
                    entry["parse_status"],
                    entry["error_msg"],
                ),
            )
            if entry["parse_status"] == "OK":
                ok += 1
    return ok
