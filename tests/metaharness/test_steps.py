# SPDX-License-Identifier: Apache-2.0
"""Built-in steps for recovery remedies: repair against the task's shown tests, and a second look at an over-used
label (DECISIONS #27)."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import Check
from g6.metaharness import Profile, Suite, SuiteCase, adapt
from g6.metaharness import recovery as R
from g6.metaharness import steps
from g6.metaharness.executor import context_for, model_call_factory

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

TASK = "Write a Python function add(a, b) that returns the sum.\n\nYour code should pass this example test:\n" \
       "assert add(1, 2) == 3"
WRONG = "```python\ndef add(a, b):\n    return a - b\n```"
RIGHT = "```python\ndef add(a, b):\n    return a + b\n```"


@dataclass
class Script:
    """Replies in order; records every call."""
    replies: list[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _ctx(model, step):
    wf, _ = rig.build(stub=model)
    wf = replace(wf, pipeline_steps={step.name: step})
    return context_for(wf, Profile(routing={"answer": "local"}, pipeline=(step.name,)))


def _run(model, step, task=TASK):
    return model_call_factory()(_ctx(model, step))(SimpleNamespace(task=task))


def test_a_failing_answer_is_repaired_from_the_shown_test() -> None:
    model = Script([WRONG, RIGHT])
    assert _run(model, steps.repair_with_tests(rounds=3)) == RIGHT
    assert len(model.calls) == 2 and "It fails the example test" in model.calls[1][1]


def test_a_passing_answer_costs_nothing_more() -> None:
    model = Script([RIGHT])
    assert _run(model, steps.repair_with_tests()) == RIGHT and len(model.calls) == 1


def test_repairs_stop_after_the_rounds_given() -> None:
    model = Script([WRONG])
    _run(model, steps.repair_with_tests(rounds=2))
    assert len(model.calls) == 1 + 2


def test_only_the_shown_tests_run() -> None:
    imports, asserts = steps.shown_tests("import math\nWrite it.\nassert f(1) == 2\nnot a test: assert-like")
    assert imports == ["import math"] and asserts == ["assert f(1) == 2"]
    assert steps.run_shown("def f(x):\n    return x + 1", [], ["assert f(1) == 2"], timeout_s=10) == ""
    assert steps.run_shown("def f(x):\n    return x", [], ["assert f(1) == 2"], timeout_s=10)
    with pytest.raises(ValueError):
        steps.repair_with_tests(rounds=0)


LABELS = ("SUPPORT", "CONTRADICT", "NOT_ENOUGH_INFO")


def test_a_second_look_revises_only_the_over_used_label() -> None:
    step = steps.second_look("SUPPORT", LABELS)
    model = Script(["SUPPORT", "CONTRADICT"])
    assert _run(model, step, "CLAIM: x\nABSTRACT: y") == "CONTRADICT" and len(model.calls) == 2
    other = Script(["NOT_ENOUGH_INFO"])
    assert _run(other, step, "CLAIM: x") == "NOT_ENOUGH_INFO" and len(other.calls) == 1
    vague = Script(["SUPPORT", "SUPPORT or CONTRADICT"])
    assert _run(vague, step, "CLAIM: x") == "SUPPORT"          # a revision naming two labels is not used
    assert steps.labels_named("not_enough_info", LABELS) == ["NOT_ENOUGH_INFO"]


def test_the_steps_pass_the_profile_checks(tmp_path) -> None:
    wf, _ = rig.build()
    wf = replace(wf, pipeline_steps={"repair_with_tests": steps.repair_with_tests(),
                                     "second_look": steps.second_look("SUPPORT", LABELS, name="second_look")})
    for name in wf.pipeline_steps:
        assert Profile(routing={"answer": "local"}, pipeline=(name,)).problems_for(wf) == []


def test_diagnose_names_an_over_used_label_and_the_second_look(tmp_path) -> None:
    dev = tuple(SuiteCase(f"d{i}", f"CLAIM: c{i}", lab) for i, lab in
                enumerate(("CONTRADICT", "NOT_ENOUGH_INFO", "CONTRADICT", "NOT_ENOUGH_INFO", "SUPPORT")))
    held = tuple(SuiteCase(f"h{i}", f"CLAIM: h{i}", "SUPPORT") for i in range(40))
    wf, _ = rig.build(stub=rig.Fixed("SUPPORT"),
                      checks=(Check("names one label", lambda o: o.strip() in LABELS, check_id="label",
                                    known_bad=("", "maybe"), known_good="SUPPORT"),))
    wf = replace(wf, task="Label the claim. {input}", suite=Suite(dev, held, wf.suite.provenance))
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft", mh.envelope
    got = mh.diagnose()["recovery"]
    over = next(c for c in got["causes"] if c["code"] == R.OVERUSED_LABEL)
    assert over["cases"] == 4 and "'SUPPORT'" in over["what"]
    assert got["remedies"][0]["addresses"] == R.OVERUSED_LABEL and "second_look" in got["remedies"][0]["how"]


# --- RecoveryStrategy: the diagnosis as candidates --------------------------------------------------------------------

def _label_workflow(reply: str, steps_declared: dict | None = None):
    from g6.metaharness import RecoveryStrategy  # noqa: F401 - exported

    dev = tuple(SuiteCase(f"d{i}", f"CLAIM: c{i}", lab) for i, lab in
                enumerate(("CONTRADICT", "NOT_ENOUGH_INFO", "CONTRADICT", "NOT_ENOUGH_INFO", "SUPPORT")))
    held = tuple(SuiteCase(f"h{i}", f"CLAIM: h{i}", "SUPPORT") for i in range(40))
    wf, _ = rig.build(stub=rig.Fixed(reply),
                      checks=(Check("names one label", lambda o: o.strip() in LABELS, check_id="label",
                                    known_bad=("", "maybe"), known_good="SUPPORT"),))
    return replace(wf, task="Label the claim. {input}", suite=Suite(dev, held, wf.suite.provenance),
                   pipeline_steps=steps_declared or {})


def test_recovery_proposes_the_second_look_it_diagnosed_and_examples(tmp_path) -> None:
    from g6.metaharness import RecoveryStrategy

    wf = _label_workflow("SUPPORT", {"second_look_support": steps.second_look("SUPPORT", LABELS)})
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    got = mh.propose(RecoveryStrategy(wf))
    made = [p.candidate for p in got["proposals"]]
    assert any(c.pipeline == ("second_look_support",) for c in made)
    assert any(c.exemplars == 8 for c in made)
    assert all(p.proposer == "auto:recovery" for p in got["proposals"])


def test_recovery_picks_the_second_look_named_for_the_label_not_one_ending_like_it(tmp_path) -> None:
    from g6.metaharness import RecoveryStrategy

    labels = (*LABELS, "WEAK_SUPPORT")                   # banking77 has such pairs: exchange_rate and
    looks = {"second_look_weak_support": steps.second_look("WEAK_SUPPORT", labels),   # card_payment_wrong_exchange_rate
             "second_look_support": steps.second_look("SUPPORT", labels)}
    wf = _label_workflow("SUPPORT", looks)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    made = [p.candidate.pipeline for p in mh.propose(RecoveryStrategy(wf))["proposals"]]
    assert ("second_look_support",) in made and ("second_look_weak_support",) not in made


def test_recovery_never_invents_a_step(tmp_path) -> None:
    from g6.metaharness import RecoveryStrategy

    wf = _label_workflow("SUPPORT")                      # over-used label, but no second_look step declared
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    made = [p.candidate for p in mh.propose(RecoveryStrategy(wf))["proposals"]]
    assert made and all(not c.pipeline for c in made)


def test_recovery_proposes_the_working_prompt_for_numbers_and_nothing_when_all_pass(tmp_path) -> None:
    from g6.metaharness import RecoveryStrategy
    from g6.metaharness.propose import WORKING_PROMPT

    wf, _ = rig.build(base_acc=0.3)
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    assert [p.candidate.prompt for p in mh.propose(RecoveryStrategy(wf))["proposals"]] == [WORKING_PROMPT]
    perfect, _ = rig.build(base_acc=1.0)
    mh2 = adapt(perfect, workspace=tmp_path / "ws2", clock=rig.Clock())
    mh2.confirm("Ada")
    assert mh2.propose(RecoveryStrategy(perfect))["warning_card"]["code"] == "G6_E_META_NO_NEW_CANDIDATE"


def test_recovery_proposes_a_declared_repair_step_for_code(tmp_path) -> None:
    from g6.metaharness import RecoveryStrategy

    code_case = "Write add(a, b).\nassert add(1, 2) == 3"
    dev = tuple(SuiteCase(f"d{i}", code_case, {"tests": ["assert add(1, 2) == 3"]}) for i in range(4))
    held = tuple(SuiteCase(f"h{i}", f"{code_case}\n# {i}", {"tests": ["assert add(2, 2) == 4"]}) for i in range(40))
    wf, _ = rig.build(stub=Script([WRONG]), oracle_fn=lambda out, exp: False,
                      checks=(Check("defines a function", lambda o: "def " in o, check_id="def",
                                    known_bad=("",), known_good="def f(): pass"),))
    wf = replace(wf, task="{input}", suite=Suite(dev, held, wf.suite.provenance),
                 pipeline_steps={"repair_with_tests": steps.repair_with_tests(rounds=1)})
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    made = [p.candidate for p in mh.propose(RecoveryStrategy(wf))["proposals"]]
    assert [c.pipeline for c in made] == [("repair_with_tests",)]
