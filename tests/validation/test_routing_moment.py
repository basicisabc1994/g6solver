# SPDX-License-Identifier: Apache-2.0
"""The moment-of-need kinds of the routing trials: ``call_before_final``, ``stuck_call`` and ``call_after_refusal``.

Each kind is read from a session's order of steps (edits, test runs, calls, the final message), and that order is read
from the client's own event log, for both clients. The logs here are synthetic: no client is started and no model is
called."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
T = score.Transcript
CLIENTS = ("claude_code", "codex")
BUDGET = {"seconds": 600, "calls": 20, "questions": 1}
SENSITIVE = json.dumps({"ok": False, "error": "[G6_E_SENSITIVE_COMPONENT_DENIED] patch_review.review runs repository "
                        "code; the host must allow it", "completion_state": "blocked-escalated"})
WITHHELD = json.dumps({"ok": False, "error": "[G6_E_CLOUD_CONSENT_REQUIRED] Results were withheld from a source not "
                       "shared with this provider. Withheld source identifiers: host_configuration.",
                       "completion_state": "blocked-escalated"})
STARTED = json.dumps({"ok": True, "job_id": "j1", "completion_state": "qualified-draft"})
# src/g6/mcp/cloud_consent.py: request_grant's granted result, wrapped by reply().
GRANTED = json.dumps({"ok": True, "result": {"status": "granted", "source": "host_configuration",
                                          "provider": "openai:codex"},
                      "completion_state": "qualified-draft", "reliability_label": "qualified-draft",
                      "warning_card": {"code": "G6_E_CLOUD_CONSENT"}, "evidence": []})
REVIEW = {"repo": "/work/shop", "task": "make total refuse a negative quantity"}


def _task(kind: str, **extra: Any) -> dict[str, Any]:
    polarity = {"negative": "negative", "missing_tool": "missing_tool"}.get(kind, "positive")
    task = {"id": f"moment.{kind}.1", "kind": kind, "polarity": polarity, "cohort": "moment", "fixture": "shop",
            "prompt": "Make the order total refuse a negative quantity, then tell me it is finished.",
            "budget": BUDGET, "permissions": ["patch_review.review"],
            "grants": ["agent_environment", "fixture", "workspace"],
            "moment": "the moment this task's rule names, read from the order of the session's steps",
            "fair": "why this is fair, stated before any run so that it cannot be tuned afterwards"}
    return {**task, **extra}


BEFORE = _task("before_done", expected_calls=[
    {"tool": "g6_review_patch", "args_include": {"repo": "shop"}, "args_present": ["task"]},
    {"tool": "g6_review_until_clean", "args_include": {"repo": "shop"}, "args_present": ["task", "session"]}],
    completion={"kind": "call_before_final"})
STUCK = _task("stuck", expected_calls=[
    {"tool": "g6_premortem", "args_include": {"repo": "shop"}, "args_present": ["task"]},
    {"tool": "g6_review_patch", "args_include": {"repo": "shop"}, "args_present": ["task", "proposed_checks"]}],
    completion={"kind": "stuck_call", "failed_test_runs": 2})
REFUSED = _task("after_refusal", permissions=[], expected_calls=[], completion={
    "kind": "call_after_refusal", "refusal": {"tools": ["g6_review_patch", "g6_review_until_clean"],
                                              "codes": ["G6_E_SENSITIVE_COMPONENT_DENIED"]},
    "tell_any": ["patch_review.review"]})
WITHHOLDING = _task("after_refusal", id="moment.after_refusal.2", expected_calls=[
    {"tool": "g6_cloud_grant", "args_include": {"source": "host_configuration"}, "args_present": ["provider"]}],
    completion={"kind": "call_after_refusal",
                "refusal": {"tools": ["g6_review_patch", "metaharness_job"], "codes": ["G6_E_CLOUD_CONSENT_REQUIRED"]},
                "tell_any": ["host_configuration"]})
NEGATIVE = _task("negative", forbidden="any")
MISSING = _task("missing_tool", withhold=["g6_review_patch", "g6_review_until_clean"])


def claude_log(steps: list[tuple], final: str = "Done.", *, success: bool = True) -> bytes:
    """A Claude Code ``stream-json`` log of ``steps``: each an edit of a path, a test run that failed or passed, or
    an MCP call with its reply."""
    events: list[dict[str, Any]] = [{"type": "system", "subtype": "init"}]
    for number, step in enumerate(steps):
        ident = f"u{number}"
        if step[0] == "edit":
            use = {"type": "tool_use", "id": ident, "name": "Edit",
                   "input": {"file_path": step[1], "old_string": "a", "new_string": "b"}}
            result = {"type": "tool_result", "tool_use_id": ident, "content": "The file was updated."}
        elif step[0] == "test":
            failed = step[1]
            use = {"type": "tool_use", "id": ident, "name": "Bash", "input": {"command": "python -m pytest -q"}}
            result = {"type": "tool_result", "tool_use_id": ident, "is_error": failed,
                      "content": "1 failed, 2 passed in 0.10s" if failed else "3 passed in 0.10s"}
        else:
            _, tool, args, reply = step
            use = {"type": "tool_use", "id": ident, "name": f"mcp__g6__{tool}", "input": args}
            result = {"type": "tool_result", "tool_use_id": ident, "is_error": False,
                      "content": [{"type": "text", "text": reply}]}
        events += [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Next."}, use]}},
                   {"type": "user", "message": {"content": [result]}}]
    events.append({"type": "result", "subtype": "success" if success else "error_max_turns", "is_error": False,
                   "result": final, "total_cost_usd": 0.1})
    return "\n".join(json.dumps(event) for event in events).encode("utf-8")


def codex_log(steps: list[tuple], final: str = "Done.", *, success: bool = True) -> bytes:
    """A ``codex exec --json`` log of the same ``steps``."""
    events: list[dict[str, Any]] = [{"type": "thread.started"}, {"type": "turn.started"}]
    for step in steps:
        if step[0] == "edit":
            item = {"type": "file_change", "changes": [{"path": step[1], "kind": "update"}], "status": "completed"}
        elif step[0] == "test":
            item = {"type": "command_execution", "command": "bash -lc 'python -m pytest -q'",
                    "aggregated_output": "1 failed" if step[1] else "3 passed", "exit_code": 1 if step[1] else 0,
                    "status": "failed" if step[1] else "completed"}
        else:
            _, tool, args, reply = step
            item = {"type": "mcp_tool_call", "server": "g6", "tool": tool, "arguments": args, "status": "completed",
                    "error": None, "result": {"content": [{"type": "text", "text": reply}]}}
        events += [{"type": "item.completed", "item": {"type": "agent_message", "text": "Next."}},
                   {"type": "item.completed", "item": item}]
    events += [{"type": "item.completed", "item": {"type": "agent_message", "text": final}},
               {"type": "turn.completed" if success else "turn.failed"}]
    return "\n".join(json.dumps(event) for event in events).encode("utf-8")


LOGS = {"claude_code": claude_log, "codex": codex_log}


def judged(client: str, task: dict[str, Any], steps: list[tuple], final: str = "Done.", **kwargs: Any) -> dict:
    """One task judged from a synthetic event log of ``client``, read as every kept log is read (``run.reading``)."""
    found = run.reading(client, LOGS[client](steps, final, **kwargs), 0)
    return score.judge_moment(task, T(task["id"], client, produced=f"client:{client}", **found), run.schemas())


@pytest.mark.parametrize("client", CLIENTS)
def test_the_order_of_a_sessions_steps_is_read_from_its_log(client: str) -> None:
    steps = [("edit", "shop/prices.py"), ("test", True), ("call", "g6_review_patch", REVIEW, SENSITIVE),
             ("test", False)]
    timeline = run.reading(client, LOGS[client](steps, "All done."), 0)["timeline"]
    kinds = [step["kind"] for step in timeline if step["kind"] != "text"]
    assert kinds == ["edit", "test", "call", "test", "final"]
    edit, failed, call, passed, final = [step for step in timeline if step["kind"] != "text"]
    assert edit["path"] == "shop/prices.py" and failed["failed"] is True and passed["failed"] is False
    assert call["tool"] == "g6_review_patch" and call["args"] == REVIEW and call["valid"] is True
    assert call["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and call["result_state"] == "blocked-escalated"
    assert final["text"] == "All done." and timeline[-1] is final
    # the calls the existing gates read are the same calls, in the same order
    assert [dict(c) for c in run.reading(client, LOGS[client](steps), 0)["calls"]] == [
        {k: v for k, v in call.items() if k not in ("kind", "code")}]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("reply", [
    "Note: G6_E_SENSITIVE_COMPONENT_DENIED is documented here",
    "README.md says the refusal is: G6_E_SENSITIVE_COMPONENT_DENIED",
    json.dumps({"error": "Something: G6_E_SENSITIVE_COMPONENT_DENIED"}),
], ids=["note", "readme-quote", "json-error"])
def test_a_mentioned_code_cannot_reach_or_credit_the_refusal_moment(client: str, reply: str) -> None:
    found = judged(client, REFUSED, [("call", "g6_review_patch", REVIEW, reply)],
                   "Allow patch_review.review where G6 is registered.")
    assert not found["moment_reached"]
    assert not found["moment_hit"]


@pytest.mark.parametrize("client", CLIENTS)
def test_call_before_final_is_a_review_after_the_last_python_edit(client: str) -> None:
    edit, review = ("edit", "shop/prices.py"), ("call", "g6_review_patch", REVIEW, STARTED)
    hit = judged(client, BEFORE, [edit, ("test", False), review])
    assert hit["moment_reached"] and hit["moment_hit"] and hit["kind"] == "before_done"
    early = judged(client, BEFORE, [review, edit])                       # the change went on after the review
    assert early["moment_reached"] and not early["moment_hit"]
    assert judged(client, BEFORE, [edit, review, ("edit", "README.md")])["moment_hit"]   # a later note is no change
    nothing = judged(client, BEFORE, [review])                           # no Python change: the moment never came
    assert not nothing["moment_reached"] and not nothing["moment_hit"]
    other = judged(client, BEFORE, [edit, ("call", "g6_find", {"query": "review"}, STARTED)])
    assert not other["moment_hit"]
    bad = judged(client, BEFORE, [edit, ("call", "g6_review_patch", {"repo": "/work/shop"}, STARTED)])
    assert not bad["moment_hit"] and bad["malformed_calls"] == 1                # no task: not a well-formed call
    elsewhere = judged(client, BEFORE, [edit, ("call", "g6_review_patch", dict(REVIEW, repo="/work/notes"), STARTED)])
    assert not elsewhere["moment_hit"]
    unfinished = judged(client, BEFORE, [edit, review], success=False)
    assert unfinished["state"] == "interrupted" and not unfinished["moment_hit"]


@pytest.mark.parametrize("client", CLIENTS)
def test_stuck_call_comes_after_the_second_failed_test_run(client: str) -> None:
    premortem = ("call", "g6_premortem", {"repo": "/work/shop", "task": "the totals"}, STARTED)
    edit = ("edit", "shop/prices.py")
    hit = judged(client, STUCK, [edit, ("test", True), ("test", True), premortem])
    assert hit["moment_reached"] and hit["moment_hit"] and hit["failed_test_runs"] == 2
    early = judged(client, STUCK, [edit, ("test", True), premortem, ("test", True)])
    assert early["moment_reached"] and not early["moment_hit"]
    passed = judged(client, STUCK, [edit, ("test", True), ("test", False), premortem])
    assert not passed["moment_reached"] and not passed["moment_hit"]
    assert not judged(client, STUCK, [edit, ("test", True), ("test", True),
                                      ("call", "g6_find", {"query": "why"}, STARTED)])["moment_hit"]
    plain = ("call", "g6_review_patch", REVIEW, STARTED)              # a review that proposes no check of the failure
    assert not judged(client, STUCK, [edit, ("test", True), ("test", True), plain])["moment_hit"]
    proposed = ("call", "g6_review_patch", dict(REVIEW, proposed_checks=["tests/test_prices.py"]), STARTED)
    assert judged(client, STUCK, [edit, ("test", True), ("test", True), proposed])["moment_hit"]


@pytest.mark.parametrize("client", CLIENTS)
def test_call_after_refusal_takes_the_remedy_or_tells_the_person_what_to_allow(client: str) -> None:
    refused = ("call", "g6_review_patch", REVIEW, SENSITIVE)
    told = judged(client, REFUSED, [("edit", "shop/prices.py"), refused],
                  "Allow patch_review.review where G6 is registered and I can run it.")
    assert told["moment_reached"] and told["moment_hit"]
    again = judged(client, REFUSED, [refused, refused], "Allow patch_review.review where G6 is registered.")
    assert again["moment_reached"] and not again["moment_hit"]                   # the same call, retried
    gave_up = judged(client, REFUSED, [refused], "It could not be run, so the change is done without it.")
    assert gave_up["moment_reached"] and not gave_up["moment_hit"]
    never = judged(client, REFUSED, [("edit", "shop/prices.py")], "patch_review.review")
    assert not never["moment_reached"] and not never["moment_hit"]               # no refusal: the moment never came
    withheld = ("call", "g6_review_patch", REVIEW, WITHHELD)
    grant = ("call", "g6_cloud_grant", {"source": "host_configuration", "provider": "openai:codex"}, GRANTED)
    assert judged(client, WITHHOLDING, [withheld, grant, ("call", "g6_review_patch", REVIEW, STARTED)])["moment_hit"]
    assert not judged(client, WITHHOLDING, [withheld, ("call", "g6_find", {"query": "share"}, STARTED)])["moment_hit"]
    assert judged(client, WITHHOLDING, [withheld], "Please share host_configuration with this provider.")["moment_hit"]
    catalogue = ("call", "g6_find", {"query": "review"}, WITHHELD)           # another tool's refusal is not the one
    assert not judged(client, WITHHOLDING, [catalogue, grant])["moment_reached"]


@pytest.mark.parametrize("client", CLIENTS)
def test_negative_and_missing_tool_tasks_are_read_as_the_existing_kinds_are(client: str) -> None:
    quiet = judged(client, NEGATIVE, [("edit", "README.md")])
    assert quiet["quiet"] and quiet["unnecessary_calls"] == 0 and quiet["kind"] == "negative"
    noisy = judged(client, NEGATIVE, [("edit", "README.md"), ("call", "g6_find", {"query": "typo"}, STARTED)])
    assert not noisy["quiet"] and noisy["unnecessary_calls"] == 1
    said = judged(client, MISSING, [("edit", "shop/prices.py")], "g6_review_patch is not available here.")
    assert said["recovered"]
    assert not judged(client, MISSING, [("edit", "shop/prices.py")], "Done.")["recovered"]


@pytest.mark.parametrize("client", CLIENTS)
def test_stuck_without_a_python_edit_is_not_a_hit(client: str) -> None:
    call = ("call", "g6_premortem", {"repo": "/work/shop", "task": "totals"}, STARTED)
    row = judged(client, STUCK, [("test", True), ("edit", "README.md"), ("test", True), call])
    assert not row["moment_reached"] and not row["moment_hit"] and row["failed_test_runs"] == 0


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("retry_call", [False, True], ids=["no-retry", "retry"])
def test_a_job_placeholder_is_not_a_successful_grant(client: str, retry_call: bool) -> None:
    refusal = ("call", "g6_review_patch", REVIEW, WITHHELD)
    grant = ("call", "g6_cloud_grant", {"source": "host_configuration", "provider": "openai:codex"}, STARTED)
    retry = ("call", "g6_review_patch", REVIEW, STARTED)
    tail = [retry] if retry_call else []
    row = judged(client, WITHHOLDING, [refusal, grant, *tail])
    assert row["moment_reached"] and not row["moment_hit"]
    assert judged(client, WITHHOLDING, [refusal, (*grant[:3], GRANTED), *tail])["moment_hit"]


def _row(task: dict[str, Any], **timeline_and_more: Any) -> Any:
    return T(task["id"], "codex", "completed", produced="client:codex 0.0", seconds=5.0, **timeline_and_more)


def cohort() -> dict[str, Any]:
    """The moment cohort's shape: 4 before-done, 4 stuck, 4 after-refusal, 8 negative and 4 missing-tool tasks."""
    tasks = []
    for kind, base, count in (("before_done", BEFORE, 4), ("stuck", STUCK, 4), ("after_refusal", REFUSED, 4),
                              ("negative", NEGATIVE, 8), ("missing_tool", MISSING, 4)):
        tasks += [dict(base, id=f"moment.{kind}.{n}") for n in range(1, count + 1)]
    return {"format": score.FORMAT_MOMENT_TASKS, "cohort": "moment", "planned_tools": ["g6_select"],
            "ungranted": ["component_catalogue"], "tasks": tasks}


GATES = {"format": score.FORMAT_MOMENT_GATES, "cohort": "moment", "min_positive_moment_hits": 0.60,
         "min_positive_lower_bound": 0.35, "max_negative_unnecessary": 0.125, "max_malformed": 0.10,
         "min_missing_tool_recovery": 0.75, "interval": "wilson95", "release_rule": "at_least_one_client_passes",
         "comparison": "paired_exact_mcnemar_no_claim"}
EDIT = {"kind": "edit", "path": "shop/prices.py"}
CALL = {"kind": "call", "server": "g6", "tool": "g6_review_patch", "args": REVIEW, "seconds": None, "valid": True,
        "result_state": "", "code": ""}
FAIL = {"kind": "test", "command": "pytest", "failed": True}
DENIED = dict(CALL, code="G6_E_SENSITIVE_COMPONENT_DENIED")


def ideal(task: dict[str, Any], hit: bool = True) -> Any:
    final = {"kind": "final", "text": "Allow patch_review.review." if hit else "Done."}
    if task["kind"] == "before_done":
        steps = (EDIT, CALL) if hit else (CALL, EDIT)
    elif task["kind"] == "stuck":
        steps = (EDIT, FAIL, FAIL, dict(CALL, tool="g6_premortem", args={"repo": "/work/shop", "task": "x"})) \
            if hit else (EDIT, FAIL)
    elif task["kind"] == "after_refusal":
        steps = (DENIED,)
    elif task["kind"] == "negative":
        return _row(task, timeline=(final,)) if hit else _row(task, calls=(CALL,), timeline=(CALL, final))
    else:
        text = "g6_review_patch is not available here." if hit else "Done."
        return _row(task, final_text=text, timeline=({"kind": "final", "text": text},))
    calls = tuple({k: v for k, v in step.items() if k not in ("kind", "code")} for step in steps if step["kind"] == "call")
    return _row(task, calls=calls, timeline=(*steps, final))


def report(rows: list[Any], listed: dict[str, Any] | None = None) -> dict[str, Any]:
    return score.score_moment(listed or cohort(), rows, GATES, client="codex", schemas=run.schemas())


def test_a_well_routed_agent_meets_every_moment_bar() -> None:
    listed = cohort()
    found = report([ideal(task) for task in listed["tasks"]], listed)
    assert found["decision"] == "pass" and all(gate["passed"] for gate in found["gates"].values())
    assert found["overall"]["positive"] == {"tasks": 12, "moment_reached": 12, "moment_hits": 12}
    assert set(found["kinds"]) == {"before_done", "stuck", "after_refusal", "negative", "missing_tool"}
    assert found["kinds"]["stuck"]["positive"] == {"tasks": 4, "moment_reached": 4, "moment_hits": 4}
    assert found["cohort"] == "moment" and found["format"] == score.FORMAT_MOMENT_REPORT


def test_each_moment_bar_fails_one_task_past_its_edge() -> None:
    listed = cohort()
    tasks = listed["tasks"]

    def with_misses(kind: str, misses: int) -> dict[str, Any]:
        seen = 0
        rows = []
        for task in tasks:
            miss = task["kind"] == kind and seen < misses
            seen += task["kind"] == kind
            rows.append(ideal(task, hit=not miss))
        return report(rows, listed)

    positives = [task for task in tasks if task["polarity"] == "positive"]
    edge = report([ideal(t, hit=t not in positives[:4]) for t in tasks], listed)       # 8 of 12 hits: 0.667
    over = report([ideal(t, hit=t not in positives[:5]) for t in tasks], listed)       # 7 of 12 hits: 0.583
    assert edge["decision"] == "pass" and edge["gates"]["min_positive_lower_bound"]["passed"]
    assert not over["gates"]["min_positive_moment_hits"]["passed"] and over["decision"] == "fail"
    assert with_misses("negative", 1)["decision"] == "pass"                            # 1 of 8: 0.125
    assert not with_misses("negative", 2)["gates"]["max_negative_unnecessary"]["passed"]
    assert with_misses("missing_tool", 1)["decision"] == "pass"                        # 3 of 4 recovered
    assert not with_misses("missing_tool", 2)["gates"]["min_missing_tool_recovery"]["passed"]
    bad = dict(CALL, args={"repo": "/work/shop"})                                     # no task: malformed
    rows = [ideal(task) for task in tasks]
    for index in (0, 1, 2):
        rows[index] = _row(tasks[index], calls=({k: v for k, v in bad.items() if k not in ("kind", "code")},),
                           timeline=(EDIT, bad, {"kind": "final", "text": "Done."}))
    assert not report(rows, listed)["gates"]["max_malformed"]["passed"]               # 3 of 24: 0.125
    assert report(rows[1:] + [ideal(tasks[0])], listed)["gates"]["max_malformed"]["passed"]   # 2 of 24


def test_a_refused_or_missing_session_counts_against_every_moment_gate() -> None:
    listed = cohort()
    refused = report([T(t["id"], "codex", "refused", code="G6_E_CLOUD_CONSENT_REQUIRED") for t in listed["tasks"]],
                     listed)
    assert refused["overall"]["scheduled"] == 24 and not any(g["passed"] for g in refused["gates"].values())
    assert report([], listed)["decision"] == "not_run"
    named = report([ideal(t) for t in listed["tasks"]], listed)
    stand_in = report([T(r.task_id, r.client, r.state, calls=r.calls, timeline=r.timeline, final_text=r.final_text,
                         seconds=5.0, produced="stand-in:x") for r in (ideal(t) for t in listed["tasks"])], listed)
    assert named["decision"] == "pass" and stand_in["decision"] == "simulated"


def test_the_exact_mcnemar_test() -> None:
    assert score.mcnemar_exact(0, 0) == 1.0
    assert score.mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert score.mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert score.mcnemar_exact(1, 5) == pytest.approx(0.21875)
    assert score.mcnemar_exact(5, 5) == 1.0


def test_before_and_after_are_paired_by_task_and_make_no_claim() -> None:
    listed = cohort()
    before = report([ideal(t, hit=t["polarity"] != "positive") for t in listed["tasks"]], listed)
    after = report([ideal(t) for t in listed["tasks"]], listed)
    found = score.compare(before, after)
    assert found["claim"] is None and len(found["pairs"]) == 24
    positive = found["by_polarity"]["positive"]
    assert positive == {"pairs": 12, "both": 0, "before_only": 0, "after_only": 12, "neither": 0,
                        "p_exact": pytest.approx(2 * 0.5 ** 12)}
    assert found["by_kind"]["negative"]["both"] == 8 and found["by_kind"]["negative"]["p_exact"] == 1.0
    pair = next(p for p in found["pairs"] if p["task_id"] == "moment.stuck.1")
    assert pair == {"task_id": "moment.stuck.1", "kind": "stuck", "before": False, "after": True}
    with pytest.raises(score.TaskError):                      # two arms of different lists are not a pair
        score.compare(before, dict(after, tasks_digest="0" * 64))
    with pytest.raises(score.TaskError):
        score.compare(before, dict(after, client="claude_code"))


def test_a_moment_task_list_outside_its_format_is_refused() -> None:
    score.validate_moment_tasks(cohort())
    for change in (lambda d: d["tasks"][0].update(kind="maybe"),
                   lambda d: d["tasks"][0].update(polarity="negative"),
                   lambda d: d["tasks"][0].update(expected_calls=[]),
                   lambda d: d["tasks"][0].update(completion={"kind": "stuck_call", "failed_test_runs": 2}),
                   lambda d: d["tasks"][4].update(completion={"kind": "stuck_call", "failed_test_runs": 0}),
                   lambda d: d["tasks"][8].update(completion=dict(d["tasks"][8]["completion"], refusal={"tools": [],
                                                                                                    "codes": []})),
                   lambda d: d["tasks"][8].update(completion=dict(d["tasks"][8]["completion"], tell_any=[])),
                   lambda d: d["tasks"][12].update(prompt="Use G6 to fix the typo in README.md, nothing else."),
                   lambda d: d["tasks"][12].update(prompt="Fix the typo in README.md and check it with your tools."),
                   lambda d: d["tasks"][12].update(prompt="Fix the typo in README.md; review the change after."),
                   lambda d: d["tasks"][12].update(prompt="Fix the typo in README.md through the MCP server."),
                   lambda d: d["tasks"][12].update(fair="short"),
                   lambda d: d["tasks"][12].pop("moment"),
                   lambda d: d["tasks"][12].update(grants=["component_catalogue"]),
                   lambda d: d["tasks"][20].update(withhold=[]),
                   lambda d: d["tasks"][0].update(expected_calls=[{"tool": "g6_review_patch", "args_present": "task"}]),
                   lambda d: d["tasks"].append(dict(d["tasks"][0])),
                   lambda d: d.update(cohort="final")):
        broken = json.loads(json.dumps(cohort()))
        change(broken)
        with pytest.raises(score.TaskError):
            score.validate_moment_tasks(broken)
    score.validate_moment_gates(GATES)
    for change in (lambda g: g.update(max_malformed=2.0), lambda g: g.pop("min_positive_lower_bound"),
                   lambda g: g.update(comparison="pooled")):
        broken = dict(GATES)
        change(broken)
        with pytest.raises(score.TaskError):
            score.validate_moment_gates(broken)
