"""
Anthropic Messages API
Docs & version header: https://docs.anthropic.com/ (anthropic-version header)
"""

import httpx
from typing import Tuple
from app.config import settings
from app.token_utils import TokenStats


class AnthropicAdapter:
    BASE_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"  # per current docs

    async def complete(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system_prompt: str,
        timeout_s: int,
    ) -> Tuple[str, TokenStats]:
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY or "",
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        data = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=data)
            resp.raise_for_status()
            j = resp.json()
            # Concatenate text parts
            text = "".join([blk.get("text", "") for blk in j["content"] if blk.get("type") == "text"])
            usage = j.get("usage") or {}
            tokens_in = int(usage.get("input_tokens", 0))
            tokens_out = int(usage.get("output_tokens", max_tokens))
            return text, TokenStats(tokens_in=tokens_in, tokens_out=tokens_out)
