# SPDX-License-Identifier: Apache-2.0
"""The development check of V9's production environment, on V7's images only (development data, never V9's
evaluation pool). For each image it records:

1. the image's own facts (its repository's commit, its Python);
2. in the container G6's arms use (``containers.g6_command``: the toolchain mounted read-only, the bubblewrap
   flags, ``G6_REPO_PYTHON``, ``G6_REPO_ENV``), G6's repository check on ``/app`` and one confined run of one unit-test
   folder through G6's runner with the image's interpreter and the repository's import roots (``probe.py``);
3. arm 1's command (``containers.tests_command``: the pristine image, no toolchain) on the same folder, and which
   ``PYTEST_*`` variables are left once ``apply.sh`` has run;
4. the planted-change control: a throwaway change (``plant.py``) breaking a function one chosen unit test
   exercises, applied as a patch would be. Arm 1's command must fail that test with the change and pass it without
   (``control_verdict``); for ansible also without its import roots, to show what they change. The same control runs
   through G6's runner in the G6 container. A control that does not fail means the arm is not testing the patch.

Each repository's environment (``REPOSITORIES``) is the one its V7 tasks' own run procedure sets, from the benchmark's
run scripts and Dockerfiles for these V7 tasks only (INTERPRETATIONS.md item 19). Only counts, verdicts, reasons and the
chosen control tests are recorded: no task statement, hidden test or reference patch is read. Only images already on
this computer are used (``--pull never``), only the V7 tasks ``CHECKS`` names, and never the V8 scored tasks in
``NEVER``.

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
#: Each repository's interpreter in its images, frozen test environment, import roots, one small unit-test folder,
#: and its planted control (one exact replacement in one file, and the test that must catch it).
REPOSITORIES = {
    "ansible/ansible": {
        "python": "/usr/bin/python", "folder": "test/units/errors",
        "env": {"ANSIBLE_VERBOSITY": "3"}, "import_roots": ["lib"],
        "control": {"file": "lib/ansible/errors/__init__.py", "old": "self._message = to_native(message)",
                    "new": "self._message = to_native(message) + ' (planted change)'",
                    "test": "test/units/errors/test_errors.py::TestErrors::test_basic_error"}},
    "qutebrowser/qutebrowser": {
        "python": "/usr/bin/python", "folder": "tests/unit/api",
        "env": {"QUTE_QT_WRAPPER": "PyQt6", "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-dev-shm-usage --disable-gpu"},
        "import_roots": [],
        "control": {"file": "qutebrowser/api/cmdutils.py",
                    "old": 'raise CommandError("Only one of {} can be given!".format(argstr))',
                    "new": "return  # planted change",
                    "test": "tests/unit/api/test_cmdutils.py::TestCheckExclusive::test_bad"}},
    "internetarchive/openlibrary": {
        "python": "/usr/local/bin/python", "folder": "openlibrary/utils/tests",
        "env": {"OL_CONFIG": "/app/conf/openlibrary.yml"}, "import_roots": [],
        "control": {"file": "openlibrary/utils/__init__.py", "old": "for c in s.lower() if c not in to_drop)",
                    "new": "for c in s if c not in to_drop)",
                    "test": "openlibrary/utils/tests/test_utils.py::test_str_to_key"}},
}
CHECKS = tuple({"task": task, "repository": repository, **REPOSITORIES[repository]} for task, repository in (
    ("instance_ansible__ansible-1bd7dcf339dd", "ansible/ansible"),
    ("instance_qutebrowser__qutebrowser-5cef49ff3074", "qutebrowser/qutebrowser"),
    ("instance_internetarchive__openlibrary-630221ab686c", "internetarchive/openlibrary"),
    ("instance_internetarchive__openlibrary-6e889f4a733c", "internetarchive/openlibrary"),
    ("instance_internetarchive__openlibrary-a48fd6ba9482", "internetarchive/openlibrary"),
    ("instance_internetarchive__openlibrary-e010b2a13697", "internetarchive/openlibrary")))
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


def control_verdict(without: str, with_change: str) -> str:
    """What a planted control shows about an arm: the control test passes without the change and fails with it."""
    if without != "passed":
        return "inconclusive: the control test does not pass without the change"
    return "tests the patch" if with_change == "failed" else "does not test the patch"


def tests_command(image: str, *, python: str, env: dict[str, str] | None = None,
                  import_roots: list[str] | None = None) -> list[str]:
    return containers.tests_command(image, python=python, env=env, import_roots=import_roots)


def probe_command(image: str, *, python: str, folder: str, volume: str, seconds: int,
                  env: dict[str, str] | None = None, import_roots: list[str] | None = None,
                  control_test: str = "") -> list[str]:
    """G6's arms' container with ``probe.py`` in place of the arm's child."""
    command = containers.g6_command(image, toolchain=volume, study=VALIDATION, repo_python=python, env=env)
    cut = command.index(containers.G6_PYTHON)
    return [*command[:cut], containers.G6_PYTHON, "-I",
            f"{containers.STUDY_MOUNT}/patch_review_utility/environment/probe.py", f"{containers.WORK}/probe.json",
            folder, str(seconds), json.dumps(list(import_roots or [])), control_test]


def settings_left_command(image: str, *, python: str, env: dict[str, str] | None = None) -> list[str]:
    """Arm 1's container with ``env`` in place of the tests: what it shows after ``apply.sh`` has run."""
    command = tests_command(image, python=python, env=env)
    return [*command[:command.index(python, command.index(image))], "env"]


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


def _work(folder: Path, plugin: bool, change: str = "") -> None:
    (folder / "change.diff").write_text(change, encoding="utf-8", newline="\n")    # "" is the base commit
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


def _name() -> str:
    return f"v9-dev-{time.monotonic_ns():x}"


def planted(image: str, check: dict) -> str:
    """The control's change as a diff against the image's base commit, made in a throwaway container."""
    with tempfile.TemporaryDirectory(prefix="v9-dev-plant-") as folder:
        (Path(folder) / "control.json").write_text(json.dumps(check["control"]), encoding="utf-8")
        done = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "-v",
                               f"{HERE}:/plant:ro", "-v", f"{folder}:/control:ro", "--entrypoint", check["python"],
                               image, "/plant/plant.py", "/control/control.json"], capture_output=True, text=True)
    if done.returncode != 0 or not done.stdout.strip():
        raise ValueError(f"the planted change could not be made: {done.stderr.strip()[:300]}")
    return done.stdout


def arm_1(image: str, check: dict, base: str, *, tests: list[str], change: str = "", roots: bool = True,
          seconds: int = SECONDS) -> dict:
    """Arm 1's command on ``tests`` with ``change`` applied: pytest's outcomes, and the control test's."""
    with tempfile.TemporaryDirectory(prefix="v9-dev-arm1-") as support:
        _work(Path(support), plugin=True, change=change)
        name = _name()
        command = _fill(tests_command(image, python=check["python"], env=check["env"],
                                      import_roots=check["import_roots"] if roots else None),
                        {"{container}": name, "{support}": support, "{base}": base}, tests)
        found = {"container": _run(command, name, seconds)}
        report = Path(support) / "outcomes.json"
        if report.is_file():
            raw = json.loads(report.read_text(encoding="utf-8"))
            outcomes = {node: executor._outcome(phases) for node, phases in raw["tests"].items()}
            states = list(outcomes.values())
            found.update(pytest_exit=raw["exit"], collection_errors=len(raw["collection_errors"]),
                         control_test=outcomes.get(check["control"]["test"], "missing"),
                         **{state: states.count(state) for state in ("passed", "failed", "skipped")})
        if not change:
            listed = subprocess.run(_fill(settings_left_command(image, python=check["python"], env=check["env"]),
                                          {"{container}": f"{name}-env", "{support}": support, "{base}": base}, []),
                                    capture_output=True, text=True)
            found["pytest_settings_left"] = sorted(line.split("=", 1)[0] for line in listed.stdout.splitlines()
                                                   if line.startswith("PYTEST_"))
    return found


def g6_probe(image: str, check: dict, base: str, *, volume: str, folder: str, change: str = "",
             seconds: int = SECONDS) -> dict:
    """The probe in G6's arms' container, with ``change`` applied."""
    with tempfile.TemporaryDirectory(prefix="v9-dev-g6-") as job:
        _work(Path(job), plugin=False, change=change)
        name = _name()
        probe = probe_command(image, python=check["python"], folder=folder, volume=volume, seconds=seconds,
                              env=check["env"], import_roots=check["import_roots"],
                              control_test=check["control"]["test"])
        container = _run(_fill(probe, {"{container}": name, "{job}": job, "{base}": base}, []), name, seconds + 120)
        report = Path(job) / "probe.json"
        return {"container": container, **(json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {})}


def check_image(check: dict, image: str, *, volume: str, seconds: int = SECONDS) -> dict:
    head = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "git",
                           image, "-C", containers.REPOSITORY, "rev-parse", "HEAD"], capture_output=True, text=True)
    base = head.stdout.strip()
    control_file = check["control"]["test"].partition("::")[0]
    found = {"task": check["task"], "repository": check["repository"], "image_id": _image_id(image), "base": base,
             "python": check["python"], "folder": check["folder"], "env": check["env"],
             "import_roots": check["import_roots"], "control": check["control"]}
    found["g6"] = g6_probe(image, check, base, volume=volume, folder=check["folder"], seconds=seconds)
    found["arm_1"] = arm_1(image, check, base, tests=[check["folder"]], seconds=seconds)
    try:
        change = planted(image, check)
    except ValueError as exc:
        found["planted_control"] = {"error": str(exc)}
        return found
    without = found["arm_1"].get("control_test", "missing")
    with_change = arm_1(image, check, base, tests=[control_file], change=change, seconds=seconds)
    control = {"arm_1": {"without_change": without, "with_change": with_change.get("control_test", "missing"),
                         "verdict": control_verdict(without, with_change.get("control_test", "missing"))}}
    if check["import_roots"]:
        unrooted = arm_1(image, check, base, tests=[control_file], change=change, roots=False, seconds=seconds)
        control["arm_1_without_import_roots"] = {
            "with_change": unrooted.get("control_test", "missing"),
            "verdict": control_verdict(without, unrooted.get("control_test", "missing"))}
    if (found["g6"].get("repository_check") or {}).get("result") == "passed":
        g6_without = found["g6"].get("control_test", "missing")
        g6_with = g6_probe(image, check, base, volume=volume, folder=control_file, change=change, seconds=seconds)
        control["g6_runner"] = {"without_change": g6_without, "with_change": g6_with.get("control_test", "missing"),
                                "verdict": control_verdict(g6_without, g6_with.get("control_test", "missing")),
                                "note": "through G6's runner (repo_tests.run) with the import roots; review_patch "
                                        "takes import_roots once the pool-compatibility change lands"}
    found["planted_control"] = control
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
        print(json.dumps({key: results[-1].get(key) for key in ("task", "planted_control")}), flush=True)
    Path(args.output).write_text(json.dumps({"study": "V9", "kind": "development check on V7's images",
                                             "toolchain": json.loads(Path(args.lock).read_text(encoding="utf-8")),
                                             "results": results}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
