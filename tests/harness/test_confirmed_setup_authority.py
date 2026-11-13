"""A confirmed call has one setup, independent of outcome and test startup."""
import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import executable_scenario as es
from g6.harness import verification_templates as vt


def bindings():
    return {"function": ["module", "calc", "add", "calc.py"], "cases": [
        {"args": [2, 3], "kwargs": {}, "expect": "raises",
         "error": ["module", "errors", "Expected", "errors.py"]}]}


def portable(root, source):
    (root / "test_case.py").write_text(source, encoding="utf-8")
    # Ordinary directories keep subprocess fixtures accessible on managed Windows, where mode 0700 directories
    # are inaccessible to this session. This changes only scratch allocation, before pytest or product code runs.
    start = '''import os, tempfile, uuid
def scratch(suffix=None, prefix=None, dir=None):
    path = os.path.join(dir or tempfile.gettempdir(), (prefix or 'tmp') + uuid.uuid4().hex + (suffix or ''))
    os.makedirs(path)
    return path
tempfile.mkdtemp = scratch
import pytest
raise SystemExit(pytest.main(['--rootdir=.', '--confcutdir=.', 'test_case.py', '-q', '-p', 'no:cacheprovider']))
'''
    return subprocess.run([sys.executable, "-X", "utf8", "-c", start],
        cwd=root, capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(root)})


EXAMPLE_PATHS = {"ordinary": vt.TEMPLATES["example.v1"], "registry": vt.REGISTRY["example.v1"],
                 "confirmed": vt.instance_definition(vt.TemplateInstance(id="confirmed", template="example.v1",
                     requirement_id="R1", intent="change", bindings={"function": "calc.add", "cases": [
                         {"args": [2, 3], "returns": 5}]}, provenance="person_confirmed"))}


@pytest.mark.parametrize("path", [*EXAMPLE_PATHS, "scenario"])
def test_outcome_resolution_never_initialises_an_exception_module(tmp_path, path):
    (tmp_path / "calc.py").write_text(
        "ready = False\ndef add(a,b):\n    if ready:\n        from errors import Expected\n"
        "        raise Expected()\n    return a-b\n", encoding="utf-8")
    (tmp_path / "errors.py").write_text(
        "import calc\ncalc.ready = True\nclass Expected(Exception):\n    pass\n", encoding="utf-8")
    data = bindings()
    if path == "scenario":
        scenario = es.Scenario(json.dumps({"version": "example-scenario.v1", "setup": es.SETUP,
            "entrypoint": data["function"], "input": {"args": [2, 3], "kwargs": {}},
            "outcomes": {"A": {"expect": "raises", "error": data["cases"][0]["error"]},
                         "B": {"expect": "returns", "value": -1}}},
            sort_keys=True, separators=(",", ":")).encode("ascii"))
        source = scenario.source("A")
    else:
        source = vt.compile_text(EXAMPLE_PATHS[path], data)
    ran = portable(tmp_path, source)
    assert "1 failed" in ran.stdout, ran.stdout + ran.stderr
    assert "did not match the observed call" in ran.stdout and "PermissionError" not in ran.stdout


@pytest.mark.parametrize("path", EXAMPLE_PATHS)
def test_fresh_import_does_not_reuse_pytest_startup_state(tmp_path, path):
    (tmp_path / "calc.py").write_text("def add(a,b):\n    return a-b\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text("import calc\ncalc.add=lambda a,b:5\n", encoding="utf-8")
    data = {"function": bindings()["function"], "cases": [
        {"args": [2, 3], "kwargs": {}, "expect": "returns", "value": 5}]}
    ran = portable(tmp_path, vt.compile_text(EXAMPLE_PATHS[path], data))
    assert "1 failed" in ran.stdout, ran.stdout + ran.stderr
    assert "did not match the observed call" in ran.stdout and "PermissionError" not in ran.stdout


def test_confirmed_path_and_roots_allow_patched_bytes(tmp_path):
    from g6.harness.confirmed_expectation import Question
    from g6.workforce import repo_tests

    (tmp_path / "calc.py").write_text("def add(a,b):\n    return a+b\n", encoding="utf-8")
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
        outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this call",
        cited="calculation", baseline_sha256="b" * 64)
    # Resolution reads a real repository tree; no imported repository module participates.
    tree = vt.Tree(tmp_path, SimpleNamespace(base_commit="fixture"), frozenset({"calc.py"}), frozenset())
    from unittest.mock import patch
    with patch.object(vt.Tree, "read", return_value=tree), patch.object(repo_tests, "change"):
        scenario = es.Scenario.resolve(tmp_path, question)
    source = scenario.source("A")
    honest = portable(tmp_path, source)
    assert "1 passed" in honest.stdout, honest.stdout + honest.stderr
    (tmp_path / "calc.py").write_text("def add(a,b):\n    return 5\n# another referent\n", encoding="utf-8")
    changed = portable(tmp_path, source)
    assert "1 passed" in changed.stdout, changed.stdout + changed.stderr
    assert "import_roots" in json.loads(scenario.record)["entrypoint"][4]
    assert json.loads(scenario.record)["entrypoint"][4] == {"import_roots": [".", "src"]}


@pytest.mark.parametrize("custody", ["save", "load"])
def test_unbound_legacy_scenario_cannot_supply_enforcement_custody(tmp_path, custody):
    from g6.core.storage import state_path
    from g6.harness.confirmed_expectation import Question
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
        outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this call",
        cited="calculation", baseline_sha256="b" * 64)
    scenario = es.Scenario(json.dumps({"version": "example-scenario.v1", "setup": es.SETUP,
        "entrypoint": ["module", "calc", "add", "calc.py"], "input": {"args": [2, 3], "kwargs": {}},
        "outcomes": {"A": {"expect": "returns", "value": 5}, "B": {"expect": "returns", "value": -1}}},
        sort_keys=True, separators=(",", ":")).encode("ascii"))
    if custody == "load":
        path = state_path("scenarios", question.sha256 + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": "scenario-binding.v1", "question_sha256": question.sha256,
            "scenario_sha256": scenario.sha256, "scenario": scenario.record.decode("ascii")}), encoding="utf-8")
    with pytest.raises(ValueError, match="G6_E_SCENARIO_UNAVAILABLE"):
        es.save(question, scenario) if custody == "save" else es.load(question)


@pytest.mark.parametrize("startup", [
    {"conftest.py": "import calc\ncalc.add=lambda a,b:5\n"},
    {"conftest.py": "def pytest_pyfunc_call(pyfuncitem):\n    return True\n"},
    {"pytest.ini": "[pytest]\naddopts = -p override\n",
     "override.py": "def pytest_pyfunc_call(pyfuncitem):\n    return True\n"},
    {"pyproject.toml": '[tool.pytest.ini_options]\naddopts="--ignore=test_case.py"\n'},
])
@pytest.mark.parametrize("source_kind", ["compiled", "scenario"])
def test_product_observation_has_no_repository_startup_authority(tmp_path, monkeypatch, startup, source_kind):
    from g6.core import confine
    from g6.harness import verification_search as vs
    from g6.workforce import repo_tests

    (tmp_path / "calc.py").write_text("def add(a,b):\n    return a-b\n", encoding="utf-8")
    (tmp_path / "alternate").mkdir()
    (tmp_path / "alternate/calc.py").write_text("def add(a,b):\n    return a+b\n", encoding="utf-8")
    for name, text in startup.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    for args in [("init", "-q"), ("add", "."), ("-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "base")]:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, timeout=20)
    change = repo_tests.change(tmp_path)

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(repo_tests, "_bounded", execute)
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("fixture", "", spec))
    data = {"function": bindings()["function"], "cases": [
        {"args": [2, 3], "kwargs": {}, "expect": "returns", "value": 5}]}
    source = runner.ExecutableSource(vt.compile_text(vt._CONFIRMED_EXAMPLE, data), data)
    if source_kind == "scenario":
        from g6.harness.confirmed_expectation import Question
        question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
            outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this call",
            cited="calculation", baseline_sha256="b" * 64)
        source = es.Scenario.resolve(tmp_path, question).source("A")
    for roots in ((), ("alternate",)):
        ran = vs._run_check(change, "test_case.py", source, "test_case.py", at_base=False,
                            timeout_s=20, roots=roots)
        assert ran["g6_observation"]["result"] == "failed", ran
        assert ran["g6_observation"]["observed"]["outcomes"][0]["returned"] == {
            "sha256": hashlib.sha256(b"-1").hexdigest(), "size": 2, "prefix": "-1"}


@pytest.mark.parametrize("legacy_digest", [None, "0" * 64])
def test_confirmed_path_refuses_moved_or_missing_function_and_ignores_old_digest(tmp_path, monkeypatch, legacy_digest):
    from g6.core.ledger import canonical
    from g6.harness.confirmed_expectation import Question
    from tests.repair_question2.helpers import repo

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    root = repo(tmp_path)
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
        outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this call",
        cited="calculation", baseline_sha256="b" * 64)
    scenario = es.Scenario.resolve(root, question)
    if legacy_digest is not None:
        record = json.loads(scenario.record)
        record['entrypoint'][4]['sha256'] = legacy_digest
        scenario = es.Scenario(canonical(record).encode('ascii'))
    es.save(question, scenario)
    loaded = es.load(question)
    assert loaded.record == scenario.record
    scenario = loaded
    source = scenario.source('A')
    # The existing confirmation runs the patch, including a different return.
    (root / 'calc.py').write_text('def add(a,b):\n    return -1\n', encoding='utf-8')
    different = portable(root, source)
    assert '1 failed' in different.stdout, different.stdout + different.stderr
    assert 'the confirmed outcome did not match' in different.stdout, different.stdout + different.stderr
    (root / 'calc.py').write_text('def add(a,b):\n    return 5\n', encoding='utf-8')
    honest = portable(root, source)
    assert '1 passed' in honest.stdout, honest.stdout + honest.stderr
    (root / 'src').mkdir()
    (root / 'calc.py').rename(root / 'src/calc.py')
    with pytest.raises(ValueError, match='G6_E_OBSERVATION_UNAVAILABLE'):
        runner.located(root, scenario.comparison('A'))
    moved = portable(root, source)
    assert '1 passed' not in moved.stdout, moved.stdout + moved.stderr
    assert 'G6_E_OBSERVATION_UNAVAILABLE' in moved.stdout, moved.stdout + moved.stderr
    (root / 'src/calc.py').unlink()
    with pytest.raises(ValueError, match='G6_E_OBSERVATION_UNAVAILABLE'):
        runner.located(root, scenario.comparison('A'))
    missing = portable(root, source)
    assert '1 passed' not in missing.stdout, missing.stdout + missing.stderr
    assert 'G6_E_OBSERVATION_UNAVAILABLE' in missing.stdout, missing.stdout + missing.stderr
