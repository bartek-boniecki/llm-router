"""
Mistral Chat Completions API
Docs: https://docs.mistral.ai/api/  (chat/completions endpoint)
Pricing checked (Medium 3, Small 3.1, etc.). 
"""

import httpx
from typing import Tuple
from app.config import settings
from app.token_utils import TokenStats


class MistralAdapter:
    BASE_URL = "https://api.mistral.ai/v1/chat/completions"

    async def complete(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system_prompt: str,
        timeout_s: int,
    ) -> Tuple[str, TokenStats]:
        if not settings.MISTRAL_API_KEY:
            raise ValueError("MISTRAL_API_KEY not configured")
        headers = {
            "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=data)
            resp.raise_for_status()
            j = resp.json()
            text = j["choices"][0]["message"]["content"]
            usage = j.get("usage") or {}
            tokens_in = int(usage.get("prompt_tokens", 0))
            tokens_out = int(usage.get("completion_tokens", max_tokens))
            return text, TokenStats(tokens_in=tokens_in, tokens_out=tokens_out)
