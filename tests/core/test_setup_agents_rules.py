# SPDX-License-Identifier: Apache-2.0
"""Certain separators, front matter and nested lists preserve the person's bytes."""
from __future__ import annotations

import pytest

from g6.cli import setup
from g6.mcp.instructions import agents_block


@pytest.mark.parametrize("text", [
    "---\n", "***\n", "___\n", "-----\n", "*****\n", "_____\n",
    "# Rules\n\n---\n\nUse pytest.\n",
    "---\ndescription: rules\n---\n\n# Rules\n",
    "---\ndescription: rules\n...\n\n# Rules\n",
    "---\ntags:\n  - tests\n    - unit\n---\n\n# Rules\n",
    "- Tests\n  - Unit\n    - Run pytest.\n      - Keep it offline.\n",
    "- Tests\n\n  - Unit\n\n    - Run pytest.\n",
    "- Tests\n    - Unit\n        - Run pytest.\n",
])
def test_certain_rules_append_update_and_repeat(text):
    original = text.encode()
    updated = setup._agents_update(original, agents_block())
    assert updated.startswith(original)
    assert updated[len(original):].lstrip(b"\n") == (agents_block() + "\n").encode()
    assert setup._agents_update(updated, agents_block()) == updated
    stale = updated.replace(b"## G6", b"## G6 (old)")
    assert setup._agents_update(stale, agents_block()) == updated


@pytest.mark.parametrize("text", [
    "Title\n---\n", "Title\n===\n", "- - -\n", "* * *\n", "_ _ _\n",
    "---\n    code\n---\n",
    "Text\n    - code, not a list\n", "- item\n  continuation\n    - code\n",
    "- item\n      - indented code within the item\n",
    "- item\n  - nested\n    - ```\n", "- item\n  - nested\n    - > quote\n",
])
def test_uncertain_separators_and_indentation_still_refuse(text):
    with pytest.raises(setup._LeftAlone):
        setup._agents_update(text.encode(), agents_block())
