"""The reviewer domain's cases (PLAN.md, 2026-11-02 evening): each recorded patch of a bug stage 1 scored, with its label and what K1's reviewer reads.

A case holds the issue as the engineer saw it, the patch, and the code round the change as it stood at the parent, read from the agent's own image. The
label is passed or failed from the independent check, and on a bug with an amendment only the final patch has one, from the amended tests. No maintainers'
test or fix is opened, and stage 2's bugs are refused by name. Three bugs chosen by a seeded hash go behind the seal. It prints counts and no case.
Usage: export_reviewer_cases.py"""
import collections
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import run as we
import wecommon as c

TRAINING = ("black-5634", "pydantic-14052", "click-3678", "attrs-1593", "fastapi-15077", "pydantic-14065", "starlette-3720")
FRESH = ("pydantic-13243", "black-5144", "black-5148", "click-3434", "click-3404", "pydantic-13133", "starlette-3189", "attrs-1509",
         "starlette-3166", "fastapi-14616", "fastapi-13920")
HELD = ("black-5521", "click-3769", "starlette-3718")
BUGS = (*TRAINING, *FRESH, *HELD)
AMENDED = ("click-3678", "black-5144", "attrs-1509", "starlette-3166", "pydantic-13133")
SEALED, SEAL_SEED = 3, "stage3-seal"
MAX_ISSUE, MAX_PATCH, MAX_CONTEXT, AROUND, WHOLE = 6000, 12000, 16000, 60, 8000
CUT = "\n[cut here to fit]"
ASK = ("You cannot run anything. End with one line and nothing after it: VERDICT: RIGHT if the candidate fully meets the issue, or VERDICT: WRONG if it "
       "does not.")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)


def usable(tasks: Iterable[str]) -> list[str]:
    """Only bugs already scored: stage 2 stays sealed until stage 3's runs have ended, whatever a caller names."""
    sealed = {t.id for t in we.load_tasks(we.TASKS_UNSEEN)}
    return [task for task in tasks if task in BUGS and task not in sealed]


def sealed_bugs(bugs: Sequence[str]) -> set[str]:
    return set(sorted(bugs, key=lambda bug: hashlib.sha256(f"{SEAL_SEED}:{bug}".encode()).hexdigest())[:SEALED])


def is_source(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.endswith((".py", ".pyi")) and "tests/" not in f"/{path}" and not name.startswith("test_") and name != "conftest.py"


def touched(part: str) -> list[str]:
    """The files one part of a patch touches, as git's own reader of its patch format names them (``git apply --numstat -z``, run on
    the part's bytes in an empty folder, which only reads them: no tree is changed), so a name git quotes or escapes is read as
    git reads it. Each file by its name after the patch (for a rename, the new one). None for a part git cannot read.

    The empty folder is git's whole world (review 4, finding 8): git looks for no repository above it (``GIT_CEILING_DIRECTORIES``,
    its parent), and none named by the environment (``GIT_DIR``, ``GIT_WORK_TREE``), so a work tree that holds the temporary folder
    can never make git read the part's paths relative to it. A name is git's bytes, held as they are (``surrogateescape``, never a
    replacement character), so two names git keeps apart stay apart (finding 5)."""
    with tempfile.TemporaryDirectory() as empty:
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
        env["GIT_CEILING_DIRECTORIES"] = str(Path(empty).resolve().parent)
        got = subprocess.run(["git", "apply", "--numstat", "-z"], input=part.encode("utf-8", errors="surrogateescape"), cwd=empty,
                             capture_output=True, timeout=60, env=env)
    if got.returncode != 0:
        return []
    fields, names = got.stdout.split(b"\0"), []
    while fields:
        field = fields.pop(0)
        if not field:
            continue
        _, _, name = field.split(b"\t", 2)
        if not name and len(fields) >= 2:                  # a rename: its old name, then its new one, each a field of its own
            _, name = fields.pop(0), fields.pop(0)
        names.append(name.decode("utf-8", errors="surrogateescape"))
    return names


def changed(diff: str) -> dict[str, list[tuple[int, int]]]:
    """The source files a diff touches, each with the parent's line ranges it changed. Each part begins at git's own ``diff --git``
    line, which no line inside a hunk can begin with (a hunk's lines begin with a space, "+" or "-"); which file a part is about is
    read by git (``touched``), never by a pattern of this module's own."""
    found: dict[str, list[tuple[int, int]]] = {}
    for section in re.split(r"^(?=diff --git )", diff, flags=re.M):
        names = touched(section) if section.startswith("diff --git ") else []
        if not names or not is_source(names[0]):
            continue
        spans = [(int(m[1]), int(m[1]) + max(int(m[2] or 1), 1) - 1) for m in _HUNK.finditer(section)]
        found[names[0]] = spans
    return found


def windows(spans: Sequence[tuple[int, int]], *, total: int, around: int = AROUND) -> list[tuple[int, int]]:
    """Line ranges round each change, inside the file, with those that touch joined."""
    merged: list[tuple[int, int]] = []
    for first, last in sorted(spans):
        low, high = max(1, first - around), min(total, last + around)
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def context(diff: str, files: Mapping[str, str], *, limit: int = MAX_CONTEXT) -> str:
    """The parent's code round each changed source file. A short file goes in whole. A file new in the patch has no parent to show."""
    parts = []
    for path, spans in changed(diff).items():
        text = files.get(path)
        if text is None:
            continue
        lines = text.splitlines()
        shown = [(1, len(lines))] if len(text) <= WHOLE else windows(spans, total=len(lines))
        for first, last in shown:
            parts.append(f"--- {path}, lines {first} to {last}\n" + "\n".join(lines[first - 1:last]))
    joined = "\n\n".join(parts)
    return joined if len(joined) <= limit else joined[:limit] + CUT


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + CUT


def case_text(issue: str, diff: str, files: Mapping[str, str]) -> str:
    code = context(diff, files)
    return "\n\n".join((f"The issue, as the engineer saw it:\n\n{_fit(issue, MAX_ISSUE)}", f"The candidate fix, as a diff on the parent:\n\n{_fit(diff, MAX_PATCH)}",
                        *([f"The code round the change, as it stood before the fix:\n\n{code}"] if code else []), ASK))


def issue_of(prompt: str) -> str:
    at = prompt.find("Issue #")
    return prompt[at:] if at >= 0 else prompt


def distinct(rows: Iterable[tuple[str, Path | bytes, Any]]) -> dict[tuple[str, str], tuple[bytes, bool]]:
    """One case per distinct patch of a bug, each with the bytes it was named by. Two labels on the same bytes keep neither: they say the check was
    unsteady, not what the patch is. A patch is a path, read here once, or the bytes ``labelled`` already checked, which are used as they are: the
    case's id, its label and its diff (``main``, ``diff_of``) all come from those bytes, and no path is read again."""
    found: dict[tuple[str, str], tuple[bytes, bool]] = {}
    clashed = set()
    for task, patch, ok in rows:
        if not isinstance(ok, bool):
            continue
        data = patch if isinstance(patch, bytes) else patch.read_bytes() if patch.is_file() else b""
        if not data:
            continue
        key = (task, hashlib.sha256(data).hexdigest()[:16])
        if key in found and found[key][1] != ok:
            clashed.add(key)
        found.setdefault(key, (data, ok))
    return {key: value for key, value in found.items() if key not in clashed}


def diff_of(data: bytes) -> str:
    """A case's patch as text, from the bytes its id was made from: UTF-8 with a byte that is not held as it is (``surrogateescape``,
    never a replacement character, so the file names ``changed`` reads from it are git's own, and two names git keeps apart stay
    apart all the way through: study review 1, finding 14), and its line ends made "\\n", as ``Path.read_text`` read a patch before."""
    return data.decode("utf-8", errors="surrogateescape").replace("\r\n", "\n").replace("\r", "\n")


def row(task: str, sha: str, text: str, ok: bool) -> dict[str, str]:
    return {"id": f"{task}:{sha}", "bug": task, "input": text, "label": "right" if ok else "wrong"}


def _checks(root: Path, task: str) -> dict[str, list[dict[str, Any]]]:
    """Each checked run's name, exactly as its check names it, with the newest independent check of it: a row for each round. The
    index is read only through ``Archive.rows``, so its torn tail is skipped as every reader of it skips it."""
    found = {}
    for rec in c.Archive(root).rows():
        name = str(rec.get("run") or "")
        if rec.get("event") != "started" or not name.startswith(f"check/{task}/") or "checked_run" not in rec:
            continue
        got = json.loads((root / name / "run.json").read_text("utf-8")) if (root / name / "run.json").is_file() else {}
        if "checks" in got:
            found[str(rec["checked_run"])] = got["checks"]
    return found


def labelled(root: Path, task: str) -> list[tuple[str, Path | bytes, Any]]:
    """Each recorded patch of ``task`` with its verdict. Counts of tests stay on this side: only passed or failed leaves here. A saved
    row names its run and holds the verdict judged; every fact of the run comes from ``wecommon.run_facts``: only a run the one
    admission rule admits (``run.scored_run``) and that finished gives cases, and its folder is opened by its exact name only. Arm
    K3 keeps its first patch, "round-1", in its baseline folder (``run.baseline_of``), not in ``rounds``; it is a case only while
    its bytes have the digest kept at its capture (``run.baseline_bytes``), and it is handed on as those bytes, never as its path,
    so the case is what was checked. Every row, the bug it is of and its verdicts come from one read by the saved rows' one reader
    (``wecommon.shelf``: its run's start's, never its file's name or what it says), so a row that is no run's own gives no case. A
    final patch's label is the run's verdict as that read gives it, by the one rule every entry point reads (``wecommon.judge``),
    never a check's newer word (study review 1, finding 4): an amended one only when it was saved for this run, under its ending, on
    the diff as it stands (review 4, finding 2). A patch before the final one is labelled by the newest check of the run, and one
    whose verdict there is not true or false has no label."""
    checks, rows, archive = _checks(root, task), [], c.Archive(root)
    for one in c.saved_rows(archive, we.amendment):
        facts = one.facts
        if facts.task != task or not one.admitted or (facts.ending or {}).get("status") != "finished":     # as the one read found it
            continue
        run, folder = facts.name, facts.folder
        if task in AMENDED:
            if one.amendment_sha256 is not None and one.amended_file is not None:      # a diff, and a verdict kept on one
                rows.append((task, folder / "final.patch", one.amended))
            continue
        by = {check.get("patch"): check.get("passes") for check in checks.get(run, [])}
        first = Path(we.FIRST_PATCH).stem if we.rule_led(facts.arm) else None
        for name, ok in by.items():
            if name == "final":
                continue
            if name != first:
                rows.append((task, folder / "rounds" / f"{name}.patch", ok))
            elif (checked := we.baseline_bytes(archive, run)) is not None:    # K3's baseline: the bytes checked by the kept digest
                rows.append((task, checked, ok))
        rows.append((task, folder / "final.patch", one.passes))
    return rows


def _image(task: str) -> str:
    """The agent's image of ``task``, which holds the repository at the parent and neither the tests nor the fix of the maintainers' check."""
    tags = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "g6we-task"], capture_output=True, text=True, timeout=60).stdout.split()
    return next(tag for tag in tags if tag.startswith(f"g6we-task:{task}-"))


def parent_files(task: str, paths: Iterable[str], cache: Path) -> dict[str, str]:
    """The parent's text of ``paths``, read once from the image and kept."""
    saved = cache / f"{task}.json"
    wanted = set(paths)
    held = json.loads(saved.read_text("utf-8")) if saved.is_file() else {}
    if wanted <= set(held) | set(held.get("", ())):
        return {k: v for k, v in held.items() if k}
    bug = we.task_by_id(task)
    got = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", _image(task), "-c", f"cd {bug.repo} && git archive --format=tar HEAD"],
                         capture_output=True, timeout=300, check=True)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(got.stdout)) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name in wanted:
                files[member.name] = tar.extractfile(member).read().decode("utf-8", errors="replace")
    cache.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({**files, "": sorted(wanted - set(files))}), "utf-8")
    return files


def issue_for(root: Path, task: str) -> str:
    one = c.saved_row(root, "K1", task, 1, we.amendment)       # the saved row of the run whose start names K1, this bug and repeat 1
    if one is None:
        raise SystemExit(f"no saved row of K1 {task} 1 is its run's own")
    return issue_of((one.facts.folder / "prompt.txt").read_text("utf-8", errors="replace"))


def main() -> None:
    root = c.archive_root() / "round3"
    out = root / "reviewer_domain"
    (out / "sealed").mkdir(parents=True, exist_ok=True)
    behind, rows, count = sealed_bugs(BUGS), {"dev": [], "sealed": []}, collections.Counter()
    for task in usable(BUGS):
        patches = distinct(labelled(root, task))
        diffs = {key: diff_of(data) for key, (data, _) in patches.items()}     # the bytes each case's id was made from
        files = parent_files(task, {p for diff in diffs.values() for p in changed(diff)}, out / "parent")
        issue, side = issue_for(root, task), "sealed" if task in behind else "dev"
        for key, (_, ok) in patches.items():
            rows[side].append(row(task, key[1], case_text(issue, diffs[key], files), ok))
            count[side, task, "right" if ok else "wrong"] += 1
    for side, name in (("dev", out / c.NDJSON["cases"]), ("sealed", out / "sealed" / c.NDJSON["cases"])):
        name.write_text("".join(json.dumps(item) + "\n" for item in rows[side]), "utf-8")
        sizes = sorted(len(item["input"]) for item in rows[side])
        print(side, "cases", len(rows[side]), "right", sum(i["label"] == "right" for i in rows[side]), "bugs", len({i["bug"] for i in rows[side]}),
              "median chars", sizes[len(sizes) // 2] if sizes else 0, "largest", sizes[-1] if sizes else 0)
    print({f"{task}:{label}:{side}": n for (side, task, label), n in sorted(count.items())})


if __name__ == "__main__":
    main()
