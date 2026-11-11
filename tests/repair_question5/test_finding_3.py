"""A bytecode cache in the copy must not supply a dependency's code outside the execution manifest.

The copy's manifest and G6's checks of it (`vs._copy_manifest`, `repo_tests._copy_snapshot`, `vs._same_copy`) skip
`__pycache__` and `*.pyc` (`repo_tests._SKIPPED`), but the child's import system still reads a valid cache next to the
source: `-B` and `sys.dont_write_bytecode` only stop writing caches. Here a cache for the unchanged `helper.py` is
present in the copy before either manifest is read (written by a process outside the run; the run itself writes
nothing). The run counts as `passed`, bound to manifest bytes that say `VALUE = -1`.

Only the copy's contents and the OS boundary are fixtures; `_run_check`, the manifests, the runner, its bootstrap
and the frame reader are the product's, and the real isolated child runs.
"""
import importlib.util
import marshal
import struct
import subprocess
from contextlib import contextmanager
from pathlib import Path

from g6.core import confine
from g6.core.ledger import digest
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests
from tests.repair_question2.helpers import TASK, candidate, repo


def test_copied_dependency_cache_cannot_supply_unbound_positive_evidence(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={"helper.py": "VALUE = -1\n"})
    (root / "calc.py").write_text("from helper import VALUE\ndef add(a,b):\n    return VALUE\n")
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    question = bq.caller_candidates(TASK, [candidate()])[0].question("b" * 64)   # A: add(2, 3) returns 5
    source = runner.CONFIRMED + Scenario.resolve(root, question).source("A")
    real_copy = repo_tests.repository_copy
    inject = []

    @contextmanager
    def copied(*args, **kwargs):
        with real_copy(*args, **kwargs) as copy:
            if inject:
                helper = copy / "helper.py"
                stat = helper.stat()
                cache = Path(importlib.util.cache_from_source(str(helper)))
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack(
                    "<III", 0, int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF)
                    + marshal.dumps(compile("VALUE = 5\n", str(helper), "exec")))
                assert helper.read_bytes() == (root / "helper.py").read_bytes()   # the source is unchanged
            yield copy

    def execute(command, *, cwd, env, timeout_s, log, confined):
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(repo_tests, "repository_copy", copied)
    monkeypatch.setattr(repo_tests, "_bounded", execute)
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("review-fixture", "", spec))

    def run():
        return vs._run_check(change, "test_g6_example.py", source, "test_g6_example.py",
                             at_base=False, timeout_s=30, artefact=before)

    control = run()   # the same run without the cache observes the manifest's bytes
    assert control["g6_observation"]["result"] == "failed", control
    inject.append(True)
    observed = run()
    assert observed.get("verdict") != "passed", (
        "the copied source and the execution manifest say VALUE = -1, but an unmanifested cache supplied VALUE = 5; "
        f"evidence bound to source_before={observed.get('source_before')} "
        f"(manifest digest {digest(before.execution['files'])}): {observed.get('g6_observation')}")
