# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 3: guidance the routing readers do not read (an HTML block, or a part past the Markdown reader's
nesting limit) is a problem of its own (``UNREAD``), never a text that names nothing."""
import json

import pytest

from g6.core.markdown import parse
from g6.skills import routing

CODE = chr(96)


def _check(mention):
    manifest = routing.loads(json.dumps({
        "format": routing.FORMAT, "release": "0.3.0",
        "routes": {"find": {"anchor": "## Find", "detail": []}},
        "entries": {
            "mcp:g6/g6_find": {"agent_facing": True, "route": "find", "since": "0.3.0"},
            "mcp:g6/g6_private": {"agent_facing": False,
                                  "exemption": "Host-only operation, never teach agents to call it.",
                                  "since": "0.3.0"},
        },
    }))
    root = routing.ABSENT_TOOL_RULE + f"\n\n## Find\nUse {CODE}g6_find{CODE}.\n\n" + mention + "\n"
    return parse(root), routing.check(manifest, registrations={"g6": ["g6_find", "g6_private"]}, ops={},
                                      texts={routing.ROOT: root}, root_words=len(root.split()), version="0.3.0",
                                      files=[routing.ROOT])


@pytest.mark.parametrize("style", ["html", "truncated"])
def test_unread_guidance_is_a_problem(style):
    _, control = _check(f"Call {CODE}g6_private{CODE}.")
    assert [problem.code for problem in control] == ["EXEMPT_NAMED"]
    mention = ("<div>Call <code>g6_private</code>.</div>" if style == "html"
               else "> " * 20 + f"Call {CODE}g6_private{CODE}.")
    document, problems = _check(mention)
    assert document.truncated == (style == "truncated")
    assert [(problem.code, problem.key) for problem in problems] == [("UNREAD", routing.ROOT)]


def test_inline_html_in_a_paragraph_is_read():
    # the shipped guidance uses inline tags (<br>); their text stays in the prose the readers read
    _, problems = _check(f"A line<br>and {CODE}g6_find{CODE} again.")
    assert problems == []
