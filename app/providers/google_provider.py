# app/providers/google_provider.py
"""
Google (Gemini) provider adapter.

BEGINNER NOTES:
- We use google-generativeai SDK. It expects "max_output_tokens" (not "max_tokens"),
  so we translate the name.
- We run the blocking generate call in a background thread to keep FastAPI responsive.
- We ALWAYS return TokenStats so the rest of the app never crashes.

Docs: https://ai.google.dev/gemini-api/docs
"""

from __future__ import annotations

import os
import asyncio
from typing import Tuple, Optional, Any

import google.generativeai as genai

from app.token_utils import TokenStats


class GoogleAdapter:
    def __init__(self, api_key: Optional[str] = None, timeout_s: Optional[int] = None) -> None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        genai.configure(api_key=key)

        try:
            self.timeout_s = int(timeout_s or os.getenv("LLM_TIMEOUT_S", "60"))
        except ValueError:
            self.timeout_s = 60

    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        **_: Any,
    ) -> Tuple[str, TokenStats]:
        """
        Make a text generation request to a Gemini model.

        Returns: (output_text, TokenStats(tokens_in=..., tokens_out=...))
        """
        loop = asyncio.get_running_loop()

        def _call_gemini():
            # Build the model with optional system instruction
            if system_prompt:
                model_obj = genai.GenerativeModel(model, system_instruction=system_prompt)
            else:
                model_obj = genai.GenerativeModel(model)

            generation_config = {
                "max_output_tokens": int(max_tokens),
                "temperature": float(temperature),
            }
            return model_obj.generate_content(
                prompt,
                generation_config=generation_config,
                safety_settings=None,
            )

        try:
            resp = await asyncio.wait_for(loop.run_in_executor(None, _call_gemini), timeout=self.timeout_s)
        except asyncio.TimeoutError as te:
            raise RuntimeError(f"Gemini request timed out after {self.timeout_s}s") from te

        # Extract text robustly
        output_text: str = getattr(resp, "text", "") or ""
        if not output_text:
            try:
                candidates = getattr(resp, "candidates", []) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) if content else None
                    if parts:
                        output_text = "".join(getattr(p, "text", "") or "" for p in parts)
            except Exception:
                output_text = output_text or ""

        # Extract token usage (protobuf style)
        usage = getattr(resp, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

        return output_text, TokenStats(tokens_in=tokens_in, tokens_out=tokens_out)
