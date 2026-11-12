"""An interrupted scenario write must not permanently block an unspent question."""
import pytest

from g6.harness import executable_scenario as es, question_store as qs
from tests.repair_question2.helpers import TASK, Dialog, ask, repo


class Stopped(BaseException):
    pass


def test_restart_after_creating_scenario_file_can_ask_again(tmp_path, monkeypatch):
    control_parent = tmp_path / 'control'
    control_parent.mkdir()
    assert ask(repo(control_parent)).get('status') == 'confirmed'
    root = repo(tmp_path)
    canonical = es.canonical

    def stop_before_write(value):
        if isinstance(value, dict) and value.get('version') == 'scenario-binding.v1':
            raise Stopped('process stopped after O_EXCL create, before writing the record')
        return canonical(value)

    dialog = Dialog()
    with monkeypatch.context() as fault:
        fault.setattr(es, 'canonical', stop_before_write)
        with pytest.raises(Stopped):
            ask(root, dialog=dialog)
    assert not dialog.messages
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    assert not state.asked and state.left == 1

    # A new tool invocation reopens state; it holds no old in-memory scenario.
    resumed = ask(root, dialog=dialog)
    assert resumed.get('status') == 'confirmed', (
        'an interrupted sidecar prevents every retry despite an unused budget: ' + str(resumed))
