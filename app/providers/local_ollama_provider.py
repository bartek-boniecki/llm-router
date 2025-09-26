# app/providers/local_ollama_provider.py
"""
Ollama (local LLM) adapter using the native REST API with smart model fallback.

BEGINNER NOTES:
- We talk to the local Ollama server (running in Docker or on your machine).
- If the requested model isn't installed (e.g., "llama3.1"), we automatically
  check which models ARE installed (e.g., "llama3") and pick the closest match.
- We return (text, TokenStats) so the rest of your app can record costs.

API docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import os
import difflib
from typing import Tuple, Any, List

import httpx

from app.token_utils import TokenStats


def _resolve_ollama_base_url() -> str:
    """
    Prefer OLLAMA_BASE_URL env; otherwise assume docker-compose service 'ollama'.
    This makes container-to-container calls work reliably.
    """
    url = os.getenv("OLLAMA_BASE_URL", "").strip()
    return url if url else "http://ollama:11434"


async def _list_installed_models(client: httpx.AsyncClient, base_url: str) -> List[str]:
    """
    Ask Ollama which model tags are installed. Returns a list like ["llama3", "llama3.1"].
    """
    r = await client.get(f"{base_url}/api/tags")
    r.raise_for_status()
    data = r.json() or {}
    models = data.get("models") or []
    names: List[str] = []
    for m in models:
        # "name" is like "llama3:latest"; take left side before colon.
        name = (m.get("name") or "")
        if ":" in name:
            name = name.split(":", 1)[0]
        if name:
            names.append(name)
    # Also consider "model" field if present
    for m in models:
        alt = (m.get("model") or "").strip()
        if alt and alt not in names:
            names.append(alt)
    return sorted(set(names))


def _choose_best_model(requested: str, available: List[str]) -> str | None:
    """
    If 'requested' is available, return it. Otherwise try a close match:
    - same base family (e.g., "llama3.1" -> "llama3")
    - otherwise the closest by string similarity
    """
    req = (requested or "").strip()
    if not req:
        return available[0] if available else None
    if req in available:
        return req

    # If req has a dot version, try its prefix before the dot
    if "." in req:
        prefix = req.split(".", 1)[0]
        if prefix in available:
            return prefix

    # Try closest match (ratio >= 0.6)
    matches = difflib.get_close_matches(req, available, n=1, cutoff=0.6)
    if matches:
        return matches[0]

    return available[0] if available else None


class OllamaAdapter:
    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.3,
        system_prompt: str | None = None,
        timeout_s: int | None = None,
        **_: Any,
    ) -> Tuple[str, TokenStats]:
        """
        Generate text via Ollama.

        We combine system + user into a simple prompt for /api/generate.
        If model is not installed, we fall back to the closest installed one.
        """
        base_url = _resolve_ollama_base_url()
        timeout_val = int(timeout_s or os.getenv("LLM_TIMEOUT_S", "60"))

        async with httpx.AsyncClient(timeout=timeout_val) as client:
            available = await _list_installed_models(client, base_url)
            chosen = _choose_best_model(model, available)
            if not chosen:
                raise RuntimeError(
                    "No Ollama models are installed. "
                    "Install one with: docker compose exec ollama ollama pull llama3"
                )

            # Merge system + user prompts for simple models
            full_prompt = f"{system_prompt.strip()}\n\n{prompt.strip()}" if system_prompt else prompt.strip()

            url = f"{base_url}/api/generate"
            body = {
                "model": chosen,         # e.g., "llama3" or "llama3.1"
                "prompt": full_prompt,
                "stream": False,         # single response with token counts
                "options": {
                    "temperature": float(temperature),
                    "num_predict": int(max_tokens),
                },
            }

            resp = await client.post(url, json=body)
            resp.raise_for_status()
            j = resp.json()

        text = (j.get("response") or "").strip()
        tokens_in = int(j.get("prompt_eval_count", 0) or 0)
        tokens_out = int(j.get("eval_count", 0) or 0)

        return text, TokenStats(tokens_in=tokens_in, tokens_out=tokens_out)
