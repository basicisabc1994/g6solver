"""Every actual executable-scenario field reaches the displayed record and compiled check."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from g6.harness import behaviour_question as bq

from tests.repair_question2.helpers import TASK, candidate, repo


def make(root):
    from g6.harness.executable_scenario import Scenario

    one = bq.caller_candidates(TASK, [candidate(args=["a  b", "x"], outcomes={
        "A": {"returns": "a  bx"}, "B": {"returns": "a bx"}})])[0]
    question = one.question("b" * 64)
    return one, question, Scenario.resolve(root, question)


def test_inventory_every_scenario_field_reaches_form_and_check(tmp_path):
    root = repo(tmp_path)
    one, question, scenario = make(root)
    actual = json.loads(scenario.record)
    assert set(actual) == {"version", "setup", "entrypoint", "input", "outcomes"}
    message = bq.form(question, one, scenario=scenario)[0]
    assert scenario.record.decode("ascii") in message
    namespace = {}
    exec(compile(scenario.source("A"), "<fixed example.v1>", "exec"), namespace)
    assert json.loads(namespace["BINDINGS"]["scenario"]) == actual
    assert hashlib.sha256(scenario.program.encode("utf-8")).hexdigest() == scenario.sha256
    assert scenario.sha256 in message
    assert "a  b" in message


@pytest.mark.parametrize("damage", ["none", "missing", "malformed", "digest", "question"])
def test_scenario_sidecar_survives_restart_or_refuses(tmp_path, monkeypatch, damage):
    from g6.harness.executable_scenario import Scenario, load, save
    from g6.core.storage import state_path

    root = repo(tmp_path)
    _one, question, scenario = make(root)
    save(question, scenario)
    path = state_path("scenarios", question.sha256 + ".json")
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text('{')
    elif damage in ("digest", "question"):
        record = json.loads(path.read_bytes())
        record["scenario_sha256" if damage == "digest" else "question_sha256"] = "0" * 64
        path.write_text(json.dumps(record))
    if damage == "none":
        assert load(question).record == Scenario(scenario.record).record
    else:
        with pytest.raises(ValueError, match="G6_E_SCENARIO_UNAVAILABLE"):
            load(question)


def test_confirmed_setup_excludes_startup_state_beyond_the_target_cache(tmp_path):
    from g6.harness.executable_scenario import Scenario

    root = repo(tmp_path)
    (root / "calc.py").write_text("import builtins\ndef add(a, b):\n    return getattr(builtins, 'g6_startup', a-b)\n")
    (root / "conftest.py").write_text("import builtins\nbuiltins.g6_startup = 5\n")
    one = bq.caller_candidates(TASK, [candidate()])[0]
    scenario = Scenario.resolve(root, one.question("b" * 64))
    (root / "test_scenario.py").write_text(scenario.source("A"))
    run = subprocess.run([sys.executable, "-m", "pytest", "test_scenario.py", "-q", "-p", "no:cacheprovider"],
        cwd=root, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, capture_output=True, text=True, timeout=20)
    assert "1 failed" in run.stdout, run.stdout + run.stderr


def test_scenario_digest_names_the_exact_child_bytes(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    from g6.core import confine
    from g6.harness import behaviour_runner as runner

    root = repo(tmp_path)
    _one, _question, scenario = make(root)
    observed = []

    @contextmanager
    def folder(**kwargs):
        path = tmp_path / 'owned'
        path.mkdir()
        yield path

    def execute(command, **kwargs):
        bootstrap = Path(command[command.index('utf8') + 1]).read_bytes()
        observed.append(hashlib.sha256(bootstrap).hexdigest())
        channel = Path(command[-2])
        channel.write_bytes(runner.frame({'version': 'observation.v1', 'outcomes': [
            {'returned': '"a  bx"', 'arguments': ['"a  b"', '"x"']}], 'errors': {}}))
        return 0

    monkeypatch.setattr(confine, 'run_folder', folder)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('trusted-fixture', '', spec))
    monkeypatch.setattr(runner.rt, '_bounded', execute)
    _, data = runner.bindings(scenario.source('A'))
    got = runner.run(root, data, 'fixture', timeout_s=10, deadline=runner.time.monotonic() + 30)
    assert observed == [scenario.sha256]
    assert got['bootstrap_sha256'] == scenario.sha256
    assert got['g6_observation']['result'] == 'passed'

