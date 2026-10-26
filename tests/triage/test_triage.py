# SPDX-License-Identifier: Apache-2.0
"""g6.triage (audit 022): a classification by class name, never verified; a model may only tighten. Offline."""
from __future__ import annotations

import json

import pytest

from g6.triage import classify, clamp, refine


@pytest.mark.parametrize(("error_type", "category"), [
    ("KeyError", "key"), ("ConnectionResetError", "network"), ("TimeoutError", "network"),
    ("FileNotFoundError", "io"), ("ModuleNotFoundError", "import"), ("IndentationError", "syntax"),
    ("builtins.ZeroDivisionError", "arithmetic"), ("MyAppError", "unknown"),
    # not a builtin: nothing named in an error string is imported, so it is unknown (and goes to review)
    ("json.decoder.JSONDecodeError", "unknown"),
])
def test_the_category_comes_from_the_exception_class(error_type: str, category: str) -> None:
    assert classify(error_type, "x").category == category


def test_sd005_a_classification_is_never_verified_even_when_a_model_agrees() -> None:
    """The audit's falsification: a stub planner agreeing on "key" / "auto_heal" for a missing-file KeyError."""
    base = classify("KeyError", "config.yaml not found")
    assert base.completion_state == "qualified-draft" and base.posture == "review"
    assert "message_points_at_io" in base.reasons
    stub = lambda prompt: json.dumps({"category": "key", "posture": "auto_heal"})  # noqa: E731
    refined = refine(base, complete=stub, error_type="KeyError", message="config.yaml not found")
    assert refined.completion_state == "qualified-draft" and refined.posture == "review"


def test_a_plain_code_error_may_be_fixed_without_a_person_but_the_outside_world_may_not() -> None:
    assert classify("KeyError", "'timeout'").posture == "auto_heal"
    assert classify("ConnectionRefusedError", "connection refused").posture == "review"
    assert classify("KeyError", "'timeout'", safety_approved=False).posture == "escalate"


def test_a_model_may_name_an_unknown_category_and_raise_the_posture_but_never_lower_it() -> None:
    unknown = classify("MyAppError", "quota exceeded")
    named = refine(unknown, complete=lambda p: '{"category": "resource", "posture": "escalate"}',
                   error_type="MyAppError", message="quota exceeded")
    assert (named.category, named.posture, named.source) == ("resource", "escalate", "model")
    lowered = refine(unknown, complete=lambda p: '{"category": "value", "posture": "auto_heal"}',
                     error_type="MyAppError", message="quota exceeded")
    assert lowered.posture == "review"
    renamed = refine(classify("KeyError", "'a'"), complete=lambda p: '{"category": "io", "posture": "review"}',
                     error_type="KeyError", message="'a'")
    assert renamed.category == "key" and renamed.posture == "review"   # a known category is not overwritten


@pytest.mark.parametrize("reply", ["not json", '{"category": "made-up", "posture": "review"}',
                                   '{"category": "key", "posture": "whenever"}', '[]'])
def test_an_unreadable_model_reply_leaves_the_table_answer(reply: str) -> None:
    base = classify("MyAppError", "x")
    refined = refine(base, complete=lambda p: reply, error_type="MyAppError", message="x")
    assert (refined.category, refined.posture) == (base.category, base.posture)
    assert "model_reply_unreadable" in refined.reasons


def test_clamp_is_one_way_and_fails_closed_on_unknown_input() -> None:
    assert clamp("review", "auto_heal") == "review" and clamp("auto_heal", "escalate") == "escalate"
    with pytest.raises(ValueError):
        clamp("review", "maybe")
