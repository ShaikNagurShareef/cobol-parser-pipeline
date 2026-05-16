"""COBOL copybook preprocessor with provenance tracking.

Scans a raw COBOL source file, identifies COPY statements (including
REPLACING clauses), and produces:
  - expanded_source: the full source with copybook text inserted inline
  - provenance_map:  per-line origin metadata (which file, which original
                     line, which substitutions were applied)
  - copy_statements: list of every COPY found with its copybook name and
                     REPLACING mapping — used by Layer 4 copybook_use table

This runs entirely in Python so we have provenance control. ProLeap's
preprocessor is used separately in the Java layer for grammar parsing.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Substitution:
    from_text: str
    to_text: str


@dataclass
class LineProvenance:
    """Metadata for a single line in the expanded source."""
    expanded_line_no: int       # 1-based line number in expanded source
    origin_file: str            # canonical path of the file the line came from
    origin_line_no: int         # 1-based line number in the origin file
    substitutions: list[Substitution] = field(default_factory=list)


@dataclass
class CopyStatement:
    copybook_name: str
    source_file: str
    source_line: int
    replacing: list[Substitution] = field(default_factory=list)


@dataclass
class PreprocessResult:
    expanded_source: str
    provenance_map: list[LineProvenance]
    copy_statements: list[CopyStatement]
    missing_copybooks: list[str]


# Matches:  COPY  <name>  [REPLACING  ==old== BY ==new== [==old2== BY ==new2==]*] .
_COPY_RE = re.compile(
    r"^\s{6,}\s*COPY\s+([A-Z0-9#@$-]+)"
    r"((?:\s+REPLACING\s+.*?)?)\s*\.\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REPLACING_PAIR = re.compile(r"==([^=]+)==\s+BY\s+==([^=]*)==", re.IGNORECASE)

# Matches a fixed-format COBOL comment/non-code line (col 7 = * or /)
_COMMENT_RE = re.compile(r"^.{6}[*/]")


def preprocess(
    source_file: pathlib.Path,
    copybook_dirs: list[pathlib.Path],
    _seen: set[str] | None = None,
) -> PreprocessResult:
    """Recursively expand COPY statements in *source_file*.

    Args:
        source_file:   The .cbl or .cpy file to process.
        copybook_dirs: Ordered list of directories to search for copybooks.
        _seen:         Internal guard against circular COPY chains.

    Returns:
        A PreprocessResult with expanded source, provenance, and COPY metadata.
    """
    if _seen is None:
        _seen = set()

    abs_path = str(source_file.resolve())
    if abs_path in _seen:
        return PreprocessResult("", [], [], [])
    _seen = _seen | {abs_path}

    raw_lines = source_file.read_text(errors="replace").splitlines()
    expanded: list[str] = []
    provenance: list[LineProvenance] = []
    copy_statements: list[CopyStatement] = []
    missing: list[str] = []

    def _add_line(text: str, origin: str, origin_line: int, subs: list[Substitution]) -> None:
        expanded.append(text)
        provenance.append(
            LineProvenance(
                expanded_line_no=len(expanded),
                origin_file=origin,
                origin_line_no=origin_line,
                substitutions=subs,
            )
        )

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]

        # Skip sequence-number area (cols 1-6) for matching purposes
        code_part = line[6:] if len(line) > 6 else line
        if _COMMENT_RE.match(line):
            _add_line(line, abs_path, i + 1, [])
            i += 1
            continue

        # Detect multi-line COPY by accumulating until we hit a period
        if re.match(r"^\s{6,}\s*COPY\s+", line, re.IGNORECASE):
            copy_block = line
            j = i
            while "." not in copy_block[7:] and j + 1 < len(raw_lines):
                j += 1
                copy_block += " " + raw_lines[j].strip()
            i = j

            m = _COPY_RE.match(copy_block.rstrip())
            if m:
                book_name = m.group(1).upper()
                replacing_text = m.group(2)
                subs = [
                    Substitution(p.group(1).strip(), p.group(2).strip())
                    for p in _REPLACING_PAIR.finditer(replacing_text)
                ]
                copy_stmt = CopyStatement(
                    copybook_name=book_name,
                    source_file=abs_path,
                    source_line=i + 1,
                    replacing=subs,
                )
                copy_statements.append(copy_stmt)

                book_file = _find_copybook(book_name, copybook_dirs)
                if book_file is None:
                    missing.append(book_name)
                    # Emit a comment placeholder so line count stays stable
                    _add_line(
                        f"      * COPY {book_name} (not found)",
                        abs_path, i + 1, [],
                    )
                else:
                    sub_result = preprocess(book_file, copybook_dirs, _seen)
                    missing.extend(sub_result.missing_copybooks)
                    copy_statements.extend(sub_result.copy_statements)

                    for exp_line, prov in zip(
                        sub_result.expanded_source.splitlines(),
                        sub_result.provenance_map,
                    ):
                        # Apply REPLACING substitutions
                        text = exp_line
                        applied_subs: list[Substitution] = list(prov.substitutions)
                        for sub in subs:
                            if sub.from_text in text:
                                text = text.replace(sub.from_text, sub.to_text)
                                applied_subs.append(sub)
                        _add_line(text, prov.origin_file, prov.origin_line_no, applied_subs)
        else:
            _add_line(line, abs_path, i + 1, [])

        i += 1

    return PreprocessResult(
        expanded_source="\n".join(expanded),
        provenance_map=provenance,
        copy_statements=copy_statements,
        missing_copybooks=list(dict.fromkeys(missing)),  # deduplicated
    )


def _find_copybook(
    name: str, dirs: list[pathlib.Path]
) -> Optional[pathlib.Path]:
    """Search *dirs* for a copybook file matching *name* (case-insensitive)."""
    # Try multiple extensions in order
    for ext in (".cpy", ".CPY", ".copy", ".COPY", ".cbk", ""):
        for d in dirs:
            for candidate in [
                d / f"{name}{ext}",
                d / f"{name.lower()}{ext}",
                d / f"{name.upper()}{ext}",
            ]:
                if candidate.is_file():
                    return candidate
    return None
