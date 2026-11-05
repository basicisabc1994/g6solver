# SPDX-License-Identifier: Apache-2.0
"""The oracle kept from candidate code (PLAN.md, 2026-11-05, night, later, item 2). The oracle image holds the maintainers' fixed
tree and their diff (``/oracle``). Code a candidate wrote (a patch's code under test, a reviewer's written test, a probe, G6's
review of a patch) runs only in a container whose oracle material was removed first; the maintainers' tests are copied in for
scoring alone. Fakes only: the real ``run.Docker`` sends its real commands to an in-memory Docker, which keeps, for each container,
whether the oracle's material is still in it, and records it at every command that runs or copies in candidate code."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import probe_replay  # noqa: E402
import run as we  # noqa: E402

TEST_ID = "tests.test_x::test_bug"
JUNIT = '<testsuite><testcase classname="tests.test_x" name="test_bug"/></testsuite>'
#: what a seal is to this fake: the material removed as root, and its absence checked
REMOVED = ("rm -rf /oracle", "test ! -e /oracle")


def done(code=0, out=b""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=b"")


class Containers:
    """Docker in memory, behind the real ``run.Docker``: a container made from the oracle image holds the oracle's material until
    a root shell removes it and checks it is gone (``REMOVED``); every command that copies in or runs code a candidate wrote records what it is and
    whether that material was still there (``candidate``); every command that needs the material fails without it."""

    def __init__(self):
        self.live, self.candidate, self.oracle_reads, self.started = {}, [], [], []

    def _container(self, ref):
        name = ref.split(":", 1)[0]
        return name if name in self.live else None

    def __call__(self, argv, input=None, capture_output=True, timeout=None, env=None):
        args = list(argv[1:])
        if args[0] == "run" and "-d" in args:
            name, image = args[args.index("--name") + 1], args[args.index("tail") - 1]
            self.live[name] = {"oracle": image.startswith("g6we-oracle:"), "image": image}
            self.started.append(name)
            return done()
        if args[0] == "run":                                   # ``run --rm`` of the oracle image: a read of it, no candidate code
            return done(out=b"the maintainers' diff")
        if args[0] == "rm":
            self.live.pop(args[-1], None)
            return done()
        if args[0] == "cp":
            src, dest = args[1], args[2]
            if self._container(dest):                          # into a container
                name = self._container(dest)
                kind = "patch" if dest.split(":", 1)[1].startswith("/tmp/we") else "candidate code"
                if kind == "candidate code":
                    self.candidate.append((f"copy {Path(src).name}", self.live[name]["oracle"]))
                return done()
            name, path = src.split(":", 1)                     # out of one
            Path(dest).write_text(JUNIT if path == we.JUNIT_IN else "exit=0\n", encoding="utf-8")
            return done()
        if args[0] == "exec":
            name = next(a for a in args if a in self.live)
            user = args[args.index("-u") + 1]
            rest = args[args.index(name) + 1:]
            if rest[0] != "sh":                                # G6's own review, which runs the patch's code
                self.candidate.append(("G6's review", self.live[name]["oracle"]))
                return done(out=b'{"ok": true}\n')
            script = rest[2]
            if all(part in script for part in REMOVED):
                if user != "root":
                    return done(1)
                self.live[name]["oracle"] = False
                return done()
            if "/oracle" in script:                            # the fix applied, the file list read, the tests copied in
                self.oracle_reads.append(script.split()[0])
                if not self.live[name]["oracle"]:
                    return done(1)
                return done(out=b"1\t1\ttests/test_x.py\0" if "--numstat -z" in script else b"")    # git's reader of the fix
            if "-m pytest" in script:
                self.candidate.append(("pytest", self.live[name]["oracle"]))
                return done(out=b"exit=0\n")
            return done()
        return done()


def _ctx(tmp_path, docker):
    return SimpleNamespace(archive=we.c.Archive(tmp_path / "archive"), docker=we.Docker(runner=docker), g6={"commit": "0" * 40},
                           variant=we.ROUND3)


def _no_oracle_beside(docker):
    assert docker.candidate and not any(oracle for _, oracle in docker.candidate), docker.candidate


def test_a_check_runs_the_patch_only_once_the_oracle_is_gone_and_still_scores_on_the_maintainers_tests(tmp_path):
    docker, task = Containers(), we.load_tasks()[0]
    ctx = _ctx(tmp_path, docker)
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": [TEST_ID], "pass_to_pass": []}, "finished")
    run = ctx.archive.start("run", task.id, arm="A", executor="host", repeat=1)
    run.write("final.patch", b"the candidate's patch")
    run.finish({"executor": "host", "agent_started": True}, "finished")
    got = we.check(ctx, task, run.rel)
    assert got["checks"] == [{**got["checks"][0], "patch": "final", "passes": True}]          # scored, on the maintainers' tests
    assert docker.oracle_reads == ["git", "set"]          # the file list read (git's own reader) and the tests set aside, before the seal
    assert [what for what, _ in docker.candidate] == ["pytest"]
    _no_oracle_beside(docker)


@pytest.mark.parametrize("code", ["fix", "patch"])
def test_a_reviewers_written_test_is_copied_in_and_run_only_once_the_oracle_is_gone(tmp_path, code):
    docker, task = Containers(), we.load_tasks()[0]
    ctx = _ctx(tmp_path, docker)
    test, patch = tmp_path / "test_critic.py", tmp_path / "we.patch"
    test.write_text("def test_it(): pass\n", encoding="utf-8")
    patch.write_bytes(b"a candidate's patch")
    we.replay(ctx, task, "fix" if code == "fix" else str(patch), test)
    assert docker.oracle_reads == (["git"] if code == "fix" else [])       # the maintainers' fix, applied before the seal
    assert [what for what, _ in docker.candidate] == ["copy test_critic.py", "pytest"]
    _no_oracle_beside(docker)


def test_g6s_review_of_a_patch_runs_only_once_the_oracle_is_gone(tmp_path):
    docker, task = Containers(), we.load_tasks()[0]
    ctx = _ctx(tmp_path, docker)
    patch, args = tmp_path / "we.patch", tmp_path / "args.json"
    patch.write_bytes(b"a candidate's patch")
    args.write_text(json.dumps({"task": "t"}), encoding="utf-8")
    we.g6replay(ctx, task, str(patch), args)
    assert [what for what, _ in docker.candidate] == ["G6's review"]
    _no_oracle_beside(docker)


def test_the_validation_and_the_checks_own_control_still_read_the_oracle_and_run_nothing_beside_it(tmp_path):
    docker, task = Containers(), we.load_tasks()[0]
    ctx = _ctx(tmp_path, docker)
    assert "fail_to_pass" in we.validate(ctx, task)                         # the parent's tests, then the fix's, each sealed after
    assert docker.oracle_reads == ["git", "set", "git", "git", "set"]        # the fix, the list and the tests before each seal
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": [TEST_ID], "pass_to_pass": []}, "finished")
    assert we.selfcheck(ctx, task)["gold"]["patch_applied"] is True
    _no_oracle_beside(docker)


def test_each_probe_replay_code_has_a_fresh_sealed_container_so_no_probe_runs_beside_the_oracle(tmp_path):
    docker, task = Containers(), we.load_tasks()[0]
    root = tmp_path / "round3"
    archive = we.c.Archive(root)
    for arm in ("A", "K1"):
        run = archive.start("run", task.id, arm=arm, repeat=1)
        run.write("final.patch", f"{arm}'s patch".encode())
        run.write("scout_probes/test_probe.py", "def test_probe(): pass\n")
        run.finish({"agent_started": True}, "finished")
        (root / "loop").mkdir(exist_ok=True)
        (root / "loop" / f"{arm}-{task.id}-1.json").write_text(json.dumps({"arm": arm, "task": task.id, "repeat": 1,
                                                                           "run": run.rel, "passes": arm == "K1"}), "utf-8")
    got = probe_replay.replay_task(we.Docker(runner=docker), root, task, "0" * 40)
    assert list(got["matrix"]) == ["base", "fix", "A-1", "K1-1"]
    assert len(docker.started) == 4 * 2 and not docker.live     # one container a probe set on each code, each removed (finding 21)
    assert [what for what, _ in docker.candidate].count("pytest") == 4 * 2     # two probe sets on each of the four codes
    _no_oracle_beside(docker)


def test_a_seal_that_fails_ends_the_step_before_any_candidate_code_runs(tmp_path):
    class Stuck(Containers):
        def __call__(self, argv, **kw):
            if argv[1] == "exec" and any(all(part in str(a) for part in REMOVED) for a in argv):
                return done(1)                                  # the material could not be removed
            return super().__call__(argv, **kw)

    docker, task = Stuck(), we.load_tasks()[0]
    ctx = _ctx(tmp_path, docker)
    with pytest.raises(RuntimeError, match="the oracle's material removed"):
        we.replay(ctx, task, "fix", tmp_path / "test_critic.py")
    assert docker.candidate == [] and not docker.live
