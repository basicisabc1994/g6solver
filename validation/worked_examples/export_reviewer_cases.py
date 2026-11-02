"""The reviewer domain's cases (PLAN.md, 2026-11-02 evening): each recorded patch of a bug stage 1 scored, with its label and what K1's reviewer reads.

A case holds the issue as the engineer saw it, the patch, and the code round the change as it stood at the parent, read from the agent's own image. The
label is passed or failed from the independent check, and on a bug with an amendment only the final patch has one, from the amended tests. No maintainers'
test or fix is opened, and stage 2's bugs are refused by name. Three bugs chosen by a seeded hash go behind the seal. It prints counts and no case.
Usage: export_reviewer_cases.py"""
import collections
import hashlib
import io
import json
import re
import subprocess
import tarfile
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


def changed(diff: str) -> dict[str, list[tuple[int, int]]]:
    """The source files a diff touches, each with the parent's line ranges it changed. A file's sections are found by its header line."""
    found: dict[str, list[tuple[int, int]]] = {}
    for section in re.split(r"^diff --git ", diff, flags=re.M)[1:]:
        named = re.search(r"^\+\+\+ b/(.+)$", section, re.M)
        if not named or not is_source(named[1].strip()):
            continue
        spans = [(int(m[1]), int(m[1]) + max(int(m[2] or 1), 1) - 1) for m in _HUNK.finditer(section)]
        found[named[1].strip()] = spans
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


def distinct(rows: Iterable[tuple[str, Path, Any]]) -> dict[tuple[str, str], tuple[Path, bool]]:
    """One case per distinct patch of a bug. Two labels on the same bytes keep neither: they say the check was unsteady, not what the patch is."""
    found: dict[tuple[str, str], tuple[Path, bool]] = {}
    clashed = set()
    for task, patch, ok in rows:
        if not isinstance(ok, bool) or not patch.is_file() or not patch.stat().st_size:
            continue
        key = (task, hashlib.sha256(patch.read_bytes()).hexdigest()[:16])
        if key in found and found[key][1] != ok:
            clashed.add(key)
        found.setdefault(key, (patch, ok))
    return {key: value for key, value in found.items() if key not in clashed}


def row(task: str, sha: str, text: str, ok: bool) -> dict[str, str]:
    return {"id": f"{task}:{sha}", "bug": task, "input": text, "label": "right" if ok else "wrong"}


def _checks(root: Path, task: str) -> dict[str, list[dict[str, Any]]]:
    """Run path to the newest independent check of it, with a row for each round."""
    found = {}
    for line in (root / "runs.jsonl").read_text("utf-8").splitlines():
        if f'"check/{task}/' not in line or '"checked_run"' not in line:
            continue
        rec = json.loads(line)
        got = json.loads((root / rec["run"] / "run.json").read_text("utf-8")) if (root / rec["run"] / "run.json").is_file() else {}
        if "checks" in got:
            found[str(rec["checked_run"]).replace("\\", "/")] = got["checks"]
    return found


def labelled(root: Path, task: str) -> list[tuple[str, Path, Any]]:
    """Each recorded patch of ``task`` with its verdict. Counts of tests stay on this side: only passed or failed leaves here."""
    checks, rows = _checks(root, task), []
    for path in sorted((root / "loop").glob(f"*-{task}-*.json")):
        rec = json.loads(path.read_text("utf-8"))
        if rec.get("status") != "finished":
            continue
        run = str(rec.get("run", "")).replace("\\", "/")
        folder = root / run
        if task in AMENDED:
            again = root / "loop" / "amended" / path.name
            if again.is_file():
                rows.append((task, folder / "final.patch", json.loads(again.read_text("utf-8")).get("amended_passes")))
            continue
        by = {check.get("patch"): bool(check.get("passes")) for check in checks.get(run, [])}
        rows += [(task, folder / "rounds" / f"{name}.patch", ok) for name, ok in by.items() if name != "final"]
        rows.append((task, folder / "final.patch", by.get("final", rec.get("passes"))))
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
    rec = json.loads((root / "loop" / f"K1-{task}-1.json").read_text("utf-8"))
    return issue_of((root / str(rec["run"]).replace("\\", "/") / "prompt.txt").read_text("utf-8", errors="replace"))


def main() -> None:
    root = c.archive_root() / "round3"
    out = root / "reviewer_domain"
    (out / "sealed").mkdir(parents=True, exist_ok=True)
    behind, rows, count = sealed_bugs(BUGS), {"dev": [], "sealed": []}, collections.Counter()
    for task in usable(BUGS):
        patches = distinct(labelled(root, task))
        diffs = {key: path.read_text("utf-8", errors="replace") for key, (path, _) in patches.items()}
        files = parent_files(task, {p for diff in diffs.values() for p in changed(diff)}, out / "parent")
        issue, side = issue_for(root, task), "sealed" if task in behind else "dev"
        for key, (_, ok) in patches.items():
            rows[side].append(row(task, key[1], case_text(issue, diffs[key], files), ok))
            count[side, task, "right" if ok else "wrong"] += 1
    for side, name in (("dev", out / "cases.jsonl"), ("sealed", out / "sealed" / "cases.jsonl")):
        name.write_text("".join(json.dumps(item) + "\n" for item in rows[side]), "utf-8")
        sizes = sorted(len(item["input"]) for item in rows[side])
        print(side, "cases", len(rows[side]), "right", sum(i["label"] == "right" for i in rows[side]), "bugs", len({i["bug"] for i in rows[side]}),
              "median chars", sizes[len(sizes) // 2] if sizes else 0, "largest", sizes[-1] if sizes else 0)
    print({f"{task}:{label}:{side}": n for (side, task, label), n in sorted(count.items())})


if __name__ == "__main__":
    main()
