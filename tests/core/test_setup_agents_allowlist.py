# SPDX-License-Identifier: Apache-2.0
"""Only the owner's certain CommonMark shapes may precede or occupy setup's block."""
from __future__ import annotations

import pytest

from g6.cli import setup
from g6.mcp.instructions import AGENTS_BEGIN, AGENTS_END, agents_block


ORDINARY = ("# Project rules\n\n- Use pytest.\n- Keep diffs small.\n\n"
            "```bash\nmake test\n```\n\n- Write clear messages.\n  Keep them short.\n")


def test_ordinary_agents_bytes_append_and_update():
    data = ORDINARY.encode()
    expected = data + b"\n" + agents_block().encode() + b"\n"
    assert setup._agents_update(data, agents_block()) == expected
    stale = data + f"\n{AGENTS_BEGIN}\nstale instructions\n{AGENTS_END}\n".encode()
    assert setup._agents_update(stale, agents_block()) == expected
    assert setup._agents_update(expected, agents_block()) == expected


@pytest.mark.parametrize("shape", ["<pre>", "<!-- draft", "<div>", "> quote", "    code", "\tcode",
                                       " ```bash", "Title\n---", "Title\n===", "- - -", "- - ```", "- > ~~~"])
def test_uncertain_markdown_bytes_refuse_append_and_update(shape):
    data = shape.encode() + b"\n"
    with pytest.raises(setup._LeftAlone, match="uncertain"):
        setup._agents_update(data, agents_block())
    stale = data + f"{AGENTS_BEGIN}\nstale\n{AGENTS_END}\n".encode()
    with pytest.raises(setup._LeftAlone, match="uncertain"):
        setup._agents_update(stale, agents_block())


def test_ordinary_agents_file_is_appended_and_later_updated(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    target.write_bytes(ORDINARY.encode())
    setup.codex_agents(lambda prompt: "y", lambda text: None)
    assert target.read_bytes() == (ORDINARY + "\n" + agents_block() + "\n").encode()
    target.write_bytes((ORDINARY + f"\n{AGENTS_BEGIN}\nstale instructions\n{AGENTS_END}\n").encode())
    setup.codex_agents(lambda prompt: "y", lambda text: None)
    assert target.read_bytes() == (ORDINARY + "\n" + agents_block() + "\n").encode()
    setup.codex_agents(lambda prompt: pytest.fail("an unchanged block must not ask again"), lambda text: None)


@pytest.mark.parametrize("shape", [
    "<pre>\n```\n</pre>", "<!-- draft\n```\n-->", "<div>\n```\n</div>",
    "   <pre>", "> plain quote", "    plain code", "\tplain code", " ```bash", "  ~~~",
    "- ```bash", "* ~~~", "+ ```", "1. ~~~", "Title\n---", "Title\n===", "- - -", "* * *",
    "- item\n  <pre>", "- item\n  ```bash",
])
@pytest.mark.parametrize("position", ["append", "before_block", "inside_block"])
def test_uncertain_lines_leave_bytes_untouched_and_show_the_by_hand_block(tmp_path, monkeypatch, shape, position):
    target = tmp_path / "AGENTS.md"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    stale = f"{AGENTS_BEGIN}\nstale\n{AGENTS_END}\n"
    original = {"append": shape + "\n", "before_block": shape + "\n" + stale,
                "inside_block": f"{AGENTS_BEGIN}\n{shape}\n{AGENTS_END}\n"}[position].encode()
    target.write_bytes(original)
    said = []
    setup.codex_agents(lambda prompt: pytest.fail("uncertain Markdown must not ask to write"), said.append)
    assert target.read_bytes() == original
    assert len(said) == 1 and "uncertain" in said[0]
    assert said[0].endswith("\n\n" + agents_block() + "\n")
    assert not list(tmp_path.glob(".g6-agents-*"))


@pytest.mark.parametrize("text", [
    "- Mention ``` inline.\n", "* Mention ~~~ inline.\n", "+ Use tabs.\n", "1. Run tests.\n",
    "- item\n  continuation\n   another continuation\n",
    "```python\n    ```\n\t~~~\n<pre>\n> literal\n```\n",
])
def test_certain_list_text_and_fence_contents_allow_append(text):
    data = text.encode()
    assert setup._agents_update(data, agents_block()) == data + b"\n" + agents_block().encode() + b"\n"


def test_uncertain_tail_does_not_prevent_replacing_an_earlier_block():
    tail = b"\n<pre>\n    code\n"
    original = f"{AGENTS_BEGIN}\nstale\n{AGENTS_END}".encode() + tail
    assert setup._agents_update(original, agents_block()) == agents_block().encode() + tail
