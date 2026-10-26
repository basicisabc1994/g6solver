# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-008 (the coding learner), on the store and the decision rules alone.

The archive's learner (``coding_learning.py``) labelled each validation variant with the probe's own replay, so its
trial could not tell a probe that is right from one that always fires; it drained candidates on a daemon thread after
every reply; and an approved probe could only lower a later result (kept). Each ``test_null_wf008_*`` puts the archive's
behaviour back in memory (monkeypatching only) and shows the regression test fails on it. The replay here is a fake that
calls the variant's function in this process (the variants are this file's own text); the mission-level test with real
replays is ``test_wf008_learned_probe_mission.py``.
"""
from __future__ import annotations

import threading
import time

import pytest
from g6.learning import PromotionPolicy, TheoryStore
from g6.workforce import coding_learning as cl
from g6.workforce.api import NotConfirmed

KEY = {"repository_identity": "r" * 64, "module": "policy.py", "function": "accepts"}
SIGNATURE = {"version": 1, "task_kind": "code_change", **KEY, "sha256": "s" * 64}
CAUGHT = {"module": "policy.py", "function": "accepts", "input": [3], "baseline_output": True, "candidate_output": False}
TEMPLATE = 'def accepts(value):\n    return {body}\n\n\ndef label():\n    return "ready"\n'
TRAINING = [cl.files_id({"policy.py": TEMPLATE.format(body="value > 3")}),
            cl.files_id({"policy.py": TEMPLATE.format(body="value >= 3")})]
#: Three regressions the probe at accepts(3) catches, two rewrites that keep the behaviour.
BODIES = ("value >= 4", "value >= 5", "value >= 3 and value != 3", "not value < 3", "3 <= value")
#: Small enough for five variants; the default policy (10 pairs, 5 discordant, alpha 0.05) is the library's.
LENIENT = PromotionPolicy(min_pairs=5, min_discordant=3, alpha=0.2)


def _run(text: str) -> dict:
    found: dict = {}
    exec(compile(text, "policy.py", "exec"), found)  # noqa: S102 - this file's own fixture text
    return found


def fake_replay(probe, files):
    """The probe's function at its input, called here; a run id the replay names, as the bench's would."""
    value = _run(files[probe["module"]])[probe["function"]](probe["input"])
    return {"executed": True, "value": value, "execution_id": "replay-" + cl.files_id(files)[6:22], "reason": ""}


def _hidden_evaluator(text: str) -> bool:
    """The requirement, checked by a run the probe did not produce: accepts(v) is v >= 3 for v in -5..10."""
    accepts = _run(text)["accepts"]
    return all(accepts(v) is (v >= 3) for v in range(-5, 11))


def independent(bodies=BODIES) -> list[cl.LabelledVariant]:
    return [cl.LabelledVariant({"policy.py": TEMPLATE.format(body=body)},
                               regression=not _hidden_evaluator(TEMPLATE.format(body=body)),
                               label_source={"kind": "hidden_evaluator", "run_id": f"hidden-{index}"})
            for index, body in enumerate(bodies)]


def self_labelled(kind: str = "probe_replay", bodies=BODIES) -> list[cl.LabelledVariant]:
    """The archive's labels: a variant is a regression when the probe's own replay says its answer differs."""
    out = []
    for body in bodies:
        files = {"policy.py": TEMPLATE.format(body=body)}
        replayed = fake_replay({**KEY, "input": 3, "expected": True}, files)
        out.append(cl.LabelledVariant(files, regression=replayed["value"] is not True,
                                      label_source={"kind": kind, "run_id": replayed["execution_id"]}))
    return out


@pytest.fixture
def probes(tmp_path):
    # the store's owner sets the bar its activate judges by (review B1-5); these tests use small samples
    with TheoryStore(tmp_path / "probes.db", scope="workforce.coding_probe:test", promotion_policy=LENIENT) as found:
        yield found


def _candidate(probes):
    theory = cl.propose(probes, KEY, CAUGHT, sources=TRAINING)
    assert theory is not None and theory.status == "candidate"
    return theory


# --- validation needs labels from outside the probe -------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["probe_replay", "hidden_evaluator"])
def test_wf008_a_self_labelled_trial_never_promotes(probes, kind):
    """Archive: ``LABELLED_BY`` "the probe's own replay", ``k_new`` 0 by construction, then ``promote``. Here a label
    named as the probe's (or carrying the run id of the probe's own replay under another name) refuses the trial."""
    theory = _candidate(probes)
    decision = cl.validate(probes, theory.theory_id, self_labelled(kind), fake_replay, policy=LENIENT)
    assert decision.recommendation == "refuse" and decision.code == cl.SELF_LABELLED, decision
    assert probes.get(theory.theory_id).status == "candidate"
    with pytest.raises(ValueError, match="NOT_RECOMMENDED"):
        cl.approve(probes, decision, lambda lines: "reviewer")
    assert cl.select(probes, SIGNATURE) == []


def test_null_wf008_self_labels(probes, monkeypatch):
    monkeypatch.setattr(cl, "_label_problem", lambda source, produced: "")  # the archive refused no label
    with pytest.raises(AssertionError):
        test_wf008_a_self_labelled_trial_never_promotes(probes, "probe_replay")


def test_wf008_independent_labels_give_a_recommendation_a_person_must_accept(probes):
    """Survival control: three held-out regressions caught, two rewrites left alone, labels from a hidden evaluator. The
    trial recommends; the probe is not used until a person accepts, and a refusal leaves it a candidate."""
    theory = _candidate(probes)
    decision = cl.validate(probes, theory.theory_id, independent(), fake_replay, policy=LENIENT)
    assert (decision.recommendation, decision.wins, decision.losses, decision.pairs) == ("promote", 3, 0, 5)
    assert decision.completion_state == "qualified-draft"
    assert cl.select(probes, SIGNATURE) == []
    with pytest.raises(NotConfirmed):
        cl.approve(probes, decision, lambda lines: None)
    assert probes.get(theory.theory_id).status == "candidate"
    shown = []
    active = cl.approve(probes, decision, lambda lines: shown.extend(lines) or "reviewer")
    assert active.status == "active" and active.activated_by == "approver:reviewer"
    assert "accepts" in shown[0] and "3 change(s) it caught" in shown[1]
    assert [p["probe"] for p in cl.select(probes, SIGNATURE)] == [
        {"module": "policy.py", "function": "accepts", "input": 3, "expected": True}]


@pytest.mark.parametrize("who", ["auto:bot", " AUTO:ci ", "auto:"])
def test_an_automated_approver_name_is_refused_before_it_is_recorded(probes, who):
    """``auto:`` names an automated approver, never a person: refused before the ``approver:`` prefix could hide it."""
    theory = _candidate(probes)
    decision = cl.validate(probes, theory.theory_id, independent(), fake_replay, policy=LENIENT)
    with pytest.raises(NotConfirmed, match="auto:"):
        cl.approve(probes, decision, lambda lines: who)
    assert probes.get(theory.theory_id).status == "candidate" and cl.select(probes, SIGNATURE) == []


def test_wf008_the_default_policy_wants_more_evidence(probes):
    """Five labelled variants are too few under the library's default policy: no recommendation."""
    theory = _candidate(probes)
    decision = cl.validate(probes, theory.theory_id, independent(), fake_replay)
    assert decision.recommendation == "refuse" and decision.code == "G6_E_LEARNING_EVIDENCE_INSUFFICIENT"


def test_wf008_a_training_change_is_not_held_out(probes):
    """The caught change, with only a comment added, is the same instance as its training pair: never held out."""
    theory = _candidate(probes)
    variants = [*independent(), cl.LabelledVariant(
        {"policy.py": TEMPLATE.format(body="value > 3  # the caught change again")}, regression=True,
        label_source={"kind": "hidden_evaluator", "run_id": "hidden-again"})]
    decision = cl.validate(probes, theory.theory_id, variants, fake_replay, policy=LENIENT)
    assert decision.recommendation == "refuse" and decision.code == "G6_E_LEARNING_NOT_HELD_OUT"


def test_wf008_an_unapproved_or_retired_probe_is_never_selected(probes):
    """Audit T-10: a candidate is not selected; a retired probe is not selected; a probe on another function is not."""
    theory = _candidate(probes)
    assert cl.select(probes, SIGNATURE) == []
    cl.approve(probes, cl.validate(probes, theory.theory_id, independent(), fake_replay, policy=LENIENT),
               lambda lines: "reviewer")
    assert len(cl.select(probes, SIGNATURE)) == 1
    assert cl.select(probes, {**SIGNATURE, "function": "label"}) == []
    assert cl.select(probes, {**SIGNATURE, "repository_identity": "o" * 64}) == []
    probes.retire(theory.theory_id)
    assert cl.select(probes, SIGNATURE) == []
    assert cl.propose(probes, KEY, CAUGHT, sources=TRAINING).status == "retired"  # proposing again revives nothing


def test_a_counterexample_that_is_not_one_changed_input_proposes_nothing(probes):
    assert cl.propose(probes, KEY, {**CAUGHT, "input": [3, 4]}) is None
    assert cl.propose(probes, KEY, {**CAUGHT, "candidate_output": True}) is None
    assert probes.theories() == []


# --- a probe only ever lowers a later result ------------------------------------------------------------------------------

STATES = ("verified", "qualified-draft", "blocked-escalated")


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("statuses", [(), ("agrees",), ("disagrees",), ("not_run",), ("agrees", "disagrees")])
def test_wf008_demote_only_lowers(state, statuses):
    found = cl.demote(state, [{"status": s} for s in statuses])
    assert STATES.index(found) >= STATES.index(state)  # never a higher state
    lowers = any(s in ("disagrees", "not_run") for s in statuses)
    assert found == ("qualified-draft" if lowers and state == "verified" else state)


def test_an_agreeing_probe_changes_nothing():
    """Survival control: agreement adds no support, whatever the state."""
    for state in STATES:
        assert cl.demote(state, [{"status": "agrees"}]) == state


def _raising_demote(state, findings):
    """A learner that treats agreement as support: the lift WF-008 must never allow."""
    if findings and all(f.get("status") == "agrees" for f in findings):
        return "verified"
    return "qualified-draft" if state == "verified" else state


def test_null_wf008_demote(monkeypatch):
    monkeypatch.setattr(cl, "demote", _raising_demote)
    with pytest.raises(AssertionError):
        test_an_agreeing_probe_changes_nothing()


# --- no background thread -------------------------------------------------------------------------------------------------

def test_wf008_no_background_thread(probes):
    """Archive: ``drain_in_background`` started a daemon thread after every reply, running repository code for up to
    600 s. Here each call returns when its work is done, and none leaves a thread behind."""
    before = set(threading.enumerate())
    theory = _candidate(probes)
    decision = cl.validate(probes, theory.theory_id, independent(), fake_replay, policy=LENIENT)
    cl.approve(probes, decision, lambda lines: "reviewer")
    assert cl.select(probes, SIGNATURE)
    assert [t for t in threading.enumerate() if t not in before] == []
    assert not any(hasattr(cl, name) for name in ("drain", "drain_in_background", "wait_for_background"))


def test_null_wf008_thread(probes, monkeypatch):
    real = cl.validate

    def draining(*args, **kwargs):  # the archive's reply path: answer, then start the drain on a daemon thread
        found = real(*args, **kwargs)
        threading.Thread(target=time.sleep, args=(0.5,), name="g6-learning-drain", daemon=True).start()
        return found

    monkeypatch.setattr(cl, "validate", draining)
    with pytest.raises(AssertionError):
        test_wf008_no_background_thread(probes)
