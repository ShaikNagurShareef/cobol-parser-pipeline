"""Thin Python wrapper around the CobolCstExporter fat JAR.

Calls the Java exporter as a subprocess and returns the parsed JSON.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
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
    cmd = ["java", "-jar", str(_JAR), str(cbl_file)] + [str(d) for d in dirs if d.exists()]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if not result.stdout.strip():
        # Java printed nothing to stdout — check stderr
        stderr_snippet = result.stderr[:500] if result.stderr else "(empty)"
        raise RuntimeError(
            f"CobolCstExporter produced no output for {cbl_file.name}.\n"
            f"stderr: {stderr_snippet}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON from CobolCstExporter for {cbl_file.name}: {exc}\n"
            f"First 300 chars: {result.stdout[:300]}"
        ) from exc
