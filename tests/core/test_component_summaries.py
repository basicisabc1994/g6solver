# SPDX-License-Identifier: Apache-2.0
"""What an agent reads first about a component (``describe()['summary']``, served by g6_describe, g6_components and
g6_find) is one complete, plain sentence: never cut at a docstring's line break, never an audit reference or a spec
file name, and no docstring names the private codebase."""
from __future__ import annotations

import importlib
import re

from g6.core.registry import get_registry


def test_every_component_summary_is_one_complete_plain_sentence():
    reg = get_registry()
    for name in sorted(reg.names()):
        summary = reg.describe(name)["summary"]
        assert summary.endswith("."), (name, summary)
        assert not re.search(r"audit \d|§|\.md\b", summary), (name, summary)
        assert len(summary) <= 170, (name, len(summary))


def test_no_component_docstring_names_the_private_codebase():
    reg = get_registry()
    for name in reg.names():
        assert "mvp." not in (importlib.import_module(reg.refs[name].module).__doc__ or ""), name
