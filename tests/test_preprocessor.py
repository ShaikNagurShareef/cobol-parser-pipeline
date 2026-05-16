"""Unit tests for the COBOL COPY/REPLACE preprocessor."""

import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pipeline.preprocessor import preprocess

# Fixed-format COBOL: columns 1-6 blank, column 7+ for code.
# We use 7-space indent to stay in the code area.
_IND = "       "  # 7 spaces


def _write_files(files: dict[str, str]) -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp())
    for name, content in files.items():
        (tmp / name).write_text(content)
    return tmp


def test_copy_basic():
    """COPY without REPLACING should inline the copybook verbatim."""
    tmp = _write_files({
        "MAIN.cbl": (
            f"{_IND}WORKING-STORAGE SECTION.\n"
            f"{_IND}COPY MYBOOK.\n"
            f"{_IND}PROCEDURE DIVISION.\n"
        ),
        "MYBOOK.cpy": f"{_IND}01 WS-FIELD PIC X(10).\n",
    })
    result = preprocess(tmp / "MAIN.cbl", [tmp])
    assert "WS-FIELD" in result.expanded_source
    assert "COPY MYBOOK" not in result.expanded_source


def test_copy_replacing():
    """COPY REPLACING should substitute tokens."""
    tmp = _write_files({
        "MAIN.cbl": (
            f"{_IND}WORKING-STORAGE SECTION.\n"
            f"{_IND}COPY MYBOOK REPLACING ==:TAG:== BY ==WS==.\n"
        ),
        "MYBOOK.cpy": f"{_IND}01 :TAG:-FIELD PIC X(5).\n",
    })
    result = preprocess(tmp / "MAIN.cbl", [tmp])
    assert "WS-FIELD" in result.expanded_source
    assert ":TAG:-FIELD" not in result.expanded_source


def test_nested_copy():
    """Nested COPY (copybook that COPYs another) should expand recursively."""
    tmp = _write_files({
        "MAIN.cbl": f"{_IND}COPY OUTER.\n",
        "OUTER.cpy": (
            f"{_IND}01 OUTER-FLD PIC X.\n"
            f"{_IND}COPY INNER.\n"
        ),
        "INNER.cpy": f"{_IND}01 INNER-FLD PIC 9.\n",
    })
    result = preprocess(tmp / "MAIN.cbl", [tmp])
    assert "OUTER-FLD" in result.expanded_source
    assert "INNER-FLD" in result.expanded_source


def test_provenance_map_length():
    """Provenance map should have one entry per expanded line."""
    tmp = _write_files({
        "MAIN.cbl": (
            f"{_IND}01 FIELD-A PIC X.\n"
            f"{_IND}01 FIELD-B PIC 9.\n"
        ),
    })
    result = preprocess(tmp / "MAIN.cbl", [])
    assert len(result.provenance_map) == 2


def test_provenance_tracks_origin():
    """Lines from a copybook should carry origin_file pointing to the copybook."""
    tmp = _write_files({
        "MAIN.cbl": f"{_IND}COPY BOOK1.\n",
        "BOOK1.cpy": f"{_IND}01 MY-FIELD PIC X.\n",
    })
    result = preprocess(tmp / "MAIN.cbl", [tmp])
    copybook_lines = [
        p for p in result.provenance_map
        if "BOOK1" in p.origin_file.upper()
    ]
    assert copybook_lines, "No lines attributed to BOOK1"


def test_no_copy():
    """Source without COPY should pass through unchanged."""
    tmp = _write_files({
        "PLAIN.cbl": (
            f"{_IND}IDENTIFICATION DIVISION.\n"
            f"{_IND}PROGRAM-ID. PLAIN.\n"
            f"{_IND}PROCEDURE DIVISION.\n"
            f"{_IND}    STOP RUN.\n"
        ),
    })
    result = preprocess(tmp / "PLAIN.cbl", [])
    assert "PROGRAM-ID" in result.expanded_source
    assert result.copy_statements == []
