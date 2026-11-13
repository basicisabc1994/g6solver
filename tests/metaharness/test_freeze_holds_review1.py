# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/freeze-holds: its five findings as the reviewer reproduced them, with the reviewer's support code
(the review's own temporary folder aside: pytest's serves here). FakeLLM is the only model anywhere here."""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from g6.core.llm import FakeLLM
from g6.metaharness import shadow
from g6.metaharness.domain import Judge
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.self_maintenance.pacing import ProviderWindow

_path = Path(__file__).with_name("_loop_domains.py")
_spec = importlib.util.spec_from_file_location("review_domains", _path)
domains = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = domains
_spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
SETTINGS = LoopSettings(
    windows=(ProviderWindow("claude", 1_000_000, START),), audit_per_tick=0,
    method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12,
                  reflect_calls=1, use_recovery=False, use_reflection=False),
)
FROZEN = "G6_E_LOOP_FROZEN_CHANGED"


def enrolled(tmp_path, *, domain=None, proposer=None, domain_file=None):
    domain = domain or domains.WordProblems(tmp_path)
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS,
                      proposer=proposer or domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock(), domain_file=domain_file)
    reply = loop.enrol("Review owner")
    assert reply["completion_state"] == "qualified-draft", reply
    return loop, domain


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Finding 1 --------------------------------------------------------------------------------------------------------------

def test_public_adopt_cannot_skip_the_frozen_check(tmp_path):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    loop, _ = enrolled(tmp_path, domain_file=declaration)
    # Produce a genuine recommendation using the same harness, gate and fake model.
    loop.requests.open(10_000)
    try:
        profile = loop.mh._head()[1].model_dump(mode="json")
        profile["prompt"] = domains.WORKING
        proposal = loop.mh.candidate(profile, proposer="auto:review")["proposal"]
        report = loop.mh.evaluate(proposal)["report"]
        decision = loop.mh.decide(report, proposal)["decision"]
    finally:
        loop.requests.close()
    assert decision.verdict == "recommend"
    assert loop._frozen_seen is None
    declaration.write_text("# changed after recommendation\n", encoding="utf-8")
    assert "domain_file" in loop._changed()
    reply = loop.mh.adopt(decision)
    assert not loop.mh.adoption_rows(), "public adopt wrote a generation without any frozen reading or last check"
    assert reply["warning_card"]["code"] == FROZEN


# Finding 2 --------------------------------------------------------------------------------------------------------------

def test_judge_replacement_in_the_same_file_is_frozen(tmp_path):
    folder = tmp_path / "judges"
    folder.mkdir()
    path = folder / "judge.py"
    path.write_text(
        "from review_domains import GSM\n"
        "def original(output, expected):\n"
        "    return GSM.oracle(output, expected)\n"
        "def replacement(output, expected):\n"
        '    return bool(output.strip()) and output != "this is not an answer"\n',
        encoding="utf-8",
    )
    module = load_module(path, "review_judge_swap")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.original, answers_by="independent")

    def propose(system, user):
        domain.judge = lambda: Judge(module.replacement, answers_by="independent")
        return json.dumps({"prompt": domains.WORKING})

    loop, _ = enrolled(tmp_path, domain=domain, proposer=FakeLLM(propose))
    reply = loop.tick()
    assert domain.judge().verdict is module.replacement
    assert not module.original("999999", "1") and module.replacement("999999", "1")
    assert not loop.mh.adoption_rows(), "adopted after replacing the enrolled judge with a different function in the same file"
    assert reply["warning_card"]["code"] == FROZEN


# Finding 3 --------------------------------------------------------------------------------------------------------------

def test_change_is_recorded_even_when_no_candidate_wins(tmp_path):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    calls = []

    def propose(system, user):
        calls.append(1)
        declaration.write_text("# changed during development\n", encoding="utf-8")
        return json.dumps({"prompt": "No useful improvement"})

    loop, _ = enrolled(tmp_path, domain_file=declaration, proposer=FakeLLM(propose))
    reply = loop.tick()
    assert calls and "domain_file" in loop._changed()
    assert not loop.mh.adoption_rows()
    assert loop.log.cycles()[-1]["code"] == FROZEN, "cycle silently recorded a frozen change as an ordinary non-winning cycle"
    assert reply["warning_card"]["code"] == FROZEN


# Finding 4 --------------------------------------------------------------------------------------------------------------

def judge_keyed_answer(output, expected):
    # Python keys are significant to this valid independent judge.
    return 1 in expected and domains.GSM.oracle(output, expected[1])


def test_distinct_python_answers_with_identical_json_cannot_share_a_freeze(tmp_path, monkeypatch):
    """Changed by freeze repair 3 on purpose: a dict with a key that is not a str is outside the admitted answers, so it
    never reaches a judge. The reviewer's form enrolled one and changed its key's type before the adoption; enrolment now
    refuses it, by case id, and a str-keyed answer the judge is given is a copy no one else holds."""
    domain = domains.WordProblems(tmp_path)
    domain.pool = [replace(case, expected={1: case.expected}) for case in domain.pool]
    domain.judge = lambda: Judge(judge_keyed_answer, answers_by="independent")
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    reply = loop.enrol("Review owner")
    assert reply["warning_card"]["code"] == "G6_E_LOOP_ANSWER_UNIDENTIFIABLE" and loop.log.enrolled() is None


# Finding 5 --------------------------------------------------------------------------------------------------------------

def test_imported_judging_code_is_covered_by_the_freeze(tmp_path, monkeypatch):
    folder = tmp_path / "judges"
    folder.mkdir()
    helper_path = folder / "rules.py"
    helper_path.write_text(
        "from review_domains import GSM\n"
        "def verdict(output, expected):\n"
        "    return GSM.oracle(output, expected)\n", encoding="utf-8",
    )
    helper = load_module(helper_path, "review_imported_rules")
    judge_path = folder / "judge.py"
    judge_path.write_text(
        "from review_imported_rules import verdict\n"
        "def judge(output, expected):\n"
        "    return verdict(output, expected)\n", encoding="utf-8",
    )
    judge_module = load_module(judge_path, "review_importing_judge")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(judge_module.judge, answers_by="independent")
    loop, _ = enrolled(tmp_path, domain=domain)
    original = shadow._measured
    changed = []

    def make_row(report):
        helper_path.write_text(
            "def verdict(output, expected):\n"
            "    return False\n", encoding="utf-8",
        )
        changed.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", make_row)
    reply = loop.tick()
    assert changed
    fresh = load_module(helper_path, "review_imported_rules")
    assert helper.verdict("Answer: 1", "1") and not fresh.verdict("Answer: 1", "1")
    monkeypatch.undo()
    fresh_judge = load_module(judge_path, "review_importing_judge_after_edit")
    domain.judge = lambda: Judge(fresh_judge.judge, answers_by="independent")
    restarted = ShadowLoop(domain, loop.root, SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                           now=lambda: NOW, clock=domains.Clock())
    after_restart = restarted.tick()
    assert not loop.mh.adoption_rows(), "adopted after an imported part of the judge changed on disk before the last check"
    assert reply["warning_card"]["code"] == FROZEN
    assert after_restart["warning_card"]["code"] == FROZEN
