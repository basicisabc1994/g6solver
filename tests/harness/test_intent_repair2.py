# SPDX-License-Identifier: Apache-2.0
"""Repair 2 of claude/wire-intent (Codex review 2): trusted stops reach the door from every handler on the critic and
review path; a critic's explicit verdict is final in any layout; a confirmation governs supplied candidate checks too,
and a confirmed model candidate's advertised call stays admissible. FakeLLM and monkeypatched seams only."""
from __future__ import annotations

import ast
import asyncio
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import component_process, semantic, storage
from g6.core.llm import FakeLLM, LLMError
from g6.harness import behaviour_question as bq, behaviour_runner, candidate_standard as cs
from g6.harness import completion_loop as loop, executable_scenario, outcome_history, patch_review
from g6.harness import verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
from g6.learning_history import block as history_block
from g6.mcp import learning_history as history_mcp
from g6.self_maintenance.envelope import StopUnreadable
from g6.self_maintenance.usage import ProviderStop
from g6.workforce import receipt_store, repo_tests

TASK = "Fix add(a, b)."
SOURCES = {"calc.py": "def add(a, b):\n    return a + b\n"}
PROPOSAL = {"text": "Opposite arguments cancel", "kind": "example", "function": "calc.add",
            "cases": [{"args": [-2, 2], "returns": 0}]}
STOPS = {
    "execution": lambda: component_process.ExecutionInterrupted("stopped"),
    "containment": lambda: component_process.ContainmentFailure("cleanup not confirmed"),
    "unreadable": lambda: StopUnreadable("cannot read STOP"),
    "provider": lambda: ProviderStop("provider stopped", "fixture"),
    "model_stop": lambda: LLMError("G6_E_STOPPED: person stopped"),
}
CHECK = vs.ProposedCheck(id="retained-edge", requirement_id="R1", intent="preserve", file="test_edge.py",
                         source="def test_edge():\n    assert True\n")


# --- (a) every handler on the critic and review path lets a trusted stop through ---------------------------------------

def _critic_pack(monkeypatch):
    monkeypatch.setattr(loop, "_shown", lambda *args: SimpleNamespace(masked=""))
    monkeypatch.setattr(loop, "_pack", lambda change, shown, task, proof: f"READ: {proof}\n")
    monkeypatch.setattr(loop, "_masked_pack", lambda change, shown, task, proof: (f"READ: {proof}\n", False))
    return SimpleNamespace(repo=Path("fixture"), sources=("calc.py",))


def site_semantic_resolver(monkeypatch, tmp_path, raise_):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(semantic, "resolve_caller", raise_)
    with semantic.request():
        semantic.decide("refusal", "an ordinary answer", False)


def site_semantic_worker(monkeypatch, tmp_path, raise_):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    with semantic.request(llm=FakeLLM(lambda system, user: raise_())):
        semantic.decide("refusal", "an ordinary answer", False)


def site_critic_parser(monkeypatch, tmp_path, raise_):
    """completion_loop.run_critic: the reply has no explicit verdict, so the semantic parser reads it and stops."""
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    change = _critic_pack(monkeypatch)
    critic = FakeLLM(lambda system, user: user + "I cannot determine the result.\n")
    with semantic.request(llm=FakeLLM(lambda system, user: raise_())):
        loop.run_critic(change, TASK, critic, "review", tmp_path, patch=None)


def site_critic_send(monkeypatch, tmp_path, raise_):
    """completion_loop.run_critic: the critic's own send stops."""
    change = _critic_pack(monkeypatch)
    loop.run_critic(change, TASK, FakeLLM(lambda system, user: raise_()), "review", tmp_path, patch=None)


def site_review_round_history(monkeypatch, tmp_path, raise_):
    """completion_loop.review_round: the retained check's history write."""
    monkeypatch.setattr(repo_tests, "diff_read", lambda change: SimpleNamespace(text="diff"))
    monkeypatch.setattr(loop, "run_critic", lambda *args, **kwargs: {"verdict": "none", "rule": "", "why": "",
                                                                     "test": "", "error": ""})
    monkeypatch.setattr(outcome_history, "retained_checks",
                        lambda *args: ([CHECK], {CHECK.id: vs.Origin(label="retained")}, []))
    monkeypatch.setattr(repo_tests, "snapshot", lambda repo: {})
    monkeypatch.setattr(vs, "placements", lambda root, checks: {})
    monkeypatch.setattr(vs, "search", lambda *args, **kwargs: vs.Search(
        outcomes=[(CHECK, "n", {}, {}, "passed", "retained", False)]))
    monkeypatch.setattr(outcome_history, "record_check", raise_)
    loop.review_round(SimpleNamespace(repo=tmp_path), TASK, None, SimpleNamespace(critic="", sources=()), tmp_path, [],
                      float("inf"))


def site_reproduction_history(monkeypatch, tmp_path, raise_):
    """completion_loop.record_reproductions: the K reproduction's history write."""
    monkeypatch.setattr(loop, "_checks", lambda probes, repo, only="": ([CHECK], {CHECK.id: "test_edge.py"}, []))
    monkeypatch.setattr(vs, "_run_check", lambda *args, **kwargs: {"side": "base"})
    monkeypatch.setattr(vs, "reading", lambda ran, node: ran)
    monkeypatch.setattr(vs, "_guarded", lambda change, ran, found: {
        "result": "passed" if ran.get("side") == "base" else "failed"})
    monkeypatch.setattr(outcome_history, "record_check", raise_)
    loop.record_reproductions(SimpleNamespace(repo=tmp_path), tmp_path, deadline=float("inf"),
                              _observed={CHECK.id: {"receipt": "r", "side": "patch"}})


def site_scout_host(monkeypatch, tmp_path, raise_):
    """completion_loop._scout: the scout's session."""
    @contextmanager
    def copy(change, seconds):
        yield tmp_path / "copy"

    monkeypatch.setattr(loop, "_base_copy", copy)
    monkeypatch.setattr(loop, "_clock", lambda: 0.0)
    loop._scout(SimpleNamespace(repo=tmp_path), TASK, "{number} {title} {text}", tmp_path / "scout",
                lambda *args: raise_(), 10 ** 6)


def site_patch_now(monkeypatch, tmp_path, raise_):
    """completion_loop._patch_now: the closing read of the patch."""
    monkeypatch.setattr(repo_tests, "change", raise_)
    loop._patch_now(tmp_path, "HEAD")


def site_advance(monkeypatch, tmp_path, raise_):
    """completion_loop._advance_inner: the loop's own consent handler."""
    monkeypatch.setattr(loop, "_load", lambda session: {})
    monkeypatch.setattr(repo_tests, "checked_repo", lambda repo: tmp_path)
    monkeypatch.setattr(repo_tests, "change", lambda root, base: SimpleNamespace(repo=tmp_path, sources=("calc.py",)))
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "cegis", raise_)
    with semantic.request(llm=None):
        loop._advance_inner(tmp_path, TASK, "HEAD", tmp_path / "session", None, FakeLLM(default=""), "", True, "")


def site_search(monkeypatch, tmp_path, raise_):
    """verification_search.search: a check's own error handler."""
    monkeypatch.setattr(vs, "_run_check", raise_)
    vs.search(SimpleNamespace(repo=tmp_path, sources=[]), [CHECK], {CHECK.id: vs.placement(CHECK)},
              deadline=float("inf"), artifact="a" * 64)


CHANGE = SimpleNamespace(repo=Path("fixture"), base_commit="a" * 40)


def site_run_check(monkeypatch, tmp_path, raise_):
    """verification_search._run_check: the copy and run."""
    monkeypatch.setattr(repo_tests, "repository_copy", raise_)
    vs._run_check(CHANGE, "test_x.py", "def test_x():\n    pass\n", "test_x.py::test_x", at_base=False, timeout_s=5)


def site_run_check_receipt(monkeypatch, tmp_path, raise_):
    """verification_search._run_check: an owned observation's receipt."""
    monkeypatch.setattr(repo_tests, "repository_copy", lambda *args, **kwargs: nullcontext(tmp_path))
    monkeypatch.setattr(repo_tests, "_run", lambda *args, **kwargs: {"verdict": "passed"})
    monkeypatch.setattr(receipt_store, "_record_observation", raise_)
    source = behaviour_runner.ExecutableSource("def test_x():\n    pass\n", {"kind": "fixture"})
    vs._run_check(CHANGE, "test_x.py", source, "test_x.py::test_x", at_base=False, timeout_s=5)


def site_run_mutant(monkeypatch, tmp_path, raise_):
    """verification_search._run_mutant: the mutant's copy and run."""
    monkeypatch.setattr(repo_tests, "repository_copy", raise_)
    vs._run_mutant(CHANGE, "calc.py", "x = 1\n", ["test_x.py::test_x"], 5)


def site_repository_id(monkeypatch, tmp_path, raise_):
    """receipt_store.repository_id: the history's repository identity (outcome_history.read)."""
    monkeypatch.setattr(repo_tests, "_git", raise_)
    receipt_store.repository_id(tmp_path)


def site_repo_digest(monkeypatch, tmp_path, raise_):
    """repo_tests._digest: a file's digest, between checkpoints."""
    monkeypatch.setattr(component_process, "checkpoint", raise_)
    repo_tests._digest(tmp_path / "missing.py")


def site_run_at_base(monkeypatch, tmp_path, raise_):
    """repo_tests.run_at_base: the copy and run at the base commit."""
    monkeypatch.setattr(repo_tests, "inside", raise_)
    repo_tests.run_at_base(CHANGE, ["test_x.py"])


def site_review_stage(monkeypatch, tmp_path, raise_):
    """patch_review._stage: a search stage's own error."""
    patch_review._stage([], "fixture", raise_)


def site_review_authored(monkeypatch, tmp_path, raise_):
    """patch_review._authored: whether the patch wrote a stated check."""
    monkeypatch.setattr(patch_review, "_written", raise_)
    patch_review._authored("test_x.py", CHANGE, tmp_path)


def site_review_check_shas(monkeypatch, tmp_path, raise_):
    """patch_review._check_shas: each stated check's identity."""
    (tmp_path / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(patch_review, "id_of", raise_)
    patch_review._check_shas(tmp_path, ["test_x.py::test_x"])


def site_read_probes(monkeypatch, tmp_path, raise_):
    """behaviour_runner.read_probes: the observation custody's side file."""
    behaviour_runner.read_probes(SimpleNamespace(with_suffix=raise_), b"")


def site_scenario_load(monkeypatch, tmp_path, raise_):
    """executable_scenario.load: the held scenario record."""
    monkeypatch.setattr(storage, "state_path", raise_)
    executable_scenario.load(SimpleNamespace(sha256="a" * 64))


def site_history_list(monkeypatch, tmp_path, raise_):
    """learning_history.list: the block's read of the history."""
    monkeypatch.setattr(repo_tests, "checked_repo", raise_)
    history_block.LearningHistoryBlock().infer(history_block.HistoryInput(op="list", repo="fixture"))


def site_history_forget(monkeypatch, tmp_path, raise_):
    """learning_history.forget over MCP: the read before the dialog."""
    monkeypatch.setattr(repo_tests, "checked_repo", raise_)
    asyncio.run(history_mcp.forget_reply(None, json.dumps({"repo": "fixture"}),
                                         allow=frozenset({"learning_history.forget"})))


SITES = [site_semantic_resolver, site_semantic_worker, site_critic_parser, site_critic_send, site_review_round_history,
         site_reproduction_history, site_scout_host, site_patch_now, site_advance, site_search, site_run_check,
         site_run_check_receipt, site_run_mutant, site_repository_id, site_repo_digest, site_run_at_base,
         site_review_stage, site_review_authored, site_review_check_shas, site_read_probes, site_scenario_load,
         site_history_list, site_history_forget]


@pytest.mark.parametrize("stop", sorted(STOPS))
@pytest.mark.parametrize("site", SITES, ids=lambda site: site.__name__.removeprefix("site_"))
def test_trusted_stop_reaches_the_door_from_every_handler_on_the_review_path(monkeypatch, tmp_path, site, stop):
    error = STOPS[stop]()

    def raise_(*args, **kwargs):
        raise error

    with pytest.raises(type(error)) as caught:
        site(monkeypatch, tmp_path, raise_)
    assert caught.value is error


@pytest.mark.parametrize("stop", sorted(STOPS))
def test_one_predicate_names_every_trusted_stop(stop):
    assert semantic.trusted_stop(STOPS[stop]())
    assert not semantic.trusted_stop(LLMError("G6_E_TIMEOUT"))
    assert not semantic.trusted_stop(OSError("disk full"))



# --- the class: in every try that lets a stop through, every handler that could still catch one lets it through -----

#: Every module that holds a handler on the critic and review path, or one that lets an execution-control signal
#: through by type.
GUARDED = ("g6.core.semantic", "g6.harness.completion_loop", "g6.harness.verification_search",
           "g6.workforce.receipt_store", "g6.workforce.repo_tests", "g6.harness.patch_review",
           "g6.harness.behaviour_runner", "g6.harness.confirmed_checks", "g6.harness.executable_scenario",
           "g6.harness.verification_formal", "g6.harness.verification_templates", "g6.learning_history.block",
           "g6.mcp.learning_history")
STOP_TYPES = (component_process.ExecutionInterrupted, component_process.ContainmentFailure, StopUnreadable,
              ProviderStop, LLMError)


def _names(module, tree) -> dict:
    """The module's names, with those its functions import for themselves."""
    import importlib

    names = dict(vars(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                try:
                    found = getattr(importlib.import_module(node.module), alias.name)
                except AttributeError:
                    found = importlib.import_module(f"{node.module}.{alias.name}")
                names.setdefault(alias.asname or alias.name, found)
    return names


def _caught(handler: ast.ExceptHandler, names: dict) -> tuple[type, ...]:
    if handler.type is None:
        return (BaseException,)
    value = eval(compile(ast.Expression(handler.type), "<handler>", "eval"), names)  # noqa: S307
    return value if isinstance(value, tuple) else (value,)


def _reraises(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def _asks_first(handler: ast.ExceptHandler) -> bool:
    """The handler's first statement (after an import) is ``if trusted_stop(<its exception>)[ or ...]: raise``."""
    body = [one for one in handler.body if not isinstance(one, ast.ImportFrom)]
    first = body[0] if body else None
    if not isinstance(first, ast.If) or not (len(first.body) == 1 and isinstance(first.body[0], ast.Raise)
                                             and first.body[0].exc is None):
        return False
    test = first.test.values[0] if isinstance(first.test, ast.BoolOp) and isinstance(first.test.op, ast.Or) else first.test
    if (isinstance(test, ast.Attribute) and test.attr == "stopped" and isinstance(test.value, ast.Name)
            and test.value.id == handler.name):
        return True   # an ExecutionInterrupted that is a component window's own timeout, not a stop (repo_tests._git)
    return (isinstance(test, ast.Call) and getattr(test.func, "id", "") == "trusted_stop" and len(test.args) == 1
            and isinstance(test.args[0], ast.Name) and test.args[0].id == handler.name)


def _stop_aware(handlers, names) -> bool:
    return any(_asks_first(one) or _reraises(one) and any(issubclass(stop, _caught(one, names))
                                                          for stop in STOP_TYPES[:2]) for one in handlers)


@pytest.mark.parametrize("name", GUARDED)
def test_every_try_that_lets_a_stop_through_lets_every_stop_through(name):
    import importlib

    module = importlib.import_module(name)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names, swallowed = _names(module, tree), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try | ast.TryStar) or not _stop_aware(node.handlers, names):
            continue
        through: set[type] = set()
        for handler in node.handlers:
            caught = _caught(handler, names)
            reaching = [stop for stop in STOP_TYPES if stop not in through and issubclass(stop, caught)]
            through.update(reaching)   # whatever this handler does, a later one never sees these
            if reaching and not _reraises(handler) and not _asks_first(handler):
                swallowed.append((handler.lineno, [stop.__name__ for stop in reaching]))
    assert swallowed == [], f"{name}: a handler catches a trusted stop it must let through: {swallowed}"

# --- (b) an explicit verdict is final in any layout --------------------------------------------------------------------

TOKEN = "review fixture"
FAILED = {"verdict": "unreadable", "rule": "", "why": "Cannot read the patch", "test": "", "error": ""}
LAYOUTS = {
    "line": f"READ: {TOKEN}\nVERDICT: unreadable\nWHY: Cannot read the patch\n",
    "strict_json": json.dumps({"read": TOKEN, **FAILED}),
    "json_after_header": f"READ: {TOKEN}\n" + json.dumps(FAILED),
    "json_in_fence": f"READ: {TOKEN}\n```json\n{json.dumps(FAILED, indent=2)}\n```\n",
    "fenced_json_with_read": "```json\n" + json.dumps({"read": TOKEN, **FAILED}) + "\n```",
    "json_after_progress": "[codex] Turn started\n" + f"READ: {TOKEN}\n" + json.dumps(FAILED),
    "json_inline_after_header": f"READ: {TOKEN}\nMy answer: " + json.dumps(FAILED),
    "explicit_none_in_wrong_shape": f"READ: {TOKEN}\n" + json.dumps({"verdict": "none", "note": "extra field"}),
    "markdown_line": f"READ: {TOKEN}\n**Verdict:** unreadable\nI could not open the patch.\n",
    "line_and_json_disagree":f"READ: {TOKEN}\nVERDICT: none\nWHY: fine\n" + json.dumps(FAILED),
}


def _parser():
    return FakeLLM(default=json.dumps({"value": {"verdict": "none", "rule": "", "why": "No issues", "test": "",
                                                 "error": ""}}))


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_explicit_failure_in_any_layout_is_final_and_never_parsed(monkeypatch, layout):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    parser = _parser()
    with semantic.request(llm=parser):
        got = loop.critic_report(LAYOUTS[layout], proof=TOKEN)
    assert got["verdict"] not in loop.READ_VERDICTS, got
    assert loop._round_outcome({**got, "results": {}, "file": "test_critic.py"}) == "review_failed"
    assert not parser.calls, layout


def test_explicit_defect_after_a_header_keeps_its_test_and_is_never_parsed(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    stated = {"verdict": "defect", "rule": "keep sums", "why": "add drops b", "test": "def test_a():\n    assert 0",
              "error": ""}
    parser = _parser()
    with semantic.request(llm=parser):
        got = loop.critic_report(f"READ: {TOKEN}\n```json\n{json.dumps(stated)}\n```", proof=TOKEN)
    assert got == stated and not parser.calls


def test_explicit_json_without_the_quoted_token_is_unread(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    parser = _parser()
    with semantic.request(llm=parser):
        got = loop.critic_report(json.dumps({**FAILED, "verdict": "none"}), proof=TOKEN)
    assert got["verdict"] == "unread" and not parser.calls


def test_control_a_reply_with_no_explicit_verdict_is_read_by_the_model(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    parser = _parser()
    with semantic.request(llm=parser):
        got = loop.critic_report(f"READ: {TOKEN}\nI found nothing wrong with the change.\n", proof=TOKEN)
    assert parser.calls and got["verdict"] == "none"


def test_json_failure_verdict_with_read_header_cannot_be_reinterpreted(monkeypatch, tmp_path):
    """Codex review 2, finding 2, through run_critic."""
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    change = _critic_pack(monkeypatch)
    critic = FakeLLM(lambda system, user: user + json.dumps(FAILED))
    with semantic.request(llm=_parser()):
        result = loop.run_critic(change, TASK, critic, "review", tmp_path, patch=None)
    assert result["verdict"] not in loop.READ_VERDICTS, result


# --- (c) one source of truth for what a candidate schedules ------------------------------------------------------------

def thin_review(monkeypatch):
    root = Path("fixture")
    monkeypatch.setattr(repo_tests, "checked_repo", lambda repo: root)
    monkeypatch.setattr(repo_tests, "snapshot", lambda repo: {})
    monkeypatch.setattr(repo_tests, "change", lambda repo, base: SimpleNamespace(repo=root))
    monkeypatch.setattr(cs, "sources", lambda repo: SOURCES)
    monkeypatch.setattr(cs, "baseline", lambda *args: SOURCES)
    monkeypatch.setattr(patch_review, "_import_roots", lambda *args: ())
    monkeypatch.setattr(outcome_history, "retained_checks", lambda *args: ([], {}, []))
    monkeypatch.setattr(vs, "placements", lambda root, checks: {c.id: c.file for c in checks})
    monkeypatch.setattr(vs, "bind_execution", lambda change, before, **kwargs: before)
    seen = {}

    def assess(*args, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "assessment": {"requirements": [], "completion": {"activities": []}, "report": "fixture"}}

    monkeypatch.setattr(patch_review, "_assess", assess)
    return seen


def confirmed_model():
    with semantic.request(llm=FakeLLM(default=json.dumps({"value": [PROPOSAL]}))):
        found = cs.from_sources(SOURCES, TASK, repo="fixture")
    candidate = next(c for c in found["standard"] if c["origin"] == "model_proposal")
    raw = {"requirement_id": candidate["id"], "function": "calc.add", "args": [-2, 2],
           "outcomes": {"A": {"returns": 0}, "B": {"raises": "TypeError"}},
           "cited": TASK, "evidence": ["calc.py: defines add"]}
    one, = bq.caller_candidates(TASK, [raw])
    question, rule = one.questions("b" * 64)
    reading = bq.read("accept", {"choice": "B", "name": "Fixture"}, question, rule, answered_at="2026-11-19T00:00:00Z")
    held = ExpectationStandard().with_expectation(ConfirmedExpectation(question, reading.answer))
    return found, candidate, held


def scheduled(seen, requirement):
    return [one.bindings["cases"] for one in seen["instances"] if one.requirement_id == requirement]


def test_confirmed_model_cheapest_call_is_still_admitted(monkeypatch):
    """Codex review 2, finding 4: the call G6 advertises after the confirmation names an id it still admits."""
    seen = thin_review(monkeypatch)
    found, candidate, held = confirmed_model()
    after = cs.with_confirmed(found, held)
    stated = next(c for c in after["standard"] if c["id"] == candidate["id"])
    assert stated["status"] == "stated"
    with semantic.request(llm=None):
        reply = patch_review.review_patch(**stated["cheapest_check"]["arguments"], confirmed=held)
    assert seen, reply
    assert reply["ok"] is True
    shown = next(c for c in reply["assessment"]["standard"] if c["id"] == candidate["id"])
    assert shown["status"] == "stated"
    assert scheduled(seen, candidate["id"]) == [[{"args": [-2, 2], "raises": "TypeError"}]]


def test_confirmed_case_without_the_confirmation_is_not_admitted_under_the_old_id(monkeypatch):
    """The confirmation is what lets the confirmed case carry the proposal's id: without it, the digest binds."""
    seen = thin_review(monkeypatch)
    found, candidate, held = confirmed_model()
    stated = next(c for c in cs.with_confirmed(found, held)["standard"] if c["id"] == candidate["id"])
    with semantic.request(llm=None):
        reply = patch_review.review_patch(**stated["cheapest_check"]["arguments"])
    assert not seen and reply["completion_state"] == "blocked-escalated"


def test_saved_cheapest_call_does_not_schedule_unconfirmed_alternative(monkeypatch):
    """Codex review 2, finding 3: what runs is what the display shows."""
    seen = thin_review(monkeypatch)
    found, candidate, held = confirmed_model()
    with semantic.request(llm=None):
        reply = patch_review.review_patch(**candidate["cheapest_check"]["arguments"], confirmed=held)
    shown = next(c for c in reply["assessment"]["standard"] if c["id"] == candidate["id"])
    assert shown["cases"] == [{"args": [-2, 2], "raises": "TypeError"}]
    assert scheduled(seen, candidate["id"]) == [[{"args": [-2, 2], "raises": "TypeError"}]]
    replaced, = [one for one in seen["candidate_skipped"] if one["origin"] == "caller"]
    assert replaced["check"] == candidate["id"] + ".example" and replaced["result"] == "skipped"


def test_control_unconfirmed_saved_call_runs_the_proposal(monkeypatch):
    seen = thin_review(monkeypatch)
    found, candidate, _ = confirmed_model()
    with semantic.request(llm=None):
        patch_review.review_patch(**candidate["cheapest_check"]["arguments"])
    assert scheduled(seen, candidate["id"]) == [[{"args": [-2, 2], "returns": 0}]]


def test_control_modified_model_cases_do_not_pass_digest_binding(monkeypatch):
    seen = thin_review(monkeypatch)
    found, candidate, held = confirmed_model()
    arguments = json.loads(json.dumps(candidate["cheapest_check"]["arguments"]))
    arguments["template_instances"][0]["bindings"]["cases"][0]["returns"] = 99
    with semantic.request(llm=None):
        reply = patch_review.review_patch(**arguments, confirmed=held)
    assert not seen and reply["completion_state"] == "blocked-escalated"


def test_control_prose_does_not_invent_executable_examples():
    with semantic.request(llm=None):
        found = cs.from_sources(SOURCES, "Make add(a, b) sum signed inputs and reject invalid types.", repo="fixture")
    assert not any(item["cases"] for item in found["standard"])
    assert found["question"] is None
