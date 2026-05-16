"""Grounding checker — maps LLM output sentences back to supporting UUIDs.

For each sentence in the LLM output, identifies which artifact UUID(s)
from the slice support the claim. Ungrounded sentences are flagged.
"""

from __future__ import annotations

import re
from typing import Any


UUID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8})\b",
    re.IGNORECASE,
)

COBOL_IDENT_RE = re.compile(r"\b([A-Z][A-Z0-9-]{2,})\b")


def check_grounding(
    llm_output: str,
    artifact_slice: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate how well the LLM output is grounded in the artifact slice.

    Returns:
        {
          sentences: [{text, uuid_refs, item_refs, grounded: bool}, ...],
          grounding_score: float,   # 0.0–1.0
          ungrounded: [str],        # sentences with no artifact references
        }
    """
    # Collect all UUIDs in the slice (short 8-char prefixes included)
    slice_uuids: set[str] = _collect_uuids(artifact_slice)
    slice_names: set[str] = _collect_names(artifact_slice)

    sentences = _split_sentences(llm_output)
    results = []
    ungrounded = []

    for sent in sentences:
        if len(sent.strip()) < 10:
            continue
        uuid_refs = [m for m in UUID_RE.findall(sent) if m[:8] in slice_uuids or m in slice_uuids]
        name_refs = [m for m in COBOL_IDENT_RE.findall(sent.upper()) if m in slice_names]
        grounded = bool(uuid_refs or name_refs)
        results.append({
            "text": sent.strip(),
            "uuid_refs": uuid_refs,
            "item_refs": name_refs,
            "grounded": grounded,
        })
        if not grounded:
            ungrounded.append(sent.strip())

    total = len(results)
    grounded_count = sum(1 for r in results if r["grounded"])
    score = grounded_count / max(total, 1)

    return {
        "sentences": results,
        "grounding_score": round(score, 3),
        "total_sentences": total,
        "grounded_sentences": grounded_count,
        "ungrounded": ungrounded,
    }


def _collect_uuids(obj: Any, result: set[str] | None = None) -> set[str]:
    if result is None:
        result = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "uuid" in k.lower() and isinstance(v, str) and v:
                result.add(v)
                result.add(v[:8])  # short prefix
            _collect_uuids(v, result)
    elif isinstance(obj, list):
        for item in obj:
            _collect_uuids(item, result)
    return result


def _collect_names(obj: Any, result: set[str] | None = None) -> set[str]:
    if result is None:
        result = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("name", "callee_name", "file_name", "copybook_name", "table_name"):
                if isinstance(v, str) and v:
                    result.add(v.upper())
            _collect_names(v, result)
    elif isinstance(obj, list):
        for item in obj:
            _collect_names(item, result)
    return result


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on period/newline boundaries."""
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]
