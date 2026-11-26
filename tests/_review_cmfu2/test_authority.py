import json
import os
import subprocess
import sys
import time

import pytest

from g6.harness import patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import target


API = "def changed():\n    return 1\ndef neighbour():\n    raise AssertionError('unexercised neighbour')\n    return changed()\n"
NODE = "test_api.py::test_call"


def oracle(root, selection="test_api.py", collect_only=True, extra=()):
    # Real pytest and the complete shipped bootstrap, without claiming that this
    # synthetic-code oracle provides the unavailable AppContainer confinement.
    own = root.parent / (root.name + "-reports")
    own.mkdir(exist_ok=True)
    counted = own / "counted.json"
    variables = own / "env.json"
    variables.write_text("{}", encoding="utf-8")
    args = [selection, "-q", "-p", "no:cacheprovider", "--confcutdir", str(root),
            "--rootdir", str(root), "--basetemp", str(own / "oracle-temp"),
            "--junitxml=" + str(own / "report.xml"), "--log-file=" + str(own / "pytest.log")]
    if collect_only:
        args.append("--collect-only")
    args.extend(extra)
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-I", "-B", "-X", "utf8", "-c", repo_tests._COLLECTION_BOOT,
               json.dumps([str(root)]), "0", "", "", str(counted), str(variables),
               str(own / "loaded.json"), str(repo_tests.LOADED_LIMIT), str(root), *args]
    ran = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    answer = repo_tests._read_collection(own / "counted.json.collection.json", ran.returncode, root)
    return ran, answer


def mapped(delta, answer, selection="test_api.py"):
    record = patch_review._change_neighbourhood(
        delta, (selection,), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: answer)
    return target(record, "neighbour")


@pytest.mark.parametrize("replacement", ["same_line_lambda", "rewritten_code", "exec_replacement"])
def test_matching_location_is_not_callable_identity(tmp_path, replacement):
    if replacement == "same_line_lambda":
        source = ("from api import neighbour\nfrom functools import wraps\n"
                  "@lambda fn: wraps(fn)(lambda: None)\n"
                  "def test_call():\n    neighbour()\n")
        line = 3
    elif replacement == "rewritten_code":
        source = ("from api import neighbour\n"
                  "def test_call():\n    neighbour()\n"
                  "def replacement():\n    pass\n"
                  "test_call.__code__ = replacement.__code__.replace(\n"
                  "    co_filename=test_call.__code__.co_filename, co_firstlineno=2)\n")
        line = 2
    else:
        source = ("from api import neighbour\n"
                  "def test_call():\n    neighbour()\n"
                  "exec(compile('\\ndef test_call():\\n    pass\\n', __file__, 'exec'))\n")
        line = 2
    before = {"api.py": API, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer["bodies"][NODE] == {"file": "test_api.py", "line": line}
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)


def test_repository_can_forge_the_callable_report(tmp_path):
    source = ("from api import neighbour\ndef test_call():\n    neighbour()\n"
              "def replacement():\n    pass\n")
    hook = '''import json
import sys
import pytest

def pytest_pycollect_makeitem(collector, name, obj):
    if name == "test_call":
        return pytest.Function.from_parent(collector, name=name, callobj=collector.obj.replacement)

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session):
    yield
    path = sys.modules["__main__"].counted + ".collection.json"
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    report["bodies"] = {"test_api.py::test_call": {"file": "test_api.py", "line": 2}}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
'''
    before = {"api.py": API, "test_api.py": source, "conftest.py": hook}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer["bodies"][NODE] == {"file": "test_api.py", "line": 2}
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)


def test_inherited_external_test_write_blocks_static_admission(tmp_path):
    base = ("from api import neighbour\nclass Parent:\n"
            "    def test_call(self):\n        neighbour()\nParent.__test__ = False\n")
    source = "from base import Parent\nclass TestAPI(Parent):\n    pass\n"
    before = {"api.py": API, "test_api.py": source, "base.py": base}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    selection = "test_api.py::TestAPI::test_call"
    run, _ = oracle(tmp_path, selection)
    assert run.returncode == 4 and "not found" in run.stderr, run.stdout + run.stderr
    sibling = mapped(delta, {"status": "uncertain", "reason": "collection unavailable"}, selection)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)


def test_static_admission_cannot_ignore_replacing_decorator(tmp_path):
    source = ("from api import neighbour\nfrom functools import wraps\n"
              "def discard(fn):\n    @wraps(fn)\n    def wrapper():\n        pass\n    return wrapper\n"
              "@discard\ndef test_call():\n    neighbour()\n")
    before = {"api.py": API, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path, NODE)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer["bodies"][NODE] == {"file": "test_api.py", "line": 4}
    assert mapped(delta, answer, NODE)["exercised"] == "uncertain"
    run, _ = oracle(tmp_path, NODE, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, {"status": "uncertain", "reason": "collection unavailable"}, NODE)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)


def test_transitive_replacing_decorator_cannot_borrow_helper_body(tmp_path):
    source = ("from api import neighbour\n"
              "def discard(fn):\n    return lambda: None\n"
              "@discard\ndef helper():\n    neighbour()\n"
              "def test_call():\n    helper()\n")
    before = {"api.py": API, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer["bodies"][NODE] == {"file": "test_api.py", "line": 7}
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling["exercised"] != True and sibling["checks"] == [], json.dumps(sibling)


def test_unresolved_item_does_not_erase_a_known_positive(tmp_path):
    api = API.replace("    raise AssertionError('unexercised neighbour')\n", "")
    source = ("from api import neighbour\ndef test_call():\n    assert neighbour() == 2\n"
              "def discard(fn):\n    return lambda: None\n"
              "@discard\ndef test_other():\n    pass\n")
    before = {"api.py": api, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": api.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer["nodes"] == [NODE, "test_api.py::test_other"]
    assert answer["bodies"] == {NODE: {"file": "test_api.py", "line": 2}}
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "2 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling["checks"] == ["test_api.py"], json.dumps(sibling)
    assert sibling["exercised"] is True, json.dumps(sibling)


