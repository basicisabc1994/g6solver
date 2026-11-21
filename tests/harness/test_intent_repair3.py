# SPDX-License-Identifier: Apache-2.0
"""Repair 3 of claude/wire-intent (Codex review 3): a critic's verdict counts only where the protocol states it and
two stated verdicts that disagree fail closed; trusted stops reach the door on the premortem, confirmation and
application path; a confirmed answer replaces every supplied instance of its candidate, whatever the instance's id.
FakeLLM and monkeypatched seams only."""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent, component_process, semantic, storage
from g6.core.llm import FakeLLM, LLMError
from g6.harness import behaviour_question as bq, candidate_standard as cs, completion_loop as loop
from g6.harness import outcome_history, patch_review, verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
from g6.mcp import behaviour, consent, mission, review_binding, review_reply
from g6.self_maintenance.envelope import StopUnreadable
from g6.self_maintenance.usage import ProviderStop
from g6.workforce import repo_tests
from tests.harness.test_intent_repair2 import STOP_TYPES, _asks_first, _caught, _names, _reraises

TOKEN = "review-fixture"
STOPS = {
    "execution": lambda: component_process.ExecutionInterrupted("operator stopped", stopped=True),
    "containment": lambda: component_process.ContainmentFailure("cleanup not confirmed"),
    "unreadable": lambda: StopUnreadable("cannot read STOP"),
    "provider": lambda: ProviderStop("provider stopped", "fixture"),
    "model_stop": lambda: LLMError("G6_E_STOPPED: person stopped"),
}
NONE = {"verdict": "none", "rule": "", "why": "", "test": "", "error": ""}


def _parser():
    return FakeLLM(default=json.dumps({"value": {**NONE, "why": "No issues"}}))


def _read(said, monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    parser = _parser()
    with semantic.request(llm=parser):
        got = loop.critic_report(said, proof=TOKEN)
    return got, parser


# --- 1 and 2: where a verdict counts, and that two of them disagreeing fail closed -----------------------------------

def test_python_dict_in_reproduction_is_not_a_critic_verdict(monkeypatch):
    test = 'def test_result():\n    assert api.review() == {"verdict": "none"}'
    said = f"READ: {TOKEN}\nRULE: preserve response\nVERDICT: defect\nWHY: response regressed\n```python\n{test}\n```\n"
    got, parser = _read(said, monkeypatch)
    assert got["verdict"] == "defect" and got["test"] == test and not parser.calls, got


def test_python_protocol_fixture_is_not_a_clean_critic_verdict(monkeypatch):
    said = (f"READ: {TOKEN}\nI cannot give a reliable verdict; here is the response fixture.\n```python\n"
            f"def test_response():\n    expected = {json.dumps(NONE)}\n    assert api.review() == expected\n```\n")
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    with semantic.request(llm=None):
        got = loop.critic_report(said, proof=TOKEN)
    assert got["verdict"] not in loop.READ_VERDICTS, got


@pytest.mark.parametrize("fence", ["", "py", "text"])
def test_json_in_any_code_fence_but_json_is_not_a_verdict(monkeypatch, fence):
    said = f"READ: {TOKEN}\nSome thoughts.\n```{fence}\n{json.dumps({**NONE, 'read': TOKEN})}\n```\n"
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    with semantic.request(llm=None):
        got = loop.critic_report(said, proof=TOKEN)
    assert got["verdict"] not in loop.READ_VERDICTS, got


def test_control_a_json_fence_is_the_protocols_position(monkeypatch):
    stated = {**NONE, "verdict": "defect", "rule": "r", "why": "w", "test": "def test_a():\n    assert 0"}
    got, parser = _read(f"READ: {TOKEN}\n```json\n{json.dumps(stated)}\n```\n", monkeypatch)
    assert got == stated and not parser.calls


def test_an_unclosed_python_fence_hides_nothing_after_it(monkeypatch):
    said = f"READ: {TOKEN}\nVERDICT: defect\n```python\nfixture = {json.dumps(NONE)}\n"
    got, _ = _read(said, monkeypatch)
    assert got["verdict"] == "defect", got


def test_markdown_defect_cannot_be_reinterpreted_as_clean(monkeypatch):
    got, parser = _read(f"READ: {TOKEN}\n**Verdict:** defect\nThe changed result is wrong.\n", monkeypatch)
    assert got["verdict"] == "defect" and not parser.calls, got


def test_markdown_defect_keeps_its_reproduction(monkeypatch):
    test = "def test_a():\n    assert add(1, 2) == 3"
    got, parser = _read(f"READ: {TOKEN}\n**Verdict:** Defect\n```python\n{test}\n```\n", monkeypatch)
    assert got["verdict"] == "defect" and got["test"] == test and not parser.calls, got


@pytest.mark.parametrize("said", [
    f"READ: {TOKEN}\nVERDICT: none\nVERDICT: defect\nWHY: result regressed\n```python\ndef test_r():\n    assert False\n```\n",
    f"READ: {TOKEN}\nVERDICT: defect\nVERDICT: none\n",
    f"READ: {TOKEN}\n**Verdict:** none\nVERDICT: defect\n",
    f"READ: {TOKEN}\nVERDICT: none\n```json\n" + json.dumps({**NONE, "verdict": "defect", "rule": "r", "why": "w"}) + "\n```\n",
], ids=["lines_none_then_defect", "lines_defect_then_none", "markdown_and_line", "line_and_json_fence"])
def test_conflicting_stated_verdicts_fail_closed(monkeypatch, said):
    got, parser = _read(said, monkeypatch)
    assert got["verdict"] not in loop.READ_VERDICTS and not parser.calls, got


def test_control_agreeing_verdicts_stand(monkeypatch):
    got, parser = _read(f"READ: {TOKEN}\n**Verdict:** none\nVERDICT: none\nWHY: fine\n", monkeypatch)
    assert got["verdict"] == "none" and not parser.calls, got


# --- 3: stops on the premortem, confirmation and application path ----------------------------------------------------

def test_premortem_propagates_stop_while_reading_confirmed_standard(monkeypatch):
    root = Path.cwd()
    error = STOPS["execution"]()
    calls = 0

    def checked(repo):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return root

    monkeypatch.setattr(repo_tests, "checked_repo", checked)
    monkeypatch.setattr(repo_tests, "_git", lambda *a, **k: b"calc.py\0")
    monkeypatch.setattr(cs, "build", lambda *a, **k: {"standard": [], "unknowns": [], "question": None,
                                                       "model_calls": 0, "mechanisms": []})
    with semantic.request(llm=None), pytest.raises(type(error)) as caught:
        reply = mission.premortem(str(root), "Fix add(a, b).")
        pytest.fail(f"stop swallowed: {reply['ok']=}, {reply['standard']=}")
    assert caught.value is error


def test_support_semantic_stop_is_not_treated_as_missing_knowledge(monkeypatch):
    from g6._context_engine import _scope, _support
    from g6.mcp import knowledge

    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(_scope, "host_binding", lambda: None)
    monkeypatch.setattr(_scope, "access", lambda *a: None)
    monkeypatch.setattr(knowledge, "_destination", lambda: "local")
    error = STOPS["execution"]()

    def stopped_model(system, user):
        raise error

    monkeypatch.setattr(_support, "support_for", lambda acc, candidate: _support.conflict_signal(
        "Permission is granted", "Permission is denied", False))
    with semantic.request(llm=FakeLLM(stopped_model)), pytest.raises(type(error)) as caught:
        got = behaviour._with_knowledge([SimpleNamespace(to_dict=lambda: {})])
        pytest.fail(f"semantic stop swallowed; returned {len(got)} candidate(s)")
    assert caught.value is error


def site_identity_of(monkeypatch, raise_):
    monkeypatch.setattr(repo_tests, "checked_repo", raise_)
    behaviour.identity_of("fixture", "Fix add(a, b).")


def site_enforced(monkeypatch, raise_):
    monkeypatch.setattr(repo_tests, "checked_repo", raise_)
    behaviour.enforced("fixture", "Fix add(a, b).")


def site_shared(monkeypatch, raise_):
    monkeypatch.setattr(cloud_consent, "record_sources", lambda read: None)
    monkeypatch.setattr(cloud_consent, "current_capture", lambda: SimpleNamespace(client=None, supplied=()))
    monkeypatch.setattr(cloud_consent, "refuse_unshared", raise_)
    behaviour._shared(())


def site_designated(monkeypatch, raise_):
    from g6._context_engine import _scope, _support

    monkeypatch.setattr(_scope, "host_binding", lambda: None)
    monkeypatch.setattr(_scope, "access", lambda *a: None)
    monkeypatch.setattr(_support, "designate", raise_)
    behaviour._designated(None, None)


def site_binding_matches(monkeypatch, raise_):
    review_binding.matches(SimpleNamespace(with_suffix=raise_), "a" * 64)


def site_reply_save(monkeypatch, raise_):
    monkeypatch.setattr(storage, "state_path", raise_)
    review_reply.save({}, "job")


def site_reply_record(monkeypatch, raise_):
    monkeypatch.setattr(storage, "state_path", raise_)
    review_reply.record("job", {}, ())


def site_reply_carried(monkeypatch, raise_):
    monkeypatch.setattr(storage, "state_path", raise_)
    review_reply.carried("job")


def site_consent_dialog(monkeypatch, raise_):
    async def elicit_form(**kwargs):
        raise_()

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="r")
    asyncio.run(consent.elicit(ctx, "Confirm?", {"type": "object"}))


SITES = [site_identity_of, site_enforced, site_shared, site_designated, site_binding_matches, site_reply_save,
         site_reply_record, site_reply_carried, site_consent_dialog]


@pytest.mark.parametrize("stop", sorted(STOPS))
@pytest.mark.parametrize("site", SITES, ids=lambda site: site.__name__.removeprefix("site_"))
def test_trusted_stop_reaches_the_door_on_the_confirmation_path(monkeypatch, site, stop):
    error = STOPS[stop]()

    def raise_(*args, **kwargs):
        raise error

    with pytest.raises(type(error)) as caught:
        site(monkeypatch, raise_)
    assert caught.value is error


#: The premortem -> confirmation -> application path: g6_premortem builds and shows the candidate standard
#: (mission, candidate_standard, intent_engine.elicitor, mcp.behaviour); the person's answer is asked, read, stored and
#: published (behaviour_question, question_store, confirmed_expectation, standard_publication, the knowledge support
#: reader, review_binding, review_reply, the MCP dialog); and the review applies it (patch_review, confirmed_checks,
#: executable_scenario, verification_templates). In these modules every handler that can catch a trusted stop lets
#: it through, in any try: a door that names the stop it handles (review_reply.review) is the only exception.
PATH = ("g6.mcp.mission", "g6.harness.candidate_standard", "g6.intent_engine.elicitor", "g6.mcp.behaviour",
        "g6.harness.behaviour_question", "g6.harness.question_store", "g6.harness.confirmed_expectation",
        "g6.harness.standard_publication", "g6._context_engine._support", "g6.mcp.review_binding",
        "g6.mcp.review_reply", "g6.mcp.consent", "g6.harness.patch_review", "g6.harness.confirmed_checks",
        "g6.harness.executable_scenario", "g6.harness.verification_templates")


@pytest.mark.parametrize("name", PATH)
def test_no_handler_on_the_confirmation_path_swallows_a_trusted_stop(name):
    module = importlib.import_module(name)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names, swallowed = _names(module, tree), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        through: set[type] = set()
        for handler in node.handlers:
            caught = _caught(handler, names)
            reaching = [stop for stop in STOP_TYPES if stop not in through and issubclass(stop, caught)]
            through.update(reaching)
            door = all(any(issubclass(kind, stop) for stop in STOP_TYPES[:2]) for kind in caught)
            if reaching and not door and not _reraises(handler) and not _asks_first(handler):
                swallowed.append((handler.lineno, [stop.__name__ for stop in reaching]))
    assert swallowed == [], f"{name}: a handler catches a trusted stop it must let through: {swallowed}"


# --- 4: a confirmed answer replaces every supplied instance of its candidate ------------------------------------------

SOURCES = {"calc.py": "def add(a, b):\n    return a + b\n"}
TASK = "Fix add(a, b)."
PROPOSAL = {"text": "Opposite arguments cancel", "kind": "example", "function": "calc.add",
            "cases": [{"args": [-2, 2], "returns": 0}]}


def _thin_review(monkeypatch):
    root = Path("fixture")
    monkeypatch.setattr(repo_tests, "checked_repo", lambda *a: root)
    monkeypatch.setattr(repo_tests, "snapshot", lambda *a: {})
    monkeypatch.setattr(repo_tests, "change", lambda *a: SimpleNamespace(repo=root))
    monkeypatch.setattr(cs, "sources", lambda *a: SOURCES)
    monkeypatch.setattr(cs, "baseline", lambda *a: SOURCES)
    monkeypatch.setattr(patch_review, "_import_roots", lambda *a: ())
    monkeypatch.setattr(outcome_history, "retained_checks", lambda *a: ([], {}, []))
    monkeypatch.setattr(vs, "placements", lambda root, checks: {c.id: c.file for c in checks})
    monkeypatch.setattr(vs, "bind_execution", lambda change, before, **k: before)
    seen = {}

    def assess(*args, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "assessment": {"requirements": [], "completion": {"activities": []}, "report": "fixture"}}

    monkeypatch.setattr(patch_review, "_assess", assess)
    return seen


def _model_candidate():
    with semantic.request(llm=FakeLLM(default=json.dumps({"value": [PROPOSAL]}))):
        found = cs.from_sources(SOURCES, TASK, repo="fixture")
    return next(c for c in found["standard"] if c["origin"] == "model_proposal")


def _confirmed(candidate):
    raw = {"requirement_id": candidate["id"], "function": "calc.add", "args": [-2, 2],
           "outcomes": {"A": {"returns": 0}, "B": {"raises": "TypeError"}}, "cited": TASK,
           "evidence": ["calc.py: defines add"]}
    one, = bq.caller_candidates(TASK, [raw])
    question, rule = one.questions("b" * 64)
    reading = bq.read("accept", {"choice": "B", "name": "Fixture"}, question, rule, answered_at="2026-11-19T00:00:00Z")
    return ExpectationStandard().with_expectation(ConfirmedExpectation(question, reading.answer))


def _scheduled(seen, requirement):
    return [one.bindings["cases"] for one in seen["instances"] if one.requirement_id == requirement]


def test_renamed_advertised_check_does_not_run_unconfirmed_alternative(monkeypatch):
    seen = _thin_review(monkeypatch)
    candidate = _model_candidate()
    arguments = json.loads(json.dumps(candidate["cheapest_check"]["arguments"]))
    arguments["template_instances"][0]["id"] = "old-alternative"
    with semantic.request(llm=None):
        reply = patch_review.review_patch(**arguments, confirmed=_confirmed(candidate))
    shown = next(c for c in reply["assessment"]["standard"] if c["id"] == candidate["id"])
    assert _scheduled(seen, candidate["id"]) == [shown["cases"]] == [[{"args": [-2, 2], "raises": "TypeError"}]]
    replaced, = [one for one in seen["candidate_skipped"] if one["origin"] == "caller"]
    assert replaced["check"] == "old-alternative"


def test_a_renamed_copy_of_an_unconfirmed_candidates_check_runs_once(monkeypatch):
    seen = _thin_review(monkeypatch)
    candidate = _model_candidate()
    arguments = json.loads(json.dumps(candidate["cheapest_check"]["arguments"]))
    arguments["template_instances"].append({**arguments["template_instances"][0], "id": "same-case-renamed"})
    with semantic.request(llm=None):
        patch_review.review_patch(**arguments)
    assert _scheduled(seen, candidate["id"]) == [[{"args": [-2, 2], "returns": 0}]]


def test_control_another_case_for_an_unconfirmed_candidate_still_runs(monkeypatch):
    seen = _thin_review(monkeypatch)
    candidate = _model_candidate()
    arguments = json.loads(json.dumps(candidate["cheapest_check"]["arguments"]))
    other = json.loads(json.dumps(arguments["template_instances"][0]))
    other.update(id="another-case")
    other["bindings"]["cases"] = [{"args": [3, -3], "returns": 0}]
    arguments["template_instances"].append(other)
    with semantic.request(llm=None):
        patch_review.review_patch(**arguments)
    assert sorted(_scheduled(seen, candidate["id"]), key=json.dumps) == sorted(
        [[{"args": [-2, 2], "returns": 0}], [{"args": [3, -3], "returns": 0}]], key=json.dumps)
