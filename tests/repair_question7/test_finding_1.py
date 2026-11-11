"""A form accepted within the binding limit must still compile after confirmation."""
from g6.harness import executable_scenario as es, question_store as qs, verification_templates as vt
from tests.repair_question2.helpers import TASK, Dialog, ask, candidate, repo


def test_shown_admissible_scenario_can_be_compiled(tmp_path):
    root = repo(tmp_path)
    (root / 'calc.py').write_text('def add(value):\n    return 5\n', encoding='utf-8')
    dialog = Dialog()
    reply = ask(root, dialog=dialog, data=[candidate(args=['x' * 12000])])
    state = qs.TaskQuestions(qs.task_identity(root, TASK)).state()
    if reply.get('status') != 'confirmed':
        assert not state.asked and not dialog.messages, reply
        return  # A refusal before spending a question is safe too.
    assert 'x' * 12000 in dialog.messages[0]
    assert state.left == 0
    [expectation] = state.standard().expectations
    scenario = es.load(expectation.question)

    # The historical compiler accepts exactly this question and selected outcome.
    tree = vt.Tree.read(root, vt.repo_tests.change(root))
    applied = vt.TEMPLATES['example.v1'].applies(
        tree, vt.ExampleBindings.model_validate(expectation.instance.bindings), vt.PATCH)
    vt.compiled_check(root, expectation.instance, applied.data, [])

    try:
        vt.compiled_check(root, expectation.instance,
                          {'scenario': scenario.record.decode('ascii'), 'option': 'A'}, [])
    except vt.Inapplicable as exc:
        raise AssertionError(f'G6 offered the scenario but cannot enforce it: {exc}') from exc
