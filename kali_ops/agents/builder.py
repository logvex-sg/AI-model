"""BUILDER / ENGINEER — software, systems, and automation engineer.

Creates files and projects, generates common project scaffolding, initialises
repositories, and runs build/test commands through the shared shell (which
enforces the risk policy and records the audit trail).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Agent, AgentResult

# Minimal templates for the languages the system claims to support.
_SCAFFOLDS: Dict[str, Dict[str, str]] = {
    "python": {
        "src/__init__.py": "",
        "src/main.py": '"""Entry point."""\n\n\ndef main() -> None:\n    print("hello")\n\n\nif __name__ == "__main__":\n    main()\n',
        "tests/test_main.py": "def test_placeholder() -> None:\n    assert True\n",
        "README.md": "# project\n",
        "requirements.txt": "",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
    },
    "node": {
        "src/index.js": "console.log('hello');\n",
        "package.json": '{\n  "name": "project",\n  "version": "0.1.0",\n  "main": "src/index.js"\n}\n',
        "README.md": "# project\n",
        ".gitignore": "node_modules/\n",
    },
    "go": {
        "main.go": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hello")\n}\n',
        "go.mod": "module project\n\ngo 1.22\n",
        ".gitignore": "bin/\n",
    },
    "rust": {
        "src/main.rs": 'fn main() {\n    println!("hello");\n}\n',
        "Cargo.toml": '[package]\nname = "project"\nversion = "0.1.0"\nedition = "2021"\n',
        ".gitignore": "target/\n",
    },
}

_DOCKERFILE = """FROM {base}
WORKDIR /app
COPY . .
RUN {install}
CMD [{cmd}]
"""

_CI = """name: CI
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: {command}
"""


class BuilderAgent(Agent):
    """Implements changes: files, projects, repositories, builds."""

    name = "builder"
    role = "software, systems, and automation engineer"
    capabilities = (
        "create_file", "scaffold_project", "write_dockerfile", "write_ci",
        "build", "run_tests",
    )
    limitations = (
        "writes only within the configured sandbox roots",
        "does not design features; it realises a given specification",
    )
    requires_llm = False

    def create_file(self, path: str, content: str) -> AgentResult:
        return self._timed(
            "create_file",
            lambda: (f"wrote {path}", {"path": str(self.fs.write(path, content))}),
        )

    def scaffold_project(self, destination: str, language: str = "python") -> AgentResult:
        """Create a minimal, buildable project skeleton."""

        def _run() -> tuple:
            lang = language.lower()
            if lang not in _SCAFFOLDS:
                raise ValueError(
                    f"unsupported language {language!r}; "
                    f"choose from {', '.join(sorted(_SCAFFOLDS))}"
                )
            root = Path(destination).expanduser()
            written: List[str] = []
            for rel, content in _SCAFFOLDS[lang].items():
                target = root / rel
                self.fs.write(str(target), content)
                written.append(str(target))
            return f"scaffolded {lang} project at {root} ({len(written)} files)", {
                "files": written,
                "language": lang,
            }

        return self._timed("scaffold_project", _run)

    def write_dockerfile(
        self,
        destination: str,
        *,
        base: str = "python:3.12-slim",
        install: str = "pip install -r requirements.txt",
        cmd: str = '"python", "src/main.py"',
    ) -> AgentResult:
        content = _DOCKERFILE.format(base=base, install=install, cmd=cmd)
        return self._timed(
            "write_dockerfile",
            lambda: (f"wrote Dockerfile at {destination}", {"path": str(self.fs.write(destination, content))}),
        )

    def write_ci(self, destination: str, *, command: str = "pytest -q") -> AgentResult:
        content = _CI.format(command=command)
        return self._timed(
            "write_ci",
            lambda: (f"wrote CI workflow at {destination}", {"path": str(self.fs.write(destination, content))}),
        )

    def build(self, command: str, *, confirmed: bool = False, cwd: Optional[str] = None) -> AgentResult:
        """Run a build command."""

        def _run() -> tuple:
            result = self.shell.run(command, confirmed=confirmed, cwd=cwd)
            data = result.to_dict()
            if not result.ok:
                raise RuntimeError(
                    f"build failed ({result.returncode}): {result.stderr or result.stdout}"
                )
            return f"build succeeded: {command}", data

        return self._timed("build", _run)

    def run_tests(self, command: str = "pytest -q", *, cwd: Optional[str] = None) -> AgentResult:
        """Run the test suite."""

        def _run() -> tuple:
            result = self.shell.run(command, cwd=cwd)
            data = result.to_dict()
            if not result.ok:
                raise RuntimeError(
                    f"tests failed ({result.returncode}): {result.stderr or result.stdout}"
                )
            return f"tests passed: {command}", data

        return self._timed("run_tests", _run)
