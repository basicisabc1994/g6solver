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


def _attributed_stage(monkeypatch, outcomes: dict) -> Stage:
    """``Stage``'s recorded runs, where a run on the patch made on the bound bytes (``artefact``) carries the run's own
    record of them, as ``verification_search._run_check`` writes it: a reading is decided only from such a run (the
    review of wire-diagnose, 3)."""
    from g6.core.ledger import digest

    stage = Stage(monkeypatch, outcomes)
    recorded = verification_search._run_check

    def run_check(change, placed, source, node, *, at_base, timeout_s, artefact=None, **kwargs):
        ran = recorded(change, placed, source, node, at_base=at_base, timeout_s=timeout_s)
        if artefact is not None and not at_base:
            ran = {**ran, "execution_artifact": {"version": "execution-run.v1", "artifact_digest": digest(artefact),
                                                 "base_commit": "", "files": {}}}
        return ran

    monkeypatch.setattr(verification_search, "_run_check", run_check)
    return stage


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
    plain_stage = _attributed_stage(monkeypatch, {SOURCE: {"patch": patch, "base": base}})
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
    _attributed_stage(monkeypatch, {SOURCE: {"patch": "failed", "base": "passed"}})
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


# --- review 1: a reading is bound to its own check's run, never to a caller-chosen id ------------------------------

def _colliding_review(monkeypatch):
    """Codex review 1 of wire-diagnose, its reproducer: the caller's check takes the id the proposer gives its first
    free-form check (g6p-check-1); the caller's fails on the patch, the proposer's passes on both."""
    from pathlib import Path
    from types import SimpleNamespace

    from g6.harness import patch_review, verification_proposer
    from g6.workforce import repo_tests
    from tests.harness.test_verification_proposer import Model

    repo = Path(__file__).resolve().parents[2]
    # repository I/O is synthetic: nothing here reads or writes the repository
    change = repo_tests.Change(repo, "HEAD", "a" * 40, "a" * 40, (), {}, {}, (), ())
    monkeypatch.setattr(repo_tests, "checked_repo", lambda path: repo)
    monkeypatch.setattr(repo_tests, "_git", lambda *a, **kw: b"")
    monkeypatch.setattr(repo_tests, "snapshot", lambda root: {})
    monkeypatch.setattr(repo_tests, "change", lambda *a, **kw: change)
    monkeypatch.setattr(repo_tests, "test_related", lambda *a: set())
    monkeypatch.setattr(repo_tests, "ignored_inputs", lambda *a: SimpleNamespace(
        loadable=[], other=[], complete=True, named_like_modules=[]))
    monkeypatch.setattr(repo_tests, "derive_tests", lambda *a: ([], 0))
    monkeypatch.setattr(repo_tests, "diff_text", lambda *a: "")
    monkeypatch.setattr(patch_review, "_closing", lambda *a, **kw: ({}, [], {}))
    monkeypatch.setattr(verification_proposer, "boundary", lambda root: "")
    caller_source = "def test_caller():\n    assert False\n"
    model_source = "def test_model():\n    assert True\n"
    stage = _attributed_stage(monkeypatch, {caller_source: {"patch": "failed", "base": "passed"},
                                model_source: {"patch": "passed", "base": "passed"}})
    proposed = copy.deepcopy(discriminate.CHECK)
    proposed.update(id="g6p-check-1", requirement_id="R1", source=caller_source)
    model = Model({"free_form": {"checks": [{"requirement_id": "R1", "intent": "change", "file": "test_model.py",
                                              "source": model_source}]}})
    got = review_patch(repo, task=TASK, requirements=[{"id": "R1", "text": "add returns the sum", "checks": []}],
                       proposed_checks=[proposed], search={"propose": True, "proposer": "free_form"}, llm=model.llm)
    return got, stage


def test_review1_a_caller_id_in_the_proposers_namespace_is_refused_before_anything_runs(monkeypatch):
    got, stage = _colliding_review(monkeypatch)
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "g6p-" in got["warning_card"]["message"] and stage.calls == []


@pytest.mark.parametrize("ident", ["g6p-check-1", "G6P-relation-2", "g6p-scenario-1"])
def test_review1_every_validator_refuses_a_caller_id_in_the_proposers_namespace(ident):
    from g6.harness import verification_templates

    with pytest.raises(ValueError, match="g6p-"):
        verification_search.proposed_checks([{**_filled(discriminate.TEMPLATE)["proposed_checks"][0], "id": ident}],
                                            {"diagnosis"})
    with pytest.raises(ValueError, match="g6p-"):
        verification_templates.template_instances([{"id": ident, "template": "example.v1", "requirement_id": "R1",
                                                     "intent": "change", "bindings": {}}], {"R1"})


def test_review1_each_reading_is_its_own_checks_result_even_when_ids_collide(monkeypatch):
    from g6.mcp.review_reply import compact

    # the namespace refusal switched off, to reach the reading with two checks of one id
    monkeypatch.setattr(verification_search, "reserved_id", lambda ident, **kw: False)
    got, stage = _colliding_review(monkeypatch)
    assert len(stage.calls) == 4, got
    activities = [a for a in got["assessment"]["completion"]["activities"] if a.get("activity") == "proposed check"]
    assert [a["result"] for a in activities] == ["unconfirmed_failure", "not_refuted"]
    [reading] = compact(got, "review-wd1", None)["assessment"]["explanations"]
    assert reading["observed"] == "unconfirmed_failure" and reading["consistent_with"] == ["patch_wrong_for_case"]


# --- review 2: a reading is decided only from a run attributable to the reviewed artifact --------------------------

def test_review2_a_tree_changed_during_the_review_decides_no_explanation(monkeypatch):
    """Codex review 2 of wire-diagnose, its reproducer: an editor changes the tree while the proposed check runs, so
    the closing check finds repository_changed_during_review; the reading, made from that run, decides nothing."""
    from pathlib import Path
    from types import SimpleNamespace

    from g6.harness import patch_review
    from g6.mcp.review_reply import compact
    from g6.workforce import repo_tests

    root = Path(__file__).resolve().parents[2]
    current = {"calc.py": "a" * 64}
    # repository I/O is synthetic: nothing here reads or writes the repository
    change = repo_tests.Change(root, "HEAD", "a" * 40, "a" * 40, (), {}, {}, (), ())
    monkeypatch.setattr(repo_tests, "checked_repo", lambda path: root)
    monkeypatch.setattr(repo_tests, "_git", lambda *args, **kwargs: b"")
    monkeypatch.setattr(repo_tests, "snapshot", lambda path: dict(current))
    monkeypatch.setattr(repo_tests, "change", lambda *args, **kwargs: change)
    monkeypatch.setattr(repo_tests, "test_related", lambda *args: set())
    monkeypatch.setattr(repo_tests, "ignored_inputs", lambda *args: SimpleNamespace(
        loadable=[], other=[], complete=True, named_like_modules=[]))
    monkeypatch.setattr(repo_tests, "derive_tests", lambda *args: ([], 0))
    monkeypatch.setattr(repo_tests, "diff_text", lambda *args: "")
    monkeypatch.setattr(patch_review, "_closing", lambda *args, **kwargs: ({}, [], dict(current)))
    source = "from calc import add\n\ndef test_sum():\n    assert add(2, 3) == 5\n"
    stage = _attributed_stage(monkeypatch, {source: {"patch": "passed", "base": "failed"}})
    run, race = verification_search._run_check, [False]

    def run_with_editor(*args, **kwargs):
        if race[0] and not kwargs["at_base"]:
            current["calc.py"] = "b" * 64           # an editor changes the tree after the review's first snapshot
        return run(*args, **kwargs)

    monkeypatch.setattr(verification_search, "_run_check", run_with_editor)
    chosen = selector.select("my patch broke a test", "diagnose", last_outcome="defect_detected")
    arguments = {**_filled(chosen["next"]["call"]["arguments"], source), "repo": str(root), "task": TASK}
    control = review_patch(**arguments)
    assert control["assessment"]["outcome"] != "test_integrity_compromised", control
    assert control["assessment"]["explanations"][0]["consistent_with"] == ["check_stale_or_wrong"]
    race[0] = True
    got = review_patch(**arguments)
    assert len(stage.calls) == 4, got
    assert got["assessment"]["outcome"] == "test_integrity_compromised", got
    assert any(r["code"] == "repository_changed_during_review" for r in got["assessment"]["reasons"])
    [reading] = compact(got, "wd2", None)["assessment"]["explanations"]
    assert {row["reading"] for row in reading["explanations"]} == {"not_decided"}, reading
    assert reading["consistent_with"] == [] and "integrity" in reading["summary"]


# --- review 3: attribution is shown by the run's own record of the bytes it ran on, never assumed ------------------

def _restored_race(monkeypatch, *, bind=True):
    """Codex review 3 of wire-diagnose, its reproducer: an editor saves other bytes while the check's copy is made and
    restores the tree before the closing snapshot, so no integrity reason is given. The copy, the run and the bytes
    are synthetic; the copy's record (``_copy_snapshot``) is what the fake copy holds, and the reviewed bytes are bound
    as ``bind_execution`` binds them (or, with ``bind=False``, cannot be)."""
    import hashlib
    import sys
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from g6.core.ledger import digest
    from g6.harness import patch_review
    from g6.workforce import repo_tests
    from tests.harness.test_proposed_checks import result

    root = Path(__file__).resolve().parents[2]
    good, bad = "def add(a, b):\n    return a + b\n", "def add(a, b):\n    return a - b\n"

    def sha(text):
        return hashlib.sha256(text.encode()).hexdigest()

    reviewed = {"calc.py": sha(good)}
    live, copied, race, executions = dict(reviewed), {}, [False], []
    change = repo_tests.Change(root, "HEAD", "a" * 40, "a" * 40, ("calc.py",), {"calc.py": bad}, {"calc.py": good},
                               ("calc.py",), ())
    monkeypatch.setattr(repo_tests, "checked_repo", lambda path: root)
    monkeypatch.setattr(repo_tests, "_git", lambda *args, **kwargs: b"")
    monkeypatch.setattr(repo_tests, "snapshot", lambda path: dict(live))
    monkeypatch.setattr(repo_tests, "change", lambda *args, **kwargs: change)
    monkeypatch.setattr(repo_tests, "test_related", lambda *args: set())
    monkeypatch.setattr(repo_tests, "ignored_inputs", lambda *args: SimpleNamespace(
        loadable=[], other=[], complete=True, named_like_modules=[]))
    monkeypatch.setattr(repo_tests, "derive_tests", lambda *args: ([], 0))
    monkeypatch.setattr(repo_tests, "diff_text", lambda *args: "")
    monkeypatch.setattr(repo_tests, "interpreter", lambda *args: sys.executable)
    monkeypatch.setattr(patch_review, "_closing", lambda *args, **kwargs: ({}, [], dict(live)))

    def bound(change, sparse, *, deadline):
        if not bind:
            raise OSError("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: links need a materialised-byte adapter")
        return verification_search.ExecutionSnapshot(sparse, dict(reviewed))

    monkeypatch.setattr(verification_search, "bind_execution", bound)

    @contextmanager
    def copy_tree(*args, **kwargs):
        live["calc.py"] = sha(bad if race[0] else good)      # an editor saves other bytes while the copy is made
        copied.clear()
        copied.update(live)
        copied.update({name: sha(text) for name, text in kwargs["before"].items()})
        live.update(reviewed)                                  # and restores them before the closing snapshot
        yield root / "synthetic-copy"

    def execute(copy_root, nodes, **kwargs):
        [node] = nodes
        failed = copied["calc.py"] == sha(bad)
        executions.append((copied["calc.py"], failed))
        return result(failed=[node], digest=digest(copied)) if failed else result([node], digest=digest(copied))

    monkeypatch.setattr(repo_tests, "repository_copy", copy_tree)
    monkeypatch.setattr(repo_tests, "_copy_snapshot", lambda path, **kwargs: dict(copied))
    monkeypatch.setattr(repo_tests, "_run", execute)
    monkeypatch.setattr(repo_tests, "run_at_base", lambda change, nodes, **kwargs: result(nodes, digest="b" * 64))
    chosen = selector.select("my patch broke a test", "diagnose", last_outcome="defect_detected")
    source = "from calc import add\n\ndef test_sum():\n    assert add(2, 3) == 5\n"
    arguments = {**_filled(chosen["next"]["call"]["arguments"], source), "repo": str(root), "task": TASK}
    return arguments, race, executions, live, reviewed, sha


def test_review3_a_run_shown_to_be_on_the_reviewed_bytes_decides_and_a_restored_race_does_not(monkeypatch):
    from g6.mcp.review_reply import compact

    arguments, race, executions, live, reviewed, sha = _restored_race(monkeypatch)
    control = review_patch(**arguments)
    assert control["assessment"]["explanations"][0]["consistent_with"] == ["check_stale_or_wrong"], control
    race[0] = True
    got = review_patch(**arguments)
    assert live == reviewed and not any(r["stage"] == "integrity" for r in got["assessment"]["reasons"])
    # the race's copy is not the reviewed bytes: refused before it ran, so only the control's run happened
    assert executions == [(sha("def add(a, b):\n    return a + b\n"), False)]
    [reading] = compact(got, "wd3-race", None)["assessment"]["explanations"]
    assert {e["reading"] for e in reading["explanations"]} == {"not_decided"}, reading


def test_review3_a_run_with_no_record_of_the_reviewed_bytes_decides_nothing(monkeypatch):
    arguments, race, executions, live, reviewed, sha = _restored_race(monkeypatch, bind=False)
    got = review_patch(**arguments)
    assert executions, "the check ran"
    assert not any(r["stage"] == "integrity" for r in got["assessment"]["reasons"])
    [reading] = got["assessment"]["explanations"]
    assert reading["observed"] == "not_refuted" and reading["consistent_with"] == [], reading
    assert {e["reading"] for e in reading["explanations"]} == {"not_decided"}
    assert "reviewed artifact" in reading["summary"]


# --- review 4: the bound bytes are the reviewed snapshot's, or nothing is bound -------------------------------------

def test_review4_a_manifest_that_disagrees_with_the_reviewed_snapshot_binds_nothing(monkeypatch):
    """Codex review 4 of wire-diagnose, finding 1, its reproducer: an editor's bytes are on disk while the full
    manifest is read and while the copy is made, and restored for every snapshot. bind_execution, _copy_manifest,
    _run_check and the attribution are real; the file system around them is synthetic."""
    from pathlib import Path
    from types import SimpleNamespace

    from g6.workforce import repo_tests

    real_bind = verification_search.bind_execution
    arguments, race, executions, live, reviewed, sha = _restored_race(monkeypatch)
    root = Path(arguments["repo"])
    bad = "def add(a, b):\n    return a - b\n"

    class CapturedFile:
        def is_file(self):
            return True

        def stat(self):
            return SimpleNamespace(st_size=len(bad))

        def resolve(self):
            return root / "calc.py"

    monkeypatch.setattr(verification_search, "bind_execution", real_bind)
    monkeypatch.setattr(repo_tests, "_git", lambda root, *args, **kw: b"calc.py\0" if "-co" in args else b"")
    monkeypatch.setattr(repo_tests, "_generated_files", lambda *a, **kw: ({}, {}))
    monkeypatch.setattr(repo_tests, "_disk", lambda *a: CapturedFile())
    monkeypatch.setattr(repo_tests, "_digest", lambda path: sha(bad))
    race[0] = True
    got = review_patch(**arguments)
    assert live == reviewed
    assert not any(r["stage"] == "integrity" for r in got["assessment"]["reasons"])
    reading = got["assessment"]["explanations"][0]
    assert {row["reading"] for row in reading["explanations"]} == {"not_decided"}, reading
    # nothing was bound, so the check ran unbound and its run has no record that names the reviewed artifact
    assert executions == [(sha(bad), True)] and reading["consistent_with"] == []


def test_review4_bind_execution_refuses_a_file_whose_digest_differs_from_the_snapshots():
    from pathlib import Path

    from g6.workforce import repo_tests

    change = repo_tests.Change(Path("."), "HEAD", "a" * 40, "a" * 40, (), {}, {}, (), ())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verification_search, "_copy_manifest", lambda change, **kw: {"calc.py": "b" * 64,
                                                                                    "other.py": "c" * 64})
        patch.setattr(repo_tests, "snapshot", lambda root, *a: {"calc.py": "a" * 64})
        with pytest.raises(OSError, match="G6_E_EXECUTION_ARTIFACT_UNAVAILABLE"):
            verification_search.bind_execution(change, {"calc.py": "a" * 64}, deadline=1e18)
        patch.setattr(verification_search, "_copy_manifest", lambda change, **kw: {"calc.py": "a" * 64,
                                                                                    "other.py": "c" * 64})
        bound = verification_search.bind_execution(change, {"calc.py": "a" * 64}, deadline=1e18)
        assert bound.execution["files"] == {"calc.py": "a" * 64, "other.py": "c" * 64}
