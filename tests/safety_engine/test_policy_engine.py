# SPDX-License-Identifier: Apache-2.0
"""The policy engine decides; it mints nothing, and its forbidden-path check is not fooled by spelling. Offline."""
from __future__ import annotations

import dataclasses

import pytest

from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, AuthorizationDecision, RuntimePolicy
from g6.safety_engine.state import RunState


def _decide(policy: RuntimePolicy, **action) -> AuthorizationDecision:
    return PolicyEngine().authorize_action(ActionRequest(tool_name="t", **action), RunState(policy=policy), policy)


def test_an_allow_is_a_decision_not_a_token() -> None:
    """Audit 007 SE-001/002/004: the permit was minted and consumed by one door, so it is gone."""
    decision = _decide(RuntimePolicy())
    assert decision.decision == "ALLOW" and decision.policy_checks[-1] == "allow"
    assert "permit" not in {f.name for f in dataclasses.fields(AuthorizationDecision)}


@pytest.mark.parametrize("touched", [
    "secrets/key.pem", "./secrets/key.pem", "src/../secrets/key.pem", "SECRETS/key.pem", "secrets\\key.pem",
])
def test_forbidden_paths_match_every_spelling(touched: str) -> None:
    """Audit 007 SE-008: an unnormalised, case-sensitive substring let these through."""
    decision = _decide(RuntimePolicy(forbidden_paths=frozenset({"secrets/"})), files_touched=(touched,))
    assert decision.decision == "BLOCK" and decision.reason == "forbidden path touched"


def test_an_unrelated_path_is_not_forbidden() -> None:
    decision = _decide(RuntimePolicy(forbidden_paths=frozenset({"secrets/"})), files_touched=("src/app.py",))
    assert decision.decision == "ALLOW"
