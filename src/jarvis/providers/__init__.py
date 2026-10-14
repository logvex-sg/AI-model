"""Model providers."""

from __future__ import annotations

from jarvis.config import ModelConfig
from jarvis.providers.base import Completion, Message, Provider, ProviderError
from jarvis.providers.ollama import OllamaProvider
from jarvis.providers.openai_compat import OpenAICompatibleProvider

__all__ = [
    "Completion",
    "Message",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "build_provider",
]


def build_provider(config: ModelConfig) -> Provider:
    if config.provider == "ollama":
        return OllamaProvider(config)
    if config.provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(config)
    raise ProviderError(f"unknown provider '{config.provider}'")
