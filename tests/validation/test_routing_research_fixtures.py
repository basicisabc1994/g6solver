# SPDX-License-Identifier: Apache-2.0
"""Probe repository-specific wrong fixes without host clients, git writes or model calls."""
from __future__ import annotations

import os
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("routing_research_cases", Path(__file__).with_name("routing_research_cases.py"))
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)

ROOT = Path(__file__).resolve().parents[2]
ROUTING = ROOT / "validation/routing"
INVENTORY = {t["id"]: t for t in json.loads((ROUTING / "moment_tasks.json").read_text(encoding="utf-8"))["tasks"]}
TASKS = {INVENTORY[task_id]["fixture"]: task_id
         for task_id in ("moment.before_done.4", "moment.stuck.1", "moment.stuck.2")}
sys.modules["fixture_paths"] = cases.paths
ROWS = [(name, "shipped", None, None) for name in TASKS] + cases.VARIANTS


@pytest.mark.parametrize("name,label,rel,content", ROWS, ids=[f"{r[0]}-{r[1]}" for r in ROWS])
def test_natural_wrong_fixes_and_right_fixes(tmp_path, name, label, rel, content):
    work = tmp_path / "work"
    shutil.copytree(ROUTING / "fixtures" / name, work, ignore=shutil.ignore_patterns("__pycache__"))
    if rel:
        if content is None:
            content = cases._store("upper" if "upper" in label else "reserve" if "reserve only" in label else "all")
        (work / rel).write_text(content, encoding="utf-8", newline="\n")
    held = work / "held"
    shutil.copytree(ROUTING / "moment_outcome" / TASKS[name], held,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(cases.paths.__file__, held / "fixture_paths.py")
    codes = {}
    for target in ("tests", "held"):
        env = dict(os.environ, PYTHONPATH=str(work), G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                   G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", target, "-q", "--tb=short",
                                 "--basetemp", str(tmp_path / f"pt-{target}")], cwd=work, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode in (0, 1), result.stdout + result.stderr
        codes[target] = result.returncode
        print(f"{name} {label} {target}: {result.stdout.splitlines()[-1]}")
    right = label.startswith("right:") or label == "natural 1: the documented rule"
    assert codes["held"] == (0 if right else 1)
    assert codes["tests"] == (0 if (name == "invoices" or label.startswith("right:") or label.startswith("invented:")) else 1)


@pytest.mark.parametrize("n", [1, 2, 3, 4], ids=[
    f"{n}-{cases.paths.path_for(INVENTORY[f'moment.stuck.{n}']['fixture'], 'tests').name}"
    for n in (1, 2, 3, 4)])
def test_changed_visible_tests_fail_the_unchanged_check(tmp_path, n):
    import json

    tasks = json.loads((ROUTING / "moment_tasks.json").read_text(encoding="utf-8"))["tasks"]
    task = next(t for t in tasks if t["id"] == f"moment.stuck.{n}")
    work = tmp_path / "work"
    shutil.copytree(ROUTING / "fixtures" / task["fixture"], work, ignore=shutil.ignore_patterns("__pycache__"))
    check = ROUTING / "moment_outcome" / task["id"] / "test_outcome.py"
    namespace = {}
    exec(compile(check.read_text(encoding="utf-8"), str(check), "exec"), namespace)
    prior = Path.cwd()
    try:
        os.chdir(work)
        namespace["test_the_tests_are_as_shipped"]()
        with (work / cases.paths.path_for(task["fixture"], "tests")).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n# changed\n")
        with pytest.raises(AssertionError):
            namespace["test_the_tests_are_as_shipped"]()
    finally:
        os.chdir(prior)
