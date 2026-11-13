# SPDX-License-Identifier: Apache-2.0
"""No test can start the person's installed Claude Code or Codex (``tests/conftest.py``, ``no_real_host_program``).

A test that stood a host in by patching a caller's own method was passed by once ``g6.self_maintenance.usage`` was
imported, and the real program on PATH was looked for and started. Under the suite's guard, looking for either program
finds nothing, so a host is found only where a test stands one in (``tests/host_stand_in.py``)."""
from __future__ import annotations

import os

import pytest

from g6.core import host_models
from g6.core.llm import LLMError


@pytest.mark.parametrize("kind", [host_models.ClaudeCodeCaller, host_models.CodexCaller])
def test_neither_host_program_is_found_while_a_test_runs(kind) -> None:
    with pytest.raises(LLMError, match="G6_E_OUTSIDE_PROGRAM_MISSING|is not ready"):
        kind().locate()


def test_the_guard_names_a_program_that_does_not_exist() -> None:
    for name in ("G6_CLAUDE", "G6_CODEX"):
        assert os.environ.get(name) and not os.path.exists(os.environ[name]), name
