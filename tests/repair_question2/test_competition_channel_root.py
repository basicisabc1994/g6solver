"""Inventory example.v1's outcome algebra and the observation custody boundary."""
import itertools

import pytest

from g6.harness import behaviour_question as bq
from g6.harness.confirmed_expectation import OUTCOME_FIELDS, confirm

from .helpers import TASK, candidate, namespace


OUTCOMES = ({"returns": 5}, {"returns": -1}, {"raises": "ValueError"}, {"raises": "TypeError"},
            {"effect": {"argument": 0, "equals": 5}}, {"effect": {"argument": 0, "equals": -1}})


def test_inventory_is_every_example_outcome_kind():
    assert {next(iter(one)) for one in OUTCOMES} == OUTCOME_FIELDS


@pytest.mark.parametrize("left,right", list(itertools.combinations(OUTCOMES, 2)))
def test_admitted_pairs_have_no_shared_witness(tmp_path, left, right):
    try:
        one = bq.caller_candidates(TASK, [candidate(args=[[]], outcomes={"A": left, "B": right})])[0]
    except bq.CandidateRefused:
        return
    question = one.question("b" * 64)

    class Both(ValueError, TypeError):
        pass

    for returned in (5, -1, None):
        for error in (None, ValueError, TypeError, Both):
            for effect in (5, -1, []):
                accepted = []
                for label in ("A", "B"):
                    ns = namespace(tmp_path, confirm(question, label, answered_by="person:Maintainer").instance)
                    original = ns["_load"]

                    def witness(arg):
                        if error:
                            raise error()
                        arg[:] = effect if isinstance(effect, list) else [effect]
                        return returned

                    ns["_load"] = lambda spec: original(spec) if spec[0] == "builtin" else witness
                    try:
                        ns["test_example"]()
                        accepted.append(label)
                    except pytest.fail.Exception:
                        pass
                assert len(accepted) <= 1


def test_no_differential_form_can_label_repository_stdout_as_g6():
    one = bq.Candidate.make(source="differential", kwargs={}, **candidate())
    with pytest.raises(bq.CandidateRefused, match="protected probe"):
        bq.form(one.question("b" * 64), one)


def test_pending_legacy_differential_is_refused_before_dialog(tmp_path):
    import asyncio

    from g6.harness import question_store as qs
    from g6.mcp import behaviour
    from .helpers import Dialog, repo

    root = repo(tmp_path)
    one = bq.Candidate.make(source="differential", kwargs={}, **candidate())
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    store.ask(one.question("b" * 64), None, one)
    dialog = Dialog()
    reply = asyncio.run(behaviour.confirm(dialog, str(root), TASK))
    assert not dialog.messages
    assert reply["warning_card"]["code"] == "G6_E_QUESTION_PROBE_UNAVAILABLE"


def test_previously_confirmed_error_case_keeps_its_meaning(tmp_path, monkeypatch):
    from g6.harness import question_store as qs
    from g6.harness.confirmed_expectation import Answer
    from .helpers import repo

    root = repo(tmp_path)
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    # Construct the durable record with repair 1's admission rule, then restart under the new rule.
    with monkeypatch.context() as old:
        old.setattr(bq, "competition", lambda outcomes: ("", ""))
        one = bq.Candidate.make(source="caller", kwargs={}, **candidate(outcomes={
            "A": {"raises": "ValueError"}, "B": {"raises": "TypeError"}}))
        question = one.question("b" * 64)
        store.ask(question, None, one)
        chosen = confirm(question, "A", answered_by="person:Maintainer")
        store.answer(Answer.from_record(chosen._answer))
    assert store.state().standard().expectations[0].record == chosen.record


def test_inventory_both_dialog_ingresses_require_probe_custody():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src/g6"
    form = ast.parse((root / "harness/behaviour_question.py").read_text())
    function = next(n for n in form.body if isinstance(n, ast.FunctionDef) and n.name == "form")
    guard = next(n for n in ast.walk(function) if isinstance(n, ast.If)
                 and ast.unparse(n.test) == "candidate.source == 'differential'")
    assert any(isinstance(n, ast.Call) and ast.unparse(n.func) == "require_probe" for n in ast.walk(guard))
    product = ast.parse((root / "mcp/behaviour.py").read_text())
    function = next(n for n in product.body if isinstance(n, ast.FunctionDef) and n.name == "_review_candidates")
    assert "bq.require_probe()" in ast.unparse(function)
    calls = [n for n in ast.walk(product) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == 'bq.differential_candidates']
    assert len(calls) == 1 and calls[0] in list(ast.walk(function))
    assert any(k.arg == 'owned_checks' and ast.unparse(k.value) == 'set(owned)' for k in calls[0].keywords)
    source = ast.unparse(function)
    assert 'behaviour_runner.read_probes(' in source
    assert "owned.get(a.get('check')) == digest(a)" in source
    producers = []
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and (
                    getattr(node.func, 'attr', '') == 'differential_candidates'
                    or getattr(node.func, 'id', '') == 'differential_candidates'):
                producers.append(path.relative_to(root).as_posix())
    assert producers == ['mcp/behaviour.py'], "every product producer must use the custody gate inventoried above"


def test_new_ask_cannot_bypass_current_admission_with_a_different_question(tmp_path):
    from g6.harness import question_store as qs
    from g6.harness.confirmed_expectation import Question
    from .helpers import repo

    root = repo(tmp_path)
    one = bq.Candidate.make(source='caller', kwargs={}, **candidate())
    data = one.question('b' * 64).to_dict()
    data.pop('options')
    data['outcomes'] = {'A': {'raises': 'ValueError'}, 'B': {'raises': 'TypeError'}}
    question = Question(**data)
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    with pytest.raises(bq.CandidateRefused) as refused:
        store.ask(question, None, one)
    assert refused.value.code == bq.OVERLAP
    assert not store.state().asked
