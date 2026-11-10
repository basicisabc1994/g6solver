"""Store construction and enforcement choose the same last recorded answer, including absent custody."""
import inspect
import itertools

import pytest

from g6.harness import behaviour_question as bq, confirmed_checks as cc, confirmed_expectation as ce
from g6.harness import executable_scenario as es, question_store as qs, verification_search as vs
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo, result


ORDERS = list(itertools.permutations(('legacy', 'fresh'))) + list(itertools.permutations(('legacy', 'fresh', 'fresh2')))


@pytest.mark.parametrize('order', ORDERS)
def test_both_consumers_select_latest_record_and_its_custody(tmp_path, monkeypatch, order):
    monkeypatch.setenv(qs.BUDGET_ENV, '3')
    root = repo(tmp_path)
    item = bq.caller_candidates(TASK, [candidate()])[0]
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    expected = []
    standard = ce.ExpectationStandard()
    for index, name in enumerate(order):
        case = item.question(str(index + 1) * 64)
        scenario = es.Scenario.resolve(root, case)
        es.save(case, scenario)
        store.ask(case, None, item)
        answer = ce.Answer(question_sha256=case.sha256, option='A', answered_by='person:Maintainer',
                           answered_at=f'2026-11-10T00:00:0{index}Z')
        store.answer(answer, **({'scenario': scenario} if name != 'legacy' else {}))
        standard = standard.with_expectation(ce.ConfirmedExpectation(case, answer))
        expected.append((answer, scenario.sha256 if name != 'legacy' else ''))
    state = qs.TaskQuestions(store.identity).state()
    latest, custody = expected[-1]
    assert [one.answer_sha256 for one in standard.expectations] == [latest.sha256]
    assert state.standard() == standard
    seen = []
    def execute(change, placed, source, node, **kwargs):
        seen.append(kwargs)
        return result(node)
    monkeypatch.setattr(vs, '_run_check', execute)
    [enforced] = cc.run(root, rt.change(root), standard, deadline=vs._clock() + 60,
                        artifact=vs.digest(rt.snapshot(root)), task=TASK)
    assert enforced['answer_sha256'] == latest.sha256
    assert enforced.get('scenario_sha256', '') == custody
    assert bool(enforced.get('scenario_unavailable')) == (not custody)
    assert seen


def test_inventory_one_selector_used_by_both_consumers():
    selector = getattr(ce, 'latest_confirmed', None)
    assert callable(selector)
    assert cc.latest_confirmed is selector
    assert 'latest_confirmed(' in inspect.getsource(ce.ExpectationStandard.with_expectation)
    assert 'latest_confirmed(' in inspect.getsource(cc.run)


def test_same_record_is_idempotent_but_fresh_equivalent_answer_revises_it():
    item = bq.caller_candidates(TASK, [candidate()])[0]
    first = ce.confirm(item.question('a' * 64), 'A', answered_by='person:Maintainer',
                       answered_at='2026-11-10T00:00:00Z')
    fresh = ce.confirm(item.question('b' * 64), 'A', answered_by='person:Maintainer',
                       answered_at='2026-11-10T00:00:01Z')
    old = ce.ExpectationStandard().with_expectation(first)
    assert old.with_expectation(first) is old
    new = old.with_expectation(fresh)
    assert new.expectations == (fresh,)
    assert new.revision == old.revision + 1 and new.previous == old.sha256
    assert old.expectations == (first,)


def test_distinct_bindings_keep_their_own_latest_record_and_custody():
    records = []
    for args, baseline in (([2, 3], 'a'), ([3, 2], 'b'), ([2, 3], 'c')):
        item = bq.caller_candidates(TASK, [candidate(args=args)])[0]
        records.append(ce.confirm(item.question(baseline * 64), 'A', answered_by='person:Maintainer',
                                  answered_at='2026-11-10T00:00:00Z'))
    custody = {one.answer_sha256: str(index) * 64 for index, one in enumerate(records, 1)}
    selected = ce.latest_confirmed(records, custody)
    assert [(one.answer_sha256, held) for one, held in selected] == [
        (records[2].answer_sha256, '3' * 64), (records[1].answer_sha256, '2' * 64)]
