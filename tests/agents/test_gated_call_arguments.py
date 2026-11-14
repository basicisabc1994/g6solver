# SPDX-License-Identifier: Apache-2.0
"""``GatedCalls.call`` answers a call it cannot read with a refusal, and raises nothing (issuance review of the
arguments' visit budget, finding 4; the certificate issuance window before 0.3.0).

A model writes a tool's arguments, and a framework's adapter hands them on as written. ``call`` promises a verdict for
every call and "nothing is raised", so that the framework and its model see a refusal as the tool's result. Reading the
arguments through ``g6.core.registry.outer_copy`` broke that for arguments that are no mapping: a list of pairs, text
or a number raised ``TypeError`` out of ``call``. The class, not those three: whatever is written for a call (its
arguments, its name, its paths) that G6 cannot read as a call is refused ``G6_E_INPUT_VALIDATION`` before the gate is
asked, and nothing runs. A list of pairs is refused, never converted.

Offline: no model, no program, no component runs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from g6.agents._runtime import codes
from g6.agents._runtime.tools import GatedCalls

REFUSED = "G6_E_INPUT_VALIDATION"


class NeverAsked:
    """A gate that must not be reached: a call that cannot be read is refused before any decision is asked for."""

    def decide(self, call, changes=False):   # pragma: no cover - reaching it is the failure
        raise AssertionError("the gate was asked")


class Allows:
    def decide(self, call, changes=False):
        return SimpleNamespace(runs=True, verdict="run", code="", reason="")


class Unreadable:
    """A mapping by protocol whose keys cannot be read."""

    def keys(self):
        raise RuntimeError("the keys are gone")

    def __getitem__(self, key):   # pragma: no cover - never reached
        raise AssertionError("looked up")


NOT_A_MAPPING = {"pairs": [["text", "hello"]], "tuples": [("text", "hello")], "text": "not an object", "number": 7,
                 "an empty list": [], "empty text": "", "zero": 0, "false": False, "a set": {"text"},
                 "bytes": b'{"text": "hello"}', "an object": object(), "unreadable": Unreadable()}


def _calls(gate=None):
    ran: list = []

    def a_tool(**given):
        ran.append(given)
        return given

    return GatedCalls(gate or NeverAsked(), {"a_tool": a_tool}), ran


@pytest.mark.parametrize("written", NOT_A_MAPPING.values(), ids=NOT_A_MAPPING.keys())
@pytest.mark.parametrize("name", ["a_tool", "demo.op"], ids=["a tool", "a component's op"])
def test_arguments_that_are_no_mapping_are_refused_and_nothing_is_raised_asked_or_run(written, name):
    calls, ran = _calls()
    reply = calls.call(name, written)
    assert reply["verdict"] == "refused" and reply["code"] == REFUSED, reply
    assert reply["reason"].startswith(f"{name}: ") and "a mapping" in reply["reason"]
    assert ran == [] and calls.take() == []


def test_a_tool_the_framework_holds_is_not_run_on_arguments_that_are_no_mapping():
    calls, ran = _calls()
    reply = calls.call("a_tool", [["text", "hello"]], fn=lambda **given: ran.append(given))
    assert reply["verdict"] == "refused" and reply["code"] == REFUSED and ran == []


@pytest.mark.parametrize(("name", "arguments", "paths"), [
    ("a_tool", {1: "one"}, ()),                       # a key that is no name
    ("a_tool", {"text": "hello"}, [""]),              # a path that names nothing
    ("a_tool", {"text": "hello"}, ["notes"] * 201),   # more paths than a call holds
    ("a_tool", {"text": "hello"}, 7),                 # paths that are no sequence
    ("t" * 81, {"text": "hello"}, ()),                # a name longer than a tool's
    ("", {"text": "hello"}, ()),
    (7, {"text": "hello"}, ()),
], ids=["key", "empty path", "too many paths", "paths", "long name", "no name", "name"])
def test_a_call_that_cannot_be_read_as_one_is_refused_and_nothing_is_raised_asked_or_run(name, arguments, paths):
    calls, ran = _calls()
    reply = calls.call(name, arguments, paths)
    assert reply["verdict"] == "refused" and reply["code"] == REFUSED, reply
    assert ran == [] and calls.take() == [] and len(reply["reason"]) <= 500


def test_a_call_with_no_arguments_and_one_with_a_mapping_run_as_before():
    calls, ran = _calls(Allows())
    assert calls.call("a_tool") == {"verdict": "run", "code": codes.TOOL_ALLOWED, "result": {}}
    assert calls.call("a_tool", None)["result"] == {} and calls.call("a_tool", {})["result"] == {}
    assert calls.call("a_tool", {"text": "hello"})["result"] == {"text": "hello"}
    assert ran == [{}, {}, {}, {"text": "hello"}] and len(calls.take()) == 4


def test_what_ends_a_run_is_not_taken_for_a_refusal():
    """Only an error of the call is its refusal: what is no ``Exception`` (an interrupt, an exit) still ends the run."""

    class Interrupted:
        def keys(self):
            raise KeyboardInterrupt

        def __getitem__(self, key):   # pragma: no cover - never reached
            raise AssertionError("looked up")

    calls, _ = _calls()
    with pytest.raises(KeyboardInterrupt):
        calls.call("a_tool", Interrupted())
