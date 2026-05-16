"""Provider-agnostic LLM client.

Reads LLM_PROVIDER from the environment (default: "openai").
Reads the appropriate API key automatically.

Supported providers:
  openai   → OPENAI_API_KEY  → gpt-4o (default)
  gemini   → GEMINI_API_KEY  → gemini-1.5-pro (default)

Override the model:
  LLM_MODEL=gpt-4o-mini  python llm/demo_spec.py ...
  LLM_MODEL=gemini-2.0-flash  LLM_PROVIDER=gemini  python llm/demo_spec.py ...

Usage:
  from llm.llm_client import call_llm
  text = call_llm(prompt, max_tokens=2048)
"""

from __future__ import annotations

import os


_PROVIDER_DEFAULTS = {
    "openai":  "gpt-4o",
    "gemini":  "gemini-1.5-pro",
}


def call_llm(prompt: str, max_tokens: int = 2048) -> str:
    """Send *prompt* to the configured LLM and return the response text."""
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    model = os.environ.get("LLM_MODEL", _PROVIDER_DEFAULTS.get(provider, "gpt-4o"))

    if provider == "gemini":
        return _call_gemini(prompt, model, max_tokens)
    return _call_openai(prompt, model, max_tokens)


# ─── Provider implementations ─────────────────────────────────────────────────

def _call_openai(prompt: str, model: str, max_tokens: int) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("pip install openai") from e
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set. "
            "Export it: export OPENAI_API_KEY=sk-..."
        )
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def _call_gemini(prompt: str, model: str, max_tokens: int) -> str:
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise ImportError("pip install google-generativeai") from e
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Export it: export GEMINI_API_KEY=AIza..."
        )
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)
    resp = m.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens),
    )
    return resp.text or ""
