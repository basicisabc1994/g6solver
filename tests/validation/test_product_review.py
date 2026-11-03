# SPDX-License-Identifier: Apache-2.0
"""Replay 2 (PLAN.md, 2026-11-03, afternoon): the product's reviewer, with its own pack and no tools, on the final review's patches."""
from __future__ import annotations

import ast
import io
import itertools
import json
import os
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import product_review as pr  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402
from g6.core import host_models  # noqa: E402
from g6.core.llm import LLMError  # noqa: E402
from g6.workforce import repo_tests  # noqa: E402

TASK = "black-5634"
LOOP = "src/g6/harness/completion_loop.py"
PIECES = ("_head", "_pack", "_read_whole")
QUIET = "RULE: every item is kept\nVERDICT: none\nWHY: nothing found\n"
FOUND = "RULE: every item is kept\nVERDICT: defect\nWHY: drops the last\n```python\ndef test_x():\n    assert 0\n```\n"
PATCH = ("diff --git a/src/app.py b/src/app.py\nindex 1..2 100644\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n"
         " def f():\n-    return 1\n+    return 2\n")
PARENT = {"src/app.py": b"def f():\r\n    return 1\r\n", "src/other.py": b"x = 1\n"}


def _next(rel: str) -> str:
    """``rel`` as claude/next holds it, the product's branch, which this replay never changes."""
    for ref in ("claude/next", "origin/claude/next"):
        got = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True)
        if got.returncode == 0:
            return got.stdout.decode("utf-8").replace("\r\n", "\n")
    pytest.skip("claude/next is not in this clone")


def _defs(source: str) -> dict[str, str]:
    return {node.name: ast.get_source_segment(source, node) for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def _values(source: str, names: tuple[str, ...]) -> dict:
    """The module-level assignments of ``names`` in ``source``, run in order."""
    space: dict = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)} & set(names):
            exec(compile(ast.Module([node], []), "<claude/next>", "exec"), space)
    return {name: space[name] for name in names}


def _tar(files) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return raw.getvalue()


class _Docker:
    def __init__(self, files=None):
        self.calls, self.files = [], PARENT if files is None else files

    def __call__(self, *args, input=None, timeout=None, env=None):
        self.calls.append(args)
        out, code = b"", 0
        if "ls-tree" in args:                    # read_at's way: the path named at the commit, then its blob
            out = (args[-1].encode() + b"\0") if args[-1] in self.files else b""
        elif "show" in args:
            rel = args[-1].split(":", 1)[1]
            out, code = (self.files[rel], 0) if rel in self.files else (b"", 128)
        return type("Done", (), {"stdout": out, "stderr": b"", "returncode": code})()

    def sh(self, *args, **kw):
        raise AssertionError("replay 2 runs nothing in the container")


class _Reviewer:
    """Codex as the product calls it: a system text and a request, and a reply that quotes the pack's first line."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if reply.startswith("ERROR") else f"READ: {user.splitlines()[1]}\n{reply}"


def _ctx(tmp_path, docker=None):
    class Ctx:
        pass

    ctx = Ctx()
    ctx.archive, ctx.docker, ctx.g6, ctx.variant, ctx.arm = type("A", (), {"root": tmp_path})(), docker or _Docker(), {"commit": "c" * 40}, we.ROUND3, "L9"
    return ctx


def _patch(tmp_path, body=PATCH):
    patch = tmp_path / "final.patch"
    patch.write_bytes(body.encode("utf-8"))
    return patch


def _review(tmp_path, reviewer, docker=None, body=PATCH):
    return pr.review(_ctx(tmp_path, docker), we.task_by_id(TASK), _patch(tmp_path, body), "a title", "the issue", reviewer=reviewer)


# --- the product's own pieces, held to claude/next -----------------------------------------------------------------------------

def test_the_packs_pieces_are_claude_nexts_text_byte_for_byte():
    ours, theirs = _defs(Path(pr.__file__).read_text("utf-8")), _defs(_next(LOOP))
    for name in PIECES:
        assert ours[name] == theirs[name], name


def test_the_packs_limit_the_reviewers_time_and_the_too_long_words_are_claude_nexts():
    names = ("MAX_PACK_CHARS", "CRITIC_SECONDS", "TOO_LONG")
    assert _values(_next(LOOP), names) == {name: getattr(pr, name) for name in names}
    assert pr.MAX_PACK_CHARS == 120_000


def test_the_prompt_is_the_products_critic_byte_for_byte():
    data = json.loads(_next("src/g6/harness/data/completion_harness.json"))
    added = (data.get("trained") or {}).get("critic")
    assert pr.CRITIC == (data["critic"].replace("</task>", f"4. {added}\n</task>", 1) if added else data["critic"])
    assert pr.CRITIC == we.PATCH_CRITIC


def test_codex_is_called_with_the_products_own_caller():
    assert (ROOT / "src" / "g6" / "core" / "host_models.py").read_text("utf-8") == _next("src/g6/core/host_models.py")
    host = _defs(_next(LOOP))["critic_host"]
    assert "return host_models.CodexCaller(timeout=CRITIC_SECONDS)" in host


def test_the_pack_is_what_claude_nexts_pack_builds_from_the_same_patch(tmp_path):
    space = {"repo_tests": repo_tests, **_values(_next(LOOP), ("MAX_PACK_CHARS",))}
    tree = ast.parse(_next(LOOP))
    exec(compile(ast.Module([n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in PIECES], []), "<claude/next>", "exec"),
         space)
    repo = tmp_path / "repo"
    files = {"src/app.py": b"def f():\n    return 1\n", "src/crlf.py": b"x = 1\r\ny = 2\r\n", "src/gone.py": b"z = 0\n",
             "docs/a.txt": b"alpha\nbeta\ngamma\ndelta\n", "my file.py": b"a = 1\n", "src/café.py": b"b = 1\n",
             "src/untouched.py": b"c = 1\n"}
    for name, body in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(body)

    def git(*args, env=None):
        fixed = ("core.autocrlf=false", "core.quotepath=true", "diff.renames=true", "user.name=t", "user.email=t@example.invalid")
        return subprocess.run(["git", *(x for one in fixed for x in ("-c", one)), *args], cwd=repo, check=True, capture_output=True,
                              env=env).stdout

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "parent")
    base = git("rev-parse", "HEAD").decode().strip()
    (repo / "src" / "app.py").write_bytes(b"def f():\n    return 2\n")
    (repo / "src" / "crlf.py").write_bytes(b"x = 1\r\ny = 3\r\n")
    (repo / "docs" / "a.txt").rename(repo / "docs" / "b.txt")
    (repo / "src" / "gone.py").unlink()
    (repo / "src" / "new.py").write_bytes(b"n = 1\n")
    (repo / "my file.py").write_bytes(b"a = 2\n")
    (repo / "src" / "café.py").write_bytes(b"b = 2\n")
    index = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "index")}    # run.diff_script's way: a separate index
    git("read-tree", base, env=index)
    git("add", "-A", env=index)
    patch = git("diff", "--cached", "--binary", base, env=index).decode("utf-8", "replace")
    assert "rename from docs/a.txt" in patch and '"a/src/caf\\303\\251.py"' in patch
    proof = f"review {'1' * 32}"
    want = space["_pack"](repo_tests.change(repo, base), patch, "a title\n\nthe issue", proof)
    got = pr.pack(pr.parent_files(lambda *a: git(*a), base, pr.touched(patch)), patch, "a title\n\nthe issue", proof)
    assert got == want
    for shown in ("parent/docs/a.txt:", "parent/src/gone.py:", "parent/src/crlf.py:\nx = 1\ny = 2\n", "parent/my file.py:",
                  "parent/src/café.py:"):
        assert shown in got, shown
    assert "parent/src/untouched.py" not in got and "parent/src/new.py" not in got and "parent/docs/b.txt" not in got


@pytest.mark.parametrize(("patch", "files"), [
    (PATCH, ("src/app.py",)),
    ("diff --git a/x.py b/y.py\nsimilarity index 90%\nrename from x.py\nrename to y.py\n", ("x.py", "y.py")),
    ("diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n@@ -0,0 +1 @@\n+diff --git a/z b/z\n", ("n.py",)),
    ("diff --git a/a b.py b/a b.py\ndeleted file mode 100644\n", ("a b.py",)),
    ('diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n', ("café.py",)),
    ("diff --git a/i.png b/i.png\nindex 1..2\nGIT binary patch\nliteral 3\nKcmZ\n", ("i.png",)),
])
def test_the_files_a_patch_touches_are_the_ones_git_would_list(patch, files):
    assert pr.touched(patch) == files


# --- one review ---------------------------------------------------------------------------------------------------------------

def test_the_reviewer_gets_the_products_prompt_and_pack_and_nothing_else(tmp_path):
    reviewer = _Reviewer(QUIET)
    got = _review(tmp_path, reviewer)
    (system, user), = reviewer.calls
    assert system == pr.CRITIC
    proof = user.splitlines()[1]
    assert proof.startswith("review ") and user == pr.pack(PARENT, PATCH, "a title\n\nthe issue", proof)
    assert user == f"issue.txt:\n{proof}\n\na title\n\nthe issue\n\ncandidate.patch:\n{PATCH}\n\nparent/src/app.py:\ndef f():\n    return 1\n"
    assert got["said"] == "none" and got["verdict"] == "none" and got["pack_chars"] == len(user) and not got["cut"]


def test_a_defect_is_the_reviewers_verdict_with_no_test_run(tmp_path):
    got = _review(tmp_path, _Reviewer(FOUND))
    assert got["said"] == "defect" and got["test"].startswith("def test_x")


def test_the_parent_comes_from_the_agents_image_with_no_network_and_never_the_oracles(tmp_path):
    docker = _Docker()
    _review(tmp_path, _Reviewer(QUIET), docker)
    started = next(args for args in docker.calls if args[0] == "run")
    agent, oracle = we.image_names(we.task_by_id(TASK), "c" * 40, we.ROUND3)
    assert agent in started and oracle not in started and started[started.index("--network") + 1] == "none"
    assert any(args[:2] == ("rm", "-f") for args in docker.calls)
    assert not any("/oracle" in " ".join(map(str, args)) for args in docker.calls)


def test_no_arm_and_no_verdict_reaches_the_reviewer(tmp_path, monkeypatch, capsys):
    reviewer = _Reviewer(QUIET)
    _main(tmp_path, monkeypatch, capsys, reviewer, rows=(("K1", "patch one", False), ("M9", PATCH, True)))
    assert len(reviewer.calls) == 2
    for system, user in reviewer.calls:
        assert system == pr.CRITIC and user.split("\n", 2)[2].startswith("\na title\n\nthe issue\n\nthe thread\n\ncandidate.patch:\n")
        for word in ("K1", "M9", "passes", "amended", "maintainers"):
            assert word not in system + user, word


def test_a_head_too_long_is_too_long_and_asks_no_one(tmp_path):
    reviewer, docker = _Reviewer(QUIET), _Docker()
    got = _review(tmp_path, reviewer, docker, body=PATCH + "+" * pr.MAX_PACK_CHARS)
    assert got["said"] == "too long" and got["error"] == pr.TOO_LONG and reviewer.calls == [] and docker.calls == []


def test_the_pack_is_cut_at_the_products_limit(tmp_path):
    reviewer = _Reviewer(QUIET)
    got = _review(tmp_path, reviewer, _Docker({"src/app.py": b"#" * (2 * pr.MAX_PACK_CHARS)}))
    assert len(reviewer.calls[0][1]) == pr.MAX_PACK_CHARS and got["cut"] and got["said"] == "none"


def test_a_limit_is_saved_as_failed_with_its_text_and_is_not_counted(tmp_path):
    root = tmp_path / "round3"
    row = _row(root, "A")
    raised = _review(tmp_path, _Reviewer(LLMError("G6_E_LLM_HOST_FAILED: codex exited 1: You've hit your usage limit.")))
    assert raised["said"] == "failed" and "usage limit" in raised["error"] and fr.no_review(raised)
    fr.keep(root, row, raised, pr.FOLDER)
    assert fr.said_of(root, row, pr.FOLDER) is None and len(fr.waiting(root, [TASK], pr.FOLDER)) == 1
    printed = _review(tmp_path, _Reviewer("ERROR: You've hit your usage limit. Try again at 3:05 PM."))
    assert printed["said"] == "failed" and printed["verdict"] == "unread" and "usage limit" in printed["reply_end"]
    assert fr.no_review(printed)


def test_a_container_that_fails_is_no_reviewers_failure(tmp_path):
    class Broken(_Docker):
        def __call__(self, *args, **kw):
            super().__call__(*args, **kw)
            return type("Done", (), {"stdout": b"", "stderr": b"no such image", "returncode": 1})()

    reviewer = _Reviewer(QUIET)
    got = _review(tmp_path, reviewer, Broken())
    assert got["said"] == "failed" and "review container" in got["error"] and not fr.no_review(got) and reviewer.calls == []


class _GitFails(_Docker):
    """The parent's checkout, where one git command exits as git does when it cannot read what it was asked for."""

    def __init__(self, command):
        super().__init__()
        self.command = command

    def __call__(self, *args, **kw):
        done = super().__call__(*args, **kw)
        if self.command in args:
            return type("Done", (), {"stdout": b"", "stderr": b"fatal: object read failed", "returncode": 128})()
        return done


@pytest.mark.parametrize("command", ["ls-tree", "show"])
def test_a_parent_file_git_cannot_read_fails_the_review_and_asks_no_one(tmp_path, command):
    reviewer = _Reviewer(QUIET)
    got = _review(tmp_path, reviewer, _GitFails(command))
    assert got["said"] == "failed" and f"git {command}" in got["error"] and reviewer.calls == [] and not fr.no_review(got)


def test_a_file_the_parent_does_not_hold_is_left_out_and_is_no_failure(tmp_path):
    reviewer = _Reviewer(QUIET)
    got = _review(tmp_path, reviewer, _Docker({}))
    assert got["said"] == "none" and "\nparent/" not in reviewer.calls[0][1]


@pytest.mark.parametrize("reply", ["RULE: The issue requires", "RULE: x\nVERDICT: maybe\nWHY: x\n"])
def test_a_reply_that_quotes_the_pack_but_gives_no_verdict_is_the_reviewers_failure(tmp_path, reply):
    got = _review(tmp_path, _Reviewer(reply))
    assert got["said"] == "failed" and got["failed_at"] == "reviewer" and fr.no_review(got)
    assert reply.splitlines()[0] in got["reply_end"]


def test_the_default_reviewer_is_codex_as_critic_host_builds_it(monkeypatch):
    made = []

    class Caller:
        def __init__(self, model="", *, timeout=600.0):
            made.append({"model": model, "timeout": timeout})

        def complete(self, system, user):
            made.append((system, user))
            return "a reply"

    monkeypatch.setattr(host_models, "CodexCaller", Caller)
    assert pr.codex("the prompt", "the pack") == "a reply"
    assert made == [{"model": "", "timeout": pr.CRITIC_SECONDS}, ("the prompt", "the pack")]


def test_that_caller_gives_codex_no_shell_and_an_empty_folder():
    argv = host_models.CodexCaller().argv("codex", work="WORK", last_message="LAST")
    assert argv[:4] == ["codex", "exec", "--sandbox", "read-only"] and argv[-1] == "-"
    joined = " ".join(argv)
    assert "--disable shell_tool --disable unified_exec" in joined and "-C WORK" in joined and "--ephemeral" in joined
    assert "--ignore-user-config" in joined and "approval_policy=never" in joined


# --- the replay: saving, resuming and pausing ----------------------------------------------------------------------------------

def _row(root, arm, task=TASK, repeat=1, body=PATCH, status="finished", passes=True):
    run = root / "run" / f"{arm}{repeat}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "final.patch").write_bytes(body.encode("utf-8"))
    row = {"arm": arm, "task": task, "repeat": repeat, "run": f"run/{arm}{repeat}", "status": status, "passes": passes, "failing": 0}
    (root / "loop").mkdir(exist_ok=True)
    (root / "loop" / f"{arm}-{task}-{repeat}.json").write_text(json.dumps(row), "utf-8")
    return row


def _main(tmp_path, monkeypatch, capsys, reviewer, argv=(), rows=(("A", PATCH, True), ("L9", PATCH + " ", True))):
    root = tmp_path / "round3"
    for arm, body, passes in rows:
        _row(root, arm, body=body, passes=passes)
    docker, ids, ticks = _Docker(), itertools.count(1), itertools.count(1000.0, 1.0)
    monkeypatch.setattr(pr.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(pr.we, "Docker", lambda: docker)
    monkeypatch.setattr(pr.we, "g6_info", lambda g6: {"commit": "c" * 40})
    monkeypatch.setattr(pr.we, "read_issue", lambda task: ("a title", "the issue"))
    monkeypatch.setattr(pr.we, "read_thread", lambda task: "\n\nthe thread")
    monkeypatch.setattr(pr, "codex", reviewer)
    monkeypatch.setattr(pr.uuid, "uuid4", lambda: uuid.UUID(int=next(ids)))
    monkeypatch.setattr(pr.time, "monotonic", lambda: next(ticks))
    pr.main(["--tasks", TASK, *argv])
    saved = {p.relative_to(root).as_posix(): json.loads(p.read_text("utf-8")) for p in sorted((root / "loop").rglob("*.json"))
             if p.parent.name != "loop"}
    return {"out": capsys.readouterr().out, "saved": saved, "root": root}


def test_each_review_is_saved_by_the_patchs_digest_in_its_own_folder(tmp_path, monkeypatch, capsys):
    got = _main(tmp_path, monkeypatch, capsys, _Reviewer(QUIET))
    digests = sorted(c.sha256_file(got["root"] / "run" / run / "final.patch") for run in ("A1", "L91"))
    assert sorted(got["saved"]) == [f"loop/{pr.FOLDER}/{sha}.json" for sha in digests]
    assert all(one["said"] == "none" and one["task"] == TASK for one in got["saved"].values())
    printed = [json.loads(line) for line in got["out"].splitlines()]
    assert [set(line) for line in printed] == [{"task", "said", "seconds", "error"}] * 2


def test_a_saved_review_is_skipped_and_a_failed_one_is_made_again(tmp_path, monkeypatch, capsys):
    root = tmp_path / "round3"
    done, again = _row(root, "A"), _row(root, "L9", body=PATCH + " ")
    fr.keep(root, done, {"said": "defect"}, pr.FOLDER)
    fr.keep(root, again, {"said": "failed"}, pr.FOLDER)
    reviewer = _Reviewer(QUIET)
    got = _main(tmp_path, monkeypatch, capsys, reviewer, rows=())
    assert len(reviewer.calls) == 1 and sorted(one["said"] for one in got["saved"].values()) == ["defect", "none"]


def test_two_reviews_in_a_row_with_no_review_pause_the_replay(tmp_path, monkeypatch, capsys):
    rows = (("A", PATCH, True), ("G1", PATCH + " ", True), ("L9", PATCH + "  ", True))
    got = _main(tmp_path, monkeypatch, capsys, _Reviewer(LLMError("G6_E_LLM_HOST_FAILED: codex exited 1: limit")), rows=rows)
    lines = [json.loads(line) for line in got["out"].splitlines()]
    assert [line.get("said") for line in lines[:2]] == ["failed", "failed"] and "paused" in lines[2] and len(lines) == 3
    assert len(got["saved"]) == 2


def test_two_replies_in_a_row_with_no_verdict_pause_the_replay(tmp_path, monkeypatch, capsys):
    rows = (("A", PATCH, True), ("G1", PATCH + " ", True), ("L9", PATCH + "  ", True))
    reviewer = _Reviewer("RULE: The issue requires")
    got = _main(tmp_path, monkeypatch, capsys, reviewer, rows=rows)
    assert len(reviewer.calls) == 2 and "paused" in json.loads(got["out"].splitlines()[-1])


@pytest.mark.parametrize("stop", ["", "futility product, block 1 (patches 1-40 by digest): cannot be reached"])
def test_the_futility_stop_is_read_before_each_review_so_a_resumed_replay_honours_it(tmp_path, monkeypatch, capsys, stop):
    import replay_compare as rc

    asked = []
    monkeypatch.setattr(rc, "predicted", lambda: {TASK, "x-9"})
    monkeypatch.setattr(rc, "futile", lambda root, tasks, name: asked.append((name, set(tasks))) or stop)
    reviewer = _Reviewer(QUIET)
    got = _main(tmp_path, monkeypatch, capsys, reviewer)
    if stop:
        assert reviewer.calls == [] and got["saved"] == {} and json.loads(got["out"]) == {"stopped": stop}
    else:
        assert len(reviewer.calls) == 2 and len(got["saved"]) == 2
    assert asked and set(asked[0][1]) == {TASK, "x-9"} and {name for name, _ in asked} == {rc.PRODUCT}


def test_a_training_patch_has_no_futility_stop_and_a_stop_leaves_it_to_be_reviewed(tmp_path, monkeypatch, capsys):
    import replay_compare as rc

    other = next(task for task in fr.TRAINING if task != TASK)
    assert not {TASK, other} & rc.predicted()
    asked = []
    monkeypatch.setattr(rc, "predicted", lambda: {TASK})        # as if this bug were stage 1's, and the other training
    monkeypatch.setattr(rc, "futile", lambda root, tasks, name: asked.append(name) or "futility product: cannot be reached")
    root = tmp_path / "round3"
    _row(root, "B", task=other, body=PATCH + "   ")
    reviewer = _Reviewer(QUIET)
    got = _main(tmp_path, monkeypatch, capsys, reviewer, argv=(other,))
    lines = [json.loads(line) for line in got["out"].splitlines()]
    assert [line.get("task") for line in lines if "task" in line] == [other] and len(reviewer.calls) == 1
    assert sum("stopped" in line for line in lines) == 1 and asked == [rc.PRODUCT]


@pytest.mark.parametrize("name", ["review", "pack", "touched", "read_at", "codex", "said"])
def test_the_module_says_what_each_step_does(name):
    assert getattr(pr, name).__doc__
