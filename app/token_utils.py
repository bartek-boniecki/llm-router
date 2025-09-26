"""
Token estimation:
- Use tiktoken for OpenAI-like models.
- For others, fall back to a simple heuristic (4 chars ≈ 1 token).
This is good enough for routing budget estimates.
"""

from dataclasses import dataclass
import tiktoken


@dataclass
class TokenStats:
    tokens_in: int
    tokens_out: int


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    # Heuristic: ~4 chars per token (common rule of thumb)
    return max(1, int(len(text) / 4))


def count_tokens_openai(prompt: str) -> int:
    try:
        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(prompt))
    except Exception:
        return approx_tokens(prompt)


def estimate_tokens(provider: str, model: str, prompt: str, expected_output_tokens: int) -> TokenStats:
    if provider == "openai":
        tin = count_tokens_openai(prompt)
    else:
        tin = approx_tokens(prompt)
    tout = expected_output_tokens
    return TokenStats(tokens_in=tin, tokens_out=tout)
