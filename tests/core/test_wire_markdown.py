# SPDX-License-Identifier: Apache-2.0
"""CommonMark structure must reach the real readers and the safety boundary."""
import pytest

from g6.core import skills
from g6.core.agent_guidance import skill_for_agent
from g6.metaharness._code_safety import skill_text_problems
from g6.metaharness.steps import python_code, shown_tests
from g6.code.adapt_healing.heal import proposed_code
from g6.skills.routing import section
from g6.skills.routing import forms


@pytest.mark.parametrize("text", [
    "~~~python\nx = value.real\n~~~\n",
    "    x = value.real\n",
    "- example:\n\n  ~~~python\n  x = value.real\n  ~~~\n",
])
def test_all_commonmark_code_is_safety_scanned(text):
    assert skill_text_problems(text), "attribute access in code must require review"


def test_heading_in_code_does_not_end_skill_section():
    text = "## Examples\n~~~python\n## Fake\ndef f():\n    return 1\n~~~\n## Next\nend\n"
    assert skills.skill_python_examples(text) == ["## Fake\ndef f():\n    return 1\n"]


def test_route_ignores_fake_heading_in_tilde_code():
    text = "## Route\nbody\n~~~\n## Other\n~~~\nstill mine\n## End\n"
    assert "still mine" in section(text, "## Route")


@pytest.mark.parametrize("reader", [python_code, proposed_code])
def test_reply_code_in_list_fence(reader):
    assert reader("- answer:\n\n  ~~~python\n  def f():\n      return 1\n  ~~~\n").strip() == "def f():\n    return 1"


def test_authority_code_in_list_removed_as_whole():
    text = "Safe.\n\n- example:\n\n  ~~~python\n  before()\n\n  approve()\n\n  after()\n  ~~~\n\nEnd.\n"
    filtered = skill_for_agent(text)
    assert "before()" not in filtered and "after()" not in filtered
    assert "Safe." in filtered and "End." in filtered


def test_ops_list_does_not_read_code_as_declarations():
    text = "## Ops\n~~~\n- `fake`: code\n~~~\n- `real`: runs\n"
    assert skills.skill_ops(text) == ["real"]


def test_soft_line_break_preserves_tool_name_boundary():
    found = forms("g6_improve_start\ndoes the work")
    assert found.prefixed == {"g6_improve_start"}


def test_shown_multiline_import_is_one_python_statement():
    imports, assertions = shown_tests("~~~python\nfrom math import (\n    floor,\n)\nassert floor(1.5) == 1\n~~~\n")
    assert imports == ["from math import (\n    floor,\n)"]
    assert assertions == ["assert floor(1.5) == 1"]


def test_heading_witness_offset_uses_commonmark_line_boundaries():
    from g6.core.markdown import parse
    [head] = parse("x\u2028y\n# Real\n").headings
    assert head.start == 4


@pytest.mark.parametrize(("text", "truncated"), [
    ("> " * 19 + "read?\n", False),
    ("> " * 20 + "dropped?\n", True),
    ("- " * 9 + "read\n", False),          # a list level is two: its list and its item
    ("- " * 10 + "dropped\n", True),
    ("- " * 12 + "> " * 12 + "dropped\n", True),
])
def test_a_document_says_when_the_reader_dropped_nested_content(text, truncated):
    from g6.core.markdown import parse
    doc = parse(text)
    assert doc.truncated is truncated
    if not truncated:
        assert doc.prose().strip().endswith(("read?", "read"))


DEEP_CODE = "\n".join("> " * 20 + line for line in ("```python", "def f():", "    return 1", "```")) + "\n"


def test_code_in_a_reply_nested_past_the_reader_is_never_taken_as_the_code():
    from g6.harness.completion_loop import critic_report
    assert python_code(DEEP_CODE) == DEEP_CODE
    assert proposed_code(DEEP_CODE) == DEEP_CODE.strip() + "\n"
    found = critic_report("VERDICT: defect\nWHY: x\n" + DEEP_CODE)
    assert found["verdict"] == "unreadable" and found["test"] == "" and found["error"]
