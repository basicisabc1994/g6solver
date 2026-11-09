"""The form's fresh-import setup must survive baseline pytest startup state."""
import os
import subprocess
import sys

from g6.harness import behaviour_question as bq
from g6.harness.executable_scenario import Scenario
from .helpers import TASK, candidate, repo


def test_confirmed_fresh_import_is_not_replaced_by_pytest_startup_state(tmp_path):
    root = repo(tmp_path)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "conftest.py").write_text("import calc\ncalc.add = lambda a, b: a + b\n", encoding="utf-8")
    one = bq.caller_candidates(TASK, [candidate()])[0]
    question = one.question("b" * 64)
    scenario = Scenario.resolve(root, question)
    message = bq.form(question, one, scenario=scenario)[0]
    assert "fresh import" in message and "no other state set up" in message
    source = scenario.source("A")
    (root / "test_confirmed.py").write_text(source, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    ran = subprocess.run([sys.executable, "-m", "pytest", "test_confirmed.py", "-q", "-p", "no:cacheprovider"],
                         cwd=root, env=env, text=True, capture_output=True, timeout=20)
    assert any(status in ran.stdout for status in ("1 passed", "1 failed", "1 skipped")), ran.stdout + ran.stderr
    assert "1 passed" not in ran.stdout, "confirmed fresh-import check passed on calc.add that returns -1 on a fresh import"
