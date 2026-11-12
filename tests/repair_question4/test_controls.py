"""Adversarial controls for all nine review classes, with local fakes only."""
import ast
import inspect
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

from g6.core.ledger import digest
from g6.harness import behaviour_question as bq, question_store as qs
from g6.harness import confirmed_checks as cc, patch_review, verification_templates as vt
from g6.harness.confirmed_expectation import Answer
from g6.mcp import behaviour, review_binding, review_reply
from tests.repair_question2.helpers import TASK, NODE, REQUIREMENTS, Dialog, ask, candidate, repo, result, stated
from tests.harness.example_contract import reference_instance_digest


@pytest.mark.parametrize("raw,expected", [(None, 1), ("0", 0), ("1", 1), ("10", 10), ("11", 0),
                                         ("", 0), (" 1", 0), ("01", 0), ("١", 0), ("²", 0), ("1\n", 0)])
def test_budget_language_edges(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    else:
        monkeypatch.setenv(qs.BUDGET_ENV, raw)
    assert qs.default_budget()[0] == expected


@pytest.mark.parametrize("action", ["decline", "cancel", "unavailable", "new_status"])
def test_transport_status_cannot_authorise(action):
    one = bq.caller_candidates(TASK, [candidate()])[0]
    reading = bq.read(action, {"choice": "A", "name": "Maintainer", "further": "one_more"},
                      one.question("b" * 64), None, answered_at="2026-11-08T00:00:00Z")
    assert reading.answer is None and reading.further is False


@pytest.mark.parametrize("outcomes,code", [
    ({"A": {"returns": 5}, "B": {"returns": 5}}, bq.IDENTICAL),
    ({"A": {"raises": "ValueError"}, "B": {"raises": "TypeError"}}, bq.OVERLAP),
    ({"A": {"returns": 5}, "B": {"effect": {"argument": 0, "equals": 5}}}, bq.INDEPENDENT),
])
def test_competition_boundaries(outcomes, code):
    with pytest.raises(bq.CandidateRefused) as exc:
        bq.caller_candidates(TASK, [candidate(args=[[]], outcomes=outcomes)])
    assert exc.value.code == code


@pytest.mark.parametrize("a,b", [(1, True), (1, 1.0), (-0.0, 0.0), (5, "5")])
def test_bytes_identity_and_producer_encoding(a, b):
    one = bq.caller_candidates(TASK, [candidate(outcomes={"A": {"returns": a}, "B": {"returns": b}})])[0]
    assert bq.encoded(a) != bq.encoded(b)
    assert bq.encoded(bq.decoded(bq.encoded(a))) == bq.encoded(a)
    reverse = bq.caller_candidates(TASK, [candidate(outcomes={"A": {"returns": b}, "B": {"returns": a}})])[0]
    assert one.question("b" * 64).sha256 != reverse.question("b" * 64).sha256


@pytest.mark.parametrize("observed", [{"returned": []}, {"returned": {}}, {"returned": {"x": [1]}}])
def test_lossy_foreign_observations_are_not_exact(observed):
    assert bq.observed(observed) is None


@pytest.mark.parametrize("left,right", [
    ({"returns": 1}, {"returns": True}),
    ({"returns": 1}, {"returns": 1.0}),
    ({"returns": -0.0}, {"returns": 0.0}),
    ({"returns": 5}, {"raises": "ValueError"}),
    ({"effect": {"argument": 0, "equals": [3]}}, {"effect": {"argument": 0, "equals": [4]}}),
])
def test_actual_confirmed_template_comparison_separates_admitted_outcomes(tmp_path, left, right):
    from g6.harness.confirmed_expectation import confirm
    from tests.harness.test_outcomes_compete import namespace, _stand_in

    one = bq.caller_candidates(TASK, [candidate(args=[[]], outcomes={"A": left, "B": right})])[0]
    question = one.question("b" * 64)
    for witness in (left, right):
        accepted = []
        for option in ("A", "B"):
            instance = confirm(question, option, answered_by="person:Maintainer").instance
            ns = namespace(tmp_path, instance, _stand_in(witness))
            try:
                with pytest.MonkeyPatch.context() as local:
                    local.chdir(tmp_path)
                    ns["test_example"]()
                accepted.append(option)
            except pytest.fail.Exception:
                pass
        assert accepted == (["A"] if witness is left else ["B"])


@pytest.mark.parametrize("damage", ["torn", "garbage", "empty", "reordered", "foreign"])
def test_store_damage_and_task_custody(tmp_path, damage):
    root = repo(tmp_path)
    assert ask(root)["status"] == "confirmed"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    data = store.path.read_bytes()
    if damage == "foreign":
        other = qs.TaskQuestions(qs.task_identity(root, TASK + " other"))
        other.path.write_bytes(data)
        other.head.write_bytes(store.head.read_bytes())
        store = other
    else:
        store.path.write_bytes({"torn": data[:-20], "garbage": data + b"garbage\n", "empty": b"",
                                "reordered": b"\n".join(reversed(data.splitlines())) + b"\n"}[damage])
    with pytest.raises(qs.StoreUnreadable):
        store.state()


def test_restart_between_entry_and_head_retains_budget(tmp_path, monkeypatch):
    store = qs.TaskQuestions(digest("restart fixture"))
    one = bq.caller_candidates(TASK, [candidate()])[0]
    question, rule = one.questions("b" * 64)
    with monkeypatch.context() as local:
        local.setattr(qs.TaskQuestions, "_keep", lambda *args: (_ for _ in ()).throw(RuntimeError("stop")))
        with pytest.raises(RuntimeError, match="stop"):
            store.ask(question, rule, one)
    state = qs.TaskQuestions(store.identity).state()
    assert state.left == 0 and state.pending.question.sha256 == question.sha256


def test_concurrent_calls_cannot_spend_the_last_slot_twice():
    identity = digest("concurrent review fixture")
    barrier = threading.Barrier(2)

    def record(args):
        store = qs.TaskQuestions(identity)
        one = bq.caller_candidates(TASK, [candidate(args=args)])[0]
        case, rule = one.questions("b" * 64)
        barrier.wait(timeout=10)
        try:
            return store.ask(case, rule, one).number
        except qs.BudgetSpent:
            return "spent"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(record, [2, 3])
        b = pool.submit(record, [4, 2])
        assert {a.result(timeout=15), b.result(timeout=15)} == {1, "spent"}
    state = qs.TaskQuestions(identity).state()
    assert len(state.asked) == 1 and state.left == 0


def test_only_asked_once_answers_and_pending_retries(tmp_path):
    root = repo(tmp_path)
    got = ask(root, dialog=Dialog({"choice": "skip", "name": "Maintainer"}))
    assert got["status"] == "pending"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    question = store.state().pending.question
    bad = Answer(question_sha256="f" * 64, option="A", answered_by="person:Maintainer",
                 answered_at="2026-11-08T00:00:00Z")
    with pytest.raises(qs.AnswerRefused):
        store.answer(bad)
    assert ask(root)["status"] == "confirmed"
    assert len(store.state().asked) == 1
    with pytest.raises(qs.AnswerRefused):
        store.answer(store.state().answers[0])
    assert store.state().asked[0].question.sha256 == question.sha256


@pytest.mark.parametrize("mutation", ["new_standard", "store_damage"])
def test_standard_change_and_damage_during_review_refuse(tmp_path, monkeypatch, mutation):
    root = repo(tmp_path)
    stated(monkeypatch)

    def execute(*args, **kwargs):
        if not qs.TaskQuestions(qs.task_identity(root, TASK)).exists():
            if mutation == "new_standard":
                assert ask(root)["status"] == "confirmed"
            else:
                assert ask(root, dialog=Dialog({"choice": "skip", "name": "Maintainer"}))["status"] == "pending"
                qs.TaskQuestions(qs.task_identity(root, TASK)).path.write_bytes(b"torn entry\n")
        return result(NODE)

    monkeypatch.setattr(patch_review, "_execute", execute)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert reply["ok"] is False and "assessment" not in reply
    assert reply["warning_card"]["code"] == (
        "G6_E_QUESTION_STANDARD_SUPERSEDED" if mutation == "new_standard" else qs.UNREADABLE)


@pytest.mark.parametrize("status", ["not_run", "timed_out", "never_seen", "skipped"])
def test_every_nonresult_withholds_prior_support(status):
    assert cc.patch_result([{"result": "passed"}, {"result": status}])[0] == cc.NOT_RUN
    assert cc.normal_reading({"result": "failed", "collection": True, "verdict": "collection_error"})["result"] == "error"


def test_ordinary_registry_identities_and_compiled_bytes_equal_base(tmp_path, monkeypatch):
    source = subprocess.run(["git", "show", "e47dc96:src/g6/harness/verification_templates.py"],
                            check=True, capture_output=True, timeout=20).stdout.decode("utf-8")
    old = ModuleType("g6.harness._review4_base_templates")
    monkeypatch.setitem(sys.modules, old.__name__, old)
    exec(compile(source, "<base producer>", "exec"), old.__dict__)
    for name, data in vt.EXAMPLES.items():
        assert vt.instance_digest(vt.TemplateInstance(**data)) == reference_instance_digest(
            old, old.TemplateInstance(**data)), name
    bindings = {"function": ["module", "calc", "add", "calc.py"], "cases": [
        {"args": [2, 3], "kwargs": {}, "expect": "returns", "value": 5}]}
    import hashlib
    from tests.harness.example_contract import EXAMPLE_TEXT_SHA256
    source = vt.compile_text(vt.TEMPLATES["example.v1"], bindings)
    assert hashlib.sha256(source.replace(ascii(bindings), vt.SLOT).encode()).hexdigest() == EXAMPLE_TEXT_SHA256


def test_saved_reply_custody_and_full_persisted_bytes(tmp_path):
    reply = {"ok": True, "assessment": {"outcome": "insufficient_evidence"}}
    a = Path(review_reply.save(reply, "a" * 32))
    b = Path(review_reply.save(behaviour.bound(reply, "c" * 64), "b" * 32))
    assert a.read_bytes() == b.read_bytes()
    assert review_binding.matches(b, "c" * 64)
    assert not review_binding.matches(b, "d" * 64)
    assert not review_binding.matches(b, "c" * 64, data=b"{}")


def test_dialog_admission_inventory_and_differential_refusal():
    assert set(inspect.signature(behaviour.confirm).parameters) == {
        "ctx", "repo", "task", "candidates", "review_job", "template_instances"}
    one = bq.Candidate.make(source="differential", kwargs={}, **candidate())
    with pytest.raises(bq.CandidateRefused) as exc:
        bq.form(one.question("b" * 64), one)
    assert exc.value.code == bq.PROBE_UNAVAILABLE


def test_measurement_counts_offline_and_has_no_product_importer():
    from validation.question_slice import measure
    got = measure.measure([
        {"case": "unnecessary", "asked": True, "task_decides": True, "final_correct": True},
        {"case": "missed", "asked": False, "catchable": True, "final_correct": False},
        {"case": "harmful", "asked": True, "gold_option": "A", "final_correct": False,
         "answer": {"option": "B", "answered_by": measure.SIMULATED}}])
    assert got["unnecessary"]["count"] == got["missed"]["count"] == got["harmful"]["count"] == 1
    assert got["model_calls"] == 0 and "upper bound" in got["note"]
    root = Path(__file__).resolve().parents[2] / "src/g6"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("validation") for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("validation"), path
