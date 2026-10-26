# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-015: the replay constructor says it is a fixed template, not intelligence.

The archive stored its one regex-matched replay check as "harness intelligence" with nothing in the construction
saying how narrow it is. Every construction now carries ``constructor`` (two fixed templates, each selected only by one
requirement sentence or a confirmed relation; not general) and a limit the person reads before confirming; a reworded
requirement constructs nothing. Offline.
"""
from __future__ import annotations

import pytest
from g6.reliability_pipeline import premortem

TASK = ("Make this Python event handler apply each stable event ID at most once, "
        "even when another event arrives between identical retries. Preserve existing tests.")
EXAMPLE = """from stock import receive

def test_update():
    assert receive({"count": 2, "seen": []}, {"id": "delivery-7", "delta": 3}) == {"count": 5, "seen": ["delivery-7"]}
"""


def _construct(task=TASK, **kwargs):
    return premortem.construct_replay_check(task, module="stock.py", function="receive",
                                            test_sources={"tests/test_stock.py": EXAMPLE},
                                            executed_tests=("tests/test_stock.py::test_update",), **kwargs)


@pytest.mark.parametrize("relation", [None, "adjacent-replay.v1"])
def test_wf015_every_construction_says_it_is_a_fixed_template(relation):
    pack = _construct(**({"relation": relation} if relation else {}))
    assert pack["constructor"] == {"kind": "fixed_templates", "templates": ["event-replay-v1", "adjacent-replay.v1"],
                                   "selected_by": premortem.CONSTRUCTOR["selected_by"], "general": False}
    assert pack["limits"][-1] == premortem.TEMPLATE_LIMIT
    assert "not harness intelligence" in premortem.TEMPLATE_LIMIT


@pytest.mark.parametrize("task", [
    "Make the event handler apply every stable event ID only once, even if a different event comes between two "
    "identical retries.",
    "Ensure each event id is applied at most once even when other events arrive between retries.",
])
def test_wf015_a_reworded_requirement_constructs_nothing(task):
    assert _construct(task) is None
