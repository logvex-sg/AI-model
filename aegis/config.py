"""Configuration loading and validation.

Precedence (highest wins):

1. Explicit overrides passed to :func:`load_config`
2. Environment variables (``KALI_AEGIS_*``)
3. Config file (``$KALI_AEGIS_HOME/config.toml`` or ``--config`` path)
4. Built-in defaults

The config file is optional. When it is absent the defaults plus environment
are used, which keeps a fresh install runnable with zero setup.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ConfigError

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 3.9/3.10 fallback
    _toml = None  # type: ignore[assignment]

DEFAULT_HOME = Path(os.environ.get("KALI_AEGIS_HOME", Path.home() / ".aegis"))
ENV_PREFIX = "KALI_AEGIS_"

#: Environment variables that flip a boolean config field on.
_TRUTHY = {"1", "true", "yes", "on", "enabled"}


@dataclass
class Config:
    """Runtime configuration for the agent team."""

    home: Path = field(default_factory=lambda: DEFAULT_HOME)
    log_level: str = "INFO"
    #: Where the append-only JSONL operation log is written.
    log_file: str = "operations.jsonl"
    #: Where persistent task state is written.
    state_file: str = "state.json"
    #: Optional LLM endpoint. The deterministic core runs without it.
    model_provider: str = "none"
    model_name: str = "none"
    model_base_url: str = ""
    #: Shell execution policy.
    command_timeout: int = 300
    allow_root: bool = False
    #: Path to the killswitch sentinel file.
    killswitch_file: str = "KILLSWITCH"
    #: Address the REST API binds to. Loopback by default.
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    #: Hosts explicitly authorized for offensive security testing.
    authorized_hosts: List[str] = field(default_factory=list)
    #: Directories the agent may write to. Empty means "home only".
    allowed_write_paths: List[str] = field(default_factory=list)
    #: Deny-by-default for HIGH risk operations without operator confirmation.
    auto_approve_high_risk: bool = False
    #: Maximum attempts for a single recoverable operation (section 17).
    max_retries: int = 1
    #: Base delay between retries; doubles each attempt.
    retry_backoff_s: float = 1.0
    #: Directory where scaffolded projects are created.
    projects_dir: str = "projects"

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser()
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"invalid log_level: {self.log_level!r}")
        if not 1 <= int(self.api_port) <= 65535:
            raise ConfigError(f"invalid api_port: {self.api_port!r}")
        if int(self.command_timeout) < 1:
            raise ConfigError("command_timeout must be >= 1 second")
        if int(self.max_retries) < 1:
            raise ConfigError("max_retries must be >= 1")

    # -- paths ---------------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self._resolve(self.log_file)

    @property
    def state_path(self) -> Path:
        return self._resolve(self.state_file)

    @property
    def killswitch_path(self) -> Path:
        return self._resolve(self.killswitch_file)

    def _resolve(self, name: str) -> Path:
        p = Path(name)
        return p if p.is_absolute() else self.home / p

    @property
    def projects_path(self) -> Path:
        return self._resolve(self.projects_dir)

    def write_roots(self) -> List[Path]:
        """Directories considered writable by the agent."""
        roots = [self.home, self.projects_path]
        roots.extend(Path(p).expanduser() for p in self.allowed_write_paths)
        return roots

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["home"] = str(self.home)
        return data


def _coerce(field_type: Any, raw: str) -> Any:
    """Coerce an environment string to the declared field type."""
    if field_type is bool:
        return raw.strip().lower() in _TRUTHY
    if field_type is int:
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"expected integer, got {raw!r}") from exc
    if field_type is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"expected number, got {raw!r}") from exc
    if field_type is list:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _resolved_types() -> Dict[str, Any]:
    """Field name -> real type object.

    ``from __future__ import annotations`` turns ``dataclasses.fields()``
    ``.type`` into a string, so the declared types must be resolved through
    ``get_type_hints`` before they can be compared against ``int``/``bool``.
    """
    import typing

    try:
        return typing.get_type_hints(Config)
    except Exception:  # pragma: no cover - defensive
        return {f.name: f.type for f in fields(Config)}


def _from_env(config: Config) -> None:
    types = _resolved_types()
    for f in fields(Config):
        env_key = ENV_PREFIX + f.name.upper()
        if env_key not in os.environ:
            continue
        raw = os.environ[env_key]
        setattr(config, f.name, _coerce(types.get(f.name, f.type), raw))


def _from_toml(config: Config, path: Path) -> None:
    if _toml is None:
        raise ConfigError(
            "TOML config files require Python 3.11+; use environment variables instead"
        )
    try:
        with path.open("rb") as fh:
            data = _toml.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
    for key, value in data.items():
        setattr(config, key, value)


def load_config(
    config_path: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Config:
    """Build a :class:`Config` from file, environment, and explicit overrides."""
    config = Config()

    path = Path(config_path).expanduser() if config_path else config.home / "config.toml"
    if config_path and not path.exists():
        raise ConfigError(f"config file not found: {path}")
    if path.exists():
        _from_toml(config, path)

    _from_env(config)

    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key not in {f.name for f in fields(Config)}:
            raise ConfigError(f"unknown override: {key}")
        setattr(config, key, value)

    # Re-run validation now that file/env/overrides are applied.
    config.__post_init__()
    return config
