import json
import os
import subprocess
import sys
import time

import pytest

from g6.harness import patch_review
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import target


API = "def changed():\n    return 1\ndef neighbour():\n    return changed()\n"


def oracle(root, selection):
    # This runs only the review's own synthetic fixture, never arbitrary input.
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "--confcutdir", str(root), "--rootdir", str(root),
         "--basetemp", str(root / "oracle-temp"), selection],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("suppressed", ["class", "method"])
def test_explicit_suppressed_body_is_not_admitted_without_collection(tmp_path, suppressed):
    source = ("from api import neighbour\nclass TestAPI:\n"
              "    def test_call(self):\n        neighbour()\n")
    source += ("TestAPI.__test__ = False\n" if suppressed == "class" else
               "TestAPI.test_call.__test__ = False\n")
    before = {"api.py": API, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    node = "test_api.py::TestAPI::test_call"
    collected = oracle(tmp_path, node)
    assert collected.returncode == 4, collected.stdout + collected.stderr
    assert "not found" in collected.stderr
    record = patch_review._change_neighbourhood(
        delta, (node,), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: {"status": "uncertain", "reason": "collection unavailable"},
    )
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)


@pytest.mark.parametrize("config,contents", [
    ("pytest.ini", "[pytest]\npython_classes = Spec*\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\npython_classes = ["Spec*"]\n'),
    ("setup.cfg", "[tool:pytest]\npython_classes = Spec*\n"),
    ("tox.ini", "[pytest]\npython_classes = Spec*\n"),
], ids=["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"])
def test_nested_configuration_prevents_static_admission(tmp_path, config, contents):
    source = ("from api import neighbour\nclass TestAPI:\n"
              "    def test_call(self):\n        neighbour()\n")
    before = {"api.py": API, "suite/test_api.py": source, "suite/" + config: contents}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    node = "suite/test_api.py::TestAPI::test_call"
    collected = oracle(tmp_path, node)
    assert collected.returncode == 4, collected.stdout + collected.stderr
    assert "not found" in collected.stderr
    record = patch_review._change_neighbourhood(
        delta, (node,), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: {"status": "uncertain", "reason": "collection unavailable"},
    )
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == [], json.dumps(sibling)
