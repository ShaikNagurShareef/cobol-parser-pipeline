"""Thin Python wrapper around the CobolCstExporter fat JAR.

Calls the Java exporter as a subprocess and returns the parsed JSON.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile
from typing import Any

# Resolved once at import time
_HERE = pathlib.Path(__file__).parent
_PROJECT_ROOT = _HERE.parent.parent
_JAR = (
    _PROJECT_ROOT
    / "cobol-exporter"
    / "target"
    / "cobol-exporter-1.0.0-jar-with-dependencies.jar"
)

# Standard copybook search paths (order matters — first match wins)
_DEFAULT_CPY_DIRS: list[pathlib.Path] = [
    _PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy",
    _PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-bms",
    _PROJECT_ROOT / "external" / "carddemo" / "app" / "cpy-stubs",
]


_ID_COMMENT_PARAS = frozenset({
    "AUTHOR", "DATE-WRITTEN", "DATE-COMPILED",
    "INSTALLATION", "REMARKS", "SECURITY",
})
_DIVISION_KEYWORDS = frozenset({
    "IDENTIFICATION", "ID", "ENVIRONMENT", "DATA", "PROCEDURE",
})


def _needs_fixed_format_cleanup(source: str) -> bool:
    """Return True if the file has fixed-format comment/indicator issues."""
    for line in source.splitlines()[:30]:
        if len(line) >= 7 and line[6] in ("*", "/"):
            return True
    return False


def _apply_fixed_format_cleanup(source: str) -> str:
    """Pre-clean COBOL fixed-format source so ProLeap sees it reliably.

    Handles two classes of issue:
    1. Indicator-area comments ('*' or '/' at col 7) — blanked so the ANTLR
       lexer does not see '**' at the start of the code area.
    2. IDENTIFICATION DIVISION free-text paragraphs (AUTHOR, DATE-WRITTEN,
       DATE-COMPILED, INSTALLATION, REMARKS, SECURITY) — their content lines
       are blanked because ProLeap's COMMENTENTRYLINE token does not match
       arbitrary month/day names, causing parser recovery failures that prevent
       the PROCEDURE DIVISION from being extracted.
    """
    out_lines: list[str] = []
    in_id_div = False
    in_comment_para = False  # inside an AUTHOR / DATE-WRITTEN etc. paragraph

    for line in source.splitlines():
        if len(line) < 7:
            out_lines.append(line)
            continue

        indicator = line[6]

        # Always blank actual comment lines (indicator = * or /)
        if indicator in ("*", "/"):
            out_lines.append("")
            continue

        # Preserve continuation lines verbatim — stripping the '-' indicator
        # breaks ProLeap's preprocessor which needs it to join string literals
        # split across lines (e.g. long HTML VALUE clauses in CBSTM03A).
        if indicator == "-":
            out_lines.append("      -" + line[7:72].rstrip())
            continue

        # Blank sequence area; cap at column 72; keep indicator + code area
        code_area = line[7:72].rstrip()  # cols 8-72 (0-indexed 7-71)
        stripped_upper = code_area.strip().upper()

        # Track IDENTIFICATION DIVISION entry
        if any(stripped_upper.startswith(kw) for kw in
               ("IDENTIFICATION DIVISION", "ID DIVISION")):
            in_id_div = True
            in_comment_para = False

        # Leaving IDENTIFICATION DIVISION
        elif in_id_div and any(
            stripped_upper.startswith(kw + " DIVISION") or
            stripped_upper.startswith(kw + ".")
            for kw in ("ENVIRONMENT", "DATA", "PROCEDURE")
        ):
            in_id_div = False
            in_comment_para = False

        # Detect start of an ID-division comment paragraph
        elif in_id_div:
            para_name = stripped_upper.rstrip(".").strip()
            if para_name in _ID_COMMENT_PARAS:
                in_comment_para = True
                # Keep the paragraph header line itself (e.g. DATE-WRITTEN.)
                out_lines.append("      " + " " + code_area)
                continue
            elif stripped_upper.endswith(".") and not in_comment_para:
                # Any other paragraph heading ends the comment-para scope
                in_comment_para = False

        # Blank content lines inside ID-division comment paragraphs
        if in_id_div and in_comment_para and stripped_upper:
            # Check if this is actually a new paragraph header, not content
            para_name = stripped_upper.rstrip(".").strip()
            if para_name in _ID_COMMENT_PARAS or any(
                stripped_upper.startswith(kw) for kw in
                ("PROGRAM-ID", "IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE")
            ):
                in_comment_para = False
            else:
                out_lines.append("")  # blank the free-text content
                continue

        # Normalise COPY 'name'[.] → COPY name. (ProLeap doesn't resolve single-quoted names)
        cleaned_code = re.sub(
            r"\bCOPY\s+'([^']+)'\s*(\.?)",
            lambda m: f"COPY {m.group(1).strip()}" + ("." if m.group(2) else "."),
            code_area,
            flags=re.IGNORECASE,
        )
        # Strip VALUE literals containing CSS colons (e.g. HTML VALUE clauses like
        # `VALUE '<td style="font:12px">'`). ProLeap's ANTLR grammar treats ':'
        # as a special token and chokes on it inside NONNUMERICLITERAL context.
        # Replacing with SPACE keeps the data-item structure syntactically valid.
        cleaned_code = re.sub(
            r"\bVALUE\b(\s+ALL)?\s+'[^']*:[^']*'",
            "VALUE SPACE",
            cleaned_code,
            flags=re.IGNORECASE,
        )
        out_lines.append("      " + " " + cleaned_code)

    return "\n".join(out_lines)


def parse_cobol_file(
    cbl_file: pathlib.Path,
    copybook_dirs: list[pathlib.Path] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Parse a COBOL source file and return the compact JSON structure.

    Args:
        cbl_file:      Path to the .cbl / .CBL source file.
        copybook_dirs: Directories to search for copybooks (COPY resolution).
        timeout:       Seconds before the Java process is killed.

    Returns:
        Dict with keys: file, parse_errors, preprocess_errors, compilation_units.

    Raises:
        FileNotFoundError: if cbl_file or the JAR does not exist.
        RuntimeError:      if the Java process exits non-zero.
    """
    cbl_file = pathlib.Path(cbl_file)
    if not cbl_file.exists():
        raise FileNotFoundError(f"COBOL file not found: {cbl_file}")
    if not _JAR.exists():
        raise FileNotFoundError(
            f"CobolCstExporter JAR not found: {_JAR}\n"
            "Run: cd cobol-exporter && mvn clean package -DskipTests"
        )

    dirs = copybook_dirs if copybook_dirs is not None else _DEFAULT_CPY_DIRS

    # Apply Python-side fixed-format cleanup for files that need it.
    # This prevents ProLeap's preprocessor fallback from handing raw
    # fixed-format lines (with '**' in the comment indicator area) directly
    # to the ANTLR lexer, which then fails with "mismatched input '**'".
    source = cbl_file.read_text(encoding="utf-8", errors="replace")
    tmp_path: pathlib.Path | None = None
    try:
        if _needs_fixed_format_cleanup(source):
            cleaned = _apply_fixed_format_cleanup(source)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cbl", delete=False,
                encoding="utf-8", dir=cbl_file.parent
            ) as tmp:
                tmp.write(cleaned)
                tmp_path = pathlib.Path(tmp.name)
            target = tmp_path
        else:
            target = cbl_file

        cmd = ["java", "-jar", str(_JAR), str(target)] + [
            str(d) for d in dirs if d.exists()
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()

    if not result.stdout.strip():
        stderr_snippet = result.stderr[:500] if result.stderr else "(empty)"
        raise RuntimeError(
            f"CobolCstExporter produced no output for {cbl_file.name}.\n"
            f"stderr: {stderr_snippet}"
        )

    try:
        data = json.loads(result.stdout)
        # Restore the original file path (temp file path leaks into JSON)
        data["file"] = str(cbl_file)
        return data
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON from CobolCstExporter for {cbl_file.name}: {exc}\n"
            f"First 300 chars: {result.stdout[:300]}"
        ) from exc
