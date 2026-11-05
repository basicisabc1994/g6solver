# SPDX-License-Identifier: Apache-2.0
"""Study review 2's 14 findings on the trust fixes, each a test that failed on 158b1a9 for the reason the review gave.

Finding 8: one bug is one observation of every decisive sign test, by construction. Failed commands read as success (6, 7, 11):
every command's result is read through one status reader (``wecommon.completed``). The seal and the tests set aside (5, 10). One
owner of the saved rows, still (1, 13, 14). Check then act, and durability (2, 3, 4, 9). A saved review is one bug's (12). Fakes
only: no Docker, no model, no network; git runs read-only, on bytes the test made."""
from __future__ import annotations

import contextlib
import json
import os
import posixpath
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import final_review  # noqa: E402
import loop  # noqa: E402
import probe_replay  # noqa: E402
import replay_compare  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

JUNIT = '<testsuite><testcase classname="tests.x" name="test_bug"/></testsuite>'


def finished(root, arm="K2", task="bug", repeat=1, executor="container"):
    """A finished round 3 run, its start naming its arm, bug, repeat, executor and model, and the row ``loop.one`` keeps."""
    archive = c.Archive(root)
    run = archive.start("run", task, arm=arm, repeat=repeat, executor=executor, model=we.R3_MODELS[1])
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True, "executor": executor}, "finished")
    row = {"run": run.rel, "arm": arm, "task": task, "repeat": repeat, "status": "finished", "passes": True, "failing": 0,
           c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    return archive, run, row


def save(root, row, name="K2-bug-1.json", amended=False):
    folder = Path(root) / "loop" / ("amended" if amended else "")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(row), encoding="utf-8")
    return folder / name


def done(code=0, stdout=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")


@pytest.fixture
def no_amendment(monkeypatch):
    monkeypatch.setattr(we, "amendment", lambda task: None)


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    folder = tmp_path / "scratch"
    folder.mkdir()
    monkeypatch.setattr(we.tempfile, "TemporaryDirectory", lambda **kw: contextlib.nullcontext(folder))
    return folder


# --- finding 8: one bug, one observation ---------------------------------------------------------------------------------------
def test_finding_8_one_bug_named_five_times_is_one_observation_of_either_decision():
    rows = {("K2", "bug", 1): {"first": False, "maintainers": True}, ("A", "bug", 1): {"maintainers": False}}
    tasks = ["bug"] * 5
    between = compare.compare(rows, "K2", "A", tasks, [1], "maintainers")
    within = compare.within(rows, "K2", tasks, [1])
    assert (compare.judged([between]), compare.within_judged(within)) == (False, False), "one bug becomes five significant wins"
    assert between == compare.compare(rows, "K2", "A", ["bug"], [1, 1, 1], "maintainers")      # a repeat named twice is one run
    assert within == compare.within(rows, "K2", ["bug"], [1])
    assert between["bugs"] == {"better": 1, "worse": 0, "same": 0} and between["pairs"] == 1


# --- failed commands read as success: findings 6, 7 and 11 -------------------------------------------------------------------
def test_finding_6_a_failed_g6replay_exec_is_no_review(tmp_path):
    class Docker:
        def __call__(self, *args, **kwargs):
            return done(137, b'{"ok": true}\n') if args[0] == "exec" else done()

        def sh(self, *args, **kwargs):
            return done()

    args = tmp_path / "args.json"
    args.write_text("{}", encoding="utf-8")
    ctx = SimpleNamespace(archive=c.Archive(tmp_path / "archive"), docker=Docker(), g6={"commit": "0" * 40}, variant=we.ROUND3)
    try:
        got = we.g6replay(ctx, we.load_tasks()[0], "fix", args)
    except RuntimeError:
        return
    assert got["status"] != "finished" or got["review"].get("ok") is not True, "failed exec saved as a successful review"


def test_finding_7_a_failed_probe_exec_is_no_passing_probe(tmp_path, no_amendment):
    task = we.load_tasks()[0]
    archive, run, row = finished(tmp_path, task=task.id)
    save(tmp_path, row, c.saved_name("K2", task.id, 1))
    run.write("scout_probes/test_probe.py", "def test_it(): pass\n")

    class Docker:
        def __call__(self, *args, **kwargs):
            return done()

        def sh(self, name, script, *args, **kwargs):
            return done(137, b"1 passed\n") if "-m pytest" in script else done()

    try:
        got = probe_replay.replay_task(Docker(), tmp_path, task, "0" * 40)
    except RuntimeError:
        return
    assert probe_replay.verdict(got["matrix"]["base"]["K2-1"]) == "?", "failed exec reported as passing probes"


def test_finding_11_a_failed_pytest_exec_is_no_passing_check(tmp_path, scratch):
    class Docker:
        def __call__(self, *args, **kwargs):
            if args[0] == "cp" and args[1].endswith(":" + we.JUNIT_IN):
                Path(args[2]).write_text(JUNIT, encoding="utf-8")
            return done()

        def sh(self, name, script, *args, **kwargs):
            return done(137, b"exit=0\n") if "-m pytest" in script else done()

    task = we.load_tasks()[0]
    archive, run, row = finished(tmp_path, task=task.id)
    archive.start("validate", task.id).finish({"fail_to_pass": ["tests.x::test_bug"], "pass_to_pass": []}, "finished")
    ctx = SimpleNamespace(archive=archive, docker=Docker(), g6={"commit": "0" * 40}, variant=we.ROUND3)
    try:
        got = we.check(ctx, task, run.rel)
    except RuntimeError:
        return
    assert got["status"] != "finished" or got["checks"][0]["passes"] is not True, "failed exec accepted as passing check"


# --- the seal and the tests set aside: findings 5 and 10 -------------------------------------------------------------------------
def test_finding_5_a_literal_backslash_in_a_name_never_makes_it_a_test(tmp_path):
    name = '"a/tests\\\\implementation.py" "b/tests\\\\implementation.py"'
    patch = (f"diff --git {name}\n" '--- "a/tests\\\\implementation.py"\n' '+++ "b/tests\\\\implementation.py"\n'
             "@@ -1 +1 @@\n-old implementation\n+maintainers secret fix\n").encode()
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path.parent)
    got = subprocess.run(["git", "apply", "--numstat", "-z"], input=patch, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert got.returncode == 0, got.stderr
    assert c.numstat_names(got.stdout) == ["tests\\implementation.py"]          # one POSIX name, in no tests folder
    assert we.test_changes(got.stdout) == [], "a source fix is set aside as a test and survives the seal"


def test_finding_10_a_test_that_is_a_link_is_set_aside_as_a_link_never_as_the_fix_it_names(tmp_path, scratch):
    secret = b"the maintainers' private implementation fix\n"

    class Container:
        """A POSIX container's files and links, where ``cp`` follows a link unless told not to (``-P``)."""

        def __init__(self):
            self.files = {"/oracle/fixed/pkg/api.py": secret}
            self.links = {"/oracle/fixed/tests/test_alias.py": "../pkg/api.py"}
            self.ran, self.leaked = False, None

        def read(self, path):
            while path in self.links:
                path = posixpath.normpath(posixpath.join(posixpath.dirname(path), self.links[path]))
            return self.files.get(path)

        def __call__(self, *args, **kwargs):
            if args[0] == "cp" and args[1].endswith(":" + we.JUNIT_IN):
                Path(args[2]).write_text(JUNIT, encoding="utf-8")
            return done()

        def sh(self, name, script, *args, **kwargs):
            if script == we.FIX_FILES:
                return done(stdout=b"1\t1\tpkg/api.py\0" b"1\t0\ttests/test_alias.py\0")
            if script == we.HOLD_TESTS:
                if "; cp " not in script:
                    return done(1)
                words = shlex.split("cp " + script.split("; cp ", 1)[1].splitlines()[0])
                for p in args:
                    src, dst = (word.replace("$p", p) for word in words[-2:])
                    if "-P" in words or "--no-dereference" in words:
                        self.links[dst] = self.links[src]
                    else:
                        self.files[dst] = self.read(src)
                return done()
            if script == we.SEAL:
                self.files = {p: b for p, b in self.files.items() if not p.startswith("/oracle/")}
                self.links = {p: b for p, b in self.links.items() if not p.startswith("/oracle/")}
                return done()
            if "-m pytest" in script:
                self.ran, self.leaked = True, next((data for data in self.files.values() if data == secret), None)
                return done(stdout=b"exit=0\n")
            return done()

    docker = Container()
    try:
        we._oracle_run(SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3), we.load_tasks()[0])
    except RuntimeError:
        return
    assert not any(p.startswith("/oracle/") for p in docker.files) and docker.ran
    assert docker.leaked is None, "cp dereferenced the test symlink; the source fix survived outside /oracle"
    assert docker.links == {f"{we.TESTS_HELD}/tests/test_alias.py": "../pkg/api.py"}      # the link, held as a link


#: Every place the study's code names the oracle's material (``/oracle``), by (module, the function or constant it is in), and what
#: it does: nothing but these reads it, and only the tests set aside (``HOLD_TESTS``) carry any of it across the seal.
ORACLE_USES = {
    ("run", "oracle_dockerfile"): "builds the oracle image: the maintainers' tree and diff put in /oracle",
    ("run", "ORACLE"): "the folder the seal removes",
    ("run", "FIX_FILES"): "git's reading of the fix's diff, names only, before the seal",
    ("run", "HOLD_TESTS"): "the maintainers' test files set aside under /tmp/we-tests, links as links, before the seal",
    ("run", "_oracle_run"): "the maintainers' fix applied, before the seal, when the fix is the code under test",
    ("run", "selfcheck"): "the fix's diff read out of the image, as the check's own control's gold patch",
    ("run", "g6replay"): "the maintainers' fix applied, before the seal, when the fix is the code reviewed",
    ("probe_replay", "_sealed"): "the maintainers' fix applied, before the seal, when the fix is the code probed",
}


def test_finding_10_nothing_but_the_listed_steps_reads_the_oracles_material():
    import ast
    found = set()
    for path in sorted((ROOT / "validation" / "worked_examples").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for top in tree.body:
            owner = top.name if isinstance(top, (ast.FunctionDef, ast.ClassDef)) else (
                top.targets[0].id if isinstance(top, ast.Assign) and isinstance(top.targets[0], ast.Name) else "<module>")
            for node in ast.walk(top):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/oracle" in node.value \
                        and id(node) not in docs:
                    found.add((path.stem, owner))
    assert found == set(ORACLE_USES), sorted(found ^ set(ORACLE_USES))


# --- one owner, still: findings 1, 13 and 14 -------------------------------------------------------------------------------------
def test_finding_1_a_refused_row_bars_its_key_for_every_reader(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row)
    save(tmp_path, {**row, "arm": "A"}, "contradiction.json")
    key = ("K2", "bug", 1)
    assert c.shelf(archive, we.amendment).bars(key)
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a barred key is never run"))
    with pytest.raises(SystemExit, match="refused"):
        loop.one(tmp_path, "g6", *key)
    assert key not in compare.saved(tmp_path), "compare scores a key loop.one refuses"


@pytest.mark.skipif(os.name != "nt", reason="names that differ only in case are one folder on Windows alone")
def test_finding_13_no_store_beside_the_rows_is_the_amended_folder_by_another_name(tmp_path):
    archive, run, row = finished(tmp_path)
    sha = c.sha256_file(run.path / "final.patch")
    stored = c.keep(archive, {**row, "amended_passes": True, "amendment_sha256": "diff"}, amended=True, file=f"{sha}.json")
    for alias in ("AMENDED", "Amended", "amended.", "amended "):
        with pytest.raises(ValueError):
            c.beside(archive, alias)
    try:
        raw = final_review.saved(tmp_path, row, folder="AMENDED")
    except ValueError:
        return
    assert raw is None and stored.is_file(), "a non-owner reader can read an amended row through AMENDED"


def test_finding_14_two_runs_amended_rows_on_one_key_give_no_amended_verdict(tmp_path, monkeypatch):
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"amended tests")
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    archive, first, row = finished(tmp_path)
    _, other, other_row = finished(tmp_path)
    save(tmp_path, row)
    for name, rec in (("first.json", row), ("other.json", other_row)):
        save(tmp_path, {**rec, "amended_passes": True, "amendment_sha256": c.sha256_file(diff)}, name, amended=True)
    held = c.shelf(archive, we.amendment).get(("K2", "bug", 1))
    assert held is None or held.amended is None, "two amended files hold one key but its pass is still counted"
    assert c.shelf(archive, we.amendment).bars(("K2", "bug", 1), amended=True)


# --- check then act, and durability: findings 2, 3, 4 and 9 ------------------------------------------------------------------------
@pytest.mark.parametrize("ledger_state", ["missing", "empty", "torn"])
def test_finding_2_a_restart_recovers_a_made_run_whose_ledger_line_was_never_written(tmp_path, monkeypatch, no_amendment,
                                                                                      ledger_state):
    archive, run, row = finished(tmp_path)
    assert c.run_facts(archive, run.rel).ending["status"] == "finished"       # stopped after its end, before the ledger's line
    ledger = tmp_path / c.NDJSON["ledger"]
    if ledger_state != "missing":
        ledger.write_bytes(b'{"run":' if ledger_state == "torn" else b"")
    commands = []

    def tool(command, *args, **kwargs):
        commands.append(command)
        if command == "run":            # the runner's own rule sees the durable, finished run
            ctx = SimpleNamespace(archive=archive, tasks=[], model=we.R3_MODELS[1], arm="K2", repeat=1,
                                  g6={"commit": we.R3_LOOP_G6}, rerun_why="", executor="host")
            why, _, _ = we.r3_refusal(ctx, SimpleNamespace(id="bug"))
            assert "already made" in why
            return json.dumps({"status": "refused", "why": why})
        return json.dumps({"checks": [{"patch": "final", "passes": True, "failing": []}]})

    monkeypatch.setattr(loop, "tool", tool)
    try:
        resumed = loop.one(tmp_path, "g6", "K2", "bug", 1)
    except (IndexError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise AssertionError("completed run cannot recover without a ledger line") from exc
    assert resumed["run"] == run.rel and commands == ["run", "check"]


@pytest.mark.parametrize("entry", ["new", "saved", "rescore"])
def test_finding_3_an_ending_read_from_the_runs_record_that_changes_during_the_check_is_refused(tmp_path, monkeypatch, entry):
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"amended tests")
    monkeypatch.setattr(we, "amendment", lambda task: diff if entry == "rescore" else None)
    archive, run, row = finished(tmp_path)
    if entry != "new":
        save(tmp_path, {**row, c.MADE_UNDER: "stale"} if entry == "saved" else row)
    before = c.run_facts(archive, run.rel)

    def tool(command, *args, **kwargs):
        if command == "run":
            return json.dumps(row)
        record = json.loads((run.path / "run.json").read_text("utf-8"))      # a container run's ending is its own record
        (run.path / "run.json").write_text(json.dumps({**record, "status": "error"}), encoding="utf-8")
        assert c.run_facts(archive, run.rel).ending != before.ending
        return json.dumps({"checks": [{"patch": "final", "passes": True, "failing": []}],
                           "amendment_sha256": c.sha256_file(diff)})

    monkeypatch.setattr(loop, "tool", tool)
    with pytest.raises(SystemExit, match="ending changed"):
        if entry == "rescore":
            loop.rescore(tmp_path, "g6")
        else:
            loop.one(tmp_path, "g6", "K2", "bug", 1)


def test_finding_4_the_writers_last_read_refuses_a_contradicting_row_that_appeared_during_the_check(tmp_path, monkeypatch,
                                                                                                   no_amendment):
    archive, run, row = finished(tmp_path, executor="host")
    refused = {**row, "arm": "A"}

    def tool(command, *args, **kwargs):
        if command == "run":
            return json.dumps(row)
        save(tmp_path, refused)          # a contradicting row at the destination, after the read and before the write
        return json.dumps({"checks": [{"patch": "final", "passes": True, "failing": []}]})

    monkeypatch.setattr(loop, "tool", tool)
    with pytest.raises(SystemExit, match="refused"):
        loop.one(tmp_path, "g6", "K2", "bug", 1)
    assert json.loads((tmp_path / "loop" / "K2-bug-1.json").read_text("utf-8")) == refused


def test_finding_9_an_amended_verdict_is_bound_to_the_amendment_the_check_ran(tmp_path, monkeypatch):
    archive, run, row = finished(tmp_path, executor="host")
    save(tmp_path, row)
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"old amendment")
    monkeypatch.setattr(we, "amendment", lambda task: diff)

    def tool(*args, **kwargs):
        ran = c.sha256_file(diff)
        diff.write_bytes(b"new amendment")
        return json.dumps({"checks": [{"patch": "final", "passes": True}], "amendment_sha256": ran})

    monkeypatch.setattr(loop, "tool", tool)
    try:
        got = loop.rescore(tmp_path, "g6")
    except SystemExit:
        return
    assert got[0]["amended_passes"] is compare.saved(tmp_path)[("K2", "bug", 1)]["amended"], "rescore gives a stale pass"


def test_finding_9_a_check_that_names_another_amendment_than_the_one_read_is_refused(tmp_path, monkeypatch):
    archive, run, row = finished(tmp_path, executor="host")
    save(tmp_path, row)
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"the amendment")
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps({"checks": [{"patch": "final", "passes": True}],
                                                                   "amendment_sha256": "another"}))
    with pytest.raises(SystemExit, match="amendment"):
        loop.rescore(tmp_path, "g6")
    assert not (tmp_path / "loop" / "amended" / "K2-bug-1.json").exists()


# --- finding 12: a saved review is one bug's ------------------------------------------------------------------------------------
@pytest.mark.parametrize("consumer", ["waiting", "convert_start"])
def test_finding_12_another_bugs_review_is_never_reused_by_patch_digest_alone(tmp_path, monkeypatch, no_amendment, consumer):
    archive, first, row_a = finished(tmp_path, task="bug-a")
    _, second, row_b = finished(tmp_path, task="bug-b")
    assert (first.path / "final.patch").read_bytes() == (second.path / "final.patch").read_bytes()
    save(tmp_path, row_a, "a.json")
    save(tmp_path, row_b, "b.json")
    with pytest.raises(ValueError, match="two bugs"):
        replay_compare.patches(tmp_path, ["bug-a", "bug-b"])
    store = c.beside(tmp_path, "final_review")
    store.mkdir()
    (store / f"{c.sha256_file(first.path / 'final.patch')}.json").write_text(json.dumps(
        {"task": "bug-a", "first_seen_in": first.rel, "said": "reproduced", "rule": "bug A's rule", "why": "bug A's reason",
         "test": "test for bug A"}), encoding="utf-8")
    if consumer == "waiting":
        try:
            waiting = final_review.waiting(tmp_path, ["bug-b"])
        except ValueError:
            return
        assert [r["task"] for r in waiting] == ["bug-b"], "another bug's review suppresses this bug's review"
    else:
        monkeypatch.setattr(we, "convert_cases", lambda: [{"task": "bug-b", "repeat": 1, "arm": "K2", "of": 1}])
        try:
            patch, review = we.convert_start(tmp_path, "bug-b", 1)
        except ValueError:
            return
        assert review["rule"] != "bug A's rule", "conversion is seeded with another bug's finding"
