# SPDX-License-Identifier: Apache-2.0
"""Sensitive ops over MCP: refused unless the person who runs the server lists them (owner, 2026-10-20)."""
from __future__ import annotations

from returns.result import Success

from g6.invariants import check_envelope
from g6.mcp import SENSITIVE_ENV, allowed_sensitive, describe_reply, invoke_reply


class _Registry:
    def __init__(self) -> None:
        self.blocked: list[bool] = []

    def invoke(self, component, op, params, *, block_sensitive=False):  # noqa: ANN001, ANN201
        self.blocked.append(block_sensitive)
        return Success({"completion_state": "qualified-draft"})


def _blocked(allow: str, component: str = "lean", op: str = "check_proof") -> bool:
    registry = _Registry()
    found = invoke_reply(component, op, "{}", registry=registry, allow_sensitive=allowed_sensitive(allow))
    assert check_envelope(found) == [], found
    return registry.blocked[-1]


def test_with_nothing_listed_every_sensitive_op_stays_refused() -> None:
    assert allowed_sensitive("") == frozenset()
    assert _blocked("") is True


def test_the_person_lists_a_component_or_one_of_its_ops() -> None:
    assert _blocked("lean") is False
    assert _blocked("lean.check_proof") is False
    assert _blocked("lean.lean_version") is True  # another op of the same component is not listed
    assert _blocked("model_checking, lean ,prolog") is False  # commas, spaces


def test_there_is_no_wildcard() -> None:
    assert _blocked("*") is True
    assert _blocked("all") is True
    assert _blocked("lea") is True


def test_the_setting_is_read_from_the_server_environment(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(SENSITIVE_ENV, "lean.check_proof")
    assert allowed_sensitive() == frozenset({"lean.check_proof"})
    monkeypatch.delenv(SENSITIVE_ENV)
    assert allowed_sensitive() == frozenset()


def test_a_refusal_says_only_the_person_running_the_server_can_allow_it() -> None:
    found = invoke_reply("lean", "check_proof", "{}", allow_sensitive=frozenset())
    assert found["ok"] is False and found["completion_state"] == "blocked-escalated"
    assert found["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert SENSITIVE_ENV in found["error"] and "lean.check_proof" in found["error"]
    assert "person" in found["error"]


def test_an_allowed_op_reaches_its_component() -> None:
    found = invoke_reply("lean", "check_proof", '{"source": "theorem t : 1 = 1 := rfl", "theorem": "t"}',
                         allow_sensitive=frozenset({"lean.check_proof"}))
    assert check_envelope(found) == [], found
    assert found["warning_card"]["code"] != "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert found["completion_state"] != "verified"  # Lean's own answer, never lifted


def test_describe_says_which_sensitive_ops_this_server_allows() -> None:
    info = describe_reply("lean", allow_sensitive=frozenset({"lean.check_proof"}))
    assert set(info["sensitive_ops"]) == {"check_proof", "lean_version", "prove"}  # prove runs Lean (core run 7)
    assert info["sensitive_ops_allowed_here"] == ["check_proof"]
    assert describe_reply("lean", allow_sensitive=frozenset())["sensitive_ops_allowed_here"] == []
    assert "sensitive_ops_allowed_here" not in describe_reply("formal", allow_sensitive=frozenset({"formal"}))
