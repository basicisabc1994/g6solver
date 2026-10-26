# SPDX-License-Identifier: Apache-2.0
"""The frontier programme's six meta-train workflows, on synthetic fixtures in each dataset's own file format."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from frontier import workflows
from g6.metaharness import Profile
from g6.metaharness.executor import model_call_factory
from g6.metaharness.propose import DevBudget, build_view

FIX = Path(__file__).with_name("fixtures") / "frontier_data"


class Stub:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return self.reply


def _cases(key, *, distinct=False):
    spec = workflows.SPECS[key]
    return spec.cases(FIX, workflows.distinct_pool(spec, FIX) if distinct else spec.pool(FIX))


def test_distinct_pool_keeps_one_id_per_input():
    # the fixture's ARC rows repeat a question (and options) across splits, as real pools do (MASSIVE 4, SciFact 3)
    spec = workflows.SPECS["arc"]
    kept = workflows.distinct_pool(spec, FIX)
    assert len(kept) == 3 < len(spec.pool(FIX)) and kept == sorted(kept)
    assert len({c.input for c in spec.cases(FIX, kept)}) == len(kept)


def _splits(cases):
    return {"dev": cases[:1], "selection": cases[1:2], "confirmation": cases[2:]}


def test_pools_and_cases_parse_each_file_format():
    counts = {key: len(spec.pool(FIX)) for key, spec in workflows.SPECS.items()}
    # SciFact: train and dev claims that cite an abstract; MBPP: task ids 11-974 only
    assert counts == {"gsm8k": 7, "massive": 6, "scifact": 6, "mbpp": 3, "arc": 7, "banking77": 6}
    for key in workflows.SPECS:
        cases = _cases(key)
        assert len({c.case_id for c in cases}) == len(cases) == counts[key], key
    assert {c.case_id.rsplit("-", 1)[0] for c in _cases("scifact")} == {"scifact-train", "scifact-dev"}


def test_arc_relabels_numeric_options_and_reads_exactly_one_letter():
    numeric = next(c for c in _cases("arc") if c.case_id == "arc-Synthetic_test_2")
    assert "A. first" in numeric.input and "D. fourth" in numeric.input and numeric.expected == "D"
    read = workflows.arc_choice
    assert (read("Answer: C"), read("The answer is (B)."), read("B. second"), read("D")) == ("C", "B", "B", "D")
    assert read("A or B") is None and read("") is None and read("a dog, then a cat") is None


def test_banking77_labels_are_its_category_file():
    assert workflows.BANKING77_LABELS == tuple(json.loads((FIX / "banking77" / "categories.json").read_text("utf-8")))
    case = _cases("banking77")[0]
    assert case.expected in workflows.BANKING77_LABELS and "Refund_not_showing_up" in case.input


def test_every_oracle_passes_its_references_and_fails_empty():
    for key, spec in workflows.SPECS.items():
        for case in _cases(key)[:5]:
            assert spec.oracle(spec.reference(case), case.expected), (key, case.case_id)
            assert not spec.oracle("", case.expected), (key, case.case_id)


def test_every_workflow_is_valid():
    for key, spec in workflows.SPECS.items():
        cases = _cases(key, distinct=True)
        examples = spec.examples(FIX, cases) if spec.examples else ()
        wf = workflows.build(spec, _splits(cases), Stub("x"), examples=examples)
        assert wf.problems() == [], (key, wf.problems())
        assert wf.policy.looks == 4 and wf.policy.max_evaluations == 1 and wf.suite.selection
        assert wf.models["local"].max_calls_per_run == 3 and wf.accept_risky == workflows.APPROVER
        assert all(not c.meta for c in (*wf.suite.dev, *wf.suite.held_out, *wf.suite.selection)), key
    assert workflows.SPECS["massive"].examples and workflows.SPECS["banking77"].examples


def test_feedback_reaches_dev_cases_only():
    spec = workflows.SPECS["gsm8k"]
    cases = _cases("gsm8k")
    seen: list[str] = []

    def recording(output, case):
        seen.append(case.case_id)
        return spec.feedback(output, case)

    wf = replace(workflows.build(spec, _splits(cases), Stub("Answer: 0")), feedback=recording)
    view = build_view(wf, model_call_factory(), Profile(routing={"answer": "local"}), theories=None,
                      clock=time.monotonic, budget=DevBudget(100))
    assert seen and set(seen) <= {c.case_id for c in wf.suite.dev}
    assert "expected answer is 7" in view.failures[0].feedback


def test_mbpp_feedback_names_the_first_failing_test():
    case = _cases("mbpp")[0]                             # task 11 (task 5 is a few-shot prompt, outside the pool)
    wrong = "```python\ndef double(x):\n    return x\n```"
    assert "assert double(2) == 4" in workflows.SPECS["mbpp"].feedback(wrong, case)
    assert "no Python function" in workflows.SPECS["mbpp"].feedback("I don't know", case)


@pytest.mark.parametrize("key", list(workflows.SPECS))
def test_the_edit_space_holds_only_declared_options(key):
    spec = workflows.SPECS[key]
    space = spec.space.space
    assert set(space) == {"prompt", "pipeline", "extra_checks", "exemplars", "retries"}
    assert all(set(p) <= set(spec.pipeline_steps) for p in space["pipeline"])
    assert all(set(c) <= set(spec.optional_checks) for c in space["extra_checks"])
    assert space["exemplars"] == [0, 8] and space["retries"] == [0, 1]
    assert len(space["pipeline"]) <= 8                   # banking77's 77 second looks are left to recovery


@pytest.mark.parametrize("key", ["gsm8k", "massive", "scifact", "mbpp"])
def test_every_component_on_is_v6bs_arm_g(key):
    from v6b_metaharness import workflows as v6b

    assert workflows.SPECS[key].everything() == v6b.SPECS[key].everything()
    every = Profile.model_validate({"routing": {"answer": "local"}, **workflows.SPECS[key].everything()})
    assert every.exemplars == 8 and list(every.pipeline) == list(workflows.SPECS[key].pipeline_steps)


def test_a_workflow_takes_its_call_budget():
    spec = workflows.SPECS["gsm8k"]
    wf = workflows.build(spec, _splits(_cases("gsm8k", distinct=True)), Stub("x"), calls=8, mean_calls=4.0)
    assert wf.models["local"].max_calls_per_run == 8
    assert next(o.bound for o in wf.objectives if o.name == "model_calls") == 4.0
