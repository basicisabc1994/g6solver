# SPDX-License-Identifier: Apache-2.0
"""Every Python example on the developer page must run as written."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "site" / "docs" / "developers.md"
TEXT = PAGE.read_text(encoding="utf-8")
# Each block keeps its own line numbers in the page, so a check's source that the harness reads back (inspect, through
# the page's file name) is that check's own line. Numbered from the block's first line, a lambda's source was read
# from the page's prose at the same line number, and a prose line with an apostrophe broke the tokenizer.
BLOCKS = ["\n" * TEXT[:found.start(1)].count("\n") + found.group(1)
          for found in re.finditer(r"```python\n(.*?)```", TEXT, flags=re.S)]


@pytest.mark.parametrize("code", BLOCKS, ids=[f"block{i}" for i in range(len(BLOCKS))])
def test_example_runs(code):
    exec(compile(code, str(PAGE), "exec"), {})  # noqa: S102 - our own documentation
