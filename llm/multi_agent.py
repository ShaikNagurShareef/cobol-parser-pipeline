"""Multi-agent orchestration for spec generation and forward engineering."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).parent.parent
PROMPTS_DIR  = PROJECT_ROOT / "llm" / "prompts"

_jinja = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), autoescape=False)


def _call_llm(prompt: str, max_tokens: int = 16384) -> str:
    """Call the configured LLM provider and return the response text."""
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        model  = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o")
        resp   = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
        model_name = os.environ.get("LLM_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=min(max_tokens, 8192)),
        )
        return resp.text or ""
    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        resp = client.messages.create(
            model=os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=min(max_tokens, 8192),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else ""
    else:
        return f"[LLM provider '{provider}' not configured — set LLM_PROVIDER and corresponding API key]"


def _load_slice(program_name: str, scope: str, uuid_: str) -> dict[str, Any]:
    """Load artifact slice for a program or paragraph."""
    from storage.db import get_connection
    from llm.retrieval import assemble_program_slice, assemble_paragraph_slice

    db_path = PROJECT_ROOT / "artifacts" / "pipeline.db"
    con = get_connection(db_path)
    try:
        if scope == "paragraph" and uuid_:
            return assemble_paragraph_slice(uuid_, con)
        # Program scope: resolve UUID from name
        if uuid_:
            rows = con.execute("SELECT uuid FROM nodes WHERE uuid=?", (uuid_,)).fetchall()
            prog_uuid = uuid_ if rows else None
        else:
            prog_uuid = None
        if not prog_uuid and program_name:
            rows = con.execute(
                "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
                (program_name,),
            ).fetchall()
            prog_uuid = rows[0]["uuid"] if rows else None
        if not prog_uuid:
            return {"program": {"name": program_name}, "paragraphs": [], "data_items": [], "business_rules": [], "migration_risks": [], "cfg_edge_summary": {}, "risk_summary": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}}
        return assemble_program_slice(prog_uuid, con)
    finally:
        con.close()


def generate_persona_spec(persona: str, program_name: str, scope: str, uuid_: str) -> dict[str, Any]:
    """Generate a spec for a single persona. Called in a thread pool."""
    slice_data = _load_slice(program_name, scope, uuid_)
    template_name = f"spec_{persona}.jinja2"
    try:
        tmpl = _jinja.get_template(template_name)
    except Exception:
        # Fallback to program_spec template with persona context
        tmpl = _jinja.get_template("program_spec.jinja2")

    prompt = tmpl.render(**slice_data, persona=persona, program_name=program_name)
    content = _call_llm(prompt)

    # Simple grounding score: fraction of UUIDs from slice found in output
    uuids_in_slice: list[str] = []
    for item in slice_data.get("data_items", []):
        if item.get("uuid"):
            uuids_in_slice.append(item["uuid"][:8])
    for stmt in slice_data.get("statements", []):
        if stmt.get("uuid"):
            uuids_in_slice.append(stmt["uuid"][:8])
    grounded = sum(1 for u in uuids_in_slice if u in content) if uuids_in_slice else 0
    grounding_score = grounded / max(len(uuids_in_slice), 1)

    return {"content": content, "grounding_score": round(grounding_score, 2)}


def run_transform_step(
    step_id: int,
    program_name: str,
    framework: str,
    previous_steps: list[dict],
    con: sqlite3.Connection,
) -> dict[str, Any]:
    """Run a single forward-engineering transform step using the LLM agent."""
    from llm.retrieval import assemble_program_slice

    STEP_TEMPLATES = [
        "transform_discovery.jinja2",
        "transform_spec.jinja2",
        "transform_architecture.jinja2",
        "transform_domain_model.jinja2",
        "transform_business_logic.jinja2",
        "transform_integration.jinja2",
        "transform_tests.jinja2",
    ]

    # Load artifact slice
    rows = con.execute(
        "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
        (program_name,),
    ).fetchall()
    if not rows:
        raise ValueError(f"Program '{program_name}' not found in DB")
    prog_uuid = rows[0]["uuid"]
    slice_data = assemble_program_slice(prog_uuid, con)

    # Build previous step context summary
    prev_context = "\n\n".join(
        f"## Step {s['id']}: {s['name']}\n{(s.get('output') or '')[:2000]}"
        for s in previous_steps if s.get("approved") and s.get("output")
    )

    template_name = STEP_TEMPLATES[step_id]
    try:
        tmpl = _jinja.get_template(template_name)
    except Exception:
        # Inline fallback template
        step_names = ["Discovery", "Specification", "Architecture", "Domain Model", "Business Logic", "Integration", "Tests"]
        prompt = f"""You are a senior Java architect transforming COBOL to {framework}.

## Task: {step_names[step_id]} for program {program_name}

## Artifact Summary
- Paragraphs: {slice_data.get('paragraph_count', 0)}
- Data Items: {slice_data.get('data_item_count', 0)}
- Business Rules: {slice_data.get('business_rule_count', 0)}
- Risks: {len(slice_data.get('migration_risks', []))}

## Previous Steps
{prev_context or '(first step)'}

Generate the {step_names[step_id]} output. Be specific, detailed, and reference the COBOL artifacts.
End with a ## Rationale section explaining your design decisions."""
        content = _call_llm(prompt)
    else:
        prompt = tmpl.render(**slice_data, framework=framework, program_name=program_name, previous_steps_context=prev_context)
        content = _call_llm(prompt)

    # Split out rationale if present
    rationale = ""
    if "## Rationale" in content:
        parts = content.split("## Rationale", 1)
        content   = parts[0].strip()
        rationale = parts[1].strip()

    return {"output": content, "rationale": rationale}
