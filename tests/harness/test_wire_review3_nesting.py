# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from g6.harness import Check
from g6.harness import completion_loop as loop
from g6.metaharness._code_safety import skill_text_problems
from g6.metaharness.schema import Model, Profile, Suite, SuiteCase, Workflow


def test_deep_commonmark_code_still_requires_review():
    shallow = "\n".join(
        "> " + line for line in ("```python", "x = value.real", "```")
    )
    assert skill_text_problems(shallow)
    nested = "\n".join(
        "> " * 20 + line
        for line in ("```python", "x = value.real", "```")
    )
    workflow = Workflow(
        name="review", task="{input}",
        checks=(Check("nonempty", bool),),
        oracle=lambda output, expected: output == expected,
        suite=Suite(
            (SuiteCase("dev", "one", "1"),),
            (SuiteCase("held", "two", "2"),),
        ),
        models={"local": Model(SimpleNamespace(complete=lambda *_: "unused"))},
    )
    assert workflow.problems() == []
    profile = Profile(routing={"answer": "local"}, skill_text=nested)
    problems = profile.problems_for(workflow)
    assert any("G6_E_META_SKILL_CODE" in why for _, why in problems), problems


def test_deep_commonmark_question_keeps_completion_waiting():
    reply = "> " * 20 + "May I install the missing dependency?"
    repair = loop.Repairs(
        [{"failing": ["check"], "text": "fix this", "tree": "same"}],
        lambda: ({"check"}, "fix this"), lambda: "same",
        answered=True, asked=loop.asks(reply),
    )
    repair.search(None, None)
    repair.search(None, None)
    assert repair.waiting, repair.why
