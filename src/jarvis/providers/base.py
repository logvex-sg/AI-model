"""Model provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """Raised when the model backend is unreachable or returns an error."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    raw: dict[str, Any] | None = None


class Provider(ABC):
    """A chat model backend."""

    name: str
    is_local: bool

    @abstractmethod
    def available(self) -> dict[str, Any]:
        """Report reachability and installed models. Never guesses."""

    @abstractmethod
    def complete(self, messages: list[Message]) -> Completion:
        """Return a full completion."""

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """Yield response chunks. Providers without streaming yield once."""
        yield self.complete(messages).content
