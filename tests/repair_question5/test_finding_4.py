"""A dependency replaced after G6's last read of the copy, before the child starts, must invalidate the run.

`behaviour_runner.run` reads the copy once (`before = rt._copy_snapshot(copy)`), then resolves the entry point,
writes its bootstrap, plans confinement and starts the child; it never reads the copy again ("binding names the
starting copy, never a later live read"). The ordinary executor's after-run read (`repo_tests._run`:
`_moved(before, after)` -> CHANGED) does not apply to owned runs. A write by a process outside the run in that window
runs other bytes than the ones the evidence names.

Only the OS boundary is a fixture (the write is placed at the moment G6 hands the command to it); `_run_check`, the
manifests, the runner, its bootstrap and the frame reader are the product's, and the real isolated child runs.
"""
import subprocess

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests
from tests.repair_question2.helpers import TASK, candidate, repo


def test_dependency_replaced_between_last_read_and_startup_invalidates_run(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={"helper.py": "VALUE = -1\n"})
    (root / "calc.py").write_text("from helper import VALUE\ndef add(a,b):\n    return VALUE\n")
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    question = bq.caller_candidates(TASK, [candidate()])[0].question("b" * 64)   # A: add(2, 3) returns 5
    source = runner.CONFIRMED + Scenario.resolve(root, question).source("A")
    replace = []
    executed = []

    def execute(command, *, cwd, env, timeout_s, log, confined):
        if replace:
            # A writer outside the run replaces the unchanged dependency; the child itself changes no file.
            part = cwd / "replacement"
            part.write_bytes(b"VALUE = 5\n")
            part.replace(cwd / "helper.py")
        executed.append(repo_tests._digest(cwd / "helper.py"))
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(repo_tests, "_bounded", execute)
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("review-fixture", "", spec))

    def run():
        return vs._run_check(change, "test_g6_example.py", source, "test_g6_example.py",
                             at_base=False, timeout_s=30, artefact=before)

    control = run()   # the same run without the write observes the manifest's bytes
    assert control["g6_observation"]["result"] == "failed", control
    assert executed[-1] == before.execution["files"]["helper.py"]
    replace.append(True)
    observed = run()
    assert executed[-1] != before.execution["files"]["helper.py"]
    assert observed.get("verdict") != "passed", (
        "other bytes than the manifest's ran and their pass was kept, bound to the old manifest: "
        f"helper.py in execution_artifact={observed.get('execution_artifact', {}).get('files', {}).get('helper.py')}, "
        f"executed={executed[-1]}; {observed.get('g6_observation')}")
