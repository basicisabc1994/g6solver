"""Resolving an unselected error outcome changes the advertised fresh-import setup.

The review's test, with one assertion changed on purpose (the number of child runs: see the comment there) and an
import it did not use removed."""
import subprocess
import sys

from g6.core import confine
from g6.harness import behaviour_question as bq
from g6.harness import executable_scenario as es, patch_review
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, REQUIREMENTS, Dialog, ask, candidate, repo, stated


def test_unselected_exception_import_must_not_change_the_confirmed_fresh_call(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={'domain_errors.py':
        'import calc\ncalc.ready = True\nclass DomainError(Exception):\n    pass\n'})
    (root / 'calc.py').write_text(
        'ready = False\ndef add(a, b):\n    return a + b if ready else a - b\n', encoding='utf-8')
    item = bq.caller_candidates(TASK, [candidate(outcomes={
        'A': {'returns': 5}, 'B': {'raises': 'domain_errors.DomainError'}})])[0]
    question = item.question('b' * 64)
    scenario = es.Scenario.resolve(root, question)
    scenario.admit(root, question)
    message, _ = bq.form(question, item, scenario=scenario)
    assert es.SETUP in message and 'calc.add(2, 3)' in message
    dialog = Dialog()
    answered = ask(root, dialog=dialog, data=[candidate(outcomes={
        'A': {'returns': 5}, 'B': {'raises': 'domain_errors.DomainError'}})])
    assert answered['status'] == 'confirmed', answered
    assert es.SETUP in dialog.messages[0]
    fresh = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import calc; print(calc.add(2,3))', str(root)],
        capture_output=True, text=True, check=True, timeout=10)
    assert fresh.stdout.strip() == '-1'
    children = []

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        children.append(child.returncode)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('review-fixture', '', spec))
    stated(monkeypatch)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assessment = reply['assessment']
    [held] = assessment['confirmed_expectations']['expectations']
    # Changed on purpose from the review's `[0, 0, 0]`: the third child is the replay, which the product makes only
    # after the patch run passed. It passed there because of the import this test is about; without that import the
    # patch run observes the fresh call's -1 and fails, so two children run (the patch run and the base control).
    assert children == [0, 0]
    assert held['check']['runs']['patch']['result'] == 'failed' and 'again' not in held['check']['runs']
    assert held['check']['runs']['base']['result'] == 'failed'
    [evidence] = assessment['confirmed_expectations']['evidence']['expectations']
    assert evidence['checked'] is False, (
        'the fresh call returns -1, but the issuer supports returns 5 after an unselected error import',
        held, evidence)
