"""An answer recorded without canonical bytes must not regain positive support unless the person answers again.

`mcp.behaviour.confirm` saves the scenario sidecar (`executable_scenario.save(case, scenario)`) before `store.ask`,
and `store.ask` returns an already-asked question "at no cost". `confirmed_checks.run` reads the sidecar's presence as
"this answer holds canonical bytes". So an agent's call with the same candidate attaches a scenario to an answer the
person gave without it, and the person declining the new dialog does not undo it.

The earlier answer is recorded the way the store records one without the MCP dialog (store.ask, store.answer), which is
the ruled "answer with no canonical bytes". Only the OS boundary is a fixture; the store, dialog code, scenario, copy
binding, runner, review and issuer are the product's.
"""
import subprocess

from g6.core import confine
from g6.core.ledger import digest
from g6.core.storage import state_path
from g6.harness import behaviour_question as bq, patch_review, question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, Dialog, ask, candidate, repo, stated


def test_agent_call_does_not_restore_support_to_an_answer_without_canonical_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv(qs.BUDGET_ENV, "2")   # the person allows two questions on this task
    root = repo(tmp_path)   # base returns a - b; the patch returns a + b
    one = bq.caller_candidates(TASK, [candidate()])[0]
    case, rule = one.questions(digest(rt.snapshot(root)))
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    store.ask(case, rule, one)
    store.answer(Answer(question_sha256=case.sha256, option="A", answered_by="person:Maintainer",
                        answered_at=qs.now()))   # returns 5, recorded with no scenario sidecar
    sidecar = state_path("scenarios", case.sha256 + ".json")
    assert not sidecar.exists()
    stated(monkeypatch)
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("trusted-review-fixture", "", spec))

    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=min(timeout_s, 20))
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, "_bounded", execute)

    def statement():
        reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
        [found] = reply["assessment"]["confirmed_expectations"]["evidence"]["expectations"]
        return found

    first = statement()   # the ruled behaviour: no canonical bytes, no positive support
    assert first["checked"] is False and "G6_E_SCENARIO_UNAVAILABLE" in first.get("not_counted", ""), first
    # The agent calls the dialog tool again with the same candidate; the person declines the form.
    again = ask(root, dialog=Dialog(action="decline"))
    assert again.get("status") != "confirmed", again
    assert len(qs.TaskQuestions(qs.task_identity(root, TASK)).state().answers) == 1   # nothing new was answered
    second = statement()
    assert second["checked"] is False, (
        "the person declined, no new answer exists, yet the earlier answer now has positive support from a scenario "
        f"sidecar the agent's call created (sidecar exists: {sidecar.exists()}): {second}")
