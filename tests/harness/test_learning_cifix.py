"""CI regressions: refusal envelopes, execution signals and frozen mission replies."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import outcome_history, verification_templates
from g6.mcp import improve, learning_history, mission, workflow
from g6.workforce import repo_tests


@pytest.mark.parametrize('action', ['decline', 'cancel', 'unavailable', 'accept'])
def test_history_refusal_keeps_ok_and_its_bound_code(monkeypatch, action):
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda repo: Path('fixture'))
    monkeypatch.setattr(outcome_history, 'read', lambda root: outcome_history.History('a' * 64))
    monkeypatch.setattr(outcome_history, 'fingerprint', lambda repository: 'b' * 64)

    async def elicit_form(**kwargs):
        if action == 'unavailable':
            raise NotImplementedError('no dialog')
        return SimpleNamespace(action=action, content={'value': ''})

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id='fixture')
    with cloud.collect_sources():
        value = asyncio.run(learning_history.forget_reply(
            ctx, '{"repo":"fixture"}', allow=frozenset({'learning_history.forget'})))
        assert value['ok'] is False
        _, refs, controls = cloud.freeze_output(value)
        assert refs == ()
        assert controls['error_code'] == value['warning_card']['code']


@pytest.mark.parametrize('signal', outcome_history.LETS_THROUGH)
def test_retained_template_reader_lets_execution_signals_through(monkeypatch, signal):
    def interrupted(*args, **kwargs):
        raise signal('execution ended')

    monkeypatch.setattr(verification_templates.TemplateInstance, 'model_validate', interrupted)
    with pytest.raises(signal):
        outcome_history._judged_its_own_way({})


@pytest.mark.parametrize('operation', ['asked', 'status'])
def test_unexpected_folder_discovery_error_is_enveloped(monkeypatch, operation):
    def broken(folder):
        raise TypeError('discovery failed')

    monkeypatch.setattr(improve, '_root', broken)
    monkeypatch.setattr(improve, 'stop_in_force', lambda root: improve.Stop())
    value = getattr(improve, operation)('fixture')
    expected = improve.START_FAILED if operation == 'asked' else improve.STATUS
    assert value['warning_card']['code'] == expected
    assert value['running'] is (False if operation == 'asked' else None)


@pytest.mark.parametrize('operation', ['asked', 'status'])
def test_refused_folder_never_reaches_the_stop_reader(monkeypatch, operation):
    def refused(folder):
        raise repo_tests.RepoRefused('G6_E_INPUT_VALIDATION', 'unsafe folder')

    def no_read(root):
        pytest.fail('an unadmitted folder reached the stop reader')

    monkeypatch.setattr(improve, '_root', refused)
    monkeypatch.setattr(improve, 'stop_in_force', no_read)
    value = getattr(improve, operation)('fixture')
    assert value['warning_card']['code'] == improve.NOT_A_FOLDER
    assert value['completion_state'] == 'blocked-escalated'


@pytest.mark.parametrize('repo', [r'\\host.invalid\share\repo', r'\\?\C:\repo', r'\\.\pipe\repo'])
def test_workflow_foreign_spelling_refuses_without_admission(monkeypatch, repo):
    def no_admission(value):
        pytest.fail('a network/device spelling reached folder resolution')

    monkeypatch.setattr(repo_tests, 'admit_folder', no_admission)
    value = workflow.export(repo)
    assert value['ok'] is False
    assert value['warning_card']['code'] == 'G6_E_INPUT_VALIDATION'


def test_mission_finish_uses_the_original_mission_snapshot(monkeypatch):
    found = {'mission': {'status': 'original'}}
    source = cloud.Source.text('source:fixture', 'mission', 'original')

    def finish(value, job):
        value['mission']['status'] = 'changed after snapshot'
        return cloud.bind_output({'assessment': {}}, ())

    monkeypatch.setattr(mission.review_reply, 'finish', finish)
    with cloud.collect_sources():
        cloud.bind_output(found, (source,))
        value = mission.finish(found, 'fixture')
        assert value['mission']['status'] == 'original'
        body, refs, _ = cloud.freeze_output(value)
        assert json.loads(body)['mission']['status'] == 'original'
        assert source in refs


def test_history_skill_runs_without_an_installed_checkout(monkeypatch, tmp_path):
    import g6
    from g6.core import skills

    examples = skills.skill_python_examples(skills.skill_text('learning_history'))
    # An installed package need not live in a git working tree.
    monkeypatch.setattr(g6, '__file__', str(tmp_path / 'installed' / 'g6' / '__init__.py'))
    exec(compile(examples[0], '<learning_history installed example>', 'exec'),
         {'__name__': '__skill_example__'})


def test_mission_finish_detaches_the_compact_snapshot(monkeypatch):
    shown = {'assessment': {'report': 'frozen report'}}
    freeze = cloud.freeze_output

    def changed_after_freeze(value, **kwargs):
        result = freeze(value, **kwargs)
        if value is shown:
            value['assessment']['report'] = 'changed report'
        return result

    monkeypatch.setattr(mission.review_reply, 'finish', lambda *args: shown)
    monkeypatch.setattr(cloud, 'freeze_output', changed_after_freeze)
    value = mission.finish({'mission': {'status': 'fixture'}}, 'fixture')
    assert value['assessment']['report'] == 'frozen report'


@pytest.mark.parametrize('operation', ['asked', 'status'])
def test_a_late_repository_refusal_keeps_the_admitted_folders_stop(monkeypatch, operation):
    def refused(*args):
        raise repo_tests.RepoRefused('G6_E_INPUT_VALIDATION', 'later read failed')

    monkeypatch.setattr(improve, '_root', lambda folder: Path('fixture'))
    monkeypatch.setattr(improve, 'admission', lambda root, reading: reading)
    monkeypatch.setattr(improve, 'stop_in_force', lambda root: improve.Stop('G6_E_LOOP_STOPPED', 'operator stop'))
    monkeypatch.setattr(improve, '_settings' if operation == 'asked' else '_is_a_loop', refused)
    value = getattr(improve, operation)('fixture')
    assert value['stop_code'] == 'G6_E_LOOP_STOPPED'
