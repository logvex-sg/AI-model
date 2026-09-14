from __future__ import annotations

import pytest

from jarvis.memory import MemoryStore, SecretRejected, looks_like_secret


def test_remember_and_search(store: MemoryStore):
    store.remember("editor", "user prefers neovim", tags="prefs")
    assert store.get("editor").value == "user prefers neovim"
    assert [memory.key for memory in store.search("neovim")] == ["editor"]


def test_remember_updates_existing_key(store: MemoryStore):
    store.remember("shell", "bash")
    store.remember("shell", "zsh")
    assert store.get("shell").value == "zsh"
    assert len(store.search("shell")) == 1


def test_forget_and_clear(store: MemoryStore):
    store.remember("a", "1")
    store.remember("b", "2")
    assert store.forget("a") is True
    assert store.forget("a") is False
    assert store.clear() == 1
    assert store.recent() == []


@pytest.mark.parametrize(
    "value",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz12345",
        "password: hunter2",
        "API_KEY=abc123",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ],
)
def test_secrets_are_rejected(store: MemoryStore, value):
    assert looks_like_secret(value)
    with pytest.raises(SecretRejected):
        store.remember("creds", value)
    assert store.get("creds") is None


def test_ordinary_text_is_not_a_secret(store: MemoryStore):
    assert not looks_like_secret("the deploy script lives in ~/bin/deploy.sh")
    store.remember("deploy", "the deploy script lives in ~/bin/deploy.sh")


def test_conversation_history_roundtrip(store: MemoryStore):
    store.append_message("user", "hello")
    store.append_message("assistant", "hi")
    assert store.history() == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert store.clear_history() == 2
