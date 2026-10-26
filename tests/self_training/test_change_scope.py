# SPDX-License-Identifier: Apache-2.0
"""classify_change_scope: the paths a self-modification may touch (audit 024 REL-003, REL-004; tests T-02 to T-04)."""
from __future__ import annotations

import pytest

from g6.self_training import ChangeScope, ScopeVerdict, classify_change_scope


def _decide(*changed: str, deleted: tuple[str, ...] = (), component: str = "demo"):
    return classify_change_scope(ChangeScope(component, tuple(changed), deleted))


def _codes(decision) -> list[str]:
    return [reason.split(":", 1)[0] for reason in decision.reasons]


def test_a_change_inside_its_component_is_allowed() -> None:
    decision = _decide("src/g6/demo/core.py", "./src/g6/demo//util.py")
    assert decision.verdict == ScopeVerdict.ALLOW and decision.reasons == ()


@pytest.mark.parametrize("path", [
    "src/g6/demo/../../../bases/rest/app.py",          # T-03: '..' out of the component
    "tests/demo/../../src/g6/core/llm.py",
    "src/g6/llm_router/llm_block.py",                  # T-04: another component
    "src/g6/demoX/core.py",                            # a prefix is not a component
])
def test_a_path_outside_the_component_is_blocked(path: str) -> None:
    decision = _decide(path)
    assert decision.verdict == ScopeVerdict.BLOCK and "G6_E_SCOPE_OTHER_COMPONENT" in _codes(decision)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "C:/Windows/x.py", "", "src/../../x.py"])
def test_a_path_that_leaves_the_repository_is_blocked(path: str) -> None:
    assert "G6_E_SCOPE_OUTSIDE_REPO" in _codes(_decide(path))


@pytest.mark.parametrize("path", ["tests/demo/conftest.py", "src/g6/demo/pytest.ini", "tests/demo/Conftest.py"])
def test_pytest_configuration_is_blocked(path: str) -> None:
    """T-02 (REL-003): a new conftest.py that skips every test turned a broken change green."""
    decision = _decide(path)
    assert decision.verdict == ScopeVerdict.BLOCK and "G6_E_SCOPE_TEST_CONFIG" in _codes(decision)


def test_a_deletion_or_an_empty_change_is_blocked() -> None:
    assert _codes(_decide("src/g6/demo/a.py", deleted=("src/g6/demo/b.py",))) == ["G6_E_SCOPE_DELETION"]
    assert _codes(_decide()) == ["G6_E_SCOPE_EMPTY"]
    assert "G6_E_SCOPE_NO_COMPONENT" in _codes(_decide("src/g6/x.py", component=""))


def test_a_protected_fragment_needs_a_person() -> None:
    decision = _decide("tests/demo/test_core.py")   # tests are evaluators: a learner may not edit its grader
    assert decision.verdict == ScopeVerdict.HITL and _codes(decision) == ["G6_E_SCOPE_PROTECTED"]


def test_the_strictest_reason_wins() -> None:
    decision = _decide("tests/demo/test_core.py", "src/g6/core/llm.py")
    assert decision.verdict == ScopeVerdict.BLOCK
    assert {"G6_E_SCOPE_PROTECTED", "G6_E_SCOPE_OTHER_COMPONENT"} <= set(_codes(decision))
