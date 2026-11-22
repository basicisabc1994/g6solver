# SPDX-License-Identifier: Apache-2.0
"""Source-only guidance and removed inline marks retain name boundaries."""
import json

import pytest

from g6.core.markdown import parse
from g6.skills import routing


@pytest.mark.parametrize("mention", [
    '[old]: g6_removed_tool "stale reference"',
    'Fallback<em>g6_removed_tool</em>',
])
def test_source_name_is_read_or_reported_unread(mention):
    manifest = routing.loads(json.dumps({
        "format": routing.FORMAT, "release": "0.3.0",
        "routes": {"find": {"anchor": "## Find", "detail": []}},
        "entries": {"mcp:g6/g6_find": {"agent_facing": True, "route": "find", "since": "0.3.0"}},
    }))
    root = routing.ABSENT_TOOL_RULE + "\n\n## Find\nUse `g6_find`.\n\n" + mention + "\n"
    problems = routing.check(manifest, registrations={"g6": ["g6_find"]}, ops={},
                             texts={routing.ROOT: root}, root_words=len(root.split()), version="0.3.0",
                             files=[routing.ROOT])
    assert {p.code for p in problems} & {"STALE", "UNREAD"}


@pytest.mark.parametrize("text", ["word<em>name</em>", "**word**name", "[word](url)name", "![word](url)name"])
def test_prose_separates_words_at_dropped_inline_markup(text):
    assert parse(text).prose().split() == ["word", "name"]
