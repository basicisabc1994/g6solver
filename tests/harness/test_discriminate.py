# SPDX-License-Identifier: Apache-2.0
"""Diagnosis by a discriminating check (plan revision 8, phase C, item 1): ``g6_select`` offers, in the diagnose
phase or after a review reproduced a defect, a ``g6_review_patch`` template whose proposed check carries competing
explanations, each predicting a different classification of its run; the review reads the observed classification
against them, deterministically ("consistent with", never "cause"), and changes no outcome. No model is called; the
proposed checks' runs are recorded (``tests.harness.test_proposed_checks.Stage``)."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from g6.core import selector
from g6.harness import discriminate, verification_search
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope
from tests.harness.test_proposed_checks import BASE, FIXED, TESTS, Stage, Tiers, _repo

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")

TASK = "Make add return the sum of two integers."
SOURCE = "from calc import add\n\n\ndef test_sum():\n    assert add(2, 3) == 5\n"
DEFECT = json.dumps({"ok": True, "completion_state": "qualified-draft", "warning_card": {"code": "G6_E_PATCH_REVIEW"},
                     "assessment": {"outcome": "defect_detected", "reasons": [
                         {"stage": "tier_2", "code": "derived_test_regressed",
                          "detail": "test_calc.py passed at the base commit and fails on the patch"}]}})


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})


def _filled(arguments: dict, source: str = SOURCE) -> dict:
    """The template's arguments as an agent fills them: the requirement's text and the check's source."""
    out = copy.deepcopy(arguments)
    out["requirements"][0]["text"] = "add returns the sum of two integers"
    out["proposed_checks"][0]["source"] = source
    return out


def _review_calls(reply: dict) -> list[dict]:
    calls = [c["call"] for c in reply["candidates"] if isinstance(c.get("call"), dict)]
    calls += [reply["next"]["call"]] if isinstance(reply["next"].get("call"), dict) else []
    return [c for c in calls if c.get("tool") == "g6_review_patch"]


def _predicts(call: dict) -> list[str]:
    return [e["predicts"] for one in call["arguments"]["proposed_checks"] for e in one["explanations"]]


# --- the template ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("phase, outcome", [("diagnose", ""), ("verify", DEFECT), ("", "defect_detected"),
                                            ("diagnose", DEFECT)])
def test_select_offers_a_review_template_whose_check_has_competing_predictions(phase, outcome):
    reply = selector.select("my patch broke a test", phase, last_outcome=outcome)
    calls = _review_calls(reply)
    assert calls, reply["candidates"]
    for call in calls:
        assert call["arguments"]["proposed_checks"] and len(set(_predicts(call))) >= 2, call
        assert call["arguments"]["repo"] == "<repo>" and call["arguments"]["task"] == "<task>"
    review = next(c for c in reply["candidates"] if c["id"] == "tool:g6_review_patch")
    assert review["validated"] is True and review["fill"] == discriminate.FILL
    assert reply["completion_state"] == "qualified-draft"


def test_after_a_defect_the_next_call_is_the_template_and_says_how_to_fill_it():
    reply = selector.select("my patch broke a test", "verify", last_outcome=DEFECT)
    nxt = reply["next"]
    assert nxt["id"] == "tool:g6_review_patch" and nxt["call"]["arguments"]["proposed_checks"], nxt
    assert nxt["fill"] == discriminate.FILL and discriminate.SAY in nxt["say"]
    assert "cause" not in nxt["say"].replace("never the cause", "")
    assert reply["remedy"]["call"] == nxt["call"]


@pytest.mark.parametrize("phase, outcome", [("verify", ""), ("verify", "insufficient_evidence"), ("plan", "")])
def test_outside_diagnosis_the_review_template_is_unchanged(phase, outcome):
    reply = selector.select("review my patch before I say it is done", phase, last_outcome=outcome)
    for call in _review_calls(reply):
        assert call["arguments"] == {"repo": "<repo>", "task": "<task>"}, call


def test_the_template_validates_once_filled_and_its_placeholder_source_is_refused():
    arguments = discriminate.TEMPLATE
    check = arguments["proposed_checks"][0]
    assert discriminate.explanations(check["explanations"], check["intent"])
    found = verification_search.proposed_checks(_filled(arguments)["proposed_checks"], {"diagnosis"})
    assert [one.id for one in found] == ["discriminate"]
    with pytest.raises(ValueError, match="placeholder"):
        verification_search.proposed_checks(arguments["proposed_checks"], {"diagnosis"})


def test_every_predictable_result_is_one_classify_gives_and_check_error_is_never_one():
    classes = {verification_search.COUNTEREXAMPLE, verification_search.FAILS_ON_BASE_TOO,
               verification_search.NOT_REFUTED, verification_search.UNCONFIRMED_FAILURE,
               verification_search.BEHAVIOUR_CHANGED}
    for intent, allowed in discriminate.PREDICTABLE.items():
        assert set(allowed) <= classes and verification_search.CHECK_ERROR not in allowed, intent


# --- refusal when the predictions do not discriminate ----------------------------------------------------------------

E1 = {"id": "a", "said": "the patch is wrong for this case", "predicts": "unconfirmed_failure"}
E2 = {"id": "b", "said": "the old test is stale", "predicts": "not_refuted"}


@pytest.mark.parametrize("stated, intent, said", [
    ([E1, {**E2, "predicts": "unconfirmed_failure"}], "change", "cannot tell them apart"),
    ([E1], "change", "at least two"),
    ([], "change", "at least two"),
    ([E1, E2, {**E2, "id": "c", "predicts": "behaviour_changed"}, {**E2, "id": "d"}, {**E2, "id": "e"}], "change",
     "2 to 4"),
    ([E1, {**E2, "predicts": "check_error"}], "change", "never a prediction"),
    ([E1, E2], "preserve", "intent is preserve"),
    ([E1, {**E2, "id": "a"}], "change", "unique"),
    ([E1, {**E2, "weight": 2}], "change", "{id, said, predicts}"),
    ([E1, {**E2, "said": ""}], "change", "said"),
    ("a, b", "change", "at least two"),
])
def test_explanations_that_do_not_discriminate_are_refused(stated, intent, said):
    with pytest.raises(ValueError) as refused:
        discriminate.explanations(stated, intent)
    assert said in str(refused.value)


def test_a_review_with_predictions_that_do_not_discriminate_is_refused_before_anything_runs(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    tiers, stage = Tiers(monkeypatch), Stage(monkeypatch, {})
    arguments = _filled(discriminate.TEMPLATE)
    arguments["proposed_checks"][0]["explanations"][1]["predicts"] = "unconfirmed_failure"
    got = review_patch(repo, task=TASK, **arguments)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "cannot tell them apart" in got["warning_card"]["message"]
    assert tiers.calls == [] and stage.calls == []


# --- the deterministic reading ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("observed, readings, fits", [
    ("unconfirmed_failure", ["consistent", "inconsistent"], ["a"]),
    ("not_refuted", ["inconsistent", "consistent"], ["b"]),
    ("behaviour_changed", ["inconsistent", "inconsistent"], []),
    ("check_error", ["not_decided", "not_decided"], []),
    ("skipped", ["not_decided", "not_decided"], []),
    ("error", ["not_decided", "not_decided"], []),
])
def test_the_reading_is_consistent_inconsistent_or_not_decided_per_explanation(observed, readings, fits):
    stated = discriminate.explanations([E1, E2], "change")
    got = discriminate.read(stated, observed, check="c", requirement="R1", intent="change")
    assert [row["reading"] for row in got["explanations"]] == readings
    assert got["consistent_with"] == fits and got["observed"] == observed
    assert got == discriminate.read(stated, observed, check="c", requirement="R1", intent="change")
    words = (got["summary"] + got["note"]).lower()
    assert "consistent with" in words and "is the cause" not in words and "caused" not in words


@pytest.mark.parametrize("patch, base, observed, fits", [
    ("failed", "passed", "unconfirmed_failure", ["patch_wrong_for_case"]),
    ("passed", "failed", "not_refuted", ["check_stale_or_wrong"]),
    ("error", "passed", "check_error", []),
])
def test_the_review_reads_the_observed_result_against_the_predictions(repo, monkeypatch, patch, base, observed, fits):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    plain_stage = Stage(monkeypatch, {SOURCE: {"patch": patch, "base": base}})
    arguments = _filled(discriminate.TEMPLATE)
    plain = copy.deepcopy(arguments)
    del plain["proposed_checks"][0]["explanations"]
    without = review_patch(repo, task=TASK, **plain)
    got = review_patch(repo, task=TASK, **arguments)
    assert len(plain_stage.calls) == 4
    assert got["completion_state"] == "qualified-draft" and check_envelope(got) == []
    [read] = got["assessment"]["explanations"]
    assert read["check"] == "discriminate" and read["observed"] == observed and read["consistent_with"] == fits
    # it changes no outcome, no reason and no requirement: the same review without the explanations
    assert "explanations" not in without["assessment"]
    for key in ("outcome", "reasons", "requirements", "next_actions"):
        assert got["assessment"][key] == without["assessment"][key], key


def test_the_compact_reply_carries_the_reading():
    from g6.mcp.review_reply import compact

    read = discriminate.read(discriminate.explanations([E1, E2], "change"), "not_refuted", check="c",
                             requirement="R1", intent="change")
    reply = {"ok": True, "completion_state": "qualified-draft",
             "assessment": {"report": "r", "outcome": "defect_detected", "explanations": [read]}}
    assert compact(reply, "job", None)["assessment"]["explanations"] == [read]
    del reply["assessment"]["explanations"]
    assert "explanations" not in compact(reply, "job", None)["assessment"]


# --- in process over MCP ---------------------------------------------------------------------------------------------

def test_over_mcp_select_offers_the_template_and_the_review_reads_it(repo, monkeypatch, tmp_path):
    pytest.importorskip("fastmcp")
    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {SOURCE: {"patch": "failed", "base": "passed"}})
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow():
        async with Client(server) as client:
            chosen = data(await client.call_tool("g6_select", {"task": TASK, "phase": "diagnose",
                                                               "last_outcome": DEFECT}))
            template = chosen["next"]["call"]
            arguments = {**_filled(template["arguments"]), "repo": str(repo), "task": TASK}
            job = data(await client.call_tool(template["tool"], arguments))
            for _ in range(400):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return chosen, got
                await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    chosen, got = asyncio.run(flow())
    assert chosen["next"]["id"] == "tool:g6_review_patch" and len(set(_predicts(chosen["next"]["call"]))) >= 2
    assert got["status"] == "completed", got
    result = got["result"]
    assert result["completion_state"] == "qualified-draft"
    [read] = result["assessment"]["explanations"]
    assert read["observed"] == "unconfirmed_failure" and read["consistent_with"] == ["patch_wrong_for_case"]
    assert [row["reading"] for row in read["explanations"]] == ["consistent", "inconsistent"]
