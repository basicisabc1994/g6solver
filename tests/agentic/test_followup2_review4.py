# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""Review 4 of kernel follow-up 2: every budget the kernel accepts is recorded, and the developer pages name the edge
the pins follow least (a responder's module globals)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6._agentic import kernel, ledger
from g6.core import llm
from g6.reasoning.budget import Budget
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child
from tests.agentic.test_followup2 import entries, offline  # noqa: F401

GRID = '{"value":[0.5]}'
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("calls", [3, 2**63, 2**64, 10**20])
def test_every_budget_the_kernel_accepts_is_recorded(monkeypatch, entries, calls):
    in_process_child(monkeypatch)
    budget = Budget(max_model_calls=calls, max_output_chars=20_000, max_wall_clock_s=120.0)
    reply = kernel.run("cegis", "synthesize", copy.deepcopy(FLOAT_PARAMS), allow=ALLOW,
                       llm=llm.FakeLLM(default=GRID), budget=budget)
    record = reply["agentic"]
    assert record["budget"]["max_model_calls"] == calls
    assert record["outcome"] == "substituted" and record["ledger"].get("entry"), record["ledger"]
    assert len(entries) == 1 and entries[0]["budget"]["max_model_calls"] == calls


def test_an_entry_holding_more_sends_than_its_budget_is_still_refused(monkeypatch, entries):
    in_process_child(monkeypatch)
    reply = kernel.run("cegis", "synthesize", copy.deepcopy(FLOAT_PARAMS), allow=ALLOW, llm=llm.FakeLLM(default=GRID),
                       budget=Budget(max_model_calls=1, max_output_chars=20_000, max_wall_clock_s=120.0))
    assert reply["agentic"]["outcome"] == "substituted" and len(entries[0]["authority_sends"]) == 1
    doubled = copy.deepcopy(dict(entries[0]))
    doubled["authority_sends"] = doubled["authority_sends"] * 2
    with pytest.raises(ValidationError):
        ledger.Entry.model_validate(doubled)


@pytest.mark.parametrize("page", ["site/docs/developers.md", "web/content/developers.md"])
def test_the_developer_pages_name_a_responders_globals_among_unfollowed_edges(page):
    text = " ".join((ROOT / page).read_text(encoding="utf-8").split())
    assert "module globals" in text
