"""Runtime configuration for JARVIS.

Configuration is resolved from, in increasing order of precedence:
the built-in defaults, ``~/.config/jarvis/config.toml`` and ``JARVIS_*``
environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "jarvis" / "config.toml"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "jarvis"


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "ollama"
    model: str = "llama3.1"
    endpoint: str = "http://127.0.0.1:11434"
    temperature: float = 0.2
    context_size: int = 8192
    timeout: float = 120.0
    api_key_env: str = "JARVIS_API_KEY"

    @property
    def is_local(self) -> bool:
        return self.provider == "ollama"


@dataclass(frozen=True)
class Config:
    mode: str = "normal"
    model: ModelConfig = field(default_factory=ModelConfig)
    state_dir: Path = DEFAULT_STATE_DIR
    confine_to_home: bool = True
    max_tool_calls_per_task: int = 24

    @property
    def audit_log_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def memory_db_path(self) -> Path:
        return self.state_dir / "memory.db"

    @property
    def killswitch_path(self) -> Path:
        return self.state_dir / "EMERGENCY_STOP"


def load_config(path: Path | None = None, env: dict[str, str] | None = None) -> Config:
    """Load configuration from disk and environment.

    Missing files are not an error: JARVIS runs on defaults.
    """
    env = os.environ if env is None else env
    path = path or Path(env.get("JARVIS_CONFIG", DEFAULT_CONFIG_PATH))
    config = Config()

    if path.is_file():
        raw = tomllib.loads(path.read_text())
        model_raw = raw.get("model", {})
        model = ModelConfig(
            provider=str(model_raw.get("provider", config.model.provider)),
            model=str(model_raw.get("model", config.model.model)),
            endpoint=str(model_raw.get("endpoint", config.model.endpoint)),
            temperature=float(model_raw.get("temperature", config.model.temperature)),
            context_size=int(model_raw.get("context_size", config.model.context_size)),
            timeout=float(model_raw.get("timeout", config.model.timeout)),
            api_key_env=str(model_raw.get("api_key_env", config.model.api_key_env)),
        )
        config = replace(
            config,
            model=model,
            mode=str(raw.get("mode", config.mode)),
            state_dir=Path(raw.get("state_dir", config.state_dir)).expanduser(),
            confine_to_home=bool(raw.get("confine_to_home", config.confine_to_home)),
            max_tool_calls_per_task=int(
                raw.get("max_tool_calls_per_task", config.max_tool_calls_per_task)
            ),
        )

    model_overrides = {
        "provider": env.get("JARVIS_PROVIDER"),
        "model": env.get("JARVIS_MODEL"),
        "endpoint": env.get("JARVIS_ENDPOINT"),
    }
    model_overrides = {k: v for k, v in model_overrides.items() if v}
    if temperature := env.get("JARVIS_TEMPERATURE"):
        model_overrides["temperature"] = float(temperature)
    if model_overrides:
        config = replace(config, model=replace(config.model, **model_overrides))

    if mode := env.get("JARVIS_MODE"):
        config = replace(config, mode=mode)
    if state_dir := env.get("JARVIS_STATE_DIR"):
        config = replace(config, state_dir=Path(state_dir).expanduser())

    return config
