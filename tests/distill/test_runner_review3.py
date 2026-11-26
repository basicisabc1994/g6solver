"""An owner stop prevents further work while retaining work that already ran."""
import pytest


@pytest.mark.parametrize('crash', [False, True])
def test_a_stop_during_execution_keeps_the_cycle_or_crash_row(crash):
    from tests.distill.test_research_runner import fixture
    _, runner, _, calls, budget, repo = fixture()
    original = runner.execute

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        repo.stop = True
        if crash:
            raise RuntimeError('fixture execution failure')
        return result

    runner.execute = execute
    if crash:
        with pytest.raises(RuntimeError, match='fixture'):
            runner.tick(now=100.)
    else:
        assert runner.tick(now=100.).status == 'completed'
    assert calls == ['separating']
    assert budget.settlements == ([.01] if crash else [.002])
    assert [row['kind'] for row in runner.journal.entries()] == ['crash' if crash else 'cycle']
    assert runner.tick(now=104.).status == 'stopped'
    assert repo.effects == []
