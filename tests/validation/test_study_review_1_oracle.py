# SPDX-License-Identifier: Apache-2.0
"""Study review 1's class 4 findings (8, 9, 10, 15 and 21), the oracle kept from candidate code, each a test that failed on 67ea51d
for the reason the review gave: every container made from the oracle image is sealed before anything a candidate wrote is copied
in or runs, the maintainers' test files are named by git's own reader of the fix's diff and a list that cannot be read ends the
step, and each probe set runs in a container of its own. Fakes only: no Docker, no model, no network; git runs read-only, on files
the test made."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import probe_replay  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402


def finished(root, arm="K2", task="bug", repeat=1):
    """A finished run whose start names its arm, bug and repeat, and the row ``loop.one`` keeps."""
    archive = c.Archive(root)
    run = archive.start("run", task, arm=arm, repeat=repeat)
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True}, "finished")
    row = {"run": run.rel, "arm": arm, "task": task, "repeat": repeat, "status": "finished", "passes": True, "failing": 0,
           c.MADE_UNDER: c.ending_sha256(archive, run.rel)}
    return archive, run, row


def save(root, row):
    path = Path(root) / "loop"
    path.mkdir(parents=True, exist_ok=True)
    (path / c.saved_name(row["arm"], row["task"], row["repeat"])).write_text(json.dumps(row), encoding="utf-8")


@pytest.fixture
def no_amendment(monkeypatch):
    monkeypatch.setattr(we, "amendment", lambda task: None)


class OracleFake:
    """Docker for one container from the oracle image: the oracle's material is there until the seal removes it, as root; every
    file copied in is recorded with whether the material was still there. ``list_code``: the exit of whichever command reads the
    fix's file list."""

    def __init__(self, list_code=0):
        self.oracle, self.copies, self.ran, self.list_code = True, [], False, list_code

    @staticmethod
    def done(code=0, out=b""):
        return SimpleNamespace(returncode=code, stdout=out, stderr=b"")

    def __call__(self, *args, **kw):
        if args[0] == "cp":
            src, dest = args[1:3]
            if ":" in src and not Path(src).is_file():
                Path(dest).write_text('<testsuite><testcase classname="tests.test_x" name="test_bug"/></testsuite>', encoding="utf-8")
            else:
                self.copies.append((src, self.oracle))
        return self.done(out=b'{"ok":true}\n' if args[0] == "exec" else b"")

    def sh(self, name, script, *args, **kw):
        if script == we.SEAL:
            assert kw["user"] == "root"
            self.oracle = False
        if "fix.files" in script or "--numstat" in script:          # the list as the old reader and the new one read it
            return self.done(self.list_code, b"" if self.list_code else
                             (b"1\t1\ttests/test_x.py\0" if "--numstat" in script else b"M\ttests/test_x.py\n"))
        if "-m pytest" in script:
            self.ran = True
            return self.done(out=b"exit=0\n")
        return self.done()


def oracle_ctx(root, docker):
    return SimpleNamespace(archive=c.Archive(root / "archive"), docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3)


# --- class 4: the oracle kept from candidate code ------------------------------------------------------------------------------
def test_finding_8_a_check_stops_when_the_fixs_file_list_cannot_be_read(tmp_path):
    docker = OracleFake(list_code=1)
    ctx = oracle_ctx(tmp_path, docker)
    task = we.load_tasks()[0]
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["tests.test_x::test_bug"], "pass_to_pass": []}, "finished")
    run = ctx.archive.start("run", task.id, arm="A", repeat=1)
    run.write("final.patch", b"candidate patch")
    run.finish({}, "finished")
    with pytest.raises(RuntimeError):
        we.check(ctx, task, run.rel)
    assert not docker.ran


def test_finding_9_check_seals_before_any_candidate_patch_is_copied_in(tmp_path):
    docker = OracleFake()
    patch = tmp_path / "candidate.patch"
    patch.write_bytes(b"candidate source code")
    we._oracle_run(oracle_ctx(tmp_path, docker), we.load_tasks()[0], patch=patch)
    assert docker.ran
    assert docker.copies == [(str(patch), False)]


def test_finding_10_g6replay_seals_before_any_candidate_patch_is_copied_in(tmp_path):
    docker = OracleFake()
    patch, args = tmp_path / "candidate.patch", tmp_path / "args.json"
    patch.write_bytes(b"candidate source code")
    args.write_text("{}", encoding="utf-8")
    we.g6replay(oracle_ctx(tmp_path, docker), we.load_tasks()[0], str(patch), args)
    assert docker.copies == [(str(patch), False)]


def test_finding_15_the_maintainers_test_files_are_those_gits_own_reader_names(tmp_path):
    """The fix's file list as the oracle image's git produced it, served to whichever command the check sends: the quoted display
    of ``git diff --name-status`` for the list the image keeps, or ``git apply --numstat -z`` over the fix's real diff. A test
    file whose name git quotes must reach the copy of the maintainers' tests."""
    before, after = tmp_path / "before", tmp_path / "after"
    for tree, value in ((before, "old\n"), (after, "new\n")):
        (tree / "tests").mkdir(parents=True)
        (tree / "tests" / "test_café.py").write_text(value, encoding="utf-8")

    def git(*args, data=None, cwd=tmp_path):
        return subprocess.run(["git", "-c", "core.quotePath=true", *args], cwd=cwd, input=data, capture_output=True, timeout=30)

    listing = git("diff", "--no-index", "--name-status", "--no-renames", "--", "before", "after").stdout
    diff = git("diff", "--no-index", "--binary", "--no-renames", "--", "before", "after").stdout
    assert b"\\303\\251" in listing and diff
    empty = tmp_path / "empty"
    empty.mkdir()
    copied = []

    class Producer(OracleFake):
        def sh(self, name, script, *args, **kw):
            if script == "cat /oracle/fix.files":
                return self.done(0, listing)
            if "--numstat -z /oracle/fix.patch" in script:
                env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)}
                got = subprocess.run(["git", "apply", "--numstat", "-z"], input=diff, cwd=empty, capture_output=True, timeout=30,
                                     env=env)
                return self.done(got.returncode, got.stdout)
            if "/oracle/fixed/" in script:
                copied.extend(args)
            return super().sh(name, script, *args, **kw)

    we._oracle_run(oracle_ctx(tmp_path, Producer()), we.load_tasks()[0])
    assert any(str(arg).endswith("tests/test_café.py") for arg in copied), copied


class LocalContainers(OracleFake):
    """Each container a folder of its own; a probe set runs, as real Python, in the folder of the container it was copied into."""

    def __init__(self, root):
        super().__init__()
        self.root, self.worlds, self.probes = root, {}, {}

    def __call__(self, *args, **kw):
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            self.worlds[name] = self.root / name
            self.worlds[name].mkdir()
        elif args[0] == "cp" and Path(args[1]).is_dir():
            self.probes[args[2]] = Path(args[1])
        return self.done()

    def sh(self, name, script, *args, **kw):
        if "-m pytest" in script:
            probe = re.search(r"-m pytest (\S+)", script)[1]
            source = self.probes[f"{name}:{probe}"] / "test_probe.py"
            done = subprocess.run([sys.executable, str(source)], cwd=self.worlds[name], capture_output=True, timeout=30)
            return self.done(out=b"1 failed\n" if done.returncode else b"1 passed\n")
        return super().sh(name, script, *args, **kw)


def test_finding_21_no_probe_set_sees_what_another_left(tmp_path, no_amendment):
    task = we.load_tasks()[0]
    archive, run, row = finished(tmp_path, arm="A", task=task.id)
    save(tmp_path, row)
    first, second = run.path / "scout_probes", run.path / "hidden_probes"
    first.mkdir()
    second.mkdir()
    (first / "test_probe.py").write_text("from pathlib import Path\ndef test_it():\n    Path('left-by-first-probe').write_text('x')\n"
                                         "if __name__ == '__main__': test_it()\n", encoding="utf-8")
    (second / "test_probe.py").write_text("from pathlib import Path\ndef test_it():\n    assert not Path('left-by-first-probe').exists()\n"
                                          "if __name__ == '__main__': test_it()\n", encoding="utf-8")
    worlds = tmp_path / "containers"
    worlds.mkdir()
    result = probe_replay.replay_task(LocalContainers(worlds), tmp_path, task, "0" * 40)
    assert result["matrix"]["base"]["A-1h"]["failed"] == 0, "the second probe set inherited the first's file"
    assert result["matrix"]["A-1"]["A-1h"]["failed"] == 0
