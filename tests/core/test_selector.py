# SPDX-License-Identifier: Apache-2.0
"""The staged selector (0.3 design 3.5; plan revision 8, phase B): from the agent's situation, a short ranked list of
checked next calls with exact templates, and a reroute after a refusal. Pure and deterministic: nothing runs, no model,
no host fact."""
from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path

import pytest

from g6.core import selector
from g6.skills import corpus

SRC = Path(selector.__file__).resolve().parents[1]
CODE = re.compile(r"G6_E_[A-Z0-9_]+")
REVIEW_TOOLS = {"tool:g6_review_patch", "tool:g6_review_until_clean"}


def _ids(reply: dict, n: int) -> list[str]:
    return [candidate["id"] for candidate in reply["candidates"][:n]]


def _call_tool(candidate: dict) -> str:
    return candidate["call"].get("tool", "")


def test_review_before_done_ranks_a_review_tool_in_the_top_two() -> None:
    reply = selector.select("review my patch before I say it is done", phase="verify")
    assert set(_ids(reply, 2)) & REVIEW_TOOLS, _ids(reply, 5)


@pytest.mark.parametrize("task", [
    "the task does not say whether a negative price is refused or ignored",
    "add retries to the upload client; unclear what should happen to a duplicate request",
])
def test_an_ambiguous_behaviour_in_planning_raises_the_premortem_or_the_question(task: str) -> None:
    reply = selector.select(task, phase="plan")
    assert {"tool:g6_premortem", "tool:g6_confirm_behaviour"} & set(_ids(reply, 3)), _ids(reply, 5)
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "model_check")
    assert stage["ran"] and "no stated requirements" in stage["why"]


SAFETY = [("is it safe to run this git push", ""), ("is it safe to run this git push", "implement"),
          ("is it safe to git push my branch now", ""), ("is it safe to push to main now", "verify"),
          ("is it safe to delete these files", ""), ("should I run this database migration against production", ""),
          ("is it safe to pay this invoice through the API", ""), ("okay to deploy this to prod?", "plan")]


@pytest.mark.parametrize("task,phase", SAFETY + [("push my branch to origin", "implement"),
                                                 ("delete the old build folder", ""),
                                                 ("send the report by email", "implement")])
def test_a_call_that_acts_outside_g6_is_never_next_and_ranks_below_every_other(task: str, phase: str) -> None:
    reply = selector.select(task, phase, limit=8)
    acts = [bool(c["acts"]) for c in reply["candidates"]]
    assert acts == sorted(acts), [(c["id"], c["acts"]) for c in reply["candidates"]]
    chosen = next((c for c in reply["candidates"] if c["id"] == reply["next"]["id"]), None)
    assert chosen is None or not chosen["acts"], reply["next"]
    for candidate in reply["candidates"]:
        if candidate["kind"] == "op" and candidate["needs"]["sensitive"]:
            assert candidate["acts"], candidate["id"]


def test_an_outward_op_the_agent_names_is_listed_last_and_never_next() -> None:
    reply = selector.select("push my branch to the remote", "implement", considering=["ctx_git.push"])
    assert reply["candidates"][-1]["id"] == "op:ctx_git.push" and reply["candidates"][-1]["acts"]
    assert reply["next"]["id"] != "op:ctx_git.push" and reply["next"]["call"] is not None


@pytest.mark.parametrize("task,phase", SAFETY)
def test_a_safety_question_raises_the_action_gate(task: str, phase: str) -> None:
    reply = selector.select(task, phase, limit=5)
    assert "op:action_gating.evaluate" in _ids(reply, 3), _ids(reply, 5)
    if phase in ("", "implement", "plan"):
        assert reply["next"]["id"] == "op:action_gating.evaluate", _ids(reply, 5)
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "candidates")
    assert "action_safety" in stage["intents"]


@pytest.mark.parametrize("task", [
    "can I overwrite the release tag", "should we commit and merge now", "can I rm -rf the build folder",
    "is it fine to run the migration on the live database", "is it dangerous to reset the staging branch",
    "is it ok to force push to main"])
def test_a_safety_question_in_other_words_raises_the_action_gate(task: str) -> None:
    reply = selector.select(task, limit=5)
    assert "op:action_gating.evaluate" in _ids(reply, 3), _ids(reply, 5)
    assert "action_safety" in next(m for m in reply["mechanisms"] if m["stage"] == "candidates")["intents"]


@pytest.mark.parametrize("task", [
    "pushing my branch to origin now", "deleting the user table", "dropped the table by mistake, redo it",
    "merging into main", "committing the secrets file", "deploying to staging", "sending the newsletter",
    "paying the invoice", "resetting the repo", "overwriting the config files", "safely remove the old branch",
    "wipe the staging database", "erase the disk", "truncate the events table", "destroy the terraform stack",
    "terminate the cloud instances", "remove the users table", "upload the dataset to the public bucket",
    "email the customer list to the partner", "transfer 500 dollars to the vendor", "should we ship this",
    "release v2 to the app store", "can I go ahead with it"])
@pytest.mark.parametrize("phase", ["", "implement"])
def test_a_safety_question_in_any_word_form_raises_the_action_gate(task: str, phase: str) -> None:
    reply = selector.select(task, phase, limit=5)
    assert "op:action_gating.evaluate" in _ids(reply, 3), _ids(reply, 5)
    assert reply["next"]["id"] != "tool:g6_cloud_grant", reply["next"]


@pytest.mark.parametrize("task", ["share my notes with the cloud provider", "terminate the cloud instances",
                                  "a reply was withheld for consent"])
def test_cloud_grant_is_never_next_without_the_consent_code(task: str) -> None:
    for phase in ("", "stuck"):
        assert selector.select(task, phase)["next"]["id"] != "tool:g6_cloud_grant", (task, phase)
    assert selector.select(task, "", CONSENT_TEXT)["next"]["id"] == "tool:g6_cloud_grant"


CONSENT_TEXT = "[G6_E_CLOUD_CONSENT_REQUIRED] Results were withheld from a source not shared with this provider."


def test_the_safety_words_are_fixed_data_the_docstring_lists() -> None:
    assert {"overwrite", "commit", "merge", "rm", "reset", "live", "dangerous", "fine to", "okay to",
            "is it ok", "wipe", "erase", "truncate", "destroy", "terminate", "remove", "upload", "email",
            "transfer", "ship", "release", "should we", "can i"} <= set(selector.SAFETY_WORDS)
    listed = " ".join(selector.__doc__.split())
    assert all(f'"{word}"' in listed for word in selector.SAFETY_WORDS), [
        w for w in selector.SAFETY_WORDS if f'"{w}"' not in listed]


def test_the_action_gate_as_next_shows_the_field_to_fill_and_how() -> None:
    reply = selector.select("is it safe to run this git push")
    gate = next(c for c in reply["candidates"] if c["id"] == "op:action_gating.evaluate")
    assert gate["validated"] is False and gate["fill"] == ["action", "scope"]
    nxt = reply["next"]
    assert nxt["id"] == "op:action_gating.evaluate" and nxt["validated"] is False and nxt["fill"] == ["action", "scope"]
    assert "action_type" in nxt["say"] and "allow, confirm, review or block" in nxt["say"], nxt["say"]


def test_a_validated_next_that_ran_on_placeholders_or_defaults_says_to_fill_them_first() -> None:
    nxt = selector.select("what is the expected improvement of trying the next configuration", "plan")["next"]
    assert nxt["id"] == "op:decision_making.expected_improvement" and nxt["validated"], nxt
    assert {"mean", "std", "best"} <= set(nxt["fill"]), nxt
    assert "before relying on its answer" in nxt["say"] and all(f in nxt["say"] for f in nxt["fill"]), nxt["say"]


def test_the_gate_hint_example_is_an_act_the_gate_decides() -> None:
    """The example the hint teaches is an action type the gate profiles, and the gate decides it."""
    from g6.mcp import invoke_reply

    example = json.loads(re.search(r"\{.*?\}", selector.FILL_HINTS["op:action_gating.evaluate"]).group(0))
    ran = invoke_reply("action_gating", "evaluate", json.dumps({"action": example}), allow_sensitive=frozenset())
    assert example["action_type"] in ran["result"]["capabilities"]["supported_action_types"], example
    assert ran["result"]["gate_decision"]["decision"] in ("allow", "confirm", "review", "block"), ran["result"][
        "gate_decision"]


def test_a_next_call_whose_template_is_not_known_to_run_says_what_to_fill() -> None:
    for task, phase in (("check that the claims in my summary are supported by the sources", "verify"),
                        ("pytest fails with an import error after my change", "diagnose"),
                        ("plan the refactor of the payment module", "plan"), ("query the datalog program", "")):
        reply = selector.select(task, phase, limit=8)
        for candidate in reply["candidates"]:
            assert isinstance(candidate["fill"], list), candidate["id"]
        nxt = reply["next"]
        if nxt["call"] is not None and nxt["validated"] is False:
            assert nxt["say"] and (not nxt["fill"] or all(f in nxt["say"] for f in nxt["fill"])), nxt


def test_a_safety_question_while_planning_raises_the_premortem_too() -> None:
    reply = selector.select("should I run this database migration against production", "plan", limit=5)
    assert {"op:action_gating.evaluate", "tool:g6_premortem"} <= set(_ids(reply, 3)), _ids(reply, 5)


def test_an_agent_that_keeps_failing_is_sent_to_the_meta_harness() -> None:
    reply = selector.select("my agent keeps failing on the same kind of task", "stuck", limit=5)
    assert {"tool:metaharness_diagnose", "tool:metaharness_start"} & set(_ids(reply, 3)), _ids(reply, 5)
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "candidates")
    assert "workflow_improvement" in stage["intents"]


def test_an_ordinary_task_matches_no_intent() -> None:
    reply = selector.select("review my patch before I say it is done", "verify")
    assert next(m for m in reply["mechanisms"] if m["stage"] == "candidates")["intents"] == []


def test_stated_requirements_do_not_raise_the_model_check() -> None:
    reply = selector.select("review my patch before I say it is done", phase="verify",
                            considering=[{"id": "R1", "text": "totals refuse a negative price",
                                          "checks": ["tests/test_prices.py::test_negative"]}])
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "model_check")
    assert not any("model_check" in c["ranked_by"] for c in reply["candidates"])
    assert "1 stated requirement" in stage["why"]


def test_the_reply_is_a_qualified_draft_with_the_envelope_and_at_most_eight_candidates() -> None:
    reply = selector.select("check that the summary's claims are supported by the sources", limit=50)
    assert reply["completion_state"] == "qualified-draft" and reply["ok"] is True
    for field in ("warning_card", "evidence", "request_id", "run_id", "mechanisms", "next"):
        assert field in reply, field
    assert 0 < len(reply["candidates"]) <= 8
    for candidate in reply["candidates"]:
        assert {"call", "establishes", "needs", "cost", "ranked_by", "validated", "issues"} <= set(candidate)
    assert [m["stage"] for m in reply["mechanisms"]] == list(selector.STAGES)


def test_it_is_deterministic() -> None:
    args = ("my tests pass but I am not sure they test the boundary", "verify", "", ["g6_review_patch"], 6)
    first, second = selector.select(*args), selector.select(*args)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_no_blockless_component_is_ever_a_candidate() -> None:
    blockless = {name for name, data in corpus.load()["components"].items() if not data["ops"]}
    assert not blockless, "the corpus keeps runnable components only"
    from g6.core.registry import get_registry
    registry = get_registry()
    dropped = {name for name in registry.names() if registry.get_class(name) is None}
    assert {"navigator", "debate", "workforce"} <= dropped
    for task in ("a plan that fails halfway through", "taking one model's first answer unchallenged",
                 "a fix made for a failure nobody reproduced", "navigator debate workforce reasoning solver"):
        for candidate in selector.select(task, limit=8)["candidates"]:
            assert candidate.get("component") not in dropped, (task, candidate["id"])


def test_no_host_fact_reaches_a_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Whether an op is allowed, a grant exists or a model is configured is unknown until observed: the same input
    gives the same reply whatever the host says."""
    def run() -> str:
        reply = selector.select("review my patch before I say it is done", phase="verify",
                                last_outcome="G6_E_SENSITIVE_COMPONENT_DENIED patch_review.review", limit=8)
        return json.dumps(reply, sort_keys=True)

    bare = run()
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review,workforce,metaharness,lean")
    monkeypatch.setenv("G6_LLM_PROVIDER", "claude_code")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "another-home"))
    assert run() == bare
    for word in ("sensitive_ops_allowed_here", "claude_code", str(tmp_path), str(Path.home())):
        assert not _holds(json.loads(bare), word), word
    unknown = " ".join(json.loads(bare)["unknown_until_observed"])
    assert "allowed" in unknown and "grant" in unknown and "model" in unknown


def _strings(value: object) -> list[str]:
    """Every string in a reply, keys and values, as the client reads it once the JSON is parsed."""
    out, stack = [], [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            stack.extend([*item.keys(), *item.values()])
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return out


def _holds(reply: dict, word: str) -> bool:
    """Whether the reply holds ``word`` in any of its parsed strings, or in its JSON text raw or escaped as JSON
    escapes it (a Windows path's backslashes are doubled there)."""
    text = json.dumps(reply, sort_keys=True)
    return any(word in s for s in _strings(reply)) or word in text or json.dumps(word)[1:-1] in text


def test_the_host_fact_check_fails_a_reply_that_holds_the_home_folder() -> None:
    leaking = {"candidates": [{"issues": [f"does not load here ({Path.home() / 'lib' / 'x.dll'})"]}]}
    assert _holds(leaking, str(Path.home()))
    assert not _holds({"candidates": [{"issues": ["no path here"]}]}, str(Path.home()))


def test_a_caller_that_changes_a_reply_changes_no_later_reply() -> None:
    def stable(reply: dict) -> str:
        return json.dumps({k: v for k, v in reply.items() if k not in ("request_id", "run_id")}, sort_keys=True)

    args = ("review my patch before I say it is done", "verify", "G6_E_CLOUD_CONSENT_REQUIRED")
    before = stable(selector.select(*args))
    first = selector.select(*args)
    try:
        for candidate in first["candidates"]:                 # the agent fills in the templates it was given
            candidate["call"].setdefault("arguments", {})["repo"] = "/the/callers/repo"
            candidate["needs"]["confined"] = "changed"
        first["remedy"]["call"]["arguments"]["source"] = "changed"
        selector.remedy_for("G6_E_CLOUD_CONSENT_REQUIRED")["call"]["arguments"]["provider"] = "changed"
        assert stable(selector.select(*args)) == before
    finally:
        corpus._loaded.cache_clear()


def test_considering_entries_are_validated() -> None:
    reply = selector.select("review my patch", phase="verify", considering=[
        {"tool": "g6_review_patch", "arguments": {"repo": "/r", "task": "t"}},
        {"tool": "g6_review_patch", "arguments": {"repo": "/r", "task": "t", "answer": "yes"}},
        {"tool": "g6_no_such_tool", "arguments": {}},
        {"component": "grounding", "op": "check_claim", "args": {"claim": "c", "sources": {"a": "b"}}},
        {"component": "grounding", "op": "no_such_op", "args": {}},
        {"hypothesis": "the boundary at zero is wrong", "id": "missed_a_boundary", "severity": "high"},
        {"hypothesis": "it fails on Windows paths", "predicts": "test_paths fails on the patch",
         "check": "g6_review_patch"},
        "grounding.check_claims",
        "not a thing G6 has",
    ])
    seen = reply["considering"]
    assert [entry["validated"] for entry in seen] == [True, False, False, True, False, True, True, True, False]
    assert any("answer" in issue for issue in seen[1]["issues"])
    assert any("prediction" in issue for issue in seen[5]["issues"])
    considered = [c for c in reply["candidates"] if "considering" in c["ranked_by"]]
    assert {c["id"] for c in considered} >= {"tool:g6_review_patch", "op:grounding.check_claim"}


def test_a_selection_imports_no_component_and_runs_no_plan_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whether a component loads on this machine is a host fact: select reads the corpus's door check, made from
    shipped code when the corpus was built, so a reply is the same whatever the host can import."""
    import sys

    from g6.core.registry import ComponentRegistry

    task, considering = ("check that the claims in my summary are supported by the sources",
                         [{"component": "grounding", "op": "check_claim", "args": {"claim": "c", "sources": {"a": "b"}}}])
    healthy = selector.select(task, "verify", considering=considering, limit=8)

    def refuse(self: object, name: str) -> None:
        raise AssertionError(f"select imported the component {name}")

    monkeypatch.setattr(ComponentRegistry, "get_module", refuse)
    monkeypatch.setattr(ComponentRegistry, "get_class", refuse)
    monkeypatch.setitem(sys.modules, "g6.navigator.plan", None)
    reply = selector.select(task, "verify", considering=considering, limit=8)
    assert reply["ok"] is True and any(c["kind"] == "op" for c in reply["candidates"])
    assert reply["candidates"] == healthy["candidates"] and reply["considering"][0]["validated"] is True


def test_an_op_candidate_carries_the_corpus_door_check_and_validated_says_only_the_shape() -> None:
    full = {entry["id"]: entry for entry in corpus.entries()}
    reply = selector.select("push my branch to the remote", "implement", considering=["ctx_git.push"], limit=8)
    for candidate in reply["candidates"]:
        assert candidate["allowed_by_default"] is (not candidate["needs"]["sensitive"]), candidate["id"]
        if candidate["kind"] == "op":
            entry = full[candidate["id"]]
            assert (candidate["validated"], candidate["issues"]) == (entry["validated"], entry["issues"])
            assert not any("SENSITIVE" in issue or "NOT_CLEARED" in issue for issue in candidate["issues"])
    push = next(c for c in reply["candidates"] if c["id"] == "op:ctx_git.push")
    assert push["allowed_by_default"] is False


def test_a_considered_op_call_missing_a_field_its_template_holds_is_not_validated() -> None:
    reply = selector.select("check a claim", considering=[
        {"component": "grounding", "op": "check_claim", "args": {"claim": "c"}}])
    row = reply["considering"][0]
    assert row["validated"] is False and any("sources" in issue for issue in row["issues"])


def test_considering_may_be_text() -> None:
    reply = selector.select("check claims", considering="grounding.check_claim, g6_find")
    assert [entry["validated"] for entry in reply["considering"]] == [True, True]


def _nested(depth: int) -> list:
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("considering", [
    "[" * 100_000, '{"a":' * 100_000,                    # deep, and over the size cap
    "[" * 40 + "]" * 40,                                 # under the cap, deeper than MAX_DEPTH
    "x" * (64 * 1024 + 1),                               # plain text over the 64 KB cap
    '["g6_review_patch",',                               # JSON that does not decode
    _nested(2_000),                                      # a list from an in-process caller, too deep
])
def test_an_oversized_too_deep_or_broken_considering_is_refused_with_the_envelope(considering: object) -> None:
    reply = selector.select("review my patch", "verify", considering=considering)
    assert reply["completion_state"] == "blocked-escalated" and "G6_E_INPUT_VALIDATION" in json.dumps(reply)
    assert "considering" in reply["error"]


def _refused(reply: dict, field: str) -> bool:
    return (reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
            and field in reply["error"])


def test_a_task_or_last_outcome_over_its_bound_is_refused_before_it_is_read() -> None:
    import time

    start = time.perf_counter()
    assert _refused(selector.select("word " * (selector.MAX_TASK_BYTES // 5 + 1)), "task")
    assert _refused(selector.select("review my patch", last_outcome="x" * (selector.MAX_OUTCOME_BYTES + 1)),
                    "last_outcome")
    assert time.perf_counter() - start < 1.0
    assert selector.select("word " * (selector.MAX_TASK_BYTES // 5 - 1))["ok"] is True
    assert selector.select("review my patch", last_outcome="x" * selector.MAX_OUTCOME_BYTES)["ok"] is True


@pytest.mark.parametrize("considering", [
    ",".join(["a"] * (selector.MAX_CONSIDERING_ENTRIES + 1)),            # as text
    ["g6_review_patch"] * (selector.MAX_CONSIDERING_ENTRIES + 1),        # as a value
    json.dumps(["a"] * (selector.MAX_CONSIDERING_ENTRIES + 1)),          # as JSON text
])
def test_more_considering_entries_than_the_bound_are_refused(considering: object) -> None:
    assert _refused(selector.select("review my patch", "verify", considering=considering), "considering")


def test_a_wide_considering_is_refused_without_walking_it() -> None:
    import tracemalloc

    wide = [0] * 1_000_000
    tracemalloc.start()
    reply = selector.select("review my patch", considering=wide)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert _refused(reply, "considering") and peak < 2_000_000, peak


def test_a_value_g6_cannot_read_is_refused_in_g6s_own_words() -> None:
    for considering in (["\ud800 g6_review_patch"], [{"hypothesis": object()}], [10 ** 5000], [b"bytes"]):
        reply = selector.select("review my patch", considering=considering)
        if not reply["ok"]:
            assert _refused(reply, "considering"), reply["error"]
            assert not re.search(r"codec|Error|object at|int_max_str_digits|\\u", reply["error"]), reply["error"]
    reply = selector.select("review \ud800 my patch", last_outcome="\udfff", considering="\ud800 g6_review_patch")
    assert "codec" not in json.dumps(reply)


def test_the_reply_stays_within_its_stated_size() -> None:
    claim = "c" * (selector.MAX_CONSIDERING_BYTES // selector.MAX_CONSIDERING_ENTRIES - 120)
    worst = [{"hypothesis": claim, "id": f"H{i}", "predicts": "p", "check": "g6_review_patch", "severity": "x"}
             for i in range(selector.MAX_CONSIDERING_ENTRIES)]
    assert len(json.dumps(worst)) <= selector.MAX_CONSIDERING_BYTES
    for considering in (worst, ",".join(["a"] * selector.MAX_CONSIDERING_ENTRIES), ""):
        reply = selector.select("review my patch " * 200, "verify", "G6_E_CLOUD_CONSENT_REQUIRED " * 100,
                                considering, limit=8)
        assert reply["ok"] is True and len(json.dumps(reply)) <= selector.MAX_REPLY_BYTES, len(json.dumps(reply))


def test_a_considering_within_the_bounds_is_still_read() -> None:
    reply = selector.select("review my patch", "verify", considering=_nested(3) + ["g6_review_patch"])
    assert reply["ok"] is True and reply["considering"][-1]["validated"] is True


@pytest.mark.parametrize("bad", [{"phase": "later"}, {"rerank": "model"}, {"task": "  "}])
def test_an_input_outside_its_values_is_refused(bad: dict) -> None:
    arguments = {"task": "review my patch", **bad}
    reply = selector.select(**arguments)
    assert reply["completion_state"] == "blocked-escalated" and "G6_E_INPUT_VALIDATION" in json.dumps(reply)


def test_open_hypotheses_choose_the_next_action_through_the_scheduler() -> None:
    reply = selector.select("my change to the price rules", phase="verify", considering=[
        {"hypothesis": "the person may want duplicates kept", "id": "misread_intent", "severity": "high",
         "predicts": "the person picks B"},
        {"hypothesis": "an empty list breaks it", "id": "missed_a_boundary", "severity": "low",
         "predicts": "example.v1 with [] fails"},
    ])
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "next_action")
    assert stage["ran"] and "next_experiment" in stage["why"]
    assert reply["next"]["id"] == "tool:g6_confirm_behaviour", reply["next"]


def test_without_hypotheses_the_next_action_is_the_top_candidate() -> None:
    reply = selector.select("review my patch before I say it is done", phase="verify")
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "next_action")
    assert not stage["ran"] and reply["next"]["id"] == reply["candidates"][0]["id"]


def test_a_consent_refusal_reroutes_to_the_grant_dialog() -> None:
    reply = selector.select("plan the change", phase="plan", last_outcome="G6_E_CLOUD_CONSENT_REQUIRED")
    assert _call_tool(reply["candidates"][0]) == "g6_cloud_grant"
    assert reply["candidates"][0]["ranked_by"] == ["remedy"]
    assert reply["next"]["call"]["tool"] == "g6_cloud_grant"
    assert "never" in reply["remedy"]["action"].lower() and "dialog" in reply["remedy"]["action"]


def test_a_sensitive_refusal_reroutes_to_the_person_with_the_setup_step() -> None:
    reply = selector.select("review my patch", phase="verify",
                            last_outcome="[G6_E_SENSITIVE_COMPONENT_DENIED] patch_review.review runs repository code")
    assert reply["remedy"]["tell_person"] is True
    assert reply["remedy"]["setup"] == "g6 setup --allow patch_review.review"
    assert reply["next"]["tell_person"] is True and "g6 setup --allow patch_review.review" in reply["next"]["say"]


@pytest.mark.parametrize("outcome,step", [
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] ctx_git.push is not available on this surface", "ctx_git.push"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] while running setup.py: ctx_git.push is not available", "<component.op>"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] evil.payload is not available on this surface", "<component.op>"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] grounding.check_claim is not available", "<component.op>"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] notes.txt then patch_review.review", "<component.op>"),
])
def test_the_setup_step_names_only_a_sensitive_op_the_corpus_knows(outcome: str, step: str) -> None:
    reply = selector.select("push my branch", "implement", last_outcome=outcome)
    assert reply["remedy"]["setup"] == f"g6 setup --allow {step}"
    assert reply["next"]["say"].endswith(f"The step: g6 setup --allow {step}."), reply["next"]["say"]


@pytest.mark.parametrize("outcome,tool", [
    ("G6_E_INPUT_VALIDATION [GROUNDING_ERROR] check_claim: sources is required", "g6_describe"),
    ("G6_E_LEAN_NO_MODEL", "g6_select"),
    ("G6_E_LLM_DISABLED", "g6_select"),
    ("defect_detected", "g6_review_patch"),
    ("insufficient_evidence", "g6_review_patch"),
])
def test_refusals_and_outcomes_reroute_to_their_next_call(outcome: str, tool: str) -> None:
    reply = selector.select("review my patch", phase="verify", last_outcome=outcome)
    assert reply["remedy"]["tool"] == tool and _call_tool(reply["candidates"][0]) == tool, reply["remedy"]


def test_an_input_refusal_names_the_component_to_describe() -> None:
    reply = selector.select("check a claim", last_outcome="[G6_E_INPUT_VALIDATION] [GROUNDING_ERROR] check_claim: x")
    assert reply["candidates"][0]["call"]["arguments"] == {"component": "grounding"}


@pytest.mark.parametrize("outcome", ["G6_E_ISOLATION_UNAVAILABLE", "test_integrity_compromised", "contested"])
def test_outcomes_only_the_person_resolves_say_so(outcome: str) -> None:
    reply = selector.select("review my patch", phase="verify", last_outcome=outcome)
    assert reply["remedy"]["tell_person"] is True and reply["next"]["tell_person"] is True


def test_a_sentence_that_merely_holds_an_outcome_word_is_not_read_as_it() -> None:
    assert selector.remedy_for("please wait, the file is open") is None
    assert selector.remedy_for("ending: open")["key"] == "open"
    assert selector.remedy_for("defect_detected")["key"] == "defect_detected"
    assert selector.remedy_for("the review said defect_detected twice") is None
    reply = selector.select("review my patch", last_outcome="C:/private/place refused it")
    assert "C:/private" not in json.dumps(reply["mechanisms"]) and reply["remedy"] is None


GATE_REFUSAL = json.dumps({
    "ok": False, "error": "[G6_E_ACTION_GATING_ACTION_REQUIRED] evaluate: action is required",
    "completion_state": "blocked-escalated",
    "warning_card": {"code": "G6_E_ACTION_GATING_ACTION_REQUIRED", "message": "evaluate: action is required"}})


def _reply(component: str, op: str, code: str, state: str = "blocked-escalated", **more: object) -> str:
    """A reply in the shape g6_invoke returns, with its own code on its warning card."""
    return json.dumps({"ok": state != "blocked-escalated", "result": {"component": component, "op": op, **more},
                       "completion_state": state, "warning_card": {"code": code, "message": "refused"}})


@pytest.mark.parametrize("outcome,component", [
    ("G6_E_ACTION_GATING_ACTION_REQUIRED evaluate: action is required", "action_gating"),
    (GATE_REFUSAL, "action_gating"),
    (_reply("datalog", "query", "G6_E_DATALOG_PARSE_ERROR"), "datalog"),
    ("[G6_E_ALGO_INPUT_INVALID] [ALGORITHM_DESIGN_ERROR] estimate refused", "algorithm_design"),
    ("G6_E_DECISION_INPUT bandit_select: ValueError: at least one arm required", "decision_making"),
    ("G6_E_OPERATION_INPUT_INVALID run_id is required", "action_gating"),
])
def test_a_known_component_input_code_reads_that_components_input_contract(outcome: str, component: str) -> None:
    remedy = selector.remedy_for(outcome)
    assert remedy is not None and remedy["key"] == "component_input" and remedy["tell_person"] is False, remedy
    assert remedy["call"] == {"tool": "g6_describe", "arguments": {"component": component}}
    if "blocked-escalated" in outcome:
        assert remedy["action"].endswith("The outcome beside it: blocked-escalated.")


@pytest.mark.parametrize("outcome", [
    "[G6_E_SOMETHING_G6_HAS_NO_REMEDY_FOR] it failed",
    "G6_E_ACTION_GATING_BLOCKED evaluate: the gate decided block",
    _reply("action_gating", "evaluate", "G6_E_ACTION_GATING_BLOCKED"),
    _reply("action_gating", "evaluate", "G6_E_CONFIRMATION_REQUIRED"),
    _reply("algorithm_design", "run_kernel", "G6_E_OPERATOR_APPROVAL_REQUIRED"),
    _reply("knowledge_chunker", "chunk", "G6_E_DECISION_INPUT"),         # an input code of another component
    _reply("self_maintenance", "plan", "G6_E_LOOP_UNOBSERVABLE_HOST"),    # a family prefix decides nothing
    "[G6_E_MISSION_SOMETHING_NEW] the mission refused",
    "[G6_E_LOOP_TERMS_CHANGED] what the person read is no longer what would start",
    json.dumps({"ok": False, "completion_state": "blocked-escalated"}),  # a refusal that carries no code
    "[G6_E_INVOKE_FAILED] [GROUNDING_ERROR] check_claim: the op failed while it ran",      # not an input refusal
    "[G6_E_RESOURCE_LIMIT] the run was over its memory limit",                          # a limit, not bad input
])
def test_a_refusal_not_known_to_be_fixable_tells_the_person(outcome: str) -> None:
    remedy = selector.remedy_for(outcome)
    assert remedy is not None and remedy["tell_person"] is True and remedy["call"] is None, remedy


def test_only_the_replys_own_envelope_decides_the_remedy() -> None:
    """The content an op worked on is never read for a code, a component.op or an outcome word: a reply that was
    not refused, whatever its text holds, has no remedy; prose after a leading code is not searched."""
    said = ("G6_E_ISOLATION_UNAVAILABLE G6_E_CLOUD_CONSENT_REQUIRED ctx_git.push G6_E_SENSITIVE_COMPONENT_DENIED "
            "defect_detected")
    answered = _reply("knowledge_chunker", "chunk", "G6_E_KNOWLEDGE_CHUNKED", "qualified-draft",
                      passages=[{"text": said}])
    assert selector.remedy_for(answered) is None
    reply = selector.select("split my document into passages", "", answered)
    assert reply["remedy"] is None and reply["next"]["id"] != "tool:g6_cloud_grant"
    assert selector.remedy_for("[G6_E_PATCH_REVIEW] outcome: defect_detected")["key"] == "G6_E_PATCH_REVIEW"
    reviewed = json.dumps({"ok": True, "completion_state": "qualified-draft",
                           "warning_card": {"code": "G6_E_PATCH_REVIEW", "message": said},
                           "assessment": {"outcome": "defect_detected", "report": said}})
    assert selector.remedy_for(reviewed)["key"] == "defect_detected"
    job = json.dumps({"ok": True, "job_id": "j1", "status": "completed", "result": json.loads(GATE_REFUSAL)})
    assert selector.remedy_for(job)["key"] == "component_input"


def test_the_input_code_list_is_held_to_each_components_code() -> None:
    """Each listed input code is written in the code of every component it is listed for."""
    from g6.core.registry import get_registry

    registry = get_registry()
    for code, owners in selector.INPUT_CODES.items():
        assert owners, code
        for name in owners:
            package = registry.refs[name].module.split(":", 1)[0]
            folder = Path(importlib.import_module(package).__file__).parent
            text = " ".join(p.read_text(encoding="utf-8") for p in folder.rglob("*.py"))
            assert re.search(rf"\b{code}\b", text), (code, name)


def test_every_no_model_code_g6_writes_says_do_the_step_yourself_then_check_it() -> None:
    # was a list of the codes held to the code (NO_MODEL_CODES); each code's writer now declares it (g6.core.refusals)
    written = {c for p in SRC.rglob("*.py") if p.name != "refusals.py"
               for c in CODE.findall(p.read_text(encoding="utf-8", errors="replace")) if c.endswith("_NO_MODEL")}
    assert len(written) >= 15
    for code in sorted(written):
        remedy = selector.remedy_for(code)
        assert remedy["fix_by"] == "tool:g6_select" and remedy["call"]["tool"] == "g6_select", (code, remedy)
    assert not any(k.endswith("_") for k in selector.REMEDIES), [k for k in selector.REMEDIES if k.endswith("_")]


def test_the_call_last_outcome_reports_refused_is_never_next_again() -> None:
    first = selector.select("is it safe to run this git push")["next"]
    assert first["id"] == "op:action_gating.evaluate"
    again = selector.select("is it safe to run this git push", last_outcome=GATE_REFUSAL)
    assert again["next"]["call"] != first["call"] and again["next"]["id"] == "tool:g6_describe", again["next"]
    shaped = selector.select("is it safe to run this git push",
                             last_outcome=_reply("action_gating", "evaluate", "G6_E_GATE_OK", "qualified-draft"))
    assert shaped["next"]["id"] != "op:action_gating.evaluate", shaped["next"]
    # prose is never read for a component.op: only the envelope's own component and op fields name the call
    plain = selector.select("is it safe to run this git push", last_outcome="action_gating.evaluate refused it")
    assert plain["remedy"] is None and plain["next"]["id"] == "op:action_gating.evaluate", plain["next"]


def test_isolation_names_the_doctor() -> None:
    assert selector.remedy_for("G6_E_ISOLATION_UNAVAILABLE")["setup"] == "g6 doctor"


def test_every_remedy_names_a_registered_tool_or_op_or_tells_the_person() -> None:
    data = corpus.load()
    tools = {entry["call"]["tool"] for entry in data["entries"] if entry["kind"] == "tool"}
    ops = {entry["id"][3:] for entry in data["entries"] if entry["kind"] == "op"}
    for key, remedy in selector.REMEDIES.items():
        assert remedy["tell_person"] or remedy["tool"], key
        if remedy["tool"]:
            if remedy["available"] is False:
                assert remedy["tool"] in selector.PENDING_TOOLS and remedy["tool"] not in tools, key
            else:
                assert remedy["tool"] in tools or remedy["tool"] in ops, (key, remedy["tool"])
                schema = next(e["schema"] for e in data["entries"] if e["call"].get("tool") == remedy["tool"]
                              and e["kind"] == "tool")
                assert set(remedy["arguments"]) <= set(schema["properties"]), (key, remedy["arguments"])
    for remedy in selector.recovery_remedies():
        assert remedy["tell_person"] or remedy["tool"] in tools | ops, remedy


def _emitted() -> dict[str, set[str]]:
    """Every G6_E_* code the MCP modules, the door and the registry beneath it can emit: written in their text, or a
    name they use that is bound (by import or in the module) to a string that is such a code."""
    files = [*sorted((SRC / "mcp").glob("*.py")), SRC / "core" / "door.py", SRC / "core" / "registry.py"]
    found: dict[str, set[str]] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        parts = path.relative_to(SRC.parent).with_suffix("").parts
        module = importlib.import_module(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
        for code in CODE.findall(text):
            found.setdefault(code, set()).add(path.name)
        bound: dict[str, object] = {}
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "g6":
                base = importlib.import_module(node.module)
                for alias in node.names:
                    value = getattr(base, alias.name, None)
                    if value is None:
                        try:
                            value = importlib.import_module(f"{node.module}.{alias.name}")
                        except ImportError:
                            continue
                    bound[alias.asname or alias.name] = value
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Name):
                value = bound.get(node.id, getattr(module, node.id, None))
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                owner = bound.get(node.value.id, getattr(module, node.value.id, None))
                value = getattr(owner, node.attr, None) if owner is not None and not isinstance(owner, str) else None
            if isinstance(value, str) and CODE.fullmatch(value):
                found.setdefault(value, set()).add(path.name)
    return found


def _behind_mcp() -> dict[str, set[str]]:
    """Every G6_E_* code written in the modules whose refusals and results the MCP tools pass on as they are: the
    review (harness/patch_review.py, the review loop's harness/completion_loop.py, the verification_* modules it
    and the mission use), the mission's workforce/ package, and each g6.metaharness module an MCP module imports
    (the tool-facing ones). A code is counted when its text is there, so a code only a comment names is counted too:
    the inventory errs on the side of a remedy too many."""
    harness = SRC / "harness"
    files = [harness / "patch_review.py", harness / "completion_loop.py", *sorted(harness.glob("verification_*.py")),
             *sorted((SRC / "workforce").glob("*.py"))]
    imported = {m or "__init__" for path in (SRC / "mcp").glob("*.py")
                for m in re.findall(r"g6\.metaharness(?:\.(\w+))?", path.read_text(encoding="utf-8"))}
    for name in sorted(imported):
        module = SRC / "metaharness" / f"{name}.py"
        files.append(module if module.exists() else SRC / "metaharness" / name / "__init__.py")
    found: dict[str, set[str]] = {}
    for path in files:
        for code in CODE.findall(path.read_text(encoding="utf-8")):
            found.setdefault(code, set()).add(path.name)
    return found


def test_every_code_the_mcp_surface_and_the_door_emit_has_a_remedy_or_tells_the_person() -> None:
    emitted = _emitted()
    assert len(emitted) >= 60 and "G6_E_CLOUD_CONSENT_REQUIRED" in emitted and "G6_E_NO_BLOCK" in emitted
    behind = _behind_mcp()
    assert {"G6_E_PATCH_REVIEW", "G6_E_PATCH_STALE", "G6_E_NO_PYTHON_CHANGED", "G6_E_POLICY_BLOCKED",
            "G6_E_JOB_UNKNOWN"} <= set(behind)
    missing = sorted(code for code in {**emitted, **behind} if selector.remedy_for(code) is None)
    assert not missing, missing
    for code in ("G6_E_ISOLATION_UNAVAILABLE", "G6_E_LLM_DISABLED", "G6_E_LEAN_NO_MODEL"):
        assert selector.remedy_for(code) is not None, code


@pytest.mark.parametrize("outcome,key,tool", [
    (json.dumps({"completion_state": "qualified-draft", "warning_card": {"code": "G6_E_PATCH_REVIEW"},
                 "assessment": {"outcome": "defect_detected"}}), "defect_detected", "g6_review_patch"),
    (json.dumps({"completion_state": "qualified-draft", "warning_card": {"code": "G6_E_PATCH_REVIEW"},
                 "assessment": {"outcome": "insufficient_evidence"}}), "insufficient_evidence", "g6_review_patch"),
    ("[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest", "G6_E_PATCH_STALE",
     "g6_review_patch"),
    (json.dumps({"completion_state": "blocked-escalated", "error": "[G6_E_SOMETHING_G6_HAS_NO_REMEDY_FOR] stopped",
                 "ending": "untested"}), "untested", ""),
    ("[G6_E_SOMETHING_G6_HAS_NO_REMEDY_FOR] ending: untested", "unknown_code", ""),
])
def test_an_outcome_word_beside_a_review_card_code_or_an_unknown_code_decides_the_remedy(
        outcome: str, key: str, tool: str) -> None:
    remedy = selector.remedy_for(outcome)
    assert remedy is not None and remedy["key"] == key and remedy["tool"] == tool, remedy


def test_a_review_card_code_alone_says_to_read_the_outcome_beside_it() -> None:
    remedy = selector.remedy_for("G6_E_PATCH_REVIEW")
    assert remedy is not None and "outcome" in remedy["action"]


@pytest.mark.parametrize("code", ["G6_E_LOOP_WAITING_ON_PERSON", "G6_E_PENDING_APPROVAL", "G6_E_POLICY_BLOCKED"])
def test_a_wait_on_the_person_tells_the_person(code: str) -> None:
    reply = selector.select("finish my patch", "verify", last_outcome=f"[{code}] action: wait")
    assert reply["remedy"]["tell_person"] is True and reply["next"]["tell_person"] is True, reply["remedy"]
    assert reply["next"]["id"] != "tool:g6_improve_status"


@pytest.mark.parametrize("outcome", [
    "[G6_E_META_APPROVAL_REQUIRED] no person approved: a name comes only from the client's dialog",
    "[G6_E_META_APPROVAL_REQUIRED] no person confirmed: a name comes only from the client's dialog",
    "G6_E_META_APPROVER_IS_PROPOSER", "[G6_E_META_NOT_CONFIRMED] a person has not confirmed the workflow",
    "[G6_E_META_STANDARD_CHANGED] the workflow changed since it was confirmed",
    "[G6_E_META_PROMOTION_REQUIRED] each upward step needs a person's approval",
    "[G6_E_META_PROMOTION_DISABLED] no promotion policy was confirmed"])
def test_a_meta_harness_wait_on_the_person_tells_the_person(outcome: str) -> None:
    reply = selector.select("approve the recommended version", "verify", last_outcome=outcome)
    assert reply["remedy"]["tell_person"] is True and reply["next"]["tell_person"] is True, reply["remedy"]
    assert reply["next"]["call"] is None and reply["next"]["id"] is None


def test_a_meta_harness_refusal_an_agent_resolves_still_reads_the_workflow() -> None:
    reply = selector.select("evaluate my candidate", "verify", last_outcome="[G6_E_META_BUSY] another operation")
    assert reply["next"]["call"]["tool"] == "metaharness_inspect"


def test_a_declared_limit_code_points_at_the_override_tool_marked_unavailable_until_it_is_registered() -> None:
    data = json.loads(json.dumps(corpus.load()))
    data["components"]["grounding"]["limits"] = ["G6_E_GROUNDING_EXAMPLE_LIMIT"]
    reply = selector.select("check a claim", last_outcome="[G6_E_GROUNDING_EXAMPLE_LIMIT] too long", corpus=data)
    assert reply["remedy"]["tool"] == "g6_agentic_run" and reply["remedy"]["available"] is False
    assert all(_call_tool(c) != "g6_agentic_run" for c in reply["candidates"])
    assert reply["next"]["tell_person"] is False and reply["next"]["id"] != "tool:g6_agentic_run"


def test_metaharness_recovery_remedies_are_mapped_by_component() -> None:
    reply = selector.select("my workflow's answers are wrong", phase="diagnose", last_outcome="wrong_answer")
    names = {remedy["component"] for remedy in reply["remedy"]["recovery"]}
    assert {"grounding", "epistemic_integrity", "cegis"} <= names
    for remedy in reply["remedy"]["recovery"]:
        assert remedy["tool"] in {"g6_invoke", "metaharness_propose"} or remedy["tell_person"], remedy


def test_a_semantic_rerank_is_not_offered_and_the_colbert_value_is_answered_lexically(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A rerank would depend on what the host has installed and cached, which a reply admitted with no grant must not
    say: rerank="colbert" (kept for compatibility) ranks lexically, and the ColBERT rungs are never imported."""
    import sys

    monkeypatch.setitem(sys.modules, "g6.knowledge.ctx_colbert.rungs", None)     # an import of it would raise
    task = "check that the summary's claims are supported by the sources"
    lexical, colbert = selector.select(task, "verify"), selector.select(task, "verify", rerank="colbert")
    assert colbert["ok"] is True and colbert["candidates"] == lexical["candidates"]
    stage = next(m for m in colbert["mechanisms"] if m["stage"] == "candidates")
    assert "not offered" in stage["rerank"] and stage["rankers"] == ["lexical", "phase_prior"]
    assert not any("dense" in c["ranked_by"] for c in colbert["candidates"])


def test_every_prior_names_an_entry_of_the_corpus() -> None:
    ids = {entry["id"] for entry in corpus.load()["entries"]}
    for phase, priors in selector.PRIORS.items():
        assert phase in corpus.PHASES and set(priors) <= ids, (phase, set(priors) - ids)
    for name, (_, by_phase) in selector.INTENTS.items():
        for phase, priors in by_phase.items():
            assert phase in ("*", *corpus.PHASES) and set(priors) <= ids, (name, phase, set(priors) - ids)


def test_every_tool_template_fits_its_recorded_schema_and_every_op_template_its_typed_input() -> None:
    for entry in corpus.entries():
        if entry["kind"] == "tool":
            arguments = set(entry["call"]["arguments"])
            assert arguments <= set(entry["schema"]["properties"]), entry["id"]
            assert set(entry["schema"]["required"]) <= arguments, entry["id"]
        elif entry["kind"] == "op":
            params = json.loads(entry["call"]["arguments"]["params_json"])
            assert set(params) <= set(entry["fields"]), entry["id"]


# -- the remedy reads the refusal declarations (g6.core.refusals), never a table of its own ----------------------
def _with_declaration(component: str, code: str, fix_by: str) -> dict:
    data = json.loads(json.dumps(corpus.load()))
    data["refusals"].setdefault(component, {})[code] = [fix_by, "words", ""]
    return data


def test_the_remedy_is_whatever_the_writers_declaration_says() -> None:
    shaped = _reply("grounding", "check_claim", "G6_E_GROUNDING_EXAMPLE_REFUSAL")
    as_input = selector.remedy_for(shaped, _with_declaration("grounding", "G6_E_GROUNDING_EXAMPLE_REFUSAL", "input"))
    assert as_input["fix_by"] == "input" and as_input["call"] == {"tool": "g6_describe",
                                                                  "arguments": {"component": "grounding"}}
    as_person = selector.remedy_for(shaped, _with_declaration("grounding", "G6_E_GROUNDING_EXAMPLE_REFUSAL", "person"))
    assert as_person["fix_by"] == "person" and as_person["tell_person"] is True and as_person["call"] is None


def test_the_selector_keeps_no_table_of_codes() -> None:
    from g6.core import refusals

    assert not [key for key in selector.REMEDIES if key.startswith("G6_E_")]
    declared = {code: sorted(name for name, codes in refusals.component_declarations().items()
                             if codes.get(code) and codes[code].fix_by == "input")
                for codes in refusals.component_declarations().values() for code in codes}
    assert selector.INPUT_CODES == {code: tuple(names) for code, names in sorted(declared.items()) if names}


def test_a_path_the_component_cannot_take_is_an_input_to_fix() -> None:
    """Review 4, finding 1: design_governance's own remedy for G6_E_DESIGN_BAD_PATH is to give a relative path."""
    from g6.mcp import invoke_reply

    reply = invoke_reply("design_governance", "import_graph",
                         json.dumps({"package": "shop", "files": {"../shop/cart.py": "x = 1\n"}}),
                         allow_sensitive=frozenset())
    assert reply["warning_card"]["code"] == "G6_E_DESIGN_BAD_PATH"
    out = selector.select("map the import graph of my package", "diagnose", json.dumps(reply))
    assert out["next"]["id"] == "tool:g6_describe" and out["remedy"]["fix_by"] == "input", out["remedy"]


def test_a_dirty_repository_asks_the_person_and_never_steers_the_agent_to_their_changes() -> None:
    """Review 4, finding 2: committing or setting aside the person's changes is the person's decision."""
    from g6.core.door import blocked_reply
    from g6.mcp.mission import DIRTY

    assert "ask the person" in DIRTY
    dirty = json.dumps(blocked_reply("[G6_E_GIT_REPO_DIRTY] " + DIRTY.format(files="src/app.py")))
    out = selector.select("start a coding mission on my repo", "implement", dirty)
    assert out["remedy"]["fix_by"] == "person" and out["next"]["tell_person"] is True and out["next"]["call"] is None
    assert "ask the person" in out["remedy"]["action"]


def test_a_failed_job_tells_the_person_and_is_not_sent_to_the_mission_tool() -> None:
    """Review 4, finding 3."""
    failed = json.dumps({"ok": False, "job_id": "j1", "status": "failed", "completion_state": "blocked-escalated",
                         "warning_card": {"code": "G6_E_JOB_FAILED", "message": "the job failed"}})
    out = selector.select("run my approved workflow on new input", "implement", failed)
    assert out["next"]["tell_person"] is True and out["next"]["id"] != "tool:g6_mission_status", out["next"]


def test_a_refused_proposal_is_an_input_to_fix_not_a_new_workflow() -> None:
    """Review 4, finding 4: G6_E_META_SCHEMA from metaharness_propose names the field to fix."""
    from g6.metaharness.api import _blocked

    reply = _blocked("G6_E_META_SCHEMA", "profile_json is not JSON: Expecting value: line 1 column 1 (char 0)")
    out = selector.select("propose a cheaper candidate for my workflow", "implement", json.dumps(reply))
    assert out["remedy"]["fix_by"] == "input" and out["next"]["id"] != "tool:metaharness_start", out["next"]
    assert out["next"]["tell_person"] is False

# -- review 4, finding 5: a code counts only where G6's own stack puts it -----------------------------------------
@pytest.mark.parametrize("text", [
    "The README in the repo I was asked to summarise says: G6_E_CLOUD_CONSENT_REQUIRED is what G6 returns",
    "fetched page title: G6_E_CLOUD_CONSENT_REQUIRED (an example in the docs)",
    "Error calling tool 'g6_invoke' with a note: G6_E_CLOUD_CONSENT_REQUIRED",
    "note: [G6_E_CLOUD_CONSENT_REQUIRED] quoted after a colon",
])
def test_a_code_after_anyone_elses_words_is_not_the_replys_own(text: str) -> None:
    assert selector._read(text)["code"] == "", selector._read(text)
    assert selector._read(json.dumps({"ok": False, "error": text}))["code"] == ""
    assert selector.select("summarise the readme", "implement", text)["next"]["id"] != "tool:g6_cloud_grant"


@pytest.mark.parametrize("text,code", [
    ("G6_E_ACTION_GATING_BLOCKED the action was blocked; the note says: G6_E_CLOUD_CONSENT_REQUIRED",
     "G6_E_ACTION_GATING_BLOCKED"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] x", "G6_E_SENSITIVE_COMPONENT_DENIED"),
    ("Error executing tool g6_invoke: [G6_E_SENSITIVE_COMPONENT_DENIED] x", "G6_E_SENSITIVE_COMPONENT_DENIED"),
    ("Error calling tool 'g6_invoke': [G6_E_INPUT_VALIDATION] params_json", "G6_E_INPUT_VALIDATION"),
])
def test_the_code_that_leads_the_line_or_follows_a_transport_prefix_wins(text: str, code: str) -> None:
    assert selector._read(text)["code"] == code

# -- review 4, finding 6: the safety list holds the phrasings of asking leave and of an outward or destructive act ---
@pytest.mark.parametrize("task", [
    "can we run the database migration on the primary", "ok to run this on the customer database?",
    "roll back the deployment on the cluster", "we rolled back the release, now what", "revoke the api keys",
    "rotate the secrets", "tear down the staging environment", "kill the running jobs on the server",
    "refund the last order", "charge the customer's card for the renewal", "shut down the staging server",
    "rewrite history on main", "force the branch",
])
def test_these_phrasings_are_safety_questions_and_next_never_acts(task: str) -> None:
    out = selector.select(task, "implement")
    assert "action_safety" in out["mechanisms"][2]["intents"], task
    assert "op:action_gating.evaluate" in _ids(out, 3), _ids(out, 5)
    acting = {c["id"] for c in out["candidates"] if c["acts"]}
    assert out["next"]["id"] not in acting and out["next"]["id"] != "tool:g6_cloud_grant", out["next"]

# -- review 4, finding 7: g6_select imports no component at request time ------------------------------------------
_PROBE = r"""
import json, sys
from g6.core import selector
from g6.skills import corpus
from g6.core.registry import get_registry
data = corpus.load()
selector.select("warm up", "plan")
reg = get_registry()
packages = {reg.refs[c].module.split(":", 1)[0]: c for c in data["components"] if c in reg.refs}
before = {c for p, c in packages.items() if p in sys.modules}
selector.select(**json.loads(sys.argv[1]))
print(json.dumps(sorted({c for p, c in packages.items() if p in sys.modules} - before)))
"""


@pytest.mark.parametrize("arguments", [
    {"task": "find why the test fails", "phase": "diagnose",
     "considering": json.dumps([{"hypothesis": "the cache is stale", "predicts": "a rerun passes", "severity": "high"}])},
    {"task": "the workflow answers wrong", "last_outcome": "wrong_answer"},
])
def test_a_selection_imports_no_component_in_a_fresh_interpreter(arguments: dict) -> None:
    import os
    import subprocess
    import sys

    root = SRC.parents[1]
    env = {**os.environ, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"}
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE, json.dumps(arguments)], capture_output=True,
                          text=True, env=env, timeout=120, check=True)
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize("hypotheses", [
    [("H1", "high", ""), ("H2", "low", "")],
    [("H1", "medium", "g6_review_patch"), ("H2", "medium", "")],
    [("H1", "low", "op:swe_diagnostics.test_analyze"), ("H2", "high", "g6_premortem")],
])
def test_the_scheduler_picks_what_next_experiment_picks(hypotheses: list) -> None:
    from g6.complexity.ir import Hypothesis
    from g6.harness.verification_controller import Experiment, ExperimentState, next_experiment

    data = corpus.load()
    entries = {e["id"]: e for e in corpus.entries(data)}
    tools = {e["call"]["tool"]: e["id"] for e in data["entries"] if e["kind"] == "tool"}
    rows = [{"id": i, "claim": "c", "severity": s, "predicts": "p",
             "check": tools.get(c) or (c if c in entries else "")} for i, s, c in hypotheses]
    mine, _ = selector._schedule(rows, entries)
    named = {r["check"]: r["id"] for r in rows if r["check"]}
    experiments = tuple(
        Experiment(k, k, tuple(sorted((set(e.get("hypotheses", ())) & {r["id"] for r in rows})
                                      | ({named[k]} if k in named else set()))), selector.COST_UNITS[e["cost"]],
                   allowed=True)
        for k, e in sorted(entries.items()) if e["kind"] != "route"
        and ((set(e.get("hypotheses", ())) & {r["id"] for r in rows}) or k in named))
    state = ExperimentState("s", "s", "s", tuple(Hypothesis(r["id"], r["claim"], r["severity"], r["predicts"])
                                                 for r in rows), selector.BUDGET_UNITS)
    chosen = next_experiment(state, experiments)
    assert mine == (chosen.id if chosen else "")