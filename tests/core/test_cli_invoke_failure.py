# SPDX-License-Identifier: Apache-2.0
"""A component runtime error becomes a short, masked CLI failure."""
from __future__ import annotations

import json

import pytest

from g6.cli import main


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_invoked_op_failure_is_masked_and_exits_nonzero(monkeypatch, capsys, registry_reset, json_mode, error):
    from g6.core.registry import get_registry

    cls = get_registry().get_class("grounding")

    def fail(self, data):
        raise error("private exception detail /private/example.py")

    monkeypatch.setattr(cls, "infer", fail)
    argv = ["invoke", "grounding", "check_claim", "{}"] + (["--json"] if json_mode else [])
    assert main(argv) != 0
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "grounding" in text and "check_claim" in text and "failed while running" in text
    assert "private" not in text and "Traceback" not in text and error.__name__ not in text
    assert "blocked-escalated" in text
    if json_mode:
        assert json.loads(captured.out)["completion_state"] == "blocked-escalated"
        assert captured.err == ""
