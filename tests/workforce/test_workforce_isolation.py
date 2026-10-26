# SPDX-License-Identifier: Apache-2.0
"""Operating-system isolation in the workforce's test bench (``g6.core.confine``): the bench and the run in place say
how their tests ran, refuse when isolation is required and unavailable, and, with the Windows backend active, the tests
run confined. The confined tests use ``confined_bench`` (``tests/workforce/conftest.py``): this interpreter when the
owner has granted it to the container, else a copy of its base installation with pytest that this session made. Every
probe is of host access only: a sentinel file outside the copy, a write into the repository, the folders G6 would ask
the owner to open, and the links a repository holds."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.workforce import bench, repo_tests, truth

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="the AppContainer backend is Windows's")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}
PROBE = {"repo": bench.PROBE, "tests": [bench.PROBE_TEST]}
PASSING = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True, env=GIT_ENV)


def _venture_repo(root: Path, tests: str) -> Path:
    repo = root / bench.REPOS / "v1" / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return 2 * x\n",
                                  encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(tests, encoding="utf-8")
    return repo


def _git_repo(repo: Path, tests: str) -> Path:
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(tests, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _stand_in_home(monkeypatch, home: Path, g6: Path) -> None:
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("G6_HOME", str(g6))
    g6.mkdir(parents=True)
    (g6 / ".env").write_text("G6_TEST_SENTINEL=not-a-key\n", encoding="utf-8")


def _granted(why: str) -> list[str]:
    """The folders the printed grant names. It prints two commands per folder (confine._grant_command, since
    a42142ac94): the folder's own grant, then ``<folder>\\*`` for what is already in it, so each second command must be
    of the folder just named, and the folder is counted once. Removing _grant_command's second command fails this."""
    named = re.findall(r'icacls "([^"]+)"', why)
    folders = named[0::2]
    assert named[1::2] == [folder.rstrip("\\/") + "\\*" for folder in folders], why
    return [os.path.realpath(path) for path in folders]


def test_the_bench_envelope_says_its_tests_ran_unconfined(monkeypatch) -> None:
    _off(monkeypatch)
    reply = bench.run("run_tests", PROBE)
    assert reply["ok"] and reply["result"]["passed"] is True
    # fails if bench.run stops putting confine.summary(runs) in its result
    isolation = reply["result"]["isolation"]
    assert (isolation["backend"], isolation["why"]) == (None, "unavailable: switched off by this test")


def test_the_bench_refuses_when_isolation_is_required_and_unavailable(monkeypatch) -> None:
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    reply = bench.run("run_tests", PROBE)
    # fails if truth.run_isolated runs the test anyway, or bench.run does not pass its refusal through
    assert reply["ok"] is False and reply["error"].startswith(f"[{ISOLATION_UNAVAILABLE}]")
    assert truth.LAST_RUN.get("started") is False


def test_the_run_in_place_says_how_its_tests_ran_and_refuses_when_required(monkeypatch) -> None:
    _off(monkeypatch)
    reply = bench.run(bench.IN_PLACE, PROBE)
    assert reply["ok"] and reply["result"]["passed"] is True
    # fails if repo_tests.run stops recording its plan, or bench._in_place stops passing it on
    assert reply["result"]["isolation"]["backend"] is None
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    refused = bench.run(bench.IN_PLACE, PROBE)
    assert refused["ok"] is False and refused["error"].startswith(f"[{ISOLATION_UNAVAILABLE}]")


def test_the_benchs_copy_does_not_follow_links_out_of_the_repository(tmp_path, monkeypatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path / "ws"))
    repo = _venture_repo(tmp_path / "ws", PASSING)
    made = []
    try:
        os.symlink(outside, repo / "linked", target_is_directory=True)
        made.append("linked")
    except OSError:
        pass
    if WINDOWS:   # a junction, inside tmp_path only
        subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "joined"), str(outside)], check=True,
                       capture_output=True)
        made.append("joined")
    if not made:
        pytest.skip("this system can make neither a symbolic link nor a junction")
    seen: list[str] = []

    def listed(nodeid, copy, timeout=None):
        seen.extend(path.name for path in Path(copy).rglob("*"))
        return False

    monkeypatch.setattr(truth, "run_isolated", listed)
    bench.run("run_tests", {"repo": "calc", "tests": ["tests/test_calc.py::test_add"]}, venture="v1")
    # fails if bench copies the repository with shutil.copytree's default, which follows links and junctions
    assert "test_calc.py" in seen and "secret.txt" not in seen and not set(made) & set(seen)


def test_the_copies_the_controls_the_probe_and_the_intake_run_on_are_folders_g6_made(tmp_path, monkeypatch) -> None:
    from g6.workforce import coding, coding_certificate

    seen: list[bool] = []

    class Stop(Exception):
        pass

    def suite(repo, timeout=None):
        seen.append(confine.owned(repo))
        raise Stop

    def isolated(nodeid, repo, timeout=None):
        seen.append(confine.owned(repo))
        return False

    monkeypatch.setattr(truth, "run_suite", suite)
    monkeypatch.setattr(truth, "run_isolated", isolated)
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path / "ws"))
    repo = _venture_repo(tmp_path / "ws", PASSING)
    with pytest.raises(Stop):
        coding_certificate._run_mutant(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    with pytest.raises(Stop):
        coding._prepare(None, source=repo, baseline="", objective="Fix add", repository={"repo": "calc"},
                        declared_risk="R1", budget_cents=0)
    bench.run("run_tests", {"repo": "calc", "tests": [], "files": {"calc.py": "def double(x):\n    return x + x\n"},
                            "regression": {"replay": [{"module": "calc.py", "function": "double", "inputs": [1]}]}},
              venture="v1")
    # fails if _run_mutant, coding._prepare or patch_checks copy into tempfile.TemporaryDirectory instead of
    # confine.run_folder (a folder G6 cannot grant, whose grant the owner would be told to make)
    assert len(seen) >= 4 and all(seen), seen


def test_reuse_refuses_a_bench_pass_whose_record_does_not_say_it_ran_confined() -> None:
    from g6.workforce import reuse
    from g6.workforce.capabilities import load_contracts
    from g6.workforce.contracts import VERIFIED, VERIFIER_GRADE

    contract = load_contracts()[("workforce", "run_tests")]
    artifact = {"artifact_id": "a1", "unit": "patch", "sha256": "s", "at": datetime.now(UTC).isoformat(), "provenance": {}}
    verdict = {"verdict_id": "v1", "artifact_id": "a1", "result": "pass", "executed": True, "state": VERIFIED,
               "artifact_sha": "s", "authority": VERIFIER_GRADE, "run_id": "r1", "unit_key": "check-patch"}

    class Recorded:   # what a ledger written before the bench reported its isolation holds
        def find_artifact(self, artifact_id):
            return "m1", artifact

        def last_artifact(self, mission_id, unit):
            return artifact

        def mission(self, mission_id):
            return {"risk_tier": "R1"}

        def verdicts(self, mission_id, unit):
            return [verdict]

        def run(self, mission_id, run_id):
            return {"calls_json": json.dumps([{"component": "workforce", "op": "run_tests", "refused": False,
                                               "contract_sha": contract.sha256}])}

    # fails if reuse.check serves a bench pass whose call record does not say its tests ran confined
    with pytest.raises(reuse.ReuseRefused, match="isolation"):
        reuse.check(Recorded(), "a1", contract=lambda component, op: contract, now=datetime.now(UTC), risk_tier="R1")


# --- the folders G6 asks the owner to open ------------------------------------------------------------------------------


@windows_only
def test_a_repository_under_the_home_folder_is_never_answered_with_a_grant_on_home(confined_bench, tmp_path,
                                                                                     monkeypatch) -> None:
    home = tmp_path / "home"
    _stand_in_home(monkeypatch, home, home / ".g6")
    repo = _git_repo(home / "proj", PASSING)
    found = repo_tests.run(repo, ["tests/test_calc.py"])
    # fails if repo_tests still puts the folder holding the repository among the read paths
    assert found["isolation"]["backend"] is None and found["verdict"] == repo_tests.PASSED
    assert _granted(found["isolation"]["why"]) == [os.path.realpath(repo)], found["isolation"]["why"]


@windows_only
def test_a_repository_that_holds_g6s_folder_is_refused_with_the_reason(confined_bench, tmp_path, monkeypatch) -> None:
    repo = tmp_path / "holder"
    _stand_in_home(monkeypatch, tmp_path / "home", repo / ".g6")
    _git_repo(repo, PASSING)
    found = repo_tests.run(repo, ["tests/test_calc.py"])
    why = found["isolation"]["why"]
    # fails if confine names an icacls grant on a folder that holds G6's own folder and the key file
    assert found["isolation"]["backend"] is None and "icacls" not in why and "key file" in why, why


@windows_only
def test_the_printed_grant_names_only_the_repository_and_the_interpreter_folders(tmp_path) -> None:
    folders = confine.interpreter_paths(sys.executable)
    if confine._readable_all(folders):
        pytest.skip("the owner has granted this interpreter to the container")
    repo = _git_repo(tmp_path / "projects" / "repo", PASSING)
    why = repo_tests.run(repo, ["tests/test_calc.py"])["isolation"]["why"]
    # Environmental: the grant names only the folders the container cannot read yet (one the owner has granted is left
    # out), and with an editable install interpreter_paths includes the folder holding g6's source (S5), which a
    # machine whose interpreter folders are granted now names on its own.
    # fails if a folder other than the repository and the interpreter's is named (the folder holding the repository)
    unread = {os.path.realpath(folder) for folder in folders if not confine._readable_all([folder])}
    assert set(_granted(why)) == {*unread, os.path.realpath(repo)}, why


# --- with the Windows backend active ------------------------------------------------------------------------------------


@windows_only
def test_the_benchs_tests_run_confined_and_cannot_read_outside_their_copy(confined_bench, tmp_path,
                                                                           monkeypatch) -> None:
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("secret", encoding="utf-8")
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path / "ws"))
    _venture_repo(tmp_path / "ws", PASSING + "\n\n"
                                   f"def test_secret():\n    assert open({str(sentinel)!r}).read() == 'secret'\n\n\n"
                                   "def test_tmp_path(tmp_path):\n    (tmp_path / 'x').write_text('y')\n")
    nodes = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_secret", "tests/test_calc.py::test_tmp_path"]
    reply = bench.run("run_tests", {"repo": "calc", "tests": nodes}, venture="v1")
    # fails if truth.run_isolated stops passing its confine.plan to the owned process
    assert reply["result"]["isolation"]["backend"] == "appcontainer", reply
    assert reply["result"]["tests"] == {nodes[0]: True, nodes[1]: False, nodes[2]: True}, truth.LAST_RUN
    _off(monkeypatch)
    control = bench.run("run_tests", {"repo": "calc", "tests": [nodes[1]]}, venture="v1")   # unconfined, it reads it
    assert control["result"]["tests"] == {nodes[1]: True}


@windows_only
def test_a_test_that_writes_into_its_copy_passes_confined(confined_bench, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path / "ws"))
    _venture_repo(tmp_path / "ws", "from pathlib import Path\n\n\ndef test_writes():\n"
                                   "    (Path(__file__).parent.parent / 'made.txt').write_text('x')\n")
    node = "tests/test_calc.py::test_writes"
    reply = bench.run("run_tests", {"repo": "calc", "tests": [node]}, venture="v1")
    # fails if truth._confined gives the container only read access to the bench's own copy
    assert reply["result"]["isolation"]["backend"] == "appcontainer" and reply["result"]["tests"] == {node: True}, (
        truth.LAST_RUN)


@windows_only
def test_a_regression_probe_runs_confined(confined_bench, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path / "ws"))
    _venture_repo(tmp_path / "ws", PASSING)
    reply = bench.run("run_tests", {"repo": "calc", "tests": [], "files": {"calc.py": (
        "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return 2 * x\n")},
        "regression": {"replay": [{"module": "calc.py", "function": "double", "inputs": [1, 2]}]}}, venture="v1")
    # fails if patch_checks writes the child's observations outside the folders the container may write
    assert truth.LAST_RUN["isolation"]["backend"] == "appcontainer"
    assert reply["result"]["probe"]["status"] == "pass", (reply, dict(truth.LAST_RUN))


@windows_only
def test_a_run_in_place_reads_the_repository_and_cannot_write_into_it(confined_bench, tmp_path) -> None:
    repo = _git_repo(tmp_path / "projects" / "repo", PASSING + "\n\n"
                     "def test_writes_into_the_repository():\n"
                     "    from pathlib import Path\n"
                     "    (Path(__file__).parent.parent / 'planted.txt').write_text('x')\n")
    chosen, _ = confine._windows()
    chosen.grant(str(repo), chosen.all_packages, write=False)   # the owner's one-time grant, on a folder the test made
    found = repo_tests.run(repo, ["tests/test_calc.py"])
    # fails if repo_tests._run stops confining the run (or gives the container write on the repository)
    assert found["isolation"]["backend"] == "appcontainer", found["output"]
    assert found["verdict"] == repo_tests.FAILED and found["failed_ids"] == [
        "tests/test_calc.py::test_writes_into_the_repository"], found["output"]
    assert found["passed"] == 1 and not (repo / "planted.txt").exists()
    # fails if a refused write in a confined run is not named as the reason
    assert found["said"] == repo_tests.SAID["confined_write"]
