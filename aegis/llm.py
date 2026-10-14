"""LLM client — OpenAI-compatible chat completions over the standard library.

Deliberately dependency-free: ``urllib.request`` speaks the wire protocol that
OpenAI, DeepSeek, Groq, Together, OpenRouter, vLLM, Ollama, and llama.cpp all
expose at ``/chat/completions``. That covers essentially every provider an
operator is likely to point this at without pulling in an SDK.

Nothing here decides *what* to ask. The agent loop builds the messages; this
module only carries them and normalises the reply. When no provider is
configured the client reports itself unavailable and the deterministic core
runs alone — the platform never silently pretends an LLM answered.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 60


@dataclass
class LLMResponse:
    """One completion, plus what it cost."""

    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "finish_reason": self.finish_reason,
        }


class LLMError(RuntimeError):
    """A completion could not be obtained."""


class LLMClient:
    """Minimal chat-completions client.

    ``base_url`` may be given with or without a trailing ``/v1``; both forms
    are normalised to ``<base>/chat/completions``.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    # -- availability --------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def endpoint(self) -> str:
        base = self.base_url
        if not base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        return base + "/chat/completions"

    # -- completions ---------------------------------------------------------
    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion and return the first choice."""
        if not self.available:
            raise LLMError(
                "no LLM configured; set model_provider/model_base_url/model_name "
                "and provide an API key"
            )
        raw = self._raw_post(messages, tools=tools, json_mode=json_mode)
        return self._parse(raw)

    def _raw_post(
        self,
        messages: List[Dict[str, str]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        """POST the request and return the decoded body.

        Split out so the request/response handling can be exercised without
        reaching the network.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM returned invalid JSON: {exc}") from exc

    def _parse(self, raw: Dict[str, Any]) -> LLMResponse:
        choices = raw.get("choices") or []
        if not choices:
            raise LLMError(f"LLM returned no choices: {str(raw)[:200]}")
        choice = choices[0]
        message = choice.get("message") or {}
        usage = raw.get("usage") or {}
        return LLMResponse(
            text=(message.get("content") or "").strip(),
            model=raw.get("model", self.model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=str(choice.get("finish_reason") or ""),
            raw=raw,
        )

    def complete_json(
        self,
        messages: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Complete and parse a JSON object, tolerating code fences."""
        response = self.complete(messages, json_mode=True)
        return parse_json_object(response.text)


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extract a JSON object from model output.

    Models wrap JSON in ```json fences often enough that stripping them is
    worth the few lines. Anything unparseable is raised, never guessed at.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model did not return valid JSON: {exc}: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise LLMError(f"expected a JSON object, got {type(data).__name__}")
    return data


#: Where to send requests when only a provider *name* is configured.
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
}

#: Providers that need no API key (local runtimes).
KEYLESS_PROVIDERS = {"ollama", "lmstudio", "local", "vllm"}


def resolve_base_url(provider: str, explicit: str = "") -> str:
    """Explicit URL wins; otherwise look the provider name up."""
    if explicit:
        return explicit
    return PROVIDER_BASE_URLS.get((provider or "").strip().lower(), "")
