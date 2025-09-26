"""
Adapter registry that calls provider-specific clients.
This makes adding/removing providers a one-liner elsewhere.
"""

from dataclasses import dataclass

from app.providers.openai_provider import OpenAIAdapter
from app.providers.anthropic_provider import AnthropicAdapter
from app.providers.google_provider import GoogleAdapter
from app.providers.mistral_provider import MistralAdapter
from app.providers.local_ollama_provider import OllamaAdapter


@dataclass
class AdapterTokenStats:
    tokens_in: int
    tokens_out: int


class LLMAdapterRegistry:
    # Register providers by canonical names used in price_table.yaml.
    # Keep a back-compat alias for local Ollama.
    _registry = {
        "openai": OpenAIAdapter(),
        "anthropic": AnthropicAdapter(),
        "google": GoogleAdapter(),
        "mistral": MistralAdapter(),
        "ollama": OllamaAdapter(),  # <-- matches price_table.yaml
        "local": OllamaAdapter(),   # <-- legacy alias, safe to keep
    }

    @classmethod
    def warm_up(cls):
        # In a real app, you might validate keys here.
        return True

    @classmethod
    def get(cls, provider: str):
        if provider not in cls._registry:
            raise ValueError(f"Unknown provider '{provider}'")
        return cls._registry[provider]
