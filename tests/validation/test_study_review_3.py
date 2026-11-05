# SPDX-License-Identifier: Apache-2.0
"""Study review 3's findings on the trust fixes, each a test that failed on 53d31a8 for the reason the review gave, and finding 2,
ruled out (the frozen rule as registered), pinned so its reading cannot drift.

A failed command read as an answer (1, 4, 7, 8, 12): a command's own exit is told apart from the container runtime's by its own
report (``wecommon.own_exit``), and no result is left unread. Order and durability (3, 5, 11). Folders that alias, by identity (9,
13, 14), with real Windows junctions. One reader of a review's bug (10). Names as bytes (6). Fakes only: no Docker, no model, no
network; git runs read-only, on bytes the test made."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import compare  # noqa: E402
import export_reviewer_cases as export  # noqa: E402
import final_review  # noqa: E402
import loop  # noqa: E402
import probe_replay  # noqa: E402
import replay3  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

STOPPED = b"Error response from daemon: container g6we-x is not running\n"
MISSING = b"Error response from daemon: No such container: g6we-x\n"
OWN = "g6we-own-exit="          # the report's form, as ``wecommon.OWN_EXIT`` names it
APPLY = "git apply --binary --whitespace=nowarn /tmp/we.patch"


def done(code=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def own(code, stdout=b""):
    """A command's exit as the command itself reports it (``wecommon.reporting``)."""
    return done(code, stdout + f"\n{OWN}{code}\n".encode())


def host_helpers(monkeypatch):
    spec = importlib.util.spec_from_file_location("st3_host_helpers", ROOT / "tests" / "validation" / "test_host_author.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    monkeypatch.setattr(we.box_mcp, "mirror", lambda *args, **kwargs: None)
    return host


@contextlib.contextmanager
def junction(target: Path, alias: Path):
    """A real Windows junction (a directory symlink elsewhere) from ``alias`` to ``target``, removed after."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        yield alias
    finally:
        os.rmdir(alias) if os.name == "nt" else alias.unlink()


# --- 1, 7: the runtime's failure is no "does not apply" ------------------------------------------------------------------------
def checked_run(tmp_path):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path / "archive")
    candidate = archive.start("run", task.id, arm="A", repeat=1)
    candidate.write("final.patch", b"nonempty candidate patch\n")
    candidate.finish({"agent_started": True}, "finished")
    archive.start("validate", task.id).finish({"fail_to_pass": ["tests.test_api::test_bug"], "pass_to_pass": []}, "finished")
    return task, archive, candidate


class ApplyDocker:
    """A container whose ``git apply`` of the candidate answers ``answer``; everything else succeeds with no output."""

    def __init__(self, answer):
        self.answer, self.applied = answer, []

    def __call__(self, *args, **kwargs):
        return done()

    def sh(self, name, script, *args, **kwargs):
        if script == APPLY or script.startswith("git apply --binary --whitespace=nowarn /tmp/we-"):
            self.applied.append(kwargs.get("own_exit"))
            return self.answer
        return done()


@pytest.mark.parametrize("answer", [done(1, stderr=STOPPED), done(1, stderr=MISSING), done(125), done(126), done(127),
                                    done(137), done(128), own(137), own(255), done(999)])
def test_finding_1_a_check_whose_apply_the_runtime_failed_is_no_failed_patch(tmp_path, answer):
    task, archive, candidate = checked_run(tmp_path)
    docker = ApplyDocker(answer)
    ctx = SimpleNamespace(archive=archive, docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3)
    with pytest.raises(RuntimeError):
        we.check(ctx, task, candidate.rel)
    assert docker.applied == [True]          # run with its own report, and no check finished: the verdict is missing


@pytest.mark.parametrize("code", [1, 128])
def test_finding_1_gits_own_no_is_still_a_failed_patch(tmp_path, code):
    task, archive, candidate = checked_run(tmp_path)
    ctx = SimpleNamespace(archive=archive, docker=ApplyDocker(own(code)), g6={"commit": "0" * 40}, variant=we.ROUND3)
    got = we.check(ctx, task, candidate.rel)
    assert got["checks"][0]["patch_applied"] is False and got["checks"][0]["passes"] is False


@pytest.mark.parametrize("answer,applied", [(done(1, stderr=MISSING), None), (done(1, stderr=STOPPED), None), (done(137), None),
                                            (own(126), None), (done(999), None), (own(1), False), (own(128), False),
                                            (done(0), True)])
def test_finding_7_probe_replay_tells_a_missing_container_from_a_patch_that_does_not_apply(tmp_path, answer, applied):
    patch = tmp_path / "candidate.patch"
    patch.write_bytes(b"candidate patch\n")
    if applied is None:
        with pytest.raises(RuntimeError):
            probe_replay._sealed(ApplyDocker(answer), we.load_tasks()[0], "oracle-image", "K2-1", patch)
    else:
        assert probe_replay._sealed(ApplyDocker(answer), we.load_tasks()[0], "oracle-image", "K2-1", patch)[1] is applied


@pytest.mark.parametrize("answer,raises", [(done(1, stderr=MISSING), True), (done(137), True), (own(128), False), (own(1), False)])
def test_the_reviews_apply_reads_gits_own_no_and_raises_on_the_runtimes(tmp_path, answer, raises):
    patch = tmp_path / "candidate.patch"
    patch.write_bytes(b"candidate patch\n")
    ctx = SimpleNamespace(docker=ApplyDocker(answer))
    with pytest.raises(RuntimeError, match="no exit of its own" if raises else "does not apply"):
        final_review.patch_run(ctx, we.load_tasks()[0], "g6we-x", patch, we.CRITIC_AT)


@pytest.mark.parametrize("answer,said", [(done(1, stderr=MISSING), "no exit of its own"), (done(1, stderr=STOPPED), "no exit"),
                                         (done(137), "no exit"), (done(999), "no exit"), (own(1), "does not apply")])
def test_the_frozen_reviews_apply_reads_gits_own_no_and_records_the_runtimes_failure(tmp_path, monkeypatch, answer, said):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path)
    run = archive.start("run", task.id, arm="K1", repeat=1)
    patch = run.write("final.patch", b"patch\n")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w"):
        pass
    class Docker(ApplyDocker):
        def __call__(self, *args, **kwargs):
            return done(0, output.getvalue() if "archive" in args else b"")

    monkeypatch.setattr(we, "write_in", lambda *args, **kwargs: None)
    monkeypatch.setattr(we, "run_set", lambda *args, **kwargs: we.ProbeRun(0, {"t::test_x": "passed"}, {}, ""))

    def critic(pack):
        token = (pack / "issue.txt").read_text("utf-8").splitlines()[0]
        return f"READ: {token}\nRULE: r\nVERDICT: defect\nWHY: w\n```python\ndef test_x():\n    assert 0\n```\n"

    ctx = SimpleNamespace(archive=archive, docker=Docker(answer), g6={"commit": "0" * 40}, variant=we.ROUND3, arm="K1", critic=critic)
    got = final_review.review(ctx, task, patch, "title", "issue")
    assert got["said"] == "failed" and said in got["error"] and ctx.docker.applied == [True]


@pytest.mark.parametrize("answer,text", [(own(0, b"the file"), "the file"), (own(1), ""), (done(0, b"plain"), "plain")])
def test_a_file_read_in_a_container_is_none_only_by_cats_own_no(answer, text):
    assert c.OWN_EXIT == OWN and we.APPLY == APPLY
    class Docker:
        def sh(self, name, script, *args, **kwargs):
            assert kwargs.get("own_exit") is True
            return answer

    assert we.read_in(Docker(), "agent", "/x") == text


@pytest.mark.parametrize("answer", [done(1, stderr=STOPPED), done(1, stderr=MISSING), done(137), own(2), done(999)])
def test_a_file_read_the_runtime_failed_raises(answer):
    class Docker:
        def sh(self, name, script, *args, **kwargs):
            return answer

    with pytest.raises(RuntimeError):
        we.read_in(Docker(), "agent", "/x")


@pytest.mark.parametrize("probe,copied", [(done(0), False), (done(1), None), (done(1, stderr=MISSING), None), (done(137), None)])
def test_a_copy_docker_failed_is_no_file_only_when_the_container_says_nothing_is_there(tmp_path, probe, copied):
    class Docker:
        def __call__(self, *args, **kwargs):
            return done(1, stderr=b"Error response from daemon: Could not find the file")

        def sh(self, name, script, *args, **kwargs):
            assert script == we.ABSENT.format(path="/x/final.patch") and kwargs.get("user") == "root"
            return probe

    if copied is None:
        with pytest.raises(RuntimeError):
            we.copy_out(Docker(), "agent", "/x/final.patch", tmp_path / "out")
    else:
        assert we.copy_out(Docker(), "agent", "/x/final.patch", tmp_path / "out") is copied


# --- 4, 5: the final patch, captured and kept before the ending ---------------------------------------------------------------
@pytest.mark.parametrize("capture", [137, 1, 2])
def test_finding_4_a_failed_final_capture_ends_the_run_error(monkeypatch, tmp_path, capture):
    helpers = host_helpers(monkeypatch)

    class Docker(helpers.HostDocker):
        failed_capture = False

        def __call__(self, *args, **kwargs):
            if args[0] == "cp" and args[1].endswith("/final.patch"):
                return helpers.ok(code=1)
            return super().__call__(*args, **kwargs)

        def sh(self, name, script, *args, **kwargs):
            if f"{we.WE}/final.patch" in script:
                Docker.failed_capture = True
                return helpers.ok(code=capture)
            return super().sh(name, script, *args, **kwargs)

    monkeypatch.setattr(helpers, "HostDocker", Docker)
    record, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "fixed code\n", None)])
    assert Docker.failed_capture and record["status"] == "error"


def test_finding_4_a_capture_whose_git_fails_exits_with_that_failure():
    script = we.diff_script("/repo", "abc", "/we/final.patch")
    assert "&& GIT_INDEX_FILE" in script and script.endswith("(exit $code)") and "; GIT_INDEX_FILE" not in script


def test_finding_5_a_stop_before_the_final_patch_leaves_no_ending_a_restart_reads_as_made(monkeypatch, tmp_path):
    helpers = host_helpers(monkeypatch)
    original = c.RunFolder.write

    class Crash(BaseException):
        pass

    def stop_after_ending(self, name, data):
        if name == "final_report.txt":
            raise Crash()
        return original(self, name, data)

    monkeypatch.setattr(c.RunFolder, "write", stop_after_ending)
    with pytest.raises(Crash):
        helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "the only fixed tree\n", None)])
    archive = c.Archive(tmp_path / "archive")
    task = we.load_tasks()[0]
    ctx = SimpleNamespace(archive=archive, tasks=[], model=we.R3_MODELS[1], arm="A", repeat=1,
                          g6={"commit": we.R3_LOOP_G6}, rerun_why="", executor="host")
    why, _, _ = we.r3_refusal(ctx, task)
    assert "already made" not in why
    assert we.r3_made(archive.rows(), archive.root, (ctx.model, task.id, "A", 1)) is None


def test_finding_5_a_finished_host_run_has_its_patch_before_its_ending(monkeypatch, tmp_path):
    helpers = host_helpers(monkeypatch)
    order = []
    original_end, original_durable = c.RunFolder.end, c.durable

    def end(self, status, **kwargs):
        order.append(("end", status, (self.path / "final.patch").is_file()))
        return original_end(self, status, **kwargs)

    monkeypatch.setattr(c.RunFolder, "end", end)
    monkeypatch.setattr(c, "durable", lambda path: (order.append(("durable", path.name)), original_durable(path)))
    record, *_ = helpers.host_run(monkeypatch, tmp_path, "A", [("s1", "fixed code\n", None)])
    assert record["status"] == "finished"
    assert order.index(("durable", "final.patch")) < next(i for i, o in enumerate(order) if o[0] == "end")
    assert all(o[2] for o in order if o[0] == "end" and c.on_patch({"status": o[1]}))


# --- 8, 12: a held probes' copy that failed is read --------------------------------------------------------------------------
class HeldDocker:
    def __init__(self, code):
        self.code, self.failed = code, False

    def sh(self, name, script, *args, **kwargs):
        if "cp -r" in script:
            self.failed = True
            return done(self.code, stderr=b"exec killed")
        if "--junitxml" in script:
            report = 'exit=0\n' + we.JUNIT_MARK + '\n<testsuite><testcase classname="stale" name="test_pass"/></testsuite>'
            return done(0, report.encode())
        return done(0, b"1 passed\nexit=0\n")


@pytest.mark.parametrize("code", [137, 1, 125, 999])
def test_finding_8_a_failed_held_copy_raises_and_no_stale_probes_run(code):
    docker = HeldDocker(code)
    with pytest.raises(RuntimeError, match="held probes"):
        we.run_set(SimpleNamespace(docker=docker), we.load_tasks()[0], "agent", we.hidden_at(0)[0])
    assert docker.failed


def test_finding_8_the_held_copy_script_fails_with_its_own_failing_step():
    script = we.held_copy("/we/hidden")
    assert script.startswith(f"rm -rf {we.HELD_COPY} && ") and "; true" not in script and "&& chown -R" in script


@pytest.mark.parametrize("code", [137, 1])
def test_finding_12_the_legacy_repair_never_runs_stale_held_probes(tmp_path, code):
    docker = HeldDocker(code)
    ctx = SimpleNamespace(arm="L4", docker=docker)
    run = c.Archive(tmp_path).start("run", "bug", arm="L4", repeat=1)
    with pytest.raises(RuntimeError, match="held probes"):
        we.repair_loop(ctx, we.load_tasks()[0], run, "agent", [], "", c.Tally(), 1, time.monotonic())
    assert docker.failed


# --- 3, 11: the bytes certified and reviewed are the bytes run ---------------------------------------------------------------
def test_finding_3_the_amendment_control_certifies_the_bytes_both_controls_ran(tmp_path, monkeypatch):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path / "archive")
    archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    diff = tmp_path / "amendment.patch"
    original, replacement = b"original amendment\n", b"unchecked replacement amendment\n"
    diff.write_bytes(original)
    monkeypatch.setattr(we, "amendment", lambda task_id: diff)
    controlled = []

    def oracle(ctx, task, *, amended, fix=False, **kwargs):
        assert isinstance(amended, Path) and amended != diff      # a private copy, never the live diff
        controlled.append(amended.read_bytes())
        diff.write_bytes(replacement)                             # the live diff changes during each control
        return {"results": {"t": "passed" if fix else "failed"}}

    monkeypatch.setattr(we, "_oracle_run", oracle)
    result = we.amend(SimpleNamespace(archive=archive, g6={"commit": "0" * 40}), task)
    assert result["ok"] is True and controlled == [original, original]
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert we.amend_control(archive, task.id) is None             # the live diff is not the bytes the controls ran
    diff.write_bytes(original)
    assert we.amend_control(archive, task.id)["record"]["sha256"] == hashlib.sha256(original).hexdigest()


def review_ctx(tmp_path, critic):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w"):
        pass

    class Docker:
        def __call__(self, *args, **kwargs):
            return done(0, output.getvalue() if "archive" in args else b"")

    return SimpleNamespace(archive=c.Archive(tmp_path), docker=Docker(), g6={"commit": "0" * 40}, variant=we.ROUND3, arm="K1",
                           critic=critic)


def test_finding_11_a_review_is_kept_by_the_bytes_its_reviewer_read_on_the_frozen_path(tmp_path):
    archive = c.Archive(tmp_path)
    task = we.load_tasks()[0]
    candidate = archive.start("run", task.id, arm="K1", repeat=1)
    patch = candidate.write("final.patch", b"reviewed patch\n")
    (tmp_path / c.SAVED).mkdir()
    row = {"run": candidate.rel, "task": task.id}

    def critic(pack):
        assert (pack / "candidate.patch").read_bytes() == b"reviewed patch\n"
        token = (pack / "issue.txt").read_text("utf-8").splitlines()[0]
        patch.write_bytes(b"a different unreviewed patch\n")
        return f"READ: {token}\nRULE: rule\nVERDICT: none\nWHY: reviewed the supplied patch\n"

    got = final_review.review(review_ctx(tmp_path, critic), task, patch, "title", "issue")
    assert got["said"] == "none" and got["patch_sha256"] == hashlib.sha256(b"reviewed patch\n").hexdigest()
    final_review.keep(tmp_path, row, got)                     # the frozen main's call, as it stands
    assert final_review.saved(tmp_path, row) is None
    kept = c.beside(tmp_path, final_review.FOLDER) / f"{got['patch_sha256']}.json"
    assert json.loads(kept.read_text("utf-8"))["task"] == task.id
    assert not list((tmp_path / "critic_packs").glob("held-*"))     # the private copy is removed


def test_finding_11_a_review_with_no_digest_of_what_was_read_is_never_kept(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="K1", repeat=1)
    run.write("final.patch", b"patch\n")
    with pytest.raises(ValueError, match="digest"):
        final_review.keep(tmp_path, {"run": run.rel, "task": "bug"}, {"said": "none"})


# --- 9, 13, 14: folders that alias, by identity ------------------------------------------------------------------------------
def kept_row(root, file=None):
    archive = c.Archive(root)
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    patch = run.write("final.patch", b"a nonempty patch\n")
    run.finish({"agent_started": True, "executor": "host"}, "finished")
    row = {"run": run.rel, "task": "bug", "arm": "A", "repeat": 1, "passes": False, "failing": 1, "said": "none",
           c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    return archive, row, patch, c.keep(archive, row, file=file)


def test_finding_9_a_review_store_that_is_the_rows_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "amendment", lambda task: None)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    patch = run.write("final.patch", b"a nonempty patch\n")
    run.finish({"agent_started": True}, "finished")
    row = {"run": run.rel, "task": "bug", "arm": "A", "repeat": 1, "passes": True, "failing": 0, "said": "none",
           c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    c.keep(archive, row, file=f"{c.sha256_file(patch)}.json")     # a row kept under the name its review would have
    with junction(tmp_path / c.SAVED, tmp_path / c.SAVED / final_review.FOLDER):
        with pytest.raises(ValueError, match="by a junction or a link"):
            final_review.saved(tmp_path, row)
        with pytest.raises(ValueError):
            final_review.keep(tmp_path, row, {"said": "none"}, sha=c.sha256_file(patch))


def test_finding_9_a_store_that_is_another_store_or_the_amended_folder_is_refused(tmp_path):
    (tmp_path / c.SAVED / c.AMENDED).mkdir(parents=True)
    (tmp_path / c.SAVED / "replay3").mkdir()
    for target in (tmp_path / c.SAVED / c.AMENDED, tmp_path / c.SAVED / "replay3"):
        with junction(target, tmp_path / c.SAVED / "final_review"):
            with pytest.raises(ValueError):
                c.beside(tmp_path, "final_review")
    assert c.beside(tmp_path, "final_review") == tmp_path / c.SAVED / "final_review"


def test_finding_13_a_side_store_holding_a_row_is_never_written(tmp_path, monkeypatch):
    root = tmp_path / "round3"
    archive, _, _, saved = kept_row(root, file="cegis.json")
    before = saved.read_bytes()
    assert c.shelf(archive, lambda task: None).get(("A", "bug", 1)) is not None
    monkeypatch.setattr(c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(we, "load_tasks", lambda: [])
    monkeypatch.setattr(we, "read_key", lambda: "")
    monkeypatch.setattr(loop, "round_of", lambda *args, **kwargs: [])
    monkeypatch.setattr(loop.distill, "Metered", lambda caller: caller)
    monkeypatch.setattr(loop.distill.llm, "AnthropicCaller", lambda *args, **kwargs: None)
    result = SimpleNamespace(status="stopped", iterations=0, counterexamples=[], boundary=[], reason="control")
    monkeypatch.setattr(loop, "generations", lambda *args, **kwargs: (result, "E", []))
    with pytest.raises(ValueError, match="holds the row"):
        loop.main([])
    assert saved.read_bytes() == before and c.shelf(archive, lambda task: None).get(("A", "bug", 1)) is not None


def test_finding_13_a_side_store_that_is_no_row_is_written_as_before(tmp_path):
    (tmp_path / c.SAVED).mkdir()
    (tmp_path / c.SAVED / "cegis.json").write_text(json.dumps({"status": "stopped"}), "utf-8")
    assert c.beside(tmp_path, "cegis.json") == tmp_path / c.SAVED / "cegis.json"


def test_finding_14_rescore_never_writes_through_an_amended_folder_that_is_the_rows_folder(tmp_path, monkeypatch):
    archive, row, _, saved = kept_row(tmp_path)
    before = saved.read_bytes()
    diff = tmp_path / "amendment.patch"
    diff.write_bytes(b"amendment\n")
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    monkeypatch.setattr(loop, "tool", lambda *args, **kwargs: json.dumps({
        "checks": [{"patch": "final", "passes": True}], "amendment_sha256": c.sha256_file(diff)}))
    with junction(tmp_path / c.SAVED, tmp_path / c.SAVED / c.AMENDED):
        with pytest.raises(ValueError, match="saved rows' folder itself"):
            loop.rescore(tmp_path, "g6")
        with pytest.raises(ValueError, match="saved rows' folder itself"):
            c.keep(archive, row, amended=True)
        assert saved.read_bytes() == before


# --- 10: one reader of a review's bug ----------------------------------------------------------------------------------------
def test_finding_10_a_cached_review_of_another_bug_is_not_given_to_this_one(tmp_path):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path)
    candidate = archive.start("run", task.id, arm="K2", repeat=1)
    candidate.write("final.patch", b"candidate patch\n")
    candidate.finish({"agent_started": True}, "finished")
    row = {"run": candidate.rel, "task": task.id}
    (tmp_path / c.SAVED).mkdir()
    record = replay3.bug_record(tmp_path, task.id)
    record.parent.mkdir(parents=True)
    foreign = {"verdict": "none", "rule": "ANOTHER BUG'S RULE", "why": "", "test": "", "error": ""}
    record.write_text(json.dumps({"task": "another-bug", "instruction": replay3.INSTRUCTION, "found": foreign}), "utf-8")
    asked = []

    def ask(*args, **kwargs):
        asked.append(1)
        return {**foreign, "rule": "the requested bug's rule"}, None, None

    got = replay3.review_bug(SimpleNamespace(archive=archive), tmp_path, task, [row], "title", "issue", ask=ask)
    assert asked == [1] and got[0]["rule"] == "the requested bug's rule"
    assert json.loads(record.read_text("utf-8"))["task"] == task.id


def _review_reads(folder: Path = STUDY) -> list[tuple[str, str, bool]]:
    """Every call of the saved reviews' reader (``final_review.read``), as (module, function, whether its result goes straight into
    ``of_bug``), and every read of a JSON file under a store beside the rows that is not that reader's."""
    found = []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                reader = (isinstance(f, ast.Attribute) and f.attr == "read" and isinstance(f.value, ast.Name)
                          and f.value.id in ("fr", "final_review")) or (path.stem == "final_review" and isinstance(f, ast.Name)
                                                                         and f.id == "read")
                if reader:
                    up = parents.get(node)
                    named = isinstance(up, ast.Call) and getattr(up.func, "attr", getattr(up.func, "id", "")) == "of_bug"
                    found.append((path.stem, fn.name, named))
                elif getattr(f, "attr", getattr(f, "id", "")) in ("loads", "read_text", "read_bytes") and "beside(" in ast.unparse(node):
                    found.append((path.stem, fn.name, False))
    return sorted(set(found))


def test_every_reader_of_a_saved_review_goes_through_one_bugs_rule():
    """Study review 3, finding 10: replay 3 read its cached review past ``of_bug``. Every read of a saved or cached review, in every
    module of the study, goes straight into ``final_review.of_bug``, but the one that reads to refuse another bug's (``_another_bug``)."""
    reads = _review_reads()
    assert [(m, fn) for m, fn, named in reads if not named] == [("final_review", "_another_bug")], reads
    assert {(m, fn) for m, fn, _ in reads} >= {("final_review", "saved"), ("replay3", "review_bug"), ("replay_compare", "patches")}


# --- 6: names as bytes -------------------------------------------------------------------------------------------------------
def test_finding_6_a_name_with_a_carriage_return_reaches_git_as_its_bytes(tmp_path, monkeypatch):
    name = "pkg\rname.py"
    raw = (f'diff --git "a/{name}" "b/{name}"\n--- "a/{name}"\n+++ "b/{name}"\n@@ -1 +1 @@\n-old\n+new\n').encode()
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path.parent)
    read = subprocess.run(["git", "apply", "--numstat", "-z"], input=raw, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert read.returncode == 0 and c.numstat_names(read.stdout) == [name]
    monkeypatch.setattr(export.tempfile, "TemporaryDirectory", lambda **kwargs: contextlib.nullcontext(tmp_path))
    assert export.diff_of(raw).encode("utf-8", "surrogateescape") == raw
    assert export.changed(export.diff_of(raw)) == {name: [(1, 1)]}


def test_finding_6_the_diff_a_case_shows_has_its_line_ends_made_newline_as_before():
    text = export.case_text("issue", export.diff_of(b"diff --git a/x.py b/x.py\r\n+new\r\n"), {})
    assert "+new\n" in text and "\r" not in text


# --- 2, ruled out: the frozen rule as registered ------------------------------------------------------------------------------
def test_finding_2_ruled_out_the_frozen_rule_needs_more_passes_and_the_bug_level_p_both():
    """PLAN.md (647f81b), "what the frozen rule requires": with no pair missing, more passes than the other arm AND the sign test
    over bugs below the bar. Pinned so the reading cannot drift: the review's two tables, the same bug signs, opposite decisions."""
    tasks = [f"bug-{n}" for n in range(100)]
    narrow, wide = {}, {}
    for n, task in enumerate(tasks):
        for repeat in (1, 2):
            for table, a, b in ((narrow, n < 60 and repeat == 1, n >= 60), (wide, n < 60, n >= 60 and repeat == 1)):
                table[("K2", task, repeat)] = {"maintainers": a}
                table[("A", task, repeat)] = {"maintainers": b}
    a = compare.compare(narrow, "K2", "A", tasks, [1, 2], "maintainers")
    b = compare.compare(wide, "K2", "A", tasks, [1, 2], "maintainers")
    assert a["bugs"] == b["bugs"] == {"better": 60, "worse": 40, "same": 0} and a["bug_p"] == b["bug_p"] < .05
    assert (a["loop_passed"], a["other_passed"], compare.judged([a])) == (60, 80, False)
    assert (b["loop_passed"], b["other_passed"], compare.judged([b])) == (120, 40, True)


@pytest.mark.parametrize("result,judged", [
    ({"missing": 0, "loop_passed": 5, "other_passed": 3, "bug_p": 0.01}, True),
    ({"missing": 0, "loop_passed": 5, "other_passed": 5, "bug_p": 0.01}, False),     # no more passes
    ({"missing": 0, "loop_passed": 5, "other_passed": 3, "bug_p": 0.05}, False),     # the bug-level p not below the bar
    ({"missing": 1, "loop_passed": 5, "other_passed": 3, "bug_p": 0.01}, False),     # a pair missing
])
def test_finding_2_ruled_out_each_condition_of_the_frozen_rule_is_its_own(result, judged):
    assert compare.judged([result]) is judged and compare.judged([]) is False
