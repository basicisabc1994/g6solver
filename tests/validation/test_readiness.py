# SPDX-License-Identifier: Apache-2.0
"""The replay cohort's readiness report (``readiness.py``; the design's section 3.6): read-only, from its probes. Fakes only: no
docker, no model, no sign-in read."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import cohorts  # noqa: E402
import readiness  # noqa: E402


class Machine:
    """A machine's answers to the report's commands: ``daemon`` answers or not; ``held`` the image tags docker holds."""

    def __init__(self, *, daemon=True, held=()):
        self.daemon, self.held, self.asked = daemon, set(held), []

    def __call__(self, argv):
        self.asked.append(list(argv))
        sub = argv[1]
        if sub == "version":
            return SimpleNamespace(returncode=0 if self.daemon else 1, stdout=b"28.5.1\n" if self.daemon else b"", stderr=b"")
        if sub == "info":
            return SimpleNamespace(returncode=0, stdout=b"16743403520 8\n", stderr=b"")
        if argv[1:3] == ["image", "inspect"]:
            tag = argv[3]
            got = tag in self.held
            digest = "sha256:" + ("1" if "task" in tag else "2") * 64
            return SimpleNamespace(returncode=0 if got else 1, stdout=digest.encode() if got else b"", stderr=b"")
        raise AssertionError(f"a command that is not read-only: {argv}")


def _held(bugs):
    return {tag for bug in bugs for tag in cohorts.we.image_names(SimpleNamespace(id=bug), cohorts.G6_COMMIT, cohorts.VARIANT)}


def _observed(ids):
    """A control record whose four controls were observed to hold on ``ids`` (``cohorts.controls``' form): a manifest admits a
    bug by these outcomes, never by a ``failed`` flag (replay cohort, repair 1), each test known by the node id pytest's own
    report gives it (repair 3)."""
    def seen(results, exit="1"):
        return {"call": "passed", "pytest_exit": exit, "report": True, "tests": results}
    return {"failed": "", "why": "",
            "validate": {"images": ids, "fail_to_pass": ["t"], "pass_to_pass": [], "base": seen({"t": "failed"}),
                         "fixed": seen({"t": "passed"}, "0")},
            "selfcheck": {"images": ids, "gold": seen({"t": "passed"}, "0"), "empty": seen({"t": "failed"})},
            "round_trip": {"status": "finished", "kept_read": True, "known_bytes": 1, "known_sha256": "k" * 64,
                           "kept_sha256": "k" * 64, "images": ids}, "images_after": ids}


def test_the_report_reads_python_docker_memory_images_the_sign_ins_existence_and_the_usable_cases(tmp_path):
    root = tmp_path / "cohorts"
    ids = {"task_image_id": "sha256:t", "oracle_image_id": "sha256:o"}
    body = cohorts.manifest("selection", {b: dict(ids) for b in cohorts.SELECTION}, {b: _observed(ids) for b in cohorts.SELECTION})
    cohorts.write_manifest(root, body)
    machine = Machine(held=_held([*cohorts.DEVELOPMENT, *cohorts.SELECTION, *cohorts.FINAL[:6]]))
    seen = []
    got = readiness.report(root=root, home=tmp_path / "home", run=machine, which=lambda name: "docker", free=lambda: 12.345,
                           exists=lambda path: (seen.append(path), True)[1])
    assert got["python"]["supported"] is True and got["docker"] == {"client": True, "daemon": True, "version": "28.5.1",
                                                                     "memory_gb": 15.59, "cpus": 8}
    assert got["memory"] == {"available_gb": 12.35, "floor_gb": 6.5, "above_floor": True}
    assert got["images"]["development"] == {"bugs": 35, "present": 35, "missing": [], "oracle_not_its_own": []}
    assert got["images"]["final"]["present"] == 6 and got["images"]["final"]["missing"] == list(cohorts.FINAL[6:])
    assert got["subscription_sign_in"] == "present" and seen == [tmp_path / "home" / ".claude" / ".credentials.json"]
    assert got["usable_cases"]["selection"] == {"manifest": f"selection-{body['digest'][:12]}.json", "holds_its_digest": True,
                                                "usable": 21, "excluded": 0}
    assert got["usable_cases"]["development"]["usable"] is None and "no manifest" in got["usable_cases"]["development"]["why"]
    assert all(any(tuple(a[1:1 + len(words)]) == words for words in readiness.READ_ONLY) for a in machine.asked)
    assert str(tmp_path) not in json.dumps({k: v for k, v in got.items() if k != "usable_cases"})   # no path in what it prints


def test_the_sign_in_is_asked_for_its_existence_only_and_never_opened(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    secret = home / ".claude" / ".credentials.json"
    secret.write_text("not a real sign-in", encoding="utf-8")
    real_open = open

    def guarded(path, *a, **kw):
        if Path(path) == secret:
            raise AssertionError("the sign-in was opened")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", guarded)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw: pytest.fail("a file was read"))
    monkeypatch.setattr(Path, "read_bytes", lambda self, *a, **kw: pytest.fail("a file was read"))
    assert readiness.sign_in_present(home) is True and readiness.sign_in_present(tmp_path / "nobody") is False


def test_with_no_daemon_the_report_says_so_and_reads_no_image(tmp_path):
    machine = Machine(daemon=False)
    got = readiness.report(root=tmp_path, home=tmp_path, run=machine, which=lambda name: "docker", free=lambda: None,
                           exists=lambda path: False)
    assert got["docker"]["daemon"] is False and "does not answer" in got["docker"]["why"] and isinstance(got["images"], str)
    assert got["memory"]["above_floor"] is False and got["subscription_sign_in"] == "absent"
    assert [a[1] for a in machine.asked] == ["version"]
    got = readiness.report(root=tmp_path, home=tmp_path, run=machine, which=lambda name: None, free=lambda: 9.0,
                           exists=lambda path: False)
    assert got["docker"] == {"client": False, "daemon": False, "why": "no docker client on the path"}


def test_a_manifest_that_does_not_hold_its_digest_gives_no_count(tmp_path):
    body = cohorts.manifest("final", {})
    path = cohorts.write_manifest(tmp_path, body)
    path.write_text(json.dumps({**body, "usable": ["x"]}), encoding="utf-8")
    got = readiness.usable(tmp_path)["final"]
    assert got["holds_its_digest"] is False and got["usable"] is None and "digest" in got["why"]


@pytest.mark.parametrize("args", [("run", "-d", "x"), ("exec", "c", "sh"), ("image", "rm", "x"), ("rm", "-f", "c"), ("build", "."),
                                  ("system", "prune"), ()])
def test_the_reports_docker_runs_only_its_read_only_commands(args):
    docker = readiness.read_only(lambda argv: pytest.fail(f"ran {argv}"))
    with pytest.raises(RuntimeError):
        docker(*args)
    ran = []
    readiness.read_only(lambda argv: ran.append(argv))("image", "inspect", "x", "--format", "{{.Id}}")
    assert ran == [["docker", "image", "inspect", "x", "--format", "{{.Id}}"]]
