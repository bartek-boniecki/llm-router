# app/providers/openai_provider.py
"""
OpenAI provider adapter (async, via HTTP) that ALWAYS returns TokenStats.

BEGINNER NOTES:
- We send one HTTP request to OpenAI's chat completions endpoint.
- We accept extra keyword arguments (**kwargs) so if the caller passes
  additional options (like timeout_s, stop, top_p), we won't crash.
- We return (output_text, TokenStats) so the rest of the app can record costs.

Official docs: https://platform.openai.com/docs/api-reference/chat/create
"""

from __future__ import annotations

import math
from typing import Tuple, Any

import httpx

from app.config import settings
from app.token_utils import TokenStats


class OpenAIAdapter:
    BASE_URL = "https://api.openai.com/v1/chat/completions"

    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.3,
        system_prompt: str | None = None,
        timeout_s: int | None = None,
        **kwargs: Any,  # <-- accept any extra fields without crashing
    ) -> Tuple[str, TokenStats]:
        """
        Send a single chat completion request.

        Parameters:
        - model: e.g., "gpt-4o-mini"
        - prompt: user message
        - max_tokens: maximum tokens for the answer
        - temperature: creativity (0.0..1.0)
        - system_prompt: optional instruction for tone/role
        - timeout_s: seconds before we give up (fallback to settings if None)

        Returns:
        - (assistant_text, TokenStats(tokens_in, tokens_out))
        """
        if not settings.OPENAI_API_KEY:
            # Friendly error so beginners know what to set
            raise RuntimeError("OPENAI_API_KEY not configured")

        timeout_val = timeout_s if isinstance(timeout_s, int) and timeout_s > 0 else settings.LLM_TIMEOUT_S

        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Build data and include only supported fields (ignore unknown kwargs)
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }

        # If caller supplied allowed OpenAI options via kwargs, pick them safely
        for k in ("top_p", "frequency_penalty", "presence_penalty", "stop", "seed"):
            if k in kwargs:
                data[k] = kwargs[k]

        async with httpx.AsyncClient(timeout=timeout_val) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=data)
            resp.raise_for_status()
            j = resp.json()

        # Extract text
        choices = j.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message", {}) or {}
            text = (msg.get("content") or "").strip()

        # Extract usage
        usage = j.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or 0)

        # Safety: NaNs or negatives shouldn't happen but let's guard anyway
        tokens_in = max(0, int(tokens_in if tokens_in == tokens_in else 0))
        tokens_out = max(0, int(tokens_out if tokens_out == tokens_out else 0))

        return text, TokenStats(tokens_in=tokens_in, tokens_out=tokens_out)
