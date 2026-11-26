import ast
import json
import os
import subprocess
import sys
import time

from g6.harness import patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import target


def test_custom_collected_callable_cannot_borrow_an_uncollected_body(tmp_path):
    api = "def changed():\n    return 1\ndef neighbour():\n    return changed()\n"
    source = ("from api import neighbour\ndef test_call():\n    neighbour()\n"
              "    raise AssertionError('original body ran')\ndef replacement():\n    pass\n")
    hook = ("import pytest\ndef pytest_pycollect_makeitem(collector, name, obj):\n"
            "    if name == 'test_call':\n"
            "        return pytest.Function.from_parent(collector, name=name,\n"
            "                                           callobj=collector.obj.replacement)\n")
    before = {"api.py": api, "test_api.py": source, "conftest.py": hook}
    delta = repository(tmp_path, before, {**before, "api.py": api.replace("return 1", "return 2")})
    # Use the real collection report producer and real pytest on synthetic code.
    # This oracle supplies the injectable collector seam, without pretending to
    # have an AppContainer backend on this review machine.
    tree = ast.parse(repo_tests._COLLECTION_BOOT)
    counted = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    producer = ast.unparse(counted)
    report = tmp_path / "counted.json"
    driver = ("import json, pytest, sys\ncounted = " + repr(str(report)) + "\n" + producer +
              "\nraise SystemExit(pytest.main(" + repr([
                  "test_api.py", "-q", "-p", "no:cacheprovider", "--confcutdir", str(tmp_path),
                  "--rootdir", str(tmp_path), "--basetemp", str(tmp_path / "oracle-temp"),
              ]) + " + sys.argv[1:], plugins=[_Counted()]))\n")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    collected_run = subprocess.run([sys.executable, "-B", "-c", driver, "--collect-only"],
                                   cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert collected_run.returncode == 0, collected_run.stdout + collected_run.stderr
    collected = repo_tests._read_collection(tmp_path / "counted.json.collection.json", 0, tmp_path)
    assert collected == {"status": "available", "nodes": ["test_api.py::test_call"]}
    ran = subprocess.run([sys.executable, "-B", "-c", driver], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0 and "1 passed" in ran.stdout, ran.stdout + ran.stderr
    record = patch_review._change_neighbourhood(
        delta, ("test_api.py",), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: collected,
    )
    sibling = target(record, "neighbour")
    assert sibling["exercised"] is not True and sibling["checks"] == [], json.dumps(sibling)
