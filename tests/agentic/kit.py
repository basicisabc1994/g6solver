# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the agentic override kernel's tests: the cegis float example, a counted FakeLLM, and a stand-in for the
confined child.

``in_process_child`` replaces ``g6._agentic._child.launch`` for a test: it runs the child's own code (``_child.serve``)
in the test's process, on the development fixtures here only, with cegis's runner seam set for that run and restored
after it. It exists because no operating-system backend can confine a child on every machine the tests run on; the
real launch is tested where one can, and asserted refused where none can (``test_child_isolation.py``). Production code
never runs the child unconfined."""
from __future__ import annotations

import json
from typing import Any

from g6._agentic import _child
from g6.cegis import runner
from g6.core.llm import FakeLLM

FLOAT_SKETCH = "def f(x):\n    return x * ??f\n"
TWO_FLOATS = "def f(x):\n    return x * ??f + ??f\n"
INT_SKETCH = "def f(x):\n    return x * ?? + ??\n"
CASES = [{"input": {"x": 1}, "output": 0.5}, {"input": {"x": 2}, "output": 1.0}, {"input": {"x": 4}, "output": 2.0}]
INT_CASES = [{"input": {"x": 0}, "output": 1}, {"input": {"x": 1}, "output": 4}, {"input": {"x": 2}, "output": 7}]
FLOAT_PARAMS = {"sketch": FLOAT_SKETCH, "cases": CASES}
INT_PARAMS = {"sketch": INT_SKETCH, "cases": INT_CASES}
ALLOW = ("agentic.run", "cegis.synthesize")
STAND_IN = {"backend": "test-in-process", "why": "a test's stand-in for the kernel's confined child"}


class Model:
    """A FakeLLM that answers each prompt with the next scripted value as ``{"value": ...}`` (or raw text), and keeps
    every prompt it was given."""

    def __init__(self, *values: Any, raw: str | None = None) -> None:
        self.values, self.raw, self.prompts = list(values), raw, []
        self.llm = FakeLLM(self._answer)

    def _answer(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if self.raw is not None:
            return self.raw
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return json.dumps({"value": value})


def in_process_child(monkeypatch) -> list[dict]:
    """Replace the child's launch with the in-process stand-in; returns the list of requests it served."""
    served: list[dict] = []

    def launch(request: dict, *, timeout_s: float) -> tuple[dict, dict]:
        request = json.loads(json.dumps(request))
        served.append(request)
        saved = runner._INSIDE
        runner.inside_kernel_child(STAND_IN)
        try:
            reply = _child.serve(request)
        finally:
            runner._INSIDE = saved
        if "refused" in reply:
            raise _child.Refused(reply["refused"])
        return reply, dict(STAND_IN)

    monkeypatch.setattr(_child, "launch", launch)
    return served


def cegis_in_process(monkeypatch) -> None:
    """cegis's own candidate runs in the test's process (the seam), for an original that must run candidates here."""
    monkeypatch.setattr(runner, "_INSIDE", dict(STAND_IN))


def no_child(monkeypatch) -> list[dict]:
    """A launch that records and refuses: a test that must start no child fails on its record."""
    started: list[dict] = []

    def launch(request: dict, *, timeout_s: float) -> tuple[dict, dict]:
        started.append(request)
        raise AssertionError("no child may start here")

    monkeypatch.setattr(_child, "launch", launch)
    return started


def configured_model(monkeypatch, model: Model) -> None:
    """MCP uses a configured endpoint; replace only its HTTP transport with the scripted answer.

    A FakeLLM responder is explicit Python input, never a configured/MCP caller (repair 1 ruling F1).
    """
    from g6._agentic import kernel
    from g6.core import llm

    monkeypatch.setattr(kernel, "person_caller", lambda: llm.OllamaCaller("fixture-model", "http://127.0.0.1:9"))

    def post(url, body, headers, timeout):
        assert url == "http://127.0.0.1:9/api/chat"
        system, user = (message["content"] for message in body["messages"])
        return {"message": {"content": model._answer(system, user)}}

    monkeypatch.setattr(llm, "_post_json", post)
