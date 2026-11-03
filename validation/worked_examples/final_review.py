# SPDX-License-Identifier: Apache-2.0
"""The final review (PLAN.md, 2026-10-31, late): another model family reads a run's finished patch after the run has ended, and its test, if
it wrote one, runs on the parent and on the patch. It changes no run, no verdict and no rule. It asks the owner's question of a second
reader: when the review reproduces a defect, is the patch wrong, and when it finds nothing, is the patch right?

The reviewer reads the issue with its discussion, the parent from the agent's image, which holds no maintainers' test or fix, and the
saved final patch. It is told nothing of the arm or the verdict, and the order of review is the patches' digests, so a short run is not
weighted to any arm. Saved reviews are cases a harness change can be replayed on. A review uses the owner's Codex sign-in, not the
budget, and is reported apart.

``--reproduce`` is replay 1 (PLAN.md, 2026-11-03, afternoon), beside the frozen review, which it leaves as it was: the same reviewer
first writes a test of the issue's own scenario from the issue and the parent alone (``REPRODUCE_FIRST``), G6 runs it on the parent
and on the patch, and then the frozen review's text, with those results after it (``REPRODUCTION``), judges the patch as the frozen
review does. Its reviews are saved apart, in ``REPRODUCE``.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import run as we
import wecommon as c

FOLDER = "final_review"
TRAINING = ("black-5634", "pydantic-14052", "click-3678", "attrs-1593", "fastapi-15077", "pydantic-14065", "starlette-3720")
#: Replay 1's reviews, and where its first step's test waits in the container, apart from the judge's.
REPRODUCE, REPRODUCE_AT = "final_review_reproduce", f"{we.WE}/reproduce"
#: The lines of each failure's text the judge is shown; the end of a reply that was no review that is kept; and how many reviews
#: in a row with no review from the reviewer pause a replay, as at a Codex limit every call fails and one failure may be the patch's.
FAILURE_LINES, REPLY_END, PAUSE_AFTER = 5, 600, 2
#: Replay 1's first step: the issue's own scenario as a test, from a reviewer who is shown no patch. Its limits, its reply's form
#: and its grounding rules are ``run.PATCH_CRITIC``'s.
REPRODUCE_FIRST = """<task>
You are reproducing a reported bug before anyone judges a fix for it. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.

No fix is in your cwd, and none is described to you. Test the issue as it is reported.

1. State the scenario the issue reports, in one or two sentences: the inputs it gives, the behaviour it describes, and what it says
   should happen instead. Take it from the issue as reported, not from what a fix might do.
2. Find in parent/ the public interface that scenario goes through: the function, class, method, command or option the issue uses or
   names, as the repository's own code, tests and documentation show it.
3. Write one pytest file that runs the issue's scenario through that interface, with the issue's own inputs where it gives them. Each
   test must assert an exact expected value that the issue justifies, and must fail on parent/ if the issue is right. Assert nothing
   about how a fix might work. Write nothing whose expectation you cannot justify: a wrong test is worse than none. If the issue
   reports no scenario a test can run, say so and write no test. That is a valid and useful answer.
</task>

<hard_limits>
Read-only, and only inside your cwd. No writes, no git writes, no docker, no network, no model or API calls. You cannot run code, so
reason carefully about what each test will do.
</hard_limits>

<structured_output_contract>
In this exact form:

READ: <the first line of issue.txt, exactly>
RULE: <the scenario and the behaviour the issue expects, in one or two sentences>
VERDICT: defect or none
WHY: <the concrete input, what parent/ does with it, what it should do, and the evidence in issue.txt or parent/ with file and line>
```python
<a complete pytest file, or nothing if VERDICT is none>
```

VERDICT is defect if you wrote a test that reproduces the issue, and none if you wrote no test. Name each test test_<something>. The
test file must import only the project and the standard library, and pytest if needed.
</structured_output_contract>

<grounding_rules>
Every expectation must point at the issue or at a file and line in parent/. If you are unsure of an expected value, drop that test.
</grounding_rules>

<default_follow_through_policy>
Do not ask questions. Finish.
</default_follow_through_policy>
"""
#: What follows ``run.PATCH_CRITIC``, unchanged, in replay 1's second step: the first step's results, and what to read before judging.
REPRODUCTION = """
<reproduction>
Before this review, another reviewer read issue.txt and parent/, without the candidate, and wrote a test of the scenario the issue
reports. G6 ran it in the repository's own environment, on parent/ and with the candidate applied.
- Failed on parent/: {at_parent}
- With the candidate: {with_patch}
{detail}It can be wrong, as anyone can. Weigh it against the issue and the code.
</reproduction>

<before_you_judge>
Read, in parent/, the callers of each function the candidate changes and the code around each change: the rest of the function,
its neighbours in the same file, and the other code that handles the same input. Then judge.
</before_you_judge>
"""
NO_TEST = "It wrote no test, so nothing was reproduced.\n"
NOT_REPRODUCED = "It did not fail on parent/, so it does not reproduce the issue.\n"


def said(found: Mapping[str, Any], failing: Iterable[str]) -> str:
    """What the review said: "reproduced" if its own test ran and failed on the patch, "opinion" if it says defect with no such test,
    "none" if it found nothing, and "failed" if there was no review, which is no verdict at all."""
    if found.get("error") or found.get("verdict") not in ("defect", "none"):
        return "failed"
    if found["verdict"] == "none":
        return "none"
    return "reproduced" if list(failing) else "opinion"


def review(ctx: we.Context, task: we.Task, patch: Path, title: str, text: str) -> dict[str, Any]:
    """One review of one saved patch, in its own container with no network, from the agent's image and never the oracle's."""
    started = time.monotonic()
    found: dict[str, Any] = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    proof = f"review {uuid.uuid4().hex}"
    # an ordinary folder: mkdtemp's 0o700 is an ACL on Windows that shuts out the critic's sandbox account
    pack = ctx.archive.root / "critic_packs" / proof.split()[1]
    name = f"g6we-review-{uuid.uuid4().hex[:10]}"
    image, _ = we.image_names(task, str(ctx.g6["commit"]), ctx.variant)
    base = now = None
    try:
        we.checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.SECURITY, "-u", we.USER,
                              "-e", f"HOME={we.HOME}", "-w", task.repo, image, "tail", "-f", "/dev/null", timeout=600),
                   "review container")
        pack.mkdir(parents=True)
        tree = ctx.docker("exec", "-u", we.USER, "-w", task.repo, name, "git", "archive", "--format=tar", task.parent, timeout=300)
        we.checked(tree, "the parent's tree")
        with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    dest = pack / "parent" / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(member).read())
        shutil.copyfile(patch, pack / "candidate.patch")
        (pack / "issue.txt").write_text(f"{proof}\n\n{title}\n\n{text}", encoding="utf-8")
        (pack / "critic_prompt.md").write_text(we.critic_text(ctx.arm), encoding="utf-8")
        found = we.critic_report((ctx.critic or we.run_codex)(pack), proof=proof)
        if found["test"]:
            we.write_in(ctx.docker, name, f"{we.CRITIC_AT}/test_critic.py", found["test"] + "\n", user="root")
            base = we.run_set(ctx, task, name, we.CRITIC_AT)
            we.checked(ctx.docker("cp", str(patch), f"{name}:/tmp/we.patch", timeout=300), "patch copy")
            if ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo).returncode != 0:
                raise RuntimeError("the patch does not apply")
            now = we.run_set(ctx, task, name, we.CRITIC_AT)
    except (RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
        shutil.rmtree(pack, ignore_errors=True)
    failing = sorted(we.failures_of([("critic", now, base)])[0]) if now is not None and now.usable else []   # a test that will not run says nothing of the patch
    return {**found, "said": said(found, failing), "failing": failing, "at_base": base.results if base else {},
            "on_patch": now.results if now else {}, "seconds": round(time.monotonic() - started, 1)}


def ended(said: str) -> str:
    """The end of a reply that was no review, with no secret and no home folder in it: what a Codex limit says, when one stopped
    it. Nothing in it tells a limit from any other failure, so it is kept for a reader and decides nothing."""
    from g6.context.redact import mask_paths, scrub

    return mask_paths(scrub(said))[-REPLY_END:]


def no_review(found: Mapping[str, Any]) -> bool:
    """Whether the reviewer itself gave no review: its call failed or ended in failure, or its reply did not quote its pack, as
    at a Codex limit, or gave no verdict, as a reply cut short does. A container or a patch that failed is not the reviewer's."""
    return found.get("verdict") == "unread" or found.get("failed_at") == "reviewer"


def answered(found: dict[str, Any], reply: str) -> dict[str, Any]:
    """A reply as a review: one that did not quote its pack or gave no verdict is the reviewer's failure, with its end kept."""
    if found["verdict"] not in ("defect", "none"):
        found["failed_at"], found["reply_end"] = "reviewer", ended(reply)
    return found


def codex(pack: Path) -> str:
    """Replay 1's reviewer: the frozen review's Codex call (``run.codex_task``), whose exit is read as well, so a reviewer that
    ended in failure gave no review whatever it printed. The frozen review's own call (``run.run_codex``) stays as it was."""
    got = we.codex_task(pack)
    if got.returncode != 0:
        raise RuntimeError(f"the reviewer exited {got.returncode}: {ended(we.err(got))[-200:]}")
    return got.stdout.decode("utf-8", errors="replace")


def container(ctx: we.Context, task: we.Task, name: str) -> None:
    """A review's own container, as ``review`` makes the frozen review's: from the agent's image, never the oracle's, with no
    network."""
    image, _ = we.image_names(task, str(ctx.g6["commit"]), ctx.variant)
    we.checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.SECURITY, "-u", we.USER,
                          "-e", f"HOME={we.HOME}", "-w", task.repo, image, "tail", "-f", "/dev/null", timeout=600),
               "review container")


def parent_run(ctx: we.Context, task: we.Task, name: str, test: str, where: str, file: str) -> we.ProbeRun:
    """A reviewer's test written in ``name``'s ``where`` and run on the parent: the first half of the one way every replay runs
    one, kept apart so that a patch that then fails to apply leaves this run's result in hand."""
    we.write_in(ctx.docker, name, f"{where}/{file}", test + "\n", user="root")
    return we.run_set(ctx, task, name, where)


def patch_run(ctx: we.Context, task: we.Task, name: str, patch: Path, where: str) -> we.ProbeRun:
    """The second half: ``patch`` applied in ``name``, and the test in ``where`` run again."""
    we.checked(ctx.docker("cp", str(patch), f"{name}:/tmp/we.patch", timeout=300), "patch copy")
    if ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo).returncode != 0:
        raise RuntimeError("the patch does not apply")
    return we.run_set(ctx, task, name, where)


def _step(ctx: we.Context, task: we.Task, patch: Path, title: str, text: str, prompt: str, where: str, file: str, *,
          candidate: bool, run: bool = True) -> tuple[dict[str, Any], we.ProbeRun | None, we.ProbeRun | None]:
    """One of replay 1's two steps, made as ``review`` makes the frozen review: its own container (``container``), a pack with
    ``prompt`` and the issue, the parent and, if ``candidate``, the patch, and the reviewer's test, if it wrote one, in ``where``,
    run on the parent (``parent_run``) and then on the patch (``patch_run``). With ``run`` false it returns once the reviewer has
    answered, so a caller can keep the answer before anything is run."""
    found: dict[str, Any] = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    proof = f"review {uuid.uuid4().hex}"
    pack = ctx.archive.root / "critic_packs" / proof.split()[1]   # an ordinary folder, as ``review`` says
    name = f"g6we-review-{uuid.uuid4().hex[:10]}"
    base = now = None
    asking = False
    try:
        container(ctx, task, name)
        pack.mkdir(parents=True)
        tree = ctx.docker("exec", "-u", we.USER, "-w", task.repo, name, "git", "archive", "--format=tar", task.parent, timeout=300)
        we.checked(tree, "the parent's tree")
        with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    dest = pack / "parent" / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(member).read())
        if candidate:
            shutil.copyfile(patch, pack / "candidate.patch")
        (pack / "issue.txt").write_text(f"{proof}\n\n{title}\n\n{text}", encoding="utf-8")
        (pack / "critic_prompt.md").write_text(prompt, encoding="utf-8")
        asking = True
        reply = (ctx.critic or codex)(pack)
        asking = False
        found = answered(we.critic_report(reply, proof=proof), reply)
        if found["test"] and run:
            base = parent_run(ctx, task, name, found["test"], where, file)
            now = patch_run(ctx, task, name, patch, where)
    except (RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
        if asking:
            found["failed_at"] = "reviewer"
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
        shutil.rmtree(pack, ignore_errors=True)
    return found, base, now


def _failing(run: we.ProbeRun | None, label: str) -> list[str]:
    """The cases of a run that failed, as the frozen review counts them: a run that will not run has none."""
    return sorted(we.failures_of([(label, run, None)])[0]) if run is not None and run.usable else []


def reproduction(found: Mapping[str, Any], base: we.ProbeRun | None, now: we.ProbeRun | None) -> dict[str, Any]:
    """Step 1's record: what its reviewer said, its test's runs, and the note the judge reads (``REPRODUCTION``). A test that does
    not fail on the parent is not a reproduction, whatever it does on the patch. The judge is shown each case that failed with the
    patch and the last ``FAILURE_LINES`` lines of what it said, with no secret and no home folder in them."""
    from g6.context.redact import mask_paths, scrub

    at_parent, on_patch = _failing(base, "reproduce"), _failing(now, "reproduce")
    with_patch = "failed" if on_patch else "passed" if now is not None and now.usable else "did not run"
    gave = "failed" if said(found, ()) == "failed" else "reproduced" if at_parent else "not reproduced"
    shown = []
    for case in [one.removeprefix("reproduce:") for one in on_patch][:we.MAX_SHOWN]:
        lines = [line for line in mask_paths(scrub(now.texts.get(case, ""))).splitlines() if line.strip()][-FAILURE_LINES:]
        shown.append("\n".join([f"- {mask_paths(scrub(case))}", *(f"  {line}" for line in lines)])[:we.MAX_CASE_CHARS])
    detail = (NO_TEST if not found.get("test") else "" if at_parent else NOT_REPRODUCED) + (
        "Its cases that failed with the candidate:\n" + "\n".join(shown) + "\n" if shown else "")
    note = REPRODUCTION.format(at_parent="yes" if at_parent else "no", with_patch=with_patch, detail=detail)
    return {**found, "said": gave, "at_parent": "yes" if at_parent else "no", "with_patch": with_patch,
            "failing_at_parent": at_parent, "failing_on_patch": on_patch, "at_base": base.results if base else {},
            "on_patch": now.results if now else {}, "note": note}


def reproduce_review(ctx: we.Context, task: we.Task, patch: Path, title: str, text: str,
                     earlier: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Replay 1 on one saved patch. Step 1: a reviewer shown the issue and the parent and no patch writes a test of the issue's own
    scenario (``REPRODUCE_FIRST``), and G6 runs it on the parent and on the patch. Step 2: ``run.PATCH_CRITIC``, unchanged, then
    step 1's results, judges the patch as ``review`` does, and ``said`` classifies it as it classifies the frozen review. A step 1
    saved whole in ``earlier`` is not asked again, so a run resumes where it stopped; one that failed is made again, and with a
    step 1 that failed no judge is asked."""
    started = time.monotonic()
    first = dict((earlier or {}).get("reproduce") or {})
    if first.get("said") in (None, "failed"):
        first = reproduction(*_step(ctx, task, patch, title, text, REPRODUCE_FIRST, REPRODUCE_AT, "test_reproduce.py",
                                    candidate=False))
        first["seconds"] = round(time.monotonic() - started, 1)
    base = now = None
    if first["said"] == "failed":
        found: dict[str, Any] = {"verdict": "failed", "rule": "", "why": "", "test": "",
                                 "error": "step 1 failed, so no judge was asked"}
    else:
        found, base, now = _step(ctx, task, patch, title, text, we.PATCH_CRITIC + first["note"], we.CRITIC_AT, "test_critic.py",
                                 candidate=True)
    failing = _failing(now, "critic")
    return {**found, "said": said(found, failing), "failing": failing, "at_base": base.results if base else {},
            "on_patch": now.results if now else {}, "seconds": round(time.monotonic() - started, 1), "reproduce": first}


def _kept(root: Path, row: Mapping[str, Any], folder: str = FOLDER) -> Path | None:
    """Where the review of this run's patch is saved, by the patch's digest, so that identical patches share one."""
    patch = root / str(row.get("run")) / "final.patch"
    return root / "loop" / folder / f"{c.sha256_file(patch)}.json" if patch.is_file() else None


def saved(root: Path, row: Mapping[str, Any], folder: str = FOLDER) -> dict[str, Any] | None:
    """The review saved in ``folder`` for this run's patch as it stands, failed or not; None if there is none, or if what is there
    cannot be read, as a write cut short leaves it, so that patch is reviewed again."""
    path = _kept(root, row, folder)
    return read(path) if path else None


def read(path: Path) -> dict[str, Any] | None:
    """A saved review as it stands; None if there is none, or if what is there cannot be read as one, as a write cut short
    leaves it: so it is no review to the replays and to their counts alike."""
    try:
        got = json.loads(path.read_text("utf-8"))
    except (ValueError, OSError):           # a file that is not there is an OSError, and bytes that are not UTF-8 a ValueError
        return None
    return got if isinstance(got, dict) else None


#: The words a review says that count: the final review's and replay 1's (``said``), and the product's (``product_review.said``).
#: "failed" is no review, and so is a record whose word is none of these.
WORDS = frozenset({"reproduced", "opinion", "none", "defect", "too long"})


def counts(found: Mapping[str, Any] | None) -> bool:
    """Whether a saved review counts: it says one of the ``WORDS``. The one test of a review, for the replays that schedule
    reviews and for the counts read from them alike, so that a patch is never both waiting for a review and counted."""
    said = (found or {}).get("said")
    return isinstance(said, str) and said in WORDS


def said_of(root: Path, row: Mapping[str, Any], folder: str = FOLDER) -> str | None:
    """What the review said of this run's patch as it stands; None if it has no review that ``counts``: none at all, one that
    failed, one whose word is no reviewer's, or a record that cannot be read."""
    got = saved(root, row, folder)
    return got["said"] if counts(got) else None


def keep(root: Path, row: Mapping[str, Any], found: Mapping[str, Any], folder: str = FOLDER, *, sha: str | None = None) -> None:
    """Save a review under the patch's digest, whole or not at all: it is written beside its place and moved there, so a run cut
    short leaves no half record. ``sha``, where given, is the digest of the bytes the review read, so a patch file changed since
    is not given this review."""
    path = root / "loop" / folder / f"{sha}.json" if sha else _kept(root, row, folder)
    path.parent.mkdir(exist_ok=True)
    write_whole(path, json.dumps({"task": row["task"], "first_seen_in": row.get("run"), **found}, indent=1))


def write_whole(path: Path, text: str) -> None:
    """``text`` saved at ``path`` whole or not at all: written beside it and moved there, so a write cut short leaves no half
    file. The one way the replays save a record."""
    part = path.with_name(f"{path.name}.{os.getpid()}.part")     # draws no uuid: the frozen review's proofs stay as they were
    part.write_text(text, "utf-8")
    part.replace(path)


def waiting(root: Path, tasks: Sequence[str], folder: str = FOLDER) -> list[dict[str, Any]]:
    """The finished runs of these bugs with a patch no review has spoken on, one run for each distinct patch, in digest order."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "loop").glob("*-*.json")):
        row = json.loads(path.read_text("utf-8"))
        patch = root / str(row.get("run")) / "final.patch"
        if row.get("task") in tasks and row.get("status") == "finished" and patch.is_file() and patch.stat().st_size \
                and said_of(root, row, folder) is None:
            found.setdefault(c.sha256_file(patch), row)
    return [found[sha] for sha in sorted(found)]


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6, help="the G6 commit the runs' images were built from")
    ap.add_argument("--tasks", nargs="*", default=list(TRAINING), help="the bugs to review; a bug being judged only after its runs end")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many reviews")
    ap.add_argument("--reproduce", action="store_true",
                    help=f"replay 1: a reproduction first, then the frozen review's text, saved apart in {REPRODUCE}")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    ctx = we.Context(archive=c.Archive(root), docker=we.Docker(), key_reader=lambda: "", issue_reader=we.read_issue,
                     g6=we.g6_info(args.g6), variant=we.ROUND3, arm="L9")
    issues: dict[str, tuple[str, str]] = {}
    folder = REPRODUCE if args.reproduce else FOLDER
    rows = waiting(root, args.tasks, folder)
    missed, stopped, predicted = 0, "", set()
    if args.reproduce:
        import replay_compare     # here, as it imports this module

        predicted = replay_compare.predicted()
    for row in rows[:args.limit or None]:
        task = we.task_by_id(row["task"])
        if task.id not in issues:
            title, text = ctx.issue_reader(task)
            issues[task.id] = (title, text + we.read_thread(task))
        if not args.reproduce:
            got = review(ctx, task, root / row["run"] / "final.patch", *issues[task.id])
            keep(root, row, got)
            print(json.dumps({"task": task.id, "said": got["said"], "seconds": got["seconds"], "error": got["error"]}), flush=True)
            continue
        if row["task"] in predicted:          # the stop is the prediction's: stage 1 and 2 together, not training
            if stopped:
                continue
            stopped = replay_compare.futile(root, predicted, replay_compare.REPRODUCE)    # before each review: a resumed run too
            if stopped:
                print(json.dumps({"stopped": stopped}), flush=True)
                continue
        got = reproduce_review(ctx, task, root / row["run"] / "final.patch", *issues[task.id], earlier=saved(root, row, folder))
        keep(root, row, got, folder)
        print(json.dumps({"task": task.id, "step_1": got["reproduce"]["said"], "said": got["said"], "seconds": got["seconds"],
                          "error": got["error"]}), flush=True)
        missed = missed + 1 if no_review(got) or no_review(got["reproduce"]) else 0
        if missed == PAUSE_AFTER:
            print(json.dumps({"paused": f"{missed} reviews in a row had no review from the reviewer, as at a Codex limit; "
                                        "run again to resume where it stopped"}), flush=True)
            break


if __name__ == "__main__":
    main()
