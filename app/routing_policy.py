"""
Routing policy (deterministic, cost-first).

We load a price table YAML with this shape:

models:
  - provider: openai
    model: gpt-4o-mini
    price_in_per_1k: 0.15
    price_out_per_1k: 0.6
    max_input_tokens: 128000
    max_output_tokens: 16384
    baseline_quality: 4
  - ...

We then estimate tokens and pick the cheapest model satisfying:
- baseline_quality >= requested quality_floor
- estimated_cost <= budget (if cost_ceiling_usd > 0)
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
import yaml

from app.config import settings
from app.token_utils import estimate_tokens, TokenStats

@dataclass
class RouteDecision:
    provider: str
    model: str
    estimated_cost_usd: float
    system_prompt: str
    temperature: float
    cache_hit: bool
    tokens: TokenStats

# (Optional) in-memory response cache idea omitted in this minimal policy.

class RoutingPolicy:
    def __init__(self):
        # Read YAML once. Your YAML root has a "models" key (list of rows).
        with open(settings.PRICE_TABLE_PATH, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        # Defensive: allow either a plain list or a dict with "models"
        if isinstance(doc, dict) and "models" in doc:
            self.price_rows: List[Dict[str, Any]] = list(doc["models"])
        elif isinstance(doc, list):
            self.price_rows = list(doc)
        else:
            self.price_rows = []

    async def choose_model(
        self,
        input_text: str,
        expected_output_tokens: int,
        quality_floor: int,
        cost_ceiling_usd: float,
        provider_hints: Dict[str, Any],
    ) -> RouteDecision:
        candidates: List[Tuple[Dict[str, Any], float, TokenStats]] = []

        for row in self.price_rows:
            # Respect provider/model hints
            if provider_hints.get("provider") and row["provider"] != provider_hints["provider"]:
                continue
            if provider_hints.get("model") and row["model"] != provider_hints["model"]:
                continue

            # Quality gate
            if int(row["baseline_quality"]) < int(quality_floor):
                continue

            # Estimate token usage for this model
            tokens = estimate_tokens(
                row["provider"], row["model"], input_text, expected_output_tokens
            )

            # Context window limits
            if tokens.tokens_in > int(row["max_input_tokens"]):
                continue
            if tokens.tokens_out > int(row["max_output_tokens"]):
                continue

            # Estimated cost
            cost = (tokens.tokens_in / 1000.0) * float(row["price_in_per_1k"]) + \
                   (tokens.tokens_out / 1000.0) * float(row["price_out_per_1k"])

            # Budget gate (0 or less means "no cap")
            if cost_ceiling_usd > 0 and cost > cost_ceiling_usd:
                continue

            candidates.append((row, cost, tokens))

        if not candidates:
            # Friendly message for your users
            raise ValueError(
                "No models meet your quality or budget. "
                "Try lowering quality_floor or raising cost_ceiling_usd."
            )

        # Sort by estimated cost ASC; pick the cheapest
        candidates.sort(key=lambda x: x[1])
        row, cost, tokens = candidates[0]

        # Use a sensible default system prompt & temperature (could be read from config)
        system_prompt = settings.DEFAULT_SYSTEM_PROMPT
        temperature = 0.2  # keep business answers focused

        return RouteDecision(
            provider=row["provider"],
            model=row["model"],
            estimated_cost_usd=cost,
            system_prompt=system_prompt,
            temperature=temperature,
            cache_hit=False,
            tokens=tokens,
        )
