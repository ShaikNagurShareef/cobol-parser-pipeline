"""LangGraph spec-generation agent.

Wires the artifact retrieval → LLM generation → grounding check pipeline
into a LangGraph state machine. Bonus criterion (§11 of the brief).
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any, TypedDict

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from jinja2 import Environment, FileSystemLoader
from langgraph.graph import StateGraph, END

from llm.retrieval import assemble_paragraph_slice, assemble_program_slice
from llm.grounding import check_grounding
from storage.db import get_connection

_PROMPT_DIR = pathlib.Path(__file__).parent / "prompts"
_JINJA = Environment(loader=FileSystemLoader(str(_PROMPT_DIR)))


# ─── State ────────────────────────────────────────────────────────────────────

class SpecState(TypedDict):
    uuid: str
    scope: str            # "paragraph" | "program"
    artifact_slice: dict
    prompt: str
    llm_output: str
    grounding_report: dict
    final_spec: str


# ─── Nodes ────────────────────────────────────────────────────────────────────

def retrieve_artifacts(state: SpecState) -> SpecState:
    """Node 1: Retrieve artifact slice from the database."""
    with get_connection() as con:
        if state["scope"] == "paragraph":
            slice_ = assemble_paragraph_slice(state["uuid"], con)
        else:
            slice_ = assemble_program_slice(state["uuid"], con)
    return {**state, "artifact_slice": slice_}


def build_prompt(state: SpecState) -> SpecState:
    """Node 2: Render the Jinja2 prompt template."""
    template_name = (
        "paragraph_spec.jinja2" if state["scope"] == "paragraph" else "program_spec.jinja2"
    )
    try:
        tmpl = _JINJA.get_template(template_name)
        prompt = tmpl.render(**state["artifact_slice"])
    except Exception as exc:
        prompt = f"Generate a specification for UUID {state['uuid']}. Error loading template: {exc}"
    return {**state, "prompt": prompt}


def generate_spec(state: SpecState) -> SpecState:
    """Node 3: Call the configured LLM to generate the specification."""
    try:
        from llm.llm_client import call_llm
        output = call_llm(state["prompt"], max_tokens=2048)
    except Exception as exc:
        output = f"[LLM ERROR: {exc}]"
    return {**state, "llm_output": output}


def ground_check(state: SpecState) -> SpecState:
    """Node 4: Check grounding of LLM output against artifact slice."""
    report = check_grounding(state["llm_output"], state["artifact_slice"])
    return {**state, "grounding_report": report}


def emit_report(state: SpecState) -> SpecState:
    """Node 5: Compose the final spec + grounding metadata."""
    score = state["grounding_report"].get("grounding_score", 0)
    ungrounded = state["grounding_report"].get("ungrounded", [])
    final = (
        f"# Specification for {state['scope']} {state['uuid']}\n\n"
        f"{state['llm_output']}\n\n"
        f"---\n"
        f"**Grounding score:** {score:.1%} "
        f"({state['grounding_report'].get('grounded_sentences', 0)}/"
        f"{state['grounding_report'].get('total_sentences', 0)} sentences grounded)\n"
    )
    if ungrounded:
        final += "\n**Ungrounded sentences (review manually):**\n"
        for s in ungrounded[:5]:
            final += f"- {s}\n"
    return {**state, "final_spec": final}


# ─── Graph ────────────────────────────────────────────────────────────────────

def build_graph() -> Any:
    g = StateGraph(SpecState)
    g.add_node("retrieve", retrieve_artifacts)
    g.add_node("build_prompt", build_prompt)
    g.add_node("generate", generate_spec)
    g.add_node("ground_check", ground_check)
    g.add_node("emit", emit_report)

    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "build_prompt")
    g.add_edge("build_prompt", "generate")
    g.add_edge("generate", "ground_check")
    g.add_edge("ground_check", "emit")
    g.add_edge("emit", END)
    return g.compile()


_graph = None


def generate_spec_for(uuid: str, scope: str = "paragraph") -> str:
    """Top-level entry point: generate a grounded spec for the given UUID."""
    global _graph
    if _graph is None:
        _graph = build_graph()

    result = _graph.invoke({"uuid": uuid, "scope": scope, "artifact_slice": {},
                            "prompt": "", "llm_output": "", "grounding_report": {},
                            "final_spec": ""})
    return result["final_spec"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("uuid", help="Paragraph or program UUID")
    ap.add_argument("--scope", default="paragraph", choices=["paragraph", "program"])
    args = ap.parse_args()
    print(generate_spec_for(args.uuid, args.scope))
