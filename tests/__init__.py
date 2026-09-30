"""Shared test fixtures.

Tests run against a throwaway ``$KALI_OPS_HOME`` under a temporary directory so
they never touch the operator's real state, log, or killswitch sentinel.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kali_ops.config import Config
from kali_ops.killswitch import Killswitch
from kali_ops.logging import OperationLog
from kali_ops.orchestrator import Runtime


class RuntimeTestCase(unittest.TestCase):
    """Base class providing an isolated runtime per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = Config(home=self.home)
        self.config.ensure_home()
        self.killswitch = Killswitch(self.config)
        self.log = OperationLog(self.config.log_path)
        self._env_backup = os.environ.pop("KALI_OPS_KILLSWITCH", None)

    def tearDown(self) -> None:
        if self._env_backup is not None:
            os.environ["KALI_OPS_KILLSWITCH"] = self._env_backup
        self._tmp.cleanup()

    def build_runtime(self, **overrides) -> Runtime:
        config = Config(home=self.home, **overrides)
        return Runtime.build(config)
