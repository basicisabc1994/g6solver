"""A copy can acquire executable NTFS stream bytes without changing either manifest."""
import subprocess

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness import executable_scenario as es, verification_search as vs
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo


def test_an_added_ntfs_stream_cannot_supply_positive_confirmed_evidence(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={'carrier.dat': 'unchanged ordinary bytes\n'})
    (root / 'calc.py').write_text(
        'import importlib.machinery, os\n'
        'def add(a, b):\n'
        '    source = os.path.join(os.path.dirname(__file__), "carrier.dat:helper.py")\n'
        '    if not os.path.exists(source):\n'
        '        return -1\n'
        '    loader = importlib.machinery.SourceFileLoader("stream_helper", source)\n'
        '    namespace = {}\n'
        '    exec(loader.get_code("stream_helper"), namespace)\n'
        '    return namespace["VALUE"]\n', encoding='utf-8')
    change = rt.change(root)
    before = vs.bind_execution(change, rt.snapshot(root), deadline=vs._clock() + 60)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    scenario = es.Scenario.resolve(root, question)
    scenario.admit(root, question)
    source = runner.CONFIRMED + scenario.source('A')
    plant = []
    executed = []

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        if plant:
            stream = cwd / 'carrier.dat:helper.py'
            stream.write_bytes(b'VALUE = 5\n')
            assert stream.read_bytes() == b'VALUE = 5\n'
            assert not any(p.name == stream.name for p in cwd.iterdir())
            assert rt._copy_snapshot(cwd) == before.execution['files']
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        executed.append(child.returncode)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('review-fixture', '', spec))

    def run():
        return vs._run_check(change, 'test_owned.py', source, 'test_owned.py',
                             at_base=False, timeout_s=30, artefact=before)

    control = run()
    assert control['g6_observation']['result'] == 'failed', control
    plant.append(True)
    got = run()
    assert executed == [0, 0]
    assert got.get('verdict') != 'passed', (
        'unmanifested NTFS stream supplied VALUE=5 and received a bound pass', got)
