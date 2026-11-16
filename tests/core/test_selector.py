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
    assert selector.remedy_for("the review said defect_detected twice")["key"] == "defect_detected"
    reply = selector.select("review my patch", last_outcome="C:/private/place refused it")
    assert "C:/private" not in json.dumps(reply["mechanisms"]) and reply["remedy"] is None


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


def test_every_code_the_mcp_surface_and_the_door_emit_has_a_remedy_or_tells_the_person() -> None:
    emitted = _emitted()
    assert len(emitted) >= 60 and "G6_E_CLOUD_CONSENT_REQUIRED" in emitted and "G6_E_NO_BLOCK" in emitted
    missing = sorted(code for code in emitted if selector.remedy_for(code) is None)
    assert not missing, missing
    for code in ("G6_E_ISOLATION_UNAVAILABLE", "G6_E_LLM_DISABLED", "G6_E_LEAN_NO_MODEL"):
        assert selector.remedy_for(code) is not None, code


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


def test_every_tool_template_fits_its_recorded_schema_and_every_op_template_its_typed_input() -> None:
    for entry in corpus.entries():
        if entry["kind"] == "tool":
            arguments = set(entry["call"]["arguments"])
            assert arguments <= set(entry["schema"]["properties"]), entry["id"]
            assert set(entry["schema"]["required"]) <= arguments, entry["id"]
        elif entry["kind"] == "op":
            params = json.loads(entry["call"]["arguments"]["params_json"])
            assert set(params) <= set(entry["fields"]), entry["id"]
