"""OpenAI-compatible remote provider.

Credentials are read from the environment variable named by
``ModelConfig.api_key_env``; keys are never read from or written to source or
configuration files.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx

from jarvis.config import ModelConfig
from jarvis.providers.base import Completion, Message, Provider, ProviderError


class OpenAICompatibleProvider(Provider):
    name = "openai-compatible"
    is_local = False

    def __init__(self, config: ModelConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(base_url=config.endpoint, timeout=config.timeout)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.config.api_key_env)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError(
                f"no API key: set the {self.config.api_key_env} environment variable"
            )
        return {"Authorization": f"Bearer {self.api_key}"}

    def available(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "reachable": False,
                "endpoint": self.config.endpoint,
                "error": f"{self.config.api_key_env} is not set",
                "models": [],
                "model_installed": False,
            }
        try:
            response = self._client.get("/models", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {
                "reachable": False,
                "endpoint": self.config.endpoint,
                "error": str(exc),
                "models": [],
                "model_installed": False,
            }
        models = [entry["id"] for entry in response.json().get("data", [])]
        return {
            "reachable": True,
            "endpoint": self.config.endpoint,
            "models": models,
            "model_installed": self.config.model in models,
            "error": None,
        }

    def _payload(self, messages: list[Message], stream: bool) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [message.as_dict() for message in messages],
            "temperature": self.config.temperature,
            "stream": stream,
        }

    def complete(self, messages: list[Message]) -> Completion:
        try:
            response = self._client.post(
                "/chat/completions",
                json=self._payload(messages, False),
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"remote request failed: {exc}") from exc
        data = response.json()
        choices = data.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""
        return Completion(content=content, model=data.get("model", self.config.model), raw=data)

    def stream(self, messages: list[Message]) -> Iterator[str]:
        try:
            with self._client.stream(
                "POST",
                "/chat/completions",
                json=self._payload(messages, True),
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices", []):
                        piece = choice.get("delta", {}).get("content")
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            raise ProviderError(f"remote stream failed: {exc}") from exc
