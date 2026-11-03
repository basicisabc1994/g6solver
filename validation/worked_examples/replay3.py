# SPDX-License-Identifier: Apache-2.0
"""Replay 3 (PLAN.md, 2026-11-04, early): a test of the rule the issue states, written once a bug by a reviewer shown the issue and
the parent and never a patch, then run on the parent and on each distinct patch of that bug. It changes no run, no verdict and no
rule.

Why: in replay 1's pilot the agents' wrong patches passed the issue's own example (33 of 34 reproduced), so what tells a wrong patch
from a right one is the behaviour around the example. The reviewer is asked as replay 1's first step asks (``final_review._step``,
its container, pack, reply and test runs), with ``RULE_FIRST`` in place of ``REPRODUCE_FIRST``; the bug's other patches run the
same test the same way (``final_review.container``, ``final_review.test_runs``), with no further call. A test that fails no case on
the parent reproduces nothing, and the bug's patches are then "none"; otherwise a patch is "reproduced", that is wrong, when any of
its cases fails with it. Each bug's test is saved under the instruction's digest (``INSTRUCTION``), so a run resumes with no new
call and a changed instruction asks again; each patch's verdict is saved under its digest in ``FOLDER``, as the replays save theirs.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import final_review as fr
import run as we
import wecommon as c

#: Where the rule's test waits in the container, apart from the other replays' tests, and its file.
RULE_AT, FILE = f"{we.WE}/rule", "test_rule.py"
#: The instruction, in general words only: no bug, file or issue of any stage. Its limits, its reply's form and its grounding rules
#: are ``REPRODUCE_FIRST``'s.
RULE_FIRST = """<task>
You are writing the test a maintainer would write for a reported bug, before anyone shows you a fix. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.

No fix is in your cwd, and none is described to you. A fix that only makes the issue's own example pass is not a fix: test the rule.

1. State the rule the issue implies, in one or two sentences: not only its example, but what must hold for every input of the same
   kind. Take it from the issue, and from how parent/'s own code, tests and documentation treat the same behaviour.
2. Find in parent/ each public path that rule governs: the function, class, method, command or option the issue uses, and the other
   public entry points that reach the same code or handle the same kind of input, such as its siblings in the same module and the
   other types, formats or options it supports.
3. Write one pytest file that checks the rule on those paths: the issue's own example first, then the variants a maintainer would
   expect to hold as well (other values of the same kind, the edges of the input's range, each sibling path), then the behaviour
   next to it that must not change. Write each case as its own test, from 4 to 12 in all, each asserting an exact value that the
   issue or parent/ justifies. A case of the issue's rule must fail on parent/ if the issue is right; a case of behaviour that must
   not change must pass on parent/. Assert nothing about how a fix might work. Write nothing whose expectation you cannot justify:
   a wrong test is worse than none. If the issue states no rule a test can check, say so and write no test. That is a valid and
   useful answer.
</task>

<hard_limits>
Read-only, and only inside your cwd. No writes, no git writes, no docker, no network, no model or API calls. You cannot run code, so
reason carefully about what each test will do.
</hard_limits>

<structured_output_contract>
In this exact form:

READ: <the first line of issue.txt, exactly>
RULE: <the rule the issue implies, in one or two sentences>
VERDICT: defect or none
WHY: <the paths and variants you test, and the evidence in issue.txt or parent/ with file and line for each expectation>
```python
<a complete pytest file, or nothing if VERDICT is none>
```

VERDICT is defect if you wrote a test of the rule, and none if you wrote no test. Name each test test_<something>. The test file must
import only the project and the standard library, and pytest if needed.
</structured_output_contract>

<grounding_rules>
Every expectation must point at the issue or at a file and line in parent/. If you are unsure of an expected value, drop that test.
</grounding_rules>

<default_follow_through_policy>
Do not ask questions. Finish.
</default_follow_through_policy>
"""
INSTRUCTION = hashlib.sha256(RULE_FIRST.encode("utf-8")).hexdigest()[:16]
#: Each patch's verdict and each bug's test, by instruction: a record's identity holds the instruction it was made under, so a
#: changed instruction finds nothing made under another and asks and runs again.
FOLDER = f"final_review_rule-{INSTRUCTION}"
Ask = Callable[..., tuple[dict[str, Any], "we.ProbeRun | None", "we.ProbeRun | None"]]
Run = Callable[[Any, we.Task, str, Path], tuple["we.ProbeRun", "we.ProbeRun"]]


def no_answer(found: Mapping[str, Any]) -> str:
    """Why the reviewer's answer is no answer, or "": an error, a verdict that is neither defect nor none, or a defect with no
    test, which a reply cut short gives. The one test of an answer, wherever replay 3 reads one."""
    if found.get("error"):
        return str(found["error"])
    if found.get("verdict") not in ("defect", "none"):
        return "the reviewer gave no verdict"
    if found["verdict"] == "defect" and not found.get("test"):
        return "the reviewer said it wrote a test and none came, as a reply cut short gives"
    return ""


def failed_cases(run: we.ProbeRun | None) -> list[str]:
    """The cases a run recorded as failed, whatever pytest's exit: a run cut short after a failure still shows that failure."""
    return sorted(f"rule:{case}" for case, outcome in (run.results if run else {}).items() if outcome == "failed")


def verdict(found: Mapping[str, Any], base: we.ProbeRun | None, now: we.ProbeRun | None) -> dict[str, Any]:
    """One patch's record: "failed" where there was no review, "reproduced" where a test that fails on the parent has a case that
    fails with the patch, and "none" otherwise, with the reason."""
    at_parent, on_patch = failed_cases(base), failed_cases(now)
    if no_answer(found):
        said, reason = "failed", no_answer(found)
    elif not found.get("test"):
        said, reason = "none", "no test was written"
    elif base is None or now is None or base.exit is None or now.exit is None:
        said, reason = "failed", "a test run gave no exit, as when the container fails, so nothing was learnt"
    elif not at_parent:
        said, reason = "none", "the test fails no case on the parent, so it reproduces nothing"
    elif on_patch:
        said, reason = "reproduced", ""
    elif not now.usable:
        said, reason = "none", "the test did not run with the patch"
    else:
        said, reason = "none", "every case holds with the patch"
    return {"said": said, "verdict": {"reproduced": "defect", "none": "none"}.get(said, "failed"), "reason": reason,
            "rule": found.get("rule", ""), "why": found.get("why", ""), "test": found.get("test", ""),
            "error": found.get("error", ""), "failing_at_parent": at_parent, "failing_on_patch": on_patch,
            "at_base": base.results if base else {}, "on_patch": now.results if now else {}}


def bug_record(root: Path, task: str) -> Path:
    """Where a bug's test of the rule is saved: one a bug and instruction."""
    return root / "loop" / FOLDER / "bugs" / f"{task}-{INSTRUCTION}.json"


class Checked:
    """``ctx.docker`` for replay 3's test runs: any shell command in the container that ends non-zero raises, so a failed review is
    made again and nothing it printed is read as a run. No such status is normal there: staging the test copy and the probe script
    both end in a command that succeeds, so non-zero is the exec's own failure, and the one status a caller reads, ``git apply``'s,
    already makes a failed review. ``docker`` calls themselves (run, cp, rm) are passed through as they are."""

    def __init__(self, docker: Any):
        self.docker = docker

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.docker(*args, **kwargs)

    def sh(self, ctr: str, script: str, *args: Any, **kwargs: Any) -> Any:
        done = self.docker.sh(ctr, script, *args, **kwargs)
        if done.returncode != 0:
            raise RuntimeError(f"a command in the review's container exited {done.returncode}")
        return done


def run_on(ctx: we.Context, task: we.Task, test: str, patch: Path) -> tuple[we.ProbeRun, we.ProbeRun]:
    """A saved test of the rule on the parent and with ``patch``, in a container of its own, as replay 1 runs its tests, with an
    exec that failed raising (``Checked``)."""
    ctx = copy.copy(ctx)                              # the caller's context keeps its own docker
    ctx.docker = Checked(ctx.docker)
    name = f"g6we-review-{uuid.uuid4().hex[:10]}"
    try:
        fr.container(ctx, task, name)
        base = fr.parent_run(ctx, task, name, test, RULE_AT, FILE)
        return base, fr.patch_run(ctx, task, name, patch, RULE_AT)
    finally:
        ctx.docker("rm", "-f", name, timeout=300)


def review_bug(ctx: we.Context, root: Path, task: we.Task, rows: Sequence[Mapping[str, Any]], title: str, text: str, *,
               ask: Ask | None = None, run: Run | None = None) -> list[dict[str, Any]]:
    """Replay 3 on one bug's waiting patches: its test of the rule, asked once and saved whole before anything runs (or read back),
    then each patch, the first among them, run the one way (``run_on``) and its verdict saved."""
    ask, run = ask or fr._step, run or run_on
    if not rows:
        return []                                     # nothing waits, so no one is asked
    saved = bug_record(root, task.id)
    found = (fr.read(saved) or {}).get("found")       # one cut short is no record, and the bug is asked again
    got = []
    if not isinstance(found, dict) or no_answer(found):    # a saved answer meets the same test as a fresh one
        started = time.monotonic()
        found, _, _ = ask(ctx, task, root / str(rows[0]["run"]) / "final.patch", title, text, RULE_FIRST, RULE_AT, FILE,
                          candidate=False, run=False)
        if no_answer(found):
            return [_keep(root, rows[0], verdict(found, None, None), started)]    # no review: asked again on the next run
        saved.parent.mkdir(parents=True, exist_ok=True)
        fr.write_whole(saved, json.dumps({"task": task.id, "instruction": INSTRUCTION, "found": found}, indent=1))
    for row in rows:
        started = time.monotonic()
        raw = (root / str(row["run"]) / "final.patch").read_bytes()    # one read: these bytes are run and named, nothing else
        sha = hashlib.sha256(raw).hexdigest()
        base = now = None
        if found.get("test"):
            copy = root / "critic_packs" / f"{uuid.uuid4().hex}.patch"
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(raw)
            try:
                base, now = run(ctx, task, found["test"], copy)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                got.append(_keep(root, row, verdict({**found, "error": f"{type(exc).__name__}: {exc}"[:400]}, None, None),
                                 started, sha))
                continue
            finally:
                copy.unlink(missing_ok=True)
        got.append(_keep(root, row, verdict(found, base, now), started, sha))
    return got


def _keep(root: Path, row: Mapping[str, Any], record: dict[str, Any], started: float, sha: str | None = None) -> dict[str, Any]:
    record = {**record, "instruction": INSTRUCTION, "seconds": round(time.monotonic() - started, 1)}
    fr.keep(root, row, record, FOLDER, sha=sha)
    return record


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6, help="the G6 commit the runs' images were built from")
    ap.add_argument("--tasks", nargs="*", default=list(fr.TRAINING), help="the bugs to review; a bug being judged only after its runs end")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    ctx = we.Context(archive=c.Archive(root), docker=we.Docker(), key_reader=lambda: "", issue_reader=we.read_issue,
                     g6=we.g6_info(args.g6), variant=we.ROUND3, arm="L9")
    import replay_compare     # here, as it imports this module

    replay_compare.patches(root, args.tasks)          # refuses a patch on two bugs, which this replay names by its bytes alone
    print(json.dumps({"instruction": INSTRUCTION}), flush=True)
    with one_runner(root / "loop" / FOLDER):
        _run(ctx, root, args.tasks)


@contextlib.contextmanager
def one_runner(folder: Path) -> Iterator[None]:
    """One replay 3 run at a time on a folder: a second refuses to start while a first holds it, as two would each ask a bug's
    reviewer. A run that died leaves the lock, named in the refusal, for a person to remove after checking no run is alive."""
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".running"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"another replay 3 run holds {lock.name} in {folder.name} ({lock.read_text('utf-8', 'replace')}); "
                         "remove it only once no run is alive") from None
    try:
        os.write(fd, f"pid {os.getpid()}".encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _run(ctx: we.Context, root: Path, tasks: Sequence[str]) -> None:
    for name in tasks:
        rows = fr.waiting(root, [name], FOLDER)
        if not rows:
            continue
        task = we.task_by_id(name)
        title, text = ctx.issue_reader(task)
        for record in review_bug(ctx, root, task, rows, title, text + we.read_thread(task)):
            print(json.dumps({"task": name, "said": record["said"], "reason": record["reason"], "seconds": record["seconds"],
                              "error": record["error"]}), flush=True)


if __name__ == "__main__":
    main()
