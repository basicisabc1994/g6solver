# SPDX-License-Identifier: Apache-2.0
"""Replay 2 (PLAN.md, 2026-11-03, afternoon): the product's reviewer on the patches the final review read, to see whether the product
carries the measured reviewer's behaviour. It changes no run, no verdict and no rule.

The product's reviewer (``g6.harness.completion_loop`` on claude/next) sends the review loop's prompt, byte for byte, with no tools:
in place of the folder the measured reviewer read with its tools, it is given one text, the issue, the patch, and the parent's text
of each file the patch touches, cut at ``MAX_PACK_CHARS``, and a patch whose issue and patch do not fit is given to no one
(``TOO_LONG``). That text is built here by the product's own pieces, ``_head``, ``_pack`` and ``_read_whole``, copied from claude/next
byte for byte, and a test holds each to claude/next's text and the pack to what claude/next builds from the same patch. Where the
product reads the base commit with git, they read the parent tree from the agent's image as the final review gets it
(``read_at``), and the files the patch touches are the ones git would list for it (``touched``).

The reviewer is Codex as the product's ``critic_host`` calls it, ``host_models.CodexCaller``: ``codex exec`` in a new, empty folder,
its shell turned off, with the prompt as its instructions and the pack as the request (``codex``). It is told nothing of the arm or
the verdict, and no maintainers' test or fix reaches it. Its reply is read with the final review's parser, and nothing is run: what
it says is its verdict. Each review is saved under the patch's digest in ``FOLDER``, in the final review's order. A review that
failed, at a Codex limit or otherwise, is saved as failed with what it said, is no review, and is made again on the next run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import final_review as fr
import run as we
import wecommon as c

FOLDER = "final_review_product"
#: claude/next's ``completion_loop`` values, which a test holds to it: the pack's limit, the reviewer's time, and what a patch past
#: the limit is recorded as.
MAX_PACK_CHARS, CRITIC_SECONDS = 120_000, 1500
TOO_LONG = (f"the task and the patch are past the {MAX_PACK_CHARS:,} characters a review can read whole, so no reviewer was "
            "asked")
#: The product's prompt, its harness's ``critic``: the final review's text, as a test holds claude/next's data to it.
CRITIC = we.PATCH_CRITIC


@dataclass(frozen=True)
class Parent:
    """What ``_pack`` reads of the product's ``repo_tests.Change``: here the parent's files from the agent's image, by path, the
    parent commit, and the files the patch touches."""
    repo: Mapping[str, bytes]
    base_commit: str
    files: tuple[str, ...]


def read_at(repo: Mapping[str, bytes], commit: str, rel: str) -> str | None:
    """The product's ``repo_tests.read_at`` on the parent's files: ``rel``'s text at the parent, with the line endings
    ``Path.read_text`` gives, or None where the parent holds no such file. A link is no file here, as the final review's parent
    holds none, where git would give the link's target."""
    raw = repo.get(rel)
    return None if raw is None else raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


#: What ``_pack`` calls, by the name it calls it by.
repo_tests = SimpleNamespace(read_at=read_at)


# --- claude/next's own text, byte for byte ---------------------------------------------------------------------------------------

def _head(patch: str, task: str, proof: str) -> str:
    """The start of the pack, what a review must read whole: the issue, then the patch."""
    return f"issue.txt:\n{proof}\n\n{task}\n\ncandidate.patch:\n{patch}"


def _pack(change: repo_tests.Change, patch: str, task: str, proof: str) -> str:
    """What L9's critic reads in its folder, as text, since it has no tools: the issue, the patch, and of the base commit
    the files the patch touches. ``patch`` is the caller's read: reading it here would make a second patch."""
    parts = [_head(patch, task, proof)]
    for rel in change.files:
        text = repo_tests.read_at(change.repo, change.base_commit, rel)
        if text is not None:
            parts.append(f"parent/{rel}:\n{text}")
    return "\n\n".join(parts)[:MAX_PACK_CHARS]


def _read_whole(patch: str, task: str) -> bool:
    """Whether the pack holds the whole task and the whole patch: that its head fits. This is a length and never a search of
    the text, as a task may quote the patch. The pack is cut from its end, so the parent files go first, and a new file
    ``diff_text`` cuts is past this limit already. A reader of part of either has no clean review to give."""
    return len(_head(patch, task, f"review {'0' * 32}")) <= MAX_PACK_CHARS


# --- the replay ------------------------------------------------------------------------------------------------------------------

#: git's one-letter C escapes in a quoted path (``quote_c_style``); any other byte it escapes is three octal digits.
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquoted(name: str) -> str:
    """A path as git writes it in a patch: in double quotes with C escapes when it holds an unusual character, read back to
    the bytes git escaped, as UTF-8 with any other byte kept (``surrogateescape``), as the product's own listing of paths
    keeps it. A character git left as it is (as it leaves UTF-8 with ``core.quotePath`` off) is its own bytes."""
    if not (len(name) > 1 and name[0] == name[-1] == '"'):
        return name
    body, out, at = name[1:-1], bytearray(), 0
    while at < len(body):
        octal = body[at + 1:at + 4]
        if body[at] == "\\" and len(octal) == 3 and all(digit in "01234567" for digit in octal):
            out.append(int(octal, 8) & 0xFF)
            at += 4
        elif body[at] == "\\" and body[at + 1:at + 2] in _ESCAPES:
            out.append(_ESCAPES[body[at + 1]])
            at += 2
        else:
            out += body[at].encode("utf-8", "surrogateescape")
            at += 1
    return out.decode("utf-8", "surrogateescape")


def _applied(name: str) -> str:
    """A header's path as ``git apply`` reads it, as the study applies every saved patch: one leading part off (its ``-p1``),
    whatever prefix git was set to write, ``a/`` or another; "" where there is no part to take off."""
    return name.split("/", 1)[1] if "/" in name else ""


def _sides(rest: str) -> Iterator[tuple[str, str]]:
    """The two names of a ``diff --git`` header as git wrote them, at each place they may part: after a quoted first name, or
    at each space of an unquoted one, since a path may hold a space."""
    if rest.startswith('"'):
        end = 1
        while end < len(rest) and rest[end] != '"':
            end += 2 if rest[end] == "\\" else 1
        yield rest[:end + 1], rest[end + 2:]
        return
    for at, char in enumerate(rest):
        if char == " ":
            yield rest[:at], rest[at + 1:]


def touched(patch: str) -> tuple[str, ...]:
    """The files a patch touches, sorted, as the product's ``repo_tests.change`` lists them for the tree the patch makes: git's
    ``--no-renames`` names both paths of a move. Every name the patch gives a file, read as ``git apply`` reads it: its header
    line, where both sides name one path once their prefixes are off (``_applied``); its ``---`` and ``+++`` lines, each side's
    own, so that prefixes of different depths still name what git applies; and its rename and copy lines. Those are read in a
    file's header only, before its first hunk, whose lines all begin with a space, a plus or a minus, so a hunk's line is never
    a name. A line's CR before its LF is no part of it, as ``git apply`` reads a CRLF patch. A copy leaves its source as it
    was, so its source (``copy from``, and the ``---`` line naming it) is no file it touches: ``--no-renames`` lists the copy
    alone, where it lists both paths of a move."""
    found: set[str] = set()
    names: dict[str, str] = {}               # one file's names, by the line that gave each

    def close() -> None:
        if "copy from" in names:
            names.pop("copy from")
            names.pop("---", None)
        found.update(names.values())
        names.clear()

    header = False
    for line in patch.split("\n"):
        line = line[:-1] if line.endswith("\r") else line
        if line.startswith("diff --git "):
            close()
            header = True
            for old, new in _sides(line[len("diff --git "):]):
                path = _applied(_unquoted(old))
                if path and path == _applied(_unquoted(new)):
                    names["diff --git"] = path
                    break
        elif not header:
            continue
        elif line.startswith(("--- ", "+++ ")):
            name = _unquoted(line[4:].split("\t", 1)[0])       # git writes a tab after a name that holds a space
            if name != "/dev/null" and _applied(name):
                names[line[:3]] = _applied(name)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            verb, side, name = line.split(" ", 2)
            names[f"{verb} {side}"] = _unquoted(name)
        elif line.startswith(("@@", "GIT binary patch", "Binary files ")):
            header = False
    close()
    return tuple(sorted(found))


def parent_files(git: Callable[..., bytes], commit: str, files: Sequence[str]) -> dict[str, bytes]:
    """Each of ``files`` as the product's ``repo_tests.read_at`` reads it at ``commit``: named by ``git ls-tree`` there, then its
    blob by ``git show``, so an export-ignore attribute or a link hides nothing ``git archive`` would leave out. A file the parent
    does not hold is left out, as ``read_at`` gives None for it. ``git``: runs git in the parent's checkout and gives its output,
    and raises where git failed, as the product's own git call does, so a file git could not read is never a file left out."""
    found = {}
    for rel in files:
        if git("ls-tree", "-r", "-z", "--name-only", commit, "--", rel).strip(b"\0"):
            found[rel] = git("show", f"{commit}:{rel}")
    return found


def pack(parent: Mapping[str, bytes], patch: str, task: str, proof: str) -> str:
    """The product's pack for ``patch``: ``_pack`` on the parent's files (``parent_files``)."""
    files = touched(patch)
    return _pack(Parent({rel: raw for rel, raw in parent.items() if rel in files}, "", files), patch, task, proof)


def codex(system: str, user: str) -> str:
    """The product's reviewer, as its ``critic_host`` builds it: Codex on the owner's sign-in through ``host_models.CodexCaller``,
    which starts it in an empty folder with its shell off, and gives it ``system`` and then ``user`` as one prompt."""
    from g6.core import host_models

    return host_models.CodexCaller(timeout=CRITIC_SECONDS).complete(system, user)


#: Why a patch that touches a file whose name is not UTF-8 has no review here: the product names such a file to git by its
#: bytes, but this replay names it through ``docker exec`` from Windows, which cannot carry those bytes, so its parent text
#: would be left out without a word. It fails before anything starts, and is counted nowhere.
UNNAMED = ("the patch touches a file whose name is not UTF-8, which this replay cannot name to git in the container, so no "
           "reviewer was asked")


def _named(rel: str) -> bool:
    """Whether a path can be named to git through ``docker exec`` as it stands: its bytes are UTF-8."""
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def said(found: Mapping[str, Any]) -> str:
    """What the product's review said: its verdict, "defect" or "none", as it runs no test; "too long" where the product asks no
    one; and "failed" where there was no review, which is no verdict at all."""
    if found.get("error") == TOO_LONG:
        return "too long"
    return "failed" if found.get("error") or found.get("verdict") not in ("defect", "none") else found["verdict"]


def review(ctx: we.Context, task: we.Task, patch: Path, title: str, text: str, *,
           reviewer: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    """One review of one saved patch by the product's reviewer: the parent from the agent's image, in its own container with no
    network and never the oracle's, then the product's pack and prompt to ``reviewer`` (``codex``). The issue is what the final
    review's ``issue.txt`` holds. A pack whose head does not fit starts nothing and asks no one, as the product asks no one."""
    started = time.monotonic()
    found: dict[str, Any] = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    raw = patch.read_bytes()
    diff, issue = raw.decode("utf-8", "replace"), f"{title}\n\n{text}"
    files = touched(raw.decode("utf-8", "surrogateescape"))    # a name keeps its bytes, as the product's listing keeps them
    size, asking = 0, False
    if not _read_whole(diff, issue):
        found["error"] = TOO_LONG
        return {**found, "said": said(found), "pack_chars": size, "cut": False, "seconds": round(time.monotonic() - started, 1)}
    if any(not _named(rel) for rel in files):
        found["error"] = UNNAMED
        return {**found, "said": said(found), "pack_chars": size, "cut": False, "seconds": round(time.monotonic() - started, 1)}
    name = f"g6we-review-{uuid.uuid4().hex[:10]}"
    image, _ = we.image_names(task, str(ctx.g6["commit"]), ctx.variant)
    try:
        try:
            we.checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.SECURITY, "-u",
                                  we.USER, "-e", f"HOME={we.HOME}", "-w", task.repo, image, "tail", "-f", "/dev/null", timeout=600),
                       "review container")
            def git(*args: str) -> bytes:
                done = we.completed(ctx.docker("exec", "-u", we.USER, "-w", task.repo, name, "git", *args, timeout=300),
                                    f"git {args[0]} at the parent", ok=we.EXITS)       # its exit read next
                if done.returncode != 0:
                    raise RuntimeError(f"git {args[0]} at the parent failed ({done.returncode}): {fr.ended(we.err(done))[-200:]}")
                return done.stdout

            parent = parent_files(git, task.parent, files)
        finally:
            ctx.docker("rm", "-f", name, timeout=300)
        proof = f"review {uuid.uuid4().hex}"
        sent = pack(parent, diff, issue, proof)
        size, asking = len(sent), True
        reply = (reviewer or codex)(CRITIC, sent)
        asking = False
        found = fr.answered(we.critic_report(reply, proof=proof), reply)
    except (RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:   # an LLMError is a RuntimeError
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
        if asking:
            found["failed_at"] = "reviewer"
    return {**found, "said": said(found), "pack_chars": size, "cut": size == MAX_PACK_CHARS,
            "seconds": round(time.monotonic() - started, 1)}


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6, help="the G6 commit the runs' images were built from")
    ap.add_argument("--tasks", nargs="*", default=list(fr.TRAINING), help="the bugs to review; a bug being judged only after its runs end")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many reviews")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    ctx = we.Context(archive=c.Archive(root), docker=we.Docker(), key_reader=lambda: "", issue_reader=we.read_issue,
                     g6=we.g6_info(args.g6), variant=we.ROUND3, arm="L9")
    issues: dict[str, tuple[str, str]] = {}
    missed, stopped = 0, ""
    import replay_compare     # here, as it imports this module

    predicted = replay_compare.predicted()
    for row in fr.waiting(root, args.tasks, FOLDER)[:args.limit or None]:
        if row["task"] in predicted:          # the stop is the prediction's: stage 1 and 2 together, not training
            if stopped:
                continue
            stopped = replay_compare.futile(root, predicted, replay_compare.PRODUCT)    # before each review: a resumed run too
            if stopped:
                print(json.dumps({"stopped": stopped}), flush=True)
                continue
        task = we.task_by_id(row["task"])
        if task.id not in issues:
            title, text = ctx.issue_reader(task)
            issues[task.id] = (title, text + we.read_thread(task))
        got = review(ctx, task, root / row["run"] / "final.patch", *issues[task.id], reviewer=codex)
        fr.keep(root, row, got, FOLDER)
        print(json.dumps({"task": task.id, "said": got["said"], "seconds": got["seconds"], "error": got["error"]}), flush=True)
        missed = missed + 1 if fr.no_review(got) else 0
        if missed == fr.PAUSE_AFTER:
            print(json.dumps({"paused": f"{missed} reviews in a row had no review from the reviewer, as at a Codex limit; "
                                        "run again to resume where it stopped"}), flush=True)
            break


if __name__ == "__main__":
    main()
