# SPDX-License-Identifier: Apache-2.0
"""Audit 010 WF-008 (and T-09) through the library's real mission path, with real replays in the bench's scratch copies.

Mission 1 (scope ``label``) has its differential check catch a change to ``accepts`` and is then repaired; ``learn``
proposes the probe accepts(3) -> True; ``validate`` replays it on five variants a hidden evaluator labelled; a person
accepts it. The repository then moves on (a new requirement for ``accepts``), and two later missions change ``accepts``:
mission 2 so that accepts(3) is False while every frozen test passes, mission 3 keeping accepts(3) True. The approved
probe holds mission 2 at qualified-draft (never blocked-escalated, never verified) and asks a person one question;
mission 3 is unchanged (survival control); a probe that could not run is unmeasured. Mission 4 repeats mission 2 under
the owner's ``founder-d2`` override, which closes it ``verified``: the probe lowers that to qualified-draft. A person's
approval retires the probe. Nothing here leaves a thread behind.
The archive's behaviours are pinned in ``test_wf008_coding_learning.py``.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from g6.invariants import check_envelope
from g6.learning import PromotionPolicy
from g6.workforce import api, coding, service
from g6.workforce import coding_learning as cl

TEMPLATE = 'def accepts(value):\n    return {body}\n\n\ndef label():\n    return "{label}"\n'
V1 = {"policy.py": TEMPLATE.format(body="value >= 3", label="wrong"),
      "tests/test_policy.py": ("from policy import accepts\n\ndef test_policy():\n"
                               "    assert accepts(4) is True\n    assert accepts(1) is False\n"),
      "tests/test_objective.py": 'from policy import label\n\ndef test_label():\n    assert label() == "ready"\n'}
CAUGHT = TEMPLATE.format(body="value > 3", label="ready")  # passes every frozen test, changes accepts(3)
REPAIRED = TEMPLATE.format(body="value >= 3", label="ready")
CAP = "from policy import accepts\n\ndef test_cap():\n    assert accepts(101) is False\n"
DISAGREEING = TEMPLATE.format(body="3 < value <= 100", label="ready")
AGREEING = TEMPLATE.format(body="3 <= value <= 100", label="ready")
BODIES = ("value >= 4", "value >= 5", "value >= 3 and value != 3", "not value < 3", "3 <= value")
LENIENT = PromotionPolicy(min_pairs=5, min_discordant=3, alpha=0.2)


def approver(lines):
    return "probe-test approver"


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", message)):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _hidden_evaluator(text: str) -> bool:
    """The requirement, checked outside the probe: accepts(v) is v >= 3 for v in -5..10 (this file's own text)."""
    found: dict = {}
    exec(compile(text, "policy.py", "exec"), found)  # noqa: S102 - fixture text written above
    return all(found["accepts"](v) is (v >= 3) for v in range(-5, 11))


def _mission(wf, objective: str, function: str, *patches: str) -> tuple[str, dict]:
    api.allow_execution(wf, "sample", approver)
    mid = api.create_coding_mission(wf, objective, "sample", module="policy.py", function=function,
                                    risk="R0")["mission_id"]
    api.confirm(wf, mid, approver)
    found: dict = {}
    for text in patches:
        found = api.submit_patch(wf, mid, {"policy.py": text})
    return mid, found


def test_wf008_an_approved_probe_that_disagrees_holds_a_later_mission_at_qualified_draft_and_asks_a_person(
        tmp_path, monkeypatch, confined_bench):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv("G6_WORKFORCE_CERTIFICATE_POLICY", raising=False)
    before = set(threading.enumerate())
    wf = api.bind()
    repo = api.repository_dir(wf) / "sample"
    _commit(repo, V1, "v1")
    first, done = _mission(wf, "Fix the label beside the acceptance rule and preserve its behavior.", "label",
                           CAUGHT, REPAIRED)
    assert (done["status"], done["completion_state"]) == ("done", "qualified-draft"), done

    with cl.store(wf, promotion_policy=LENIENT) as probes:        # the store's owner sets its bar (review B1-5)
        learned = cl.learn(wf, first, probes)
        assert [cl.probe_of(t)["probe"] for t in learned] == [
            {"module": "policy.py", "function": "accepts", "input": 3, "expected": True}]
        variants = [cl.LabelledVariant({"policy.py": TEMPLATE.format(body=body, label="ready")},
                                       regression=not _hidden_evaluator(TEMPLATE.format(body=body, label="ready")),
                                       label_source={"kind": "hidden_evaluator", "run_id": f"hidden-{index}"})
                    for index, body in enumerate(BODIES)]
        decision = cl.validate(probes, learned[0].theory_id, variants, cl.replayer(wf, first), policy=LENIENT)
        assert (decision.recommendation, decision.wins, decision.losses, decision.pairs) == ("promote", 3, 0, 5), \
            decision
        cl.approve(probes, decision, approver)

        _commit(repo, {"policy.py": REPAIRED, "tests/test_cap.py": CAP}, "v2: a cap on accepts")
        second, closed = _mission(wf, "Fix accepts so that values above 100 are not accepted, and preserve its "
                                      "behavior.", "accepts", DISAGREEING)
        assert (closed["status"], closed["completion_state"]) == ("done", "qualified-draft"), closed
        found = cl.review(wf, second, probes, request_id="req-wf008")
        assert check_envelope(found) == []
        assert (found["completion_state"], found["mission_state"], found["state_with_probes"], found["lowered"]) == (
            "qualified-draft", "qualified-draft", "qualified-draft", False)
        assert found["warning_card"]["code"] == cl.PROBE_DISAGREES and found["request_id"] == "req-wf008"
        [probe] = found["probes"]
        assert (probe["status"], probe["expected"], probe["actual"]) == (cl.DISAGREES, True, False)
        assert probe["execution_id"] and found["evidence"][0]["execution_id"] == probe["execution_id"]
        [question] = found["questions"]
        assert "accepts(3)" in question["question"] and question["answers"] == ["approve", "reject"]
        shown = api.status(wf, second)
        assert [q["unit"] for q in shown["awaiting_human"]] == [question["unit"]]
        assert shown["completion_state"] == "qualified-draft"  # the closed result is kept; never blocked-escalated

        third, kept = _mission(wf, "Make accepts refuse values above 100 and preserve its behavior.", "accepts",
                               AGREEING)
        agreed = cl.review(wf, third, probes)
        assert check_envelope(agreed) == [] and [p["status"] for p in agreed["probes"]] == [cl.AGREES]
        assert agreed["questions"] == [] and agreed["lowered"] is False
        assert agreed["state_with_probes"] == agreed["mission_state"] == kept["completion_state"]
        assert api.status(wf, third)["awaiting_human"] == []
        unrun = cl.review(wf, third, probes, replay=lambda probe, files: {"executed": False, "reason": "stopped"})
        assert [p["status"] for p in unrun["probes"]] == [cl.NOT_RUN] and unrun["questions"] == []
        assert unrun["warning_card"]["code"] == cl.PROBE_UNMEASURED and check_envelope(unrun) == []

        # the owner's override (DECISIONS #22): repository tests may support verified, so a probe has one to lower
        monkeypatch.setenv("G6_WORKFORCE_CERTIFICATE_POLICY", "founder-d2")
        fourth, lifted = _mission(wf, "Change accepts so that values above 100 are refused, and preserve its "
                                      "behavior.", "accepts", DISAGREEING)
        monkeypatch.delenv("G6_WORKFORCE_CERTIFICATE_POLICY")
        assert lifted["completion_state"] == "verified", lifted
        held = cl.review(wf, fourth, probes)
        assert (held["mission_state"], held["state_with_probes"], held["completion_state"], held["lowered"]) == (
            "verified", "qualified-draft", "qualified-draft", True)
        assert check_envelope(held) == [] and len(held["questions"]) == 1

        retired = cl.answer(wf, probes, second, question["unit"], "approve", approver, reason="the cap is intended")
        assert retired is not None and retired["theory_id"] == learned[0].theory_id
        assert cl.select(probes, coding.frozen(wf, second)["task_signature"]) == []
        assert api.status(wf, second)["awaiting_human"] == []
    assert [t for t in threading.enumerate() if t not in before] == []
