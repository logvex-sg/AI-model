"""Ollama provider (local-first default)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from jarvis.config import ModelConfig
from jarvis.providers.base import Completion, Message, Provider, ProviderError


class OllamaProvider(Provider):
    name = "ollama"
    is_local = True

    def __init__(self, config: ModelConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(base_url=config.endpoint, timeout=config.timeout)

    def available(self) -> dict[str, Any]:
        """Ask the daemon which models exist. No assumption is made."""
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {
                "reachable": False,
                "endpoint": self.config.endpoint,
                "error": str(exc),
                "models": [],
                "model_installed": False,
            }
        models = [entry["name"] for entry in response.json().get("models", [])]
        return {
            "reachable": True,
            "endpoint": self.config.endpoint,
            "models": models,
            "model_installed": any(
                name == self.config.model or name.startswith(f"{self.config.model}:")
                for name in models
            ),
            "error": None,
        }

    def _payload(self, messages: list[Message], stream: bool) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [message.as_dict() for message in messages],
            "stream": stream,
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.context_size,
            },
        }

    def complete(self, messages: list[Message]) -> Completion:
        try:
            response = self._client.post("/api/chat", json=self._payload(messages, False))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc
        data = response.json()
        content = data.get("message", {}).get("content", "")
        return Completion(content=content, model=data.get("model", self.config.model), raw=data)

    def stream(self, messages: list[Message]) -> Iterator[str]:
        try:
            with self._client.stream(
                "POST", "/api/chat", json=self._payload(messages, True)
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama stream failed: {exc}") from exc
