# SPDX-License-Identifier: Apache-2.0
"""The development check of V9's production environment, on V7's images only (development data, never V9's
evaluation pool). For each image, with an empty change, it records:

1. the image's own facts (its repository's commit, its Python);
2. in the container G6's arms use (``containers.g6_command``: the toolchain mounted read-only, the bubblewrap
   flags, ``G6_REPO_PYTHON``), G6's repository check on ``/app`` and one confined run of one unit-test folder through
   G6 with the image's interpreter (``probe.py``);
3. arm 1's command (``containers.tests_command``: the pristine image, no toolchain) on the same folder.

Only counts, verdicts and reasons are recorded: no task statement, hidden test, reference patch, test name or file
contents is read. Only images already on this computer are used (``--pull never``), only the V7 tasks ``CHECKS``
names, and never the V8 scored tasks in ``NEVER``.

    python dev_check.py --volume <toolchain volume> --lock <its lock> --output <results.json>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parents[1]
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from patch_review_utility import containers, executor  # noqa: E402
from patch_review_utility.environment import toolchain  # noqa: E402

PREFIX = "ghcr.io/scaleapi/swe-bench_pro-v2:"
#: V7's development images this check may use, with each repository's interpreter and one small unit-test folder.
#: qutebrowser chooses its Qt binding from QUTE_QT_WRAPPER (its image has PyQt6 only) and needs a screenless Qt.
_ANSIBLE = ("ansible/ansible", "/usr/bin/python", "test/units/errors", {})
_QUTEBROWSER = ("qutebrowser/qutebrowser", "/usr/bin/python", "tests/unit/api",
                {"QUTE_QT_WRAPPER": "PyQt6", "QT_QPA_PLATFORM": "offscreen"})
_OPENLIBRARY = ("internetarchive/openlibrary", "/usr/local/bin/python", "openlibrary/utils/tests", {})
CHECKS = tuple({"task": task, "repository": repository, "python": python, "folder": folder, "env": env}
               for task, (repository, python, folder, env) in (
                   ("instance_ansible__ansible-1bd7dcf339dd", _ANSIBLE),
                   ("instance_qutebrowser__qutebrowser-5cef49ff3074", _QUTEBROWSER),
                   ("instance_internetarchive__openlibrary-630221ab686c", _OPENLIBRARY),
                   ("instance_internetarchive__openlibrary-6e889f4a733c", _OPENLIBRARY),
                   ("instance_internetarchive__openlibrary-a48fd6ba9482", _OPENLIBRARY),
                   ("instance_internetarchive__openlibrary-e010b2a13697", _OPENLIBRARY)))
#: V8's scored tasks: never pulled, never opened.
NEVER = ("instance_qutebrowser__qutebrowser-96b997802e94", "instance_qutebrowser__qutebrowser-ec2dcfce9eee")
SECONDS = 900


def select(listed: list[str], task: str) -> str:
    """The one local image of ``task`` among ``listed`` (``repository:tag`` names)."""
    if any(bad in task for bad in NEVER):
        raise ValueError(f"{task} is a V8 scored task: never opened")
    found = [name for name in listed if name.startswith(PREFIX + task)]
    if not found:
        raise ValueError(f"{task}'s image is not on this computer, and this check never pulls one")
    if len(found) > 1:
        raise ValueError(f"more than one local image matches {task}")
    return found[0]


def tests_command(image: str, *, python: str, env: dict[str, str] | None = None) -> list[str]:
    return containers.tests_command(image, python=python, env=env)


def probe_command(image: str, *, python: str, folder: str, volume: str, seconds: int,
                  env: dict[str, str] | None = None) -> list[str]:
    """G6's arms' container with ``probe.py`` in place of the arm's child; ``V9_PROBE_TEST_ENV`` tells the probe the
    names of the variables the repository's tests need."""
    command = containers.g6_command(image, toolchain=volume, study=VALIDATION, repo_python=python, env=env)
    cut = command.index(containers.G6_PYTHON)
    told = ["-e", "V9_PROBE_TEST_ENV=" + ",".join(sorted(env))] if env else []
    image_at = command.index(image)
    return [*command[:image_at], *told, *command[image_at:cut], containers.G6_PYTHON, "-I",
            f"{containers.STUDY_MOUNT}/patch_review_utility/environment/probe.py", f"{containers.WORK}/probe.json",
            folder, str(seconds)]


def _fill(command: list[str], words: dict[str, str], tests: list[str]) -> list[str]:
    filled = []
    for word in command:
        if word == "{tests}":
            filled += tests
            continue
        for key, value in words.items():
            word = word.replace(key, value)
        filled.append(word)
    return filled


def _work(folder: Path, plugin: bool) -> None:
    (folder / "change.diff").write_bytes(b"")                       # the base commit, unchanged
    (folder / "apply.sh").write_text(containers.APPLY, encoding="utf-8", newline="\n")
    if plugin:
        (folder / "v9_outcomes.py").write_text(executor.PLUGIN, encoding="utf-8")


def _run(command: list[str], name: str, seconds: int) -> dict:
    started = time.monotonic()
    try:
        done = subprocess.run(command, capture_output=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return {"timed_out": True, "container_stopped": executor.stop_container(command, name),
                "seconds": round(time.monotonic() - started, 1)}
    said = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return {"exit_code": done.returncode, "seconds": round(time.monotonic() - started, 1),
            "stderr_tail": said[-3:] if done.returncode else []}


def check_image(check: dict, image: str, *, volume: str, seconds: int = SECONDS) -> dict:
    head = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "git",
                           image, "-C", containers.REPOSITORY, "rev-parse", "HEAD"], capture_output=True, text=True)
    base = head.stdout.strip()
    found = {"task": check["task"], "repository": check["repository"], "image_id": _image_id(image), "base": base,
             "python": check["python"], "folder": check["folder"], "env": check["env"]}
    with tempfile.TemporaryDirectory(prefix="v9-dev-g6-") as job:
        _work(Path(job), plugin=False)
        name = f"v9-dev-{time.monotonic_ns():x}"
        probe = probe_command(image, python=check["python"], folder=check["folder"], volume=volume, seconds=seconds,
                              env=check["env"])
        command = _fill(probe, {"{container}": name, "{job}": job, "{base}": base}, [])
        found["g6_container"] = _run(command, name, seconds + 120)
        report = Path(job) / "probe.json"
        found["g6"] = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    with tempfile.TemporaryDirectory(prefix="v9-dev-arm1-") as support:
        _work(Path(support), plugin=True)
        name = f"v9-dev-{time.monotonic_ns():x}"
        command = _fill(tests_command(image, python=check["python"], env=check["env"]),
                        {"{container}": name, "{support}": support, "{base}": base}, [check["folder"]])
        found["arm_1_container"] = _run(command, name, seconds)
        report = Path(support) / "outcomes.json"
        if report.is_file():
            raw = json.loads(report.read_text(encoding="utf-8"))
            states = [executor._outcome(phases) for phases in raw["tests"].values()]
            found["arm_1"] = {"pytest_exit": raw["exit"], "collection_errors": len(raw["collection_errors"]),
                              **{state: states.count(state) for state in ("passed", "failed", "skipped")}}
        else:
            found["arm_1"] = None
    return found


def _image_id(image: str) -> str:
    return subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture_output=True,
                          text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--volume", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    problems = toolchain.check(toolchain.load(), volume=args.volume, lock=Path(args.lock))
    if problems:
        raise SystemExit("the toolchain does not match its lock: " + "; ".join(problems))
    listed = subprocess.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True,
                            text=True, check=True).stdout.split()
    results = []
    for check in CHECKS:
        try:
            image = select(listed, check["task"])
        except ValueError as exc:
            results.append({"task": check["task"], "skipped": str(exc)})
            continue
        results.append(check_image(check, image, volume=args.volume))
        print(json.dumps({key: results[-1].get(key) for key in ("task", "g6", "arm_1")}), flush=True)
    Path(args.output).write_text(json.dumps({"study": "V9", "kind": "development check on V7's images",
                                             "toolchain": json.loads(Path(args.lock).read_text(encoding="utf-8")),
                                             "results": results}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
