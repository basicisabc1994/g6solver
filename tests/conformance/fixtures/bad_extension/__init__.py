# SPDX-License-Identifier: Apache-2.0
"""Counts the words in a note, badly: the conformance kit's bad fixture, which breaks every check and never ships.

``test_bad_fixture.py`` copies this folder into a temporary family (``g6.conformance_bad.bad_extension``), so the
registry finds it like any family member, and asserts that each check of the kit reports what is wrong here.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Literal

from pydantic import BaseModel

from g6.core import outside
from g6.core.ai_block import AIBlock
from g6.core.result import Result

OPS = ["count", "launch", "guess", "lookup", "silent", "tally", "serve", "spawn"]
SERVICE = outside.OutsideProgram(name="fixture service", purpose="answer the serve op", install="nothing; it never runs",
                                 licence="Apache-2.0", url="http://127.0.0.1:9/")


class BadInput(BaseModel):  # D4: no extra="forbid"
    op: Literal["count", "launch", "guess", "lookup", "silent", "tally", "serve", "spawn", "ops", "help"] = "ops"
    text: str = ""


class BadBlock(AIBlock[BadInput, dict, dict]):
    def __init__(self, name: str = "bad_extension", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: BadInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"ops": OPS})
        if data.op == "help":
            raise RuntimeError("the help text is not written yet")  # D4: the help op does not answer
        if data.op == "count":
            if not data.text:
                return Result.fail("[BAD_EXTENSION_ERROR] count: text is required")  # D4: no G6_E_ code
            return Result.ok({"count": len(data.text.split()), "completion_state": "qualified_draft"})  # not a state
        if data.op == "guess":
            return Result.ok({"answer": 42, "completion_state": "blocked-escalated"})  # blocked, with no code
        if data.op == "lookup":
            import fixture_heavy_dependency  # D9: raises when its extra is missing

            return Result.ok({"found": fixture_heavy_dependency.lookup(data.text)})
        if data.op == "silent":
            try:
                import fixture_heavy_dependency  # noqa: F401
                engine = "heavy"
            except ImportError:
                engine = "light"  # D9: falls back and does not say so (a "fallback" key is not a message)
            return Result.ok({"engine_used": engine, "fallback": False, "completion_state": "qualified-draft"})
        if data.op == "tally":
            return Result.ok({"tally": len(data.text), "completion_state": "qualified-draft"})
        if data.op == "serve":  # G5: talks to a local service, yet is not a sensitive op
            try:
                outside.call_service(SERVICE, "GET", "/")
            except outside.ServiceError as exc:
                return Result.ok({"completion_state": "blocked-escalated",
                                  "warning_card": {"code": exc.code, "message": str(exc)}})
            return Result.ok({"completion_state": "qualified-draft"})
        if data.op == "spawn":  # G5: starts a program itself (bounded, so G2 allows it), yet is not a sensitive op
            try:
                subprocess.run([sys.executable, "-c", "pass"], timeout=60, env={}, check=False)
            except OSError:
                pass
            return Result.ok({"completion_state": "qualified-draft"})
        return Result.ok({"launched": True, "completion_state": "qualified-draft"})


__g6_component__ = True
__g6_block__ = "BadBlock"
__g6_requires__ = ()
__g6_mitigates__ = ("made_up_failure", "weak_link")  # D2, D6: the first is not registered; for the second the test
# ranks the fixture too low
__g6_introduces__ = ()
__g6_sensitive_ops__ = ("launch", "teleport")  # G5: teleport is not one of the block's ops
__g6_extras__ = {"no_such_extra": ("fixture_heavy_dependency",)}  # G8: not an extra of pyproject.toml
__g6_outside__ = (SERVICE,)
# D8: no row on the Components page, and a vetting declaration with an unknown kind and no source register
__g6_vetting__ = {"kinds": ("astrology",), "register": None, "independent_pass": None, "signed_review": None}
