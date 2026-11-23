# SPDX-License-Identifier: Apache-2.0
"""One reservation and its host receipt consume one request of the run."""
import ast
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud
from g6.core.consent_callers import CallerWrapper
from g6.core.llm import FakeLLM, FallbackCaller, LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from tests.harness.test_completion_review2_on_disk import _left_out, _read, disk_run as disk_run


class ProcessDied(BaseException):
    pass


class RefusedHost(CallerWrapper):
    def complete(self, *args, **kwargs):
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: send refused before admission"))


REQUEST_ENTRIES = ["prepare-scout", "advance-scout", "advance-review", "final-review"]
ENDING_ENTRIES = ["post-loop-report", "review-end", "withdrawn-review"]


@pytest.mark.parametrize("entry", REQUEST_ENTRIES)
@pytest.mark.parametrize("outcome", ["confirmed", "refused-before", "refused-after", "crashed", "fallback",
                                     "fallback-refused"])
def test_every_request_path_counts_host_receipts_once(disk_run, key_file, monkeypatch, entry, outcome):
    """Keep real storage, leases and flushes; substitute only scout/host execution."""
    repo, base, session = disk_run
    if entry == "advance-review":
        key_file("G6_LOOP_MODE=review\nG6_LOOP_SCOUTS=before_fix\n")
    loop._save(session, {loop.ATTEMPTED: 0, "sets": {}}, sources=())
    received = []

    def receive(system, user):
        received.append((system, user))
        retained = _read(session)
        # The reservation is already on disk when the fake host receives the request.
        assert retained[loop.SENT] == retained[loop.ATTEMPTED] == len(received)
        if outcome == "crashed":
            raise ProcessDied("host died after receiving the request")
        if outcome == "refused-after":
            raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: next send refused"))
        if outcome == "fallback-refused" or (outcome == "fallback" and len(received) % 2):
            raise LLMError("first host failed after receiving the request")
        return "No runnable review."

    caller = FakeLLM(receive)
    if outcome == "fallback":
        caller = FallbackCaller([caller, FakeLLM(receive)], names=["first", "second"])
    if outcome == "fallback-refused":
        caller = FallbackCaller([caller, RefusedHost(FakeLLM())], names=["first", "refused"])
    if outcome == "refused-before":
        caller = RefusedHost(caller)

    def scout(change, task, text, where, host, deadline):
        if entry != "final-review":
            caller.complete("scout", text)
        return _left_out(where)

    def final_review(*args, **kwargs):
        report = caller.complete("final", "review") if entry == "final-review" else "No probe set ran."
        return {"assessment": {"report": report}}

    monkeypatch.setattr(loop, "_scout", scout)
    monkeypatch.setattr(loop, "review_patch", final_review)

    def invoke():
        if entry == "prepare-scout":
            return loop.prepare(repo, "double returns twice its argument", base, session, scout=object())
        return loop.advance(repo, "double returns twice its argument", base, session, scout=object(),
                            critic=caller if entry == "advance-review" else None)

    if outcome == "crashed":
        with pytest.raises(ProcessDied, match="host died"):
            invoke()
    elif "refused" in outcome and entry == "prepare-scout":
        with pytest.raises(LLMError, match=cloud.REQUIRED):
            invoke()
    else:
        result = invoke()
        if "refused" in outcome:
            assert result.code == cloud.REQUIRED
    retained = _read(session)
    assert retained[loop.SENT] == retained[loop.ATTEMPTED] == len(received)
    if outcome == "refused-before":
        assert received == []
    else:
        assert received
    if outcome == "fallback":
        assert len(received) >= 2 and len(received) % 2 == 0


def test_worker_and_resumed_requests_each_consume_one_reservation(disk_run):
    _, _, session = disk_run
    received = []

    def receive(system, user):
        received.append(user)
        return "done"

    caller = FakeLLM(receive)
    state = {}
    with loop._writing(session), loop._run_count(session, state, fresh=True):
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda i: caller.complete("worker", str(i)), range(6)))
        loop._save(session, state)
    state = loop._load(session)
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        caller.complete("resumed", "seventh")
        loop._save(session, state)
    retained = _read(session)
    assert retained[loop.SENT] == retained[loop.ATTEMPTED] == len(received) == 7


def test_reservation_does_not_increment_a_live_load_result_twice(disk_run, monkeypatch):
    """A reader may return the operation's dict; reservation must not pre-edit it."""
    _, _, session = disk_run
    state = {}
    monkeypatch.setattr(loop, "_load", lambda _: state)
    with loop._run_count(session, state, fresh=True):
        note_model_call()
    assert state[loop.SENT] == state[loop.ATTEMPTED] == 1
    assert _read(session)[loop.SENT] == 1


def test_reservation_uses_the_two_argument_save_seam(disk_run, monkeypatch):
    _, _, session = disk_run
    real_save = loop._save

    def save(folder, state):
        real_save(folder, state)

    monkeypatch.setattr(loop, "_save", save)
    state = {}
    with loop._writing(session), loop._run_count(session, state, fresh=True):
        note_model_call()
    assert _read(session)[loop.SENT] == state[loop.SENT] == 1


FINAL_REVIEW_SITES = {
    ("_step", "_report", 0): ("final-review",),
    ("_step", "_report", 1): ("post-loop-report",),
    ("_step", "_review_end", 0): ("withdrawn-review",),
    ("_step", "_review_end", 1): ("review-end",),
    ("_report", "_final_review", 0): ("final-review", "post-loop-report"),
    ("_review_end", "_final_review", 0): ("review-end", "withdrawn-review"),
}


@pytest.mark.parametrize("entry", ENDING_ENTRIES)
@pytest.mark.parametrize("prior_sends", [0, 2])
@pytest.mark.parametrize("outcome", ["required-before", "required-after", "state-before", "state-after",
                                     "ordinary-error", "confirmed"])
@pytest.mark.parametrize("row_delivery", ["written", "owed"])
def test_final_review_ends_through_step_consent_guard(
        disk_run, key_file, monkeypatch, entry, prior_sends, outcome, row_delivery):
    """An ending review must retain its debt and count, even when sharing stops."""
    repo, base, session = disk_run
    task = "double returns twice its argument"
    received, ending_attempts, probe_runs = [], [], []
    state = {loop.ATTEMPTED: 0, "sets": {}}
    with loop._writing(session), loop._run_count(session, state, fresh=True):
        caller = FakeLLM(lambda system, user: received.append(user) or "earlier reply")
        for _ in range(prior_sends):
            caller.complete("earlier", "earlier request")
        loop._save(session, state, sources=())

    review = {"n": 1, "patch": loop._patch_now(repo, base), "verdict": "none", "results": {}, "file": "t.py",
              "rule": "", "why": "No defect found.", "test": "", "told": "", "failed": {}}
    if entry == "post-loop-report":
        state["sets"]["scout"] = {
            "left_out": "", "where": str(session / "sets" / "scout"), "refused": [], "when": loop.BEFORE,
            "at_base": {"exit": 0, "results": {"test_double": "passed"}, "texts": {}},
        }

        def run_set(*args, **kwargs):
            probe_runs.append("ran")
            return loop.ProbeRun(0, {"test_double": "passed"}, {})

        monkeypatch.setattr(loop, "run_set", run_set)
    else:
        key_file("G6_LOOP_MODE=review\nG6_LOOP_SCOUTS=before_fix\n")
        if entry == "withdrawn-review":
            state.update(reviews=[review], rounds=[{"tree": "old tree", "failing": [], "text": ""}])
            (repo / "mod.py").write_text("def double(x):\n    return x + 2\n", encoding="utf-8")
        else:
            def review_round(change, task, critic, shipped, folder, reviews, deadline):
                reviews.append(review)
                return set(), ""

            monkeypatch.setattr(loop, "review_round", review_round)
    with loop._writing(session):
        loop._save(session, state, sources=())
    monkeypatch.setattr(loop, "_scout", lambda change, task, text, where, host, deadline: _left_out(where))

    # Fail only the log's open; state/mark publication and all delivery logic stay real.
    if row_delivery == "owed":
        real_open = loop.os.open
        log_path = loop.state_path(*loop.FAILURES)

        def open_log(path, *args, **kwargs):
            if Path(path) == log_path:
                raise OSError("row log temporarily unavailable")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(loop.os, "open", open_log)

    refusal_code = cloud.STATE_UNAVAILABLE if outcome.startswith("state") else cloud.REQUIRED

    def receive(system, user):
        received.append(user)
        saved = _read(session)
        assert saved[loop.SENT] == saved[loop.ATTEMPTED] == len(received)
        if outcome.endswith("after"):
            raise cloud._refusal(LLMError(f"{refusal_code}: final send refused"))
        if outcome == "ordinary-error":
            raise LLMError("ordinary final review failure")
        return "Final review completed."

    caller = FakeLLM(receive)
    if outcome.endswith("before"):
        class RefusedFinal(CallerWrapper):
            def complete(self, *args, **kwargs):
                raise cloud._refusal(LLMError(f"{refusal_code}: final send refused"))

        caller = RefusedFinal(caller)

    def final_review(*args, **kwargs):
        ending_attempts.append("final")
        saved = _read(session)
        if entry != "post-loop-report":
            assert json.loads(saved["row_due"])["session"] == session.name
            assert loop.state_path(*loop.OWING, session.name).is_file()
        return {"assessment": {"report": caller.complete("final", "final request")}}

    monkeypatch.setattr(loop, "review_patch", final_review)

    def invoke():
        return loop.advance(repo, task, base, session, scout=object(),
                            critic=object() if entry == "review-end" else None)

    if outcome == "ordinary-error":
        with pytest.raises(LLMError, match="ordinary final review failure"):
            invoke()
        saved = _read(session)
        assert not saved.get("done")
        if entry != "post-loop-report":
            assert "row_due" in saved
        assert saved[loop.SENT] == saved[loop.ATTEMPTED] == len(received) == prior_sends + 1
        assert ending_attempts == ["final"]
        return

    result = invoke()  # Before the fix, every refusal here escapes as a raw LLMError.
    saved = _read(session)
    expected_total = prior_sends + (not outcome.endswith("before"))
    assert saved[loop.SENT] == saved[loop.ATTEMPTED] == len(received) == expected_total
    assert ending_attempts == ["final"], "a refused review must never be retried"
    assert result.action == loop.STOP and saved["done"]
    assert (session / "report.md").read_text(encoding="utf-8") == result.text
    if entry == "post-loop-report":
        assert probe_runs == ["ran"]
    refused = outcome != "confirmed"
    expected_code = (loop.CONSENT_STATE_AFTER_SEND if refusal_code == cloud.STATE_UNAVAILABLE and expected_total
                     else refusal_code) if refused else (
                         loop.NO_CLEAN_REVIEW if entry == "withdrawn-review" else "")
    assert result.code == saved["code"] == expected_code
    if refused:
        assert result.text == saved["stop"] == loop._consent_stop(expected_total)
        assert result.ending == "untested"
    # A successful scout-only report has no row; review endings and all refusals do.
    if refused or entry != "post-loop-report":
        mark = loop.state_path(*loop.OWING, session.name)
        if row_delivery == "owed":
            assert mark.is_file()
            assert saved["row"] == "OSError: row log temporarily unavailable"
            row = json.loads(saved["row_due"])
        else:
            assert not mark.exists()
            assert "row_due" not in saved and "row" not in saved
            rows = [json.loads(line) for line in loop.state_path(*loop.FAILURES).read_text(encoding="utf-8").splitlines()]
            own = [row for row in rows if row["session"] == session.name]
            assert len(own) == 1
            row = own[0]
        assert row["session"] == session.name
        assert row["cause"] == ("cloud_consent_missing" if refused else
                                loop.CAUSES[loop.WITHDRAWN] if entry == "withdrawn-review" else loop.FOUND_NOTHING)
    else:
        assert "row_due" not in saved and "row" not in saved
        assert not loop.state_path(*loop.OWING, session.name).exists()
        assert not loop.state_path(*loop.FAILURES).is_file()


def test_final_review_call_site_inventory_requires_consent_guard():
    """New ending paths must be inventoried and guarded on every reachable route."""
    module = ast.parse(Path(loop.__file__).read_text(encoding="utf-8"))
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    parents = {child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)}
    targets = {"_final_review", "_report", "_review_end"}
    sites, calls = {}, {}
    for name, function in functions.items():
        ordinals = Counter()
        calls[name] = []
        for call in sorted((node for node in ast.walk(function) if isinstance(node, ast.Call)),
                           key=lambda node: (node.lineno, node.col_offset)):
            if not isinstance(call.func, ast.Name):
                continue
            callee = call.func.id
            calls[name].append((call, callee))
            if callee in targets:
                sites[call] = (name, callee, ordinals[callee])
                ordinals[callee] += 1

    def guarded(call):
        ancestors = []
        node = call
        while node in parents:
            child, node = node, parents[node]
            if isinstance(node, ast.FunctionDef):
                break
            ancestors.append((node, child))
        watched = any(isinstance(node, ast.With) and child in node.body and any(
            isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "watching_consent" for item in node.items) for node, child in ancestors)
        caught = any(isinstance(node, ast.Try) and child in node.body and any(
            isinstance(handler.type, ast.Name) and handler.type.id == "LLMError" for handler in node.handlers)
                     for node, child in ancestors)
        return watched and caught

    reached, unguarded, visited = set(), [], set()

    def visit(name, inherited=False):
        if (name, inherited) in visited:
            return
        visited.add((name, inherited))
        for call, callee in calls[name]:
            protected = inherited or guarded(call)
            if call in sites:
                reached.add(sites[call])
                if not protected:
                    unguarded.append((sites[call], call.lineno))
            if callee in functions:
                visit(callee, protected)

    visit("advance")
    visit("prepare")
    assert reached == set(FINAL_REVIEW_SITES), sorted(reached)
    assert all(set(entries) <= set(REQUEST_ENTRIES + ENDING_ENTRIES) for entries in FINAL_REVIEW_SITES.values())
    assert not unguarded, f"final-review paths outside the consent guard: {unguarded}"
