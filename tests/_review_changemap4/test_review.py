import json
import os
import subprocess
import sys
import time

import pytest

from g6.harness import patch_review
from g6.workforce.repo_tests import Change


API = "def target():\n    return 1\ndef neighbour():\n    return target()\n"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout.decode().strip()


def mapped(root, source, extra=None):
    before = {"api.py": API, "test_api.py": source, **(extra or {})}
    for rel, text in before.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Review", "-c", "user.email=review@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    commit = git(root, "rev-parse", "HEAD")
    after = API.replace("return 1", "return 2")
    (root / "api.py").write_text(after, encoding="utf-8")
    delta = Change(root, "HEAD", commit, "b" * 40, ("api.py",),
                   {"api.py": API}, {"api.py": after}, ("api.py",), ())
    record = patch_review._change_neighbourhood(
        delta, ("test_api.py",), (), (), deadline=time.monotonic() + 60)
    target = next(t for t in record["targets"] if t.get("symbol") == "neighbour")
    return target, record


def collect(root):
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "--confcutdir", str(root), "--rootdir", str(root),
         "--basetemp", str(root / "pytest-temp"), "test_api.py"],
        cwd=root, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("source, extra", [
    ("class TestHidden:\n    __test__ = 0\n    def test_call(self):\n        neighbour()\n", {}),
    ("__test__ = False\ndef test_call():\n    neighbour()\n", {}),
    ("class TestAbstract(metaclass=abc.ABCMeta):\n"
     "    @abc.abstractmethod\n    def test_call(self):\n        neighbour()\n", {}),
    ("class TestCustom:\n    def test_call(self):\n        neighbour()\n",
     {"conftest.py": "def pytest_pycollect_makeitem(collector, name, obj):\n"
                     "    if name == 'TestCustom':\n        return []\n"}),
], ids=["false-class", "disabled-module", "abstract-class", "custom-collector"])
def test_uncollected_body_is_not_reported_exercised(tmp_path, source, extra):
    source = "import abc\nfrom api import neighbour\n" + source
    source += "\ndef test_control():\n    pass\n" if "__test__ = False" not in source else ""
    target, record = mapped(tmp_path, source, extra)
    collected = collect(tmp_path)
    assert collected.returncode in (0, 5), collected.stdout + collected.stderr
    assert "::test_call" not in collected.stdout, collected.stdout
    assert target["exercised"] is False, json.dumps(record)


@pytest.mark.parametrize("source", [
    "class TestNative:\n    __new__ = object.__new__\n"
    "    def test_call(self):\n        neighbour()\n",
    "import unittest\nclass Case(unittest.TestCase):\n"
    "    def test_call(self):\n        neighbour()\n",
], ids=["native-new", "unittest"])
def test_collected_body_is_reported_exercised(tmp_path, source):
    target, record = mapped(tmp_path, "from api import neighbour\n" + source)
    collected = collect(tmp_path)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "::test_call" in collected.stdout, collected.stdout
    assert target["exercised"] is True, json.dumps(record)


def test_instance_base_is_not_a_class_base(tmp_path):
    source = ("from api import neighbour\nclass Base:\n"
              "    def test_call(self):\n        neighbour()\n"
              "class TestInvalid(Base()):\n    pass\n")
    target, record = mapped(tmp_path, source)
    collected = collect(tmp_path)
    assert collected.returncode == 2, collected.stdout + collected.stderr
    assert "TypeError" in collected.stdout
    assert target["exercised"] is False, json.dumps(record)


@pytest.mark.parametrize("config, text", [
    ("pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
    (".pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
    ("pytest.ini", "[pytest]\naddopts = -o python_functions=check_*\n"),
], ids=["pytest-toml", "hidden-pytest-toml", "addopts-override"])
def test_effective_config_is_used(tmp_path, config, text):
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    target, record = mapped(tmp_path, source, {config: text})
    collected = collect(tmp_path)
    assert collected.returncode == 5, collected.stdout + collected.stderr
    assert "::test_call" not in collected.stdout
    assert target["exercised"] is False, json.dumps(record)


@pytest.mark.skipif(os.name != "nt", reason="pytest normalizes glob case on Windows")
def test_glob_case_matches_pytest_on_windows(tmp_path):
    source = "from api import neighbour\ndef TEST_call():\n    neighbour()\n"
    target, record = mapped(tmp_path, source, {"pytest.ini": "[pytest]\npython_functions = test_*\n"})
    collected = collect(tmp_path)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "::TEST_call" in collected.stdout
    assert target["exercised"] is True, json.dumps(record)
