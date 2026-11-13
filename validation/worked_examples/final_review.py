# SPDX-License-Identifier: Apache-2.0
"""The final review (PLAN.md, 2026-10-31, late): another model family reads a run's finished patch after the run has ended, and its test, if
it wrote one, runs on the parent and on the patch. It changes no run, no verdict and no rule. It asks the owner's question of a second
reader: when the review reproduces a defect, is the patch wrong, and when it finds nothing, is the patch right?

The reviewer reads the issue with its discussion, the parent from the agent's image, which holds no maintainers' test or fix, and the
saved final patch. It is told nothing of the arm or the verdict, and the order of review is the patches' digests, so a short run is not
weighted to any arm. Saved reviews are cases a harness change can be replayed on. A review uses the owner's Codex sign-in, not the
budget, and is reported apart.
"""
from __future__ import annotations

import argparse
import io
import json
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


def _kept(root: Path, row: Mapping[str, Any]) -> Path | None:
    """Where the review of this run's patch is saved, by the patch's digest, so that identical patches share one."""
    patch = root / str(row.get("run")) / "final.patch"
    return root / "loop" / FOLDER / f"{c.sha256_file(patch)}.json" if patch.is_file() else None


def said_of(root: Path, row: Mapping[str, Any]) -> str | None:
    """What the review said of this run's patch as it stands; None if it has no review, or the review failed."""
    path = _kept(root, row)
    got = json.loads(path.read_text("utf-8")).get("said") if path and path.is_file() else None
    return None if got == "failed" else got


def keep(root: Path, row: Mapping[str, Any], found: Mapping[str, Any]) -> None:
    """Save a review under the patch's digest."""
    path = _kept(root, row)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"task": row["task"], "first_seen_in": row.get("run"), **found}, indent=1), "utf-8")


def waiting(root: Path, tasks: Sequence[str]) -> list[dict[str, Any]]:
    """The finished runs of these bugs with a patch no review has spoken on, one run for each distinct patch, in digest order."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "loop").glob("*-*.json")):
        row = json.loads(path.read_text("utf-8"))
        patch = root / str(row.get("run")) / "final.patch"
        if row.get("task") in tasks and row.get("status") == "finished" and patch.is_file() and patch.stat().st_size \
                and said_of(root, row) is None:
            found.setdefault(c.sha256_file(patch), row)
    return [found[sha] for sha in sorted(found)]


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6, help="the G6 commit the runs' images were built from")
    ap.add_argument("--tasks", nargs="*", default=list(TRAINING), help="the bugs to review; a bug being judged only after its runs end")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many reviews")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    ctx = we.Context(archive=c.Archive(root), docker=we.Docker(), key_reader=lambda: "", issue_reader=we.read_issue,
                     g6=we.g6_info(args.g6), variant=we.ROUND3, arm="L9")
    issues: dict[str, tuple[str, str]] = {}
    rows = waiting(root, args.tasks)
    for row in rows[:args.limit or None]:
        task = we.task_by_id(row["task"])
        if task.id not in issues:
            title, text = ctx.issue_reader(task)
            issues[task.id] = (title, text + we.read_thread(task))
        got = review(ctx, task, root / row["run"] / "final.patch", *issues[task.id])
        keep(root, row, got)
        print(json.dumps({"task": task.id, "said": got["said"], "seconds": got["seconds"], "error": got["error"]}), flush=True)


if __name__ == "__main__":
    main()
