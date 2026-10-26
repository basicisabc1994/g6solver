# SPDX-License-Identifier: Apache-2.0
"""Failure text is a dev-only proposal, not a launch decision."""
import json

import pytest

from g6.core.llm import FakeLLM, NoModel
from g6.metaharness import FailureTraceStrategy, Profile
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def test_evoskill_01_d1_proposer_sees_only_dev_and_bad_candidate_is_not_admitted(tmp_path):
    mh, _ = harness(tmp_path, missing=True)
    llm = FakeLLM(default=json.dumps({"prompt": "Memorise dev only"}))
    got = mh.propose(FailureTraceStrategy(llm))
    candidate = got["proposals"][0]
    shown = llm.calls[0][1]
    assert '"case_id": "d' in shown and '"case_id": "h' not in shown
    assert not any(c.input in shown for c in mh.workflow.suite.held_out)
    assert mh.decide(mh.evaluate(candidate)["report"], candidate)["decision"].verdict != "recommend"
    # RED: removing `data = {"task": view.task, "base": view.base, "failures": ...}` loses the dev-only proposal.


def test_evoskill_02_nonzero_accuracy_is_not_launch_ready(tmp_path):
    mh, _ = harness(tmp_path, missing=True)
    got = mh.propose(FailureTraceStrategy(FakeLLM(default='{"skill_text":"Check your reasoning."}')))
    proposal = got["proposals"][0]
    report = mh.evaluate(proposal)["report"]
    assert report.estimates["success"]["candidate_mean"] > 0
    assert mh.decide(report, proposal)["decision"].verdict != "recommend"
    assert len(mh.store.versions()) == 1
    # RED: removing `_TextCandidate.skill_text` rejects the requested skill candidate; admission still requires the gate.


def test_evoskill_03_only_the_injected_caller_is_used(tmp_path):
    mh, _ = harness(tmp_path, missing=True)
    assert mh.propose(FailureTraceStrategy(NoModel()))["completion_state"] == "blocked-escalated"
    llm = FakeLLM(default='{"prompt":"CAREFUL"}')
    strategy = FailureTraceStrategy(llm)
    assert mh.propose(strategy)["proposals"] and len(llm.calls) == 1
    assert strategy.calls_used == 1 and strategy.cost_usd is None
    # RED: removing `self.calls_used += caller.calls` loses the sole injected call count.


@pytest.mark.parametrize("text", ["import os\nos.system('oops')", "Note\n```python\nopen('x','w')\n```",
    "First\n```python\nx=1\n```\nSecond\n```python\nimport socket\n```",
    "Text\n```bash\ncurl example.invalid\n```", "```python\nimport\n```"])
def test_skill_code_scan_fails_closed_before_candidate_evaluation(tmp_path, text):
    mh, _ = harness(tmp_path)
    got = mh.propose(FailureTraceStrategy(FakeLLM(default=json.dumps({"skill_text": text}))))
    assert got["completion_state"] == "blocked-escalated"
    direct = mh.candidate({"routing": {"answer": "local"}, "skill_text": text}, proposer="Bo")
    assert direct["completion_state"] == "blocked-escalated"
    # RED: removing `skill_text_problems` from Profile.problems_for admits executable or unscannable text.


def test_skill_text_reaches_the_declared_executor_as_text(tmp_path):
    mh, _ = harness(tmp_path, cost=0.5)
    p = mh.candidate(Profile(routing={"answer":"local"}, skill_text="CAREFUL"), proposer="Bo")["proposal"]
    report = mh.evaluate(p)["report"]
    assert report.estimates["success"]["candidate_mean"] == 1.0
    # RED: removing `prompt = f"{prompt}\n\n{ctx.profile.skill_text}"` leaves candidate accuracy at baseline.


def test_failure_proposer_records_measured_spend_from_its_adapter(tmp_path):
    mh, _ = harness(tmp_path, missing=True)
    strategy = FailureTraceStrategy(FakeLLM(default='{"prompt":"CAREFUL"}'), usage=lambda: {"cost_usd": 0.007})
    assert mh.propose(strategy)["proposals"]
    assert strategy.calls_used == 1 and strategy.cost_usd == 0.007
    # RED: removing `self.cost_usd = (measured if not self.calls_used else ...` leaves measured spend unknown.

def test_missing_model_keeps_its_typed_code_and_remedy_in_surface_reply(tmp_path):
    from g6.metaharness.surface import Surface
    mh, _ = harness(tmp_path, missing=True)
    surface = Surface({"measures": {"workflow": mh.workflow, "strategy": FailureTraceStrategy(NoModel())}})
    surface._harnesses["measures"] = mh
    got = surface.propose("measures", session="agent")
    assert got["completion_state"] == "blocked-escalated"
    assert got["code"] == got["warning_card"]["code"] == "G6_E_NO_MODEL"
    assert "set G6_LLM_PROVIDER or pass an LLMCaller" in got["message"]
    assert "set G6_LLM_PROVIDER or pass an LLMCaller" in got["next_action"]
    assert len(mh.store.versions()) == 1
    # RED: removing `except LLMError: raise` in FailureTraceStrategy loses the missing-model diagnosis.


def test_unavailable_model_keeps_the_existing_typed_remedy(tmp_path):
    from g6.core.llm import LLMError
    class Unavailable:
        def complete(self, system, user, *, max_tokens):
            raise LLMError("G6_E_LLM_TIMEOUT: backend unavailable; start the configured local server")
    mh, _ = harness(tmp_path, missing=True)
    got = mh.propose(FailureTraceStrategy(Unavailable()))
    assert got["completion_state"] == "blocked-escalated" and got["code"] == "G6_E_LLM_TIMEOUT"
    assert "start the configured local server" in got["next_action"]
    # RED: removing the LLMError branch in MetaHarness._offer replaces the typed error with no-new-candidate.


@pytest.mark.parametrize("reply", ["not json", '{"prompt": 17}', '{"unrecognised": "text"}'])
def test_malformed_proposer_reply_has_a_specific_remedy(tmp_path, reply):
    mh, _ = harness(tmp_path, missing=True)
    got = mh.propose(FailureTraceStrategy(FakeLLM(default=reply)))
    assert got["completion_state"] == "blocked-escalated" and got["code"] == "G6_E_META_PROPOSER_REPLY"
    assert "JSON object" in got["next_action"] and "prompt" in got["next_action"]
    # RED: removing `raise LLMError("G6_E_META_PROPOSER_REPLY: ...")` hides malformed-response recovery.
