"""Sandboxed filesystem operations.

Write operations are confined to the configured write roots (``$KALI_OPS_HOME``
plus any ``allowed_write_paths``). Reads are unrestricted but their contents
are redacted before being returned.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from .config import Config
from .errors import ScopeViolation
from .killswitch import Killswitch
from .logging import OperationLog
from .secrets import redact


class FileSystem:
    """Read/write helper bound to the configured sandbox roots."""

    def __init__(
        self,
        config: Config,
        killswitch: Killswitch,
        log: Optional[OperationLog] = None,
        *,
        agent: str = "builder",
    ) -> None:
        self.config = config
        self.killswitch = killswitch
        self.log = log
        self.agent = agent

    def _writable(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.config.write_roots():
            root_resolved = root.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return True
        return False

    def _guard_write(self, path: Path) -> None:
        if not self._writable(path):
            raise ScopeViolation(
                f"write outside allowed roots: {path}. Add it to "
                f"allowed_write_paths to permit this."
            )

    def read(self, path: str, *, max_bytes: int = 1_000_000, raw: bool = False) -> str:
        """Read a text file. Secrets are redacted unless ``raw=True``."""
        p = Path(path).expanduser()
        self.killswitch.guard("file read")
        data = p.read_text(encoding="utf-8", errors="replace")[:max_bytes]
        self._record("read", p, status="ok")
        return data if raw else redact(data)

    def write(self, path: str, content: str, *, append: bool = False) -> Path:
        p = Path(path).expanduser()
        self.killswitch.guard("file write")
        self._guard_write(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with p.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        self._record("write", p, status="ok", result=f"{len(content)} bytes")
        return p

    def list(self, path: str = ".", *, recursive: bool = False) -> List[str]:
        p = Path(path).expanduser()
        self.killswitch.guard("file list")
        if not p.exists():
            return []
        if recursive:
            entries = [str(child) for child in sorted(p.rglob("*"))]
        else:
            entries = [str(child) for child in sorted(p.iterdir())]
        return entries

    def mkdir(self, path: str) -> Path:
        p = Path(path).expanduser()
        self.killswitch.guard("mkdir")
        self._guard_write(p)
        p.mkdir(parents=True, exist_ok=True)
        self._record("mkdir", p, status="ok")
        return p

    def move(self, src: str, dst: str) -> Path:
        s, d = Path(src).expanduser(), Path(dst).expanduser()
        self.killswitch.guard("move")
        self._guard_write(d)
        shutil.move(str(s), str(d))
        self._record("move", d, status="ok", result=f"from {s}")
        return d

    def copy(self, src: str, dst: str) -> Path:
        s, d = Path(src).expanduser(), Path(dst).expanduser()
        self.killswitch.guard("copy")
        self._guard_write(d)
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(str(s), str(d), dirs_exist_ok=True)
        else:
            shutil.copy2(str(s), str(d))
        self._record("copy", d, status="ok", result=f"from {s}")
        return d

    def delete(self, path: str, *, missing_ok: bool = True) -> None:
        p = Path(path).expanduser()
        self.killswitch.guard("delete")
        self._guard_write(p)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=missing_ok)
        else:
            p.unlink(missing_ok=missing_ok)
        self._record("delete", p, status="ok")

    def exists(self, path: str) -> bool:
        return Path(path).expanduser().exists()

    def _record(self, operation: str, path: Path, *, status: str, result: str = "") -> None:
        if self.log is not None:
            self.log.record(self.agent, f"file.{operation}", target=str(path), result=result, status=status)
