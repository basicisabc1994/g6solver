"""Review 4 fixtures: synthetic archives and fake containers on a real disk, all under this folder."""
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

TREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TREE / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASKS = [SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40),
         SimpleNamespace(id="click-3769", repo="/synthetic/click", parent="e" * 40)]


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "_work" / uuid.uuid4().hex[:10]
    path.mkdir(parents=True)
    return path.resolve()


@pytest.fixture(autouse=True)
def no_real_host_program(monkeypatch):
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")


class Docker:
    """The patch bytes name the outcome the fake review returns: b"<anything>|<outcome>"."""

    def __init__(self, fail_after=None, interrupt_after=None, bad_pin=False):
        self.outcomes = {}
        self.execs = 0
        self.fail_after = fail_after
        self.interrupt_after = interrupt_after
        self.bad_pin = bad_pin

    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            label = "f" * 40 if self.bad_pin else COMMIT
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": label}}}]
        elif args[0] == "cp":
            text = Path(args[1]).read_bytes().decode("utf-8")
            self.outcomes[args[2].split(":")[0]] = text.split("|")[1] if "|" in text else "insufficient_evidence"
        elif args[0] == "exec":
            self.execs += 1
            if self.interrupt_after is not None and self.execs > self.interrupt_after:
                raise KeyboardInterrupt
            if self.fail_after is not None and self.execs > self.fail_after:
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic failure")
            name = next(a for a in args if isinstance(a, str) and a.startswith("g6we-replay-rc-"))
            value = {"ok": True, "assessment": {"outcome": self.outcomes[name], "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, name, script, *args, **kwargs):
        parent = args[0] if args else kwargs.get("workdir")
        value = {"head": parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")


def add_run(archive, task, patch, passes, *, arm="B", review=None, check=True):
    """One run with its final patch; optionally one matching archived review and one final check."""
    run = archive.start("run", task, arm=arm)
    run.write("final.patch", patch)
    run.write("prompt.txt", "Issue #496: synthetic issue for " + task)
    rounds = []
    if review is not None:
        run.write("rounds/round-1.patch", patch)
        rounds.append({"round": 1, "input": review})
    run.write_json("rounds.json", rounds)
    run.finish({}, "finished")
    if check:
        archive.start("check", task, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
    return run


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    """A replay world: registration parsed from the tree's file, two synthetic tasks, fake task images."""
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: list(TASKS))
    monkeypatch.setattr(rc.we, "image_names", lambda task, *a: (f"g6we-task:{task.id}-synthetic", "unused"))

    def live(source, output, *, modes=("hook",), docker=None, s0="before", expect=0):
        monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        argv = ["--source", str(source), "--registered-commit", COMMIT, "--s0", s0]
        for mode in modes:
            argv += ["--mode", mode]
        code = rc.main(argv, docker=docker or Docker())
        captured = capsys.readouterr()
        assert code == expect, (code, captured.err)
        found = list(Path(output).glob("replay-rc-batch/development/*/run.json"))
        return (json.loads(captured.out) if captured.out else None), (found[0] if found else None), captured

    return SimpleNamespace(tmp=tmp_path, live=live, capsys=capsys, monkeypatch=monkeypatch, registration=registration)


def score(path, capsys):
    code = rc.main(["--score", str(path)])
    captured = capsys.readouterr()
    return code, captured


def say(capsys, *parts):
    """Show a probe's observation without mixing it into the captured program output."""
    with capsys.disabled():
        print(*parts, flush=True)


def brief(result):
    return {k: (v["status"], v["prediction_met"], v["kill"]) for k, v in result["predictions"].items()}
