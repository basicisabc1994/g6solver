# SPDX-License-Identifier: Apache-2.0
"""The eight-question gate on a slice brief (design 17.1): tools/slice_gate.py names every question a brief leaves
unanswered, and docs/product_global_specs/slice_gate.md states the rule a builder reads."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "product_global_specs" / "slice_gate.md"


def _tool():
    spec = importlib.util.spec_from_file_location("slice_gate", _ROOT / "tools" / "slice_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _brief(tool, **changed: str | None) -> str:
    """A complete brief; ``changed`` replaces one question's answer, or drops its heading when the value is None."""
    parts = ["# Slice brief: the record", "", "Some opening text."]
    for name in tool.QUESTIONS:
        answer = changed.get(name, f"A plain answer about {name.replace('_', ' ')} for this slice, with its reason.")
        if answer is None:
            continue
        parts += ["", f"### Gate: {name}", answer]
    return "\n".join(parts) + "\n"


def test_the_questions_are_the_eight_of_the_design():
    assert _tool().QUESTIONS == ("outcome", "constraint_hypothesis", "observations_for_and_against",
                                 "simpler_alternative", "removes", "checked_by", "value_measured_by",
                                 "constraint_added")


def test_complete_brief_passes():
    tool = _tool()
    assert tool.unanswered(_brief(tool)) == []


def test_missing_heading_is_named():
    tool = _tool()
    assert tool.unanswered(_brief(tool, checked_by=None)) == ["checked_by"]
    assert tool.unanswered("") == list(tool.QUESTIONS)


@pytest.mark.parametrize("placeholder", ["n/a", "TBD", "none", "", "  \n\n", "N/A.", "- tbd", "todo", "?"])
def test_placeholder_answer_is_unanswered(placeholder):
    tool = _tool()
    assert tool.unanswered(_brief(tool, removes=placeholder)) == ["removes"]


def test_hypothesis_needs_a_planned_check():
    tool = _tool()
    alone = "hypothesis: people skip the block when it is long."
    assert tool.unanswered(_brief(tool, value_measured_by=alone)) == ["value_measured_by"]
    empty_check = alone + "\nplanned check: TBD"
    assert tool.unanswered(_brief(tool, value_measured_by=empty_check)) == ["value_measured_by"]
    whole = alone + "\nplanned check: count the tasks on which the block is marked used, under the frozen manifest."
    assert tool.unanswered(_brief(tool, value_measured_by=whole)) == []


def test_repeating_the_question_is_not_an_answer():
    tool = _tool()
    for name in tool.QUESTIONS:
        assert tool.unanswered(_brief(tool, **{name: tool.QUESTION_TEXT[name]})) == [name]
        assert tool.unanswered(_brief(tool, **{name: name.replace("_", " ") + "?"})) == [name]


def test_an_answer_ends_at_the_next_heading():
    tool = _tool()
    text = _brief(tool, outcome="")           # the next question's text must not count as this one's answer
    assert tool.unanswered(text) == ["outcome"]
    assert tool.unanswered(text.replace("### Gate: constraint_hypothesis", "## Notes")) == ["outcome",
                                                                                           "constraint_hypothesis"]


def test_a_question_answered_twice_is_judged_on_every_answer():
    tool = _tool()
    text = _brief(tool) + "\n### Gate: outcome\nTBD\n"
    assert tool.unanswered(text) == ["outcome"]


def test_gate_text_says_it_cannot_reorder_frozen_slices():
    text = " ".join(_DOC.read_text(encoding="utf-8").split())
    assert "cannot remove, reorder or delay a frozen slice" in text


def test_gate_text_adds_no_question_to_the_person():
    text = " ".join(_DOC.read_text(encoding="utf-8").split())
    assert "the builder's questions" in text and "creates no question to the person" in text
    assert "names the prohibited outcome it prevents" in text


def test_the_doc_lists_every_question_by_its_heading_name():
    tool, text = _tool(), _DOC.read_text(encoding="utf-8")
    for name in tool.QUESTIONS:
        assert f"`{name}`" in text


def test_command_line_names_what_is_unanswered(tmp_path, capsys):
    tool = _tool()
    brief = tmp_path / "brief.md"
    brief.write_text(_brief(tool, removes="TBD"), encoding="utf-8")
    assert tool.main([str(brief)]) == 1
    assert "removes" in capsys.readouterr().out
    brief.write_text(_brief(tool), encoding="utf-8")
    assert tool.main([str(brief)]) == 0
