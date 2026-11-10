"""Fresh confirmation must actually replace an answer lacking scenario custody."""
from g6.core.ledger import digest
from g6.harness import behaviour_question as bq, question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, Dialog, ask, candidate, repo


def test_fresh_answer_restores_scenario_custody_for_legacy_case(tmp_path, monkeypatch):
    root = repo(tmp_path)
    # Explicit allowance, rather than an automatic extension for recovering old answers.
    monkeypatch.setenv(qs.BUDGET_ENV, '2')
    item = bq.caller_candidates(TASK, [candidate()])[0]
    old_question = item.question(digest(rt.snapshot(root)))
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    store.ask(old_question, None, item)
    old_answer = Answer(question_sha256=old_question.sha256, option='A',
                        answered_by='person:Maintainer', answered_at=qs.now())
    store.answer(old_answer)  # Legacy answer: deliberately no scenario digest.

    # A genuinely new question about a new artefact, then a new accepted person dialog.
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b  # reviewed again\n', encoding='utf-8')
    dialog = Dialog({'choice': 'A', 'name': 'Maintainer'})
    reply = ask(root, dialog=dialog)
    assert reply.get('status') == 'confirmed', reply
    assert len(dialog.messages) == 1
    state = qs.TaskQuestions(store.identity).state()
    assert len(state.asked) == len(state.answers) == 2
    new_answer = state.answers[-1]
    assert new_answer.sha256 != old_answer.sha256
    assert state.answer_scenarios[new_answer.sha256]

    [enforced] = state.standard().expectations
    assert state.answer_scenarios[enforced.answer_sha256], (
        'fresh answered form has canonical bytes, but the standard still enforces the old answer without them')
