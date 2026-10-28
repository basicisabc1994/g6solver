# SPDX-License-Identifier: Apache-2.0
"""V9's containers (``validation/patch_review_utility/containers.py``): each arm in its task's own image, the patch
applied inside the container, G6's toolchain mounted read-only for the arms that run G6 and never for arm 1.

Development fixtures only: a tiny repository made here, and a fake container program that records what it was asked
and writes the reply a real container would. No image is started."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from patch_review_utility import arms, containers, contract, executor, trees

IMAGE = "registry.invalid/task:development-fixture"
FAKE = """\
import json, os, sys, time
args = sys.argv[1:]
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker.log")   # the executor passes a bare environment
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[0] != "run":
    sys.exit(0)
mounts = {}
for i, word in enumerate(args):
    if word == "-v":
        parts = args[i + 1].split(":")
        if parts[-1] in ("ro", "rw"):
            parts = parts[:-1]
        mounts[parts[-1]] = ":".join(parts[:-1])
work = mounts.get("/v9")
if os.environ.get("V9_FAKE_HANG"):
    time.sleep(60)
change = open(os.path.join(work, "change.diff"), encoding="utf-8").read()
assert os.path.isfile(os.path.join(work, "apply.sh"))
if "/opt/v9/patch_review_utility/child.py" in args:
    job = json.load(open(os.path.join(work, "job.json"), encoding="utf-8"))
    reply = {"disposition": {"accepted": True, "category": "accept", "error_kind": "", "time_or_cap_reached": False,
                             "detail": "", "cost": {"seconds": 0.0, "processor_seconds": 0.0, "model_calls": 0}},
             "trace": {"commands": [], "isolation": []}, "job": job, "change": change}
    json.dump(reply, open(os.path.join(work, "reply.json"), "w", encoding="utf-8"))
else:
    tests = {"test_calc.py::test_add": {"setup": "passed", "call": "passed", "teardown": "passed"}}
    json.dump({"tests": tests, "collection_errors": [], "exit": 0, "rootdir": "/app"},
              open(os.path.join(work, "outcomes.json"), "w", encoding="utf-8"))
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8", newline="\n")
    (source / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                         encoding="utf-8", newline="\n")
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "-c", "user.name=F", "-c", "user.email=f@example.invalid", "-c", "commit.gpgsign=false", "commit",
        "-qm", "development fixture")
    work = trees.checkout(source, git(source, "rev-parse", "HEAD").strip(), tmp_path / "tree")
    (work / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8", newline="\n")
    (work / "test_more.py").write_text("def test_more():\n    assert True\n", encoding="utf-8", newline="\n")
    return work


@pytest.fixture
def fake(tmp_path, monkeypatch):
    program = tmp_path / "fake_docker.py"
    program.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(program)], tmp_path / "docker.log"


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_arm_1_runs_in_the_pristine_image_with_the_network_closed_and_no_toolchain():
    command = containers.tests_command(IMAGE, python="/usr/bin/python")
    assert command[:3] == ["docker", "run", "--rm"] and "{container}" in command and "{base}" in " ".join(command)
    assert command[command.index("--pull") + 1] == "never"                  # an image is never fetched in a study
    assert containers.g6_command(IMAGE, toolchain="t", study=Path("/s"), repo_python="python")[3:5] == ["--pull",
                                                                                                         "never"]
    assert command[command.index("--network") + 1] == "none"
    assert not any(containers.TOOLCHAIN_MOUNT in word for word in command)
    assert "--cap-add" not in command and IMAGE in command and "/usr/bin/python" in command
    assert command[command.index(IMAGE) + 1:command.index(IMAGE) + 3] == ["sh", "/v9/apply.sh"]


def test_the_arms_that_run_g6_mount_the_toolchain_read_only_with_bubblewrap_flags_and_the_images_python():
    command = containers.g6_command(IMAGE, toolchain="g6-v9-toolchain-x", study=Path("/study"),
                                    repo_python="/usr/bin/python")
    joined = " ".join(command)
    for flag in ("--network none", "--cap-add SYS_ADMIN", "--security-opt seccomp=unconfined",
                 "--security-opt apparmor=unconfined"):
        assert flag in joined
    assert "g6-v9-toolchain-x:/opt/g6:ro" in command and f"{Path('/study')}:/opt/v9:ro" in command
    assert "PATH=/opt/g6/bin:" + containers.SYSTEM_PATH in command
    assert "G6_REPO_PYTHON=/usr/bin/python" in command and "G6_EXPECT_BACKEND=bubblewrap" in command
    assert command[-5:] == [containers.G6_PYTHON, "-I", "/opt/v9/patch_review_utility/child.py", "/v9/job.json",
                            "/v9/reply.json"]
    assert not any("G6_ECHA_RESEARCH" in word for word in command)


def test_a_repositorys_test_environment_is_set_in_both_containers():
    env = {"QUTE_QT_WRAPPER": "PyQt6", "QT_QPA_PLATFORM": "offscreen"}
    for command in (containers.tests_command(IMAGE, python="python", env=env),
                    containers.g6_command(IMAGE, toolchain="t", study=Path("/s"), repo_python="python", env=env)):
        before = command[:command.index(IMAGE)]
        assert "QT_QPA_PLATFORM=offscreen" in before and "QUTE_QT_WRAPPER=PyQt6" in before
        assert before[before.index("QUTE_QT_WRAPPER=PyQt6") - 1] == "-e"


def test_staging_writes_the_change_against_head_and_the_script_that_applies_it_without_touching_the_tree(tree,
                                                                                                         tmp_path):
    before = git(tree, "status", "--porcelain")
    base = containers.stage(tree, tmp_path / "work")
    assert base == git(tree, "rev-parse", "HEAD").strip()
    change = (tmp_path / "work" / "change.diff").read_text(encoding="utf-8")
    assert "+    return a + b" in change and "test_more.py" in change
    script = (tmp_path / "work" / "apply.sh").read_text(encoding="utf-8")
    assert "V9_BASE" in script and "git apply" in script and "\r" not in script
    assert git(tree, "status", "--porcelain") == before                    # the arm's tree and its index are untouched


def test_the_executor_runs_a_container_command_with_the_change_staged(tree, fake):
    program, log = fake
    command = [*program, *containers.tests_command(IMAGE)[1:]]
    run = executor.Executor(command, confined=True, network=False).run(tree, ["test_calc.py"], timeout_s=60)
    assert executor.outcome(run, "test_calc.py::test_add") == "passed" and run["error"] == ""
    (asked,) = calls(log)
    assert f"V9_BASE={git(tree, 'rev-parse', 'HEAD').strip()}" in asked
    assert not any("{" in word for word in asked)                          # every placeholder was filled


def test_g6s_arms_run_their_child_in_the_container_on_the_patched_image_tree(tree, fake, tmp_path):
    program, log = fake
    command = [*program, *containers.g6_command(IMAGE, toolchain="tc", study=tmp_path, repo_python="python")[1:]]
    requirements = contract.split("Addition returns the sum.")
    got = arms.in_child("g6", tree, requirements, ["test_more.py"], timeout_s=30, command=command)
    assert got["disposition"]["accepted"] is True and got["child"]["exit_code"] == 0
    assert got["job"]["tree"] == containers.REPOSITORY and got["job"]["roots"] == [containers.STUDY_MOUNT]
    assert "+    return a + b" in got["change"]


def test_a_container_that_overruns_is_stopped_by_name_and_is_a_tool_error(tree, fake, monkeypatch, tmp_path):
    program, log = fake
    monkeypatch.setenv("V9_FAKE_HANG", "1")
    command = [*program, *containers.g6_command(IMAGE, toolchain="tc", study=tmp_path, repo_python="python")[1:]]
    got = arms.in_child("direct_checks", tree, contract.split("x"), [], timeout_s=1, kill_after_s=4, command=command)
    assert got["disposition"]["error_kind"] == "killed_at_time_limit"
    started, *rest = calls(log)
    name = started[started.index("--name") + 1]
    assert ["kill", name] in rest and ["rm", "-f", name] in rest
