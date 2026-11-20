"""Mine ranked PyPI projects with read-only API requests and body-free checkpoints.

Run with --development-cohort, --selection-cohort, --output and --checkpoint paths.
Output and checkpoint must be outside the repository. Resume uses the saved ranking,
cohort digests and elapsed wall time, including time between invocations; increase --wall-seconds to extend a
time-exhausted run. A repository is checkpointed only after its PR scan completes.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from validation.worked_examples import selection

from ._mine_io import (atomic_json, cohort_ids, digest, entry_id, external_path, in_cohort, local_path,
                       read_json, repository_id)

LIMIT = 150
WALL_SECONDS = 2 * 24 * 60 * 60
PR_FIELDS = "number,mergedAt,baseRefName,mergeCommit,changedFiles"


class _Deadline(Exception):
    pass


#: GitHub's own site paths: a link under one of them names no repository (e.g. a funding link to /sponsors/<user>).
_SITE_PATHS = frozenset({"sponsors", "orgs", "apps", "marketplace", "topics", "features", "settings", "collections",
                         "users", "enterprise", "about", "pricing", "login", "site"})
#: A read failure for one repository is that repository's drop code; this many in a row stop the scan.
READ_FAILURES_IN_A_ROW = 5


class ReadFailed(ValueError):
    """A GitHub read that did not succeed (not found, refused, network). Other refusals still stop the scan."""


def repository_url(data: dict) -> str | None:
    info = data.get("info") or {}
    urls = list((info.get("project_urls") or {}).values()) + [info.get("home_page")]
    found = set()
    for url in urls:
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
                or parsed.query or parsed.fragment):
            continue
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if path.split("/")[0].lower() in _SITE_PATHS:
            continue
        try:
            found.add(repository_id(path))
        except ValueError:
            continue
    return next(iter(found)) if len(found) == 1 else None


def _repository(name, gh, development, selection_cohort):
    meta = json.loads(gh(["api", f"repos/{name}"]))
    canonical = repository_id(meta.get("full_name", name))
    names = {name, canonical, canonical.split("/")[1]}
    if meta.get("private") is not False or meta.get("visibility", "public") != "public":
        return canonical, None, ["PUBLIC"]
    rules = selection.project_rules(canonical, meta, [])
    if not rules["P2"]:
        return canonical, None, ["P2"]
    if not rules["P4"] or names & {r.lower() for r in selection.SWE_BENCH | selection.MULTIMODAL}:
        return canonical, None, ["P4"]
    if in_cohort(canonical, development, name):
        return canonical, None, ["DEVELOPMENT"]
    if in_cohort(canonical, selection_cohort, name):
        return canonical, None, ["SELECTION"]
    languages = json.loads(gh(["api", f"repos/{canonical}/languages"]))
    if not languages.get("Python") or any(n != "Python" and v for n, v in languages.items()):
        return canonical, None, ["P3"]
    branch = meta.get("default_branch")
    if not isinstance(branch, str) or not branch:
        return canonical, None, ["TREE"]
    tree = json.loads(gh(["api", f"repos/{canonical}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1"]))
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        return canonical, None, ["TREE"]
    if any(row["path"].lower().endswith(selection.OTHER_SOURCE) for row in tree["tree"]):
        return canonical, None, ["P3"]
    return canonical, branch, []


def _files(name, number, expected, gh):
    if type(expected) is not int or not 0 < expected < 3000:
        raise ValueError("pull request file count refused")
    files = []
    for page in range(1, 31):
        rows = json.loads(gh(["api", f"repos/{name}/pulls/{number}/files?per_page=100&page={page}"]))
        if not isinstance(rows, list):
            raise ValueError("pull request file list refused")
        files.extend(row["filename"] for row in rows)
        if len(rows) < 100:
            break
    if len(files) != expected or len(set(files)) != len(files):
        raise ValueError("pull request file list incomplete")
    return files


def _commit(name, sha, gh):
    endpoint = f"repos/{name}/commits/{sha}"
    commit = json.loads(gh(["api", endpoint]))
    # The one-parent T4 rule needs the whole commit file list, not its first page.
    if len(commit.get("parents") or []) == 1:
        files = []
        for page in range(1, 31):
            part = json.loads(gh(["api", f"{endpoint}?per_page=100&page={page}"]))
            rows = part.get("files")
            if not isinstance(rows, list):
                raise ValueError("commit file list missing")
            files.extend(rows)
            if len(rows) < 100:
                break
        else:
            raise ValueError("commit file list incomplete")
        commit["files"] = files
    return commit


def _pull_requests(name, branch, gh):
    prs = json.loads(gh(["pr", "list", "-R", name, "--state", "merged", "--limit", str(selection.PR_LIMIT),
                         "--search", f"merged:{selection.WINDOW[0]}..{selection.WINDOW[1]}", "--json", PR_FIELDS]))
    if not isinstance(prs, list) or len(prs) >= selection.PR_LIMIT:
        raise ValueError("pull request search incomplete")
    entries, decisions = [], []
    seen = set()
    for pr in sorted(prs, key=lambda p: (p.get("mergedAt") or "", p["number"]), reverse=True):
        number = pr["number"]
        if type(number) is not int or number < 1 or number in seen:
            raise ValueError("pull request number refused")
        seen.add(number)
        code = "T1"
        ok, _ = selection.t1(pr.get("mergedAt") or "", pr.get("baseRefName") or "", branch)
        if ok:
            files = _files(name, number, pr["changedFiles"], gh)
            code = "T3"
            ok, _ = selection.t3(files)
        if ok:
            sha = (pr.get("mergeCommit") or {}).get("oid")
            # Validate the SHA before making it part of an API endpoint.
            entry_id({"repository": name, "pr": number, "merge_commit": sha, "parent": "0" * 40})
            parent, _ = selection.t4(_commit(name, sha, gh), files)
            code, ok = "T4", parent is not None
        if ok:
            entry = {"repository": name, "pr": number, "merge_commit": sha, "parent": parent,
                     "rules": ["T1", "T3", "T4"]}
            entry_id(entry)
            entries.append(entry)
        decisions.append({"repository": name, "pr": number, "codes": [] if ok else [code]})
    return entries, decisions


def mine(*, output: Path, checkpoint: Path, development: Path, selection_cohort: Path,
         wall_seconds: float = WALL_SECONDS, gh=None, ranking=None, project=None,
         clock=time.monotonic, wall_clock=time.time, repository_root: Path | None = None) -> dict:
    output, checkpoint = (external_path(p, repository_root) for p in (output, checkpoint))
    development, selection_cohort = local_path(development), local_path(selection_cohort)
    if len({output, checkpoint, development, selection_cohort}) != 4:
        raise ValueError("input and output paths must differ")
    if not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError("expected positive wall time")
    dev, selected = cohort_ids(read_json(development)), cohort_ids(read_json(selection_cohort))
    binding = digest({"development": sorted(dev), "selection": sorted(selected), "window": selection.WINDOW,
                      "limit": LIMIT, "version": 1})
    started = clock()
    state = read_json(checkpoint) if checkpoint.exists() else None
    if state is not None and (state["binding"] != binding or state["schema"] != 1):
        raise ValueError("checkpoint inputs differ")
    elapsed = state["elapsed_seconds"] if state else 0.0

    def spent():
        # Monotonic elapsed prevents a backward clock adjustment from adding time.
        return max(elapsed + clock() - started, wall_clock() - state["started_at"])

    def remaining():
        left = wall_seconds - spent()
        if left <= 0:
            raise _Deadline
        return left

    def fetch_json(url):
        with urllib.request.urlopen(url, timeout=min(120, remaining())) as reply:  # noqa: S310
            return json.loads(reply.read().decode("utf-8"))

    def read_gh(args):
        left = remaining()
        if gh is not None:
            result = gh(args)
        else:
            done = subprocess.run(["gh", *args], capture_output=True, timeout=min(600, left))
            if done.returncode:
                raise ReadFailed("GitHub read failed")
            result = done.stdout.decode("utf-8")
        remaining()
        return result

    def save():
        state["elapsed_seconds"] = spent()
        atomic_json(checkpoint, state)
        # The public pool has no package metadata or checkpoint-only ranked input.
        atomic_json(output, {key: value for key, value in state.items() if key not in {"ranking", "binding"}})

    if state is None:
        state = {"schema": 1, "binding": binding, "ranking": None, "started_at": wall_clock(),
                 "window": list(selection.WINDOW),
                 "next_rank": 0, "elapsed_seconds": 0, "repositories": [], "entries": [],
                 "decisions": [], "pr_decisions": [], "complete": False}
        save()
    try:
        if state["ranking"] is None:
            top = ranking() if ranking else [r["project"] for r in fetch_json(selection.TOP_PYPI)["rows"]]
            if not isinstance(top, list) or not all(isinstance(p, str) and p for p in top):
                raise ValueError("ranking refused")
            state["ranking"] = top
            save()
        while state["next_rank"] < len(state["ranking"]) and len(state["repositories"]) < LIMIT:
            remaining()
            index = state["next_rank"]
            package = state["ranking"][index]
            data = project(package) if project else fetch_json(
                f"https://pypi.org/pypi/{urllib.parse.quote(package, safe='')}/json")
            remaining()
            name = repository_url(data)
            codes, branch = ["P1_REPOSITORY"], None
            entries, decisions = [], []
            seen = {r.get("repository") for r in state["decisions"]}
            if name in seen:
                codes = ["DUPLICATE"]
            elif name:
                try:
                    canonical, branch, codes = _repository(name, read_gh, dev, selected)
                    if canonical in seen and not codes:
                        codes, branch = ["DUPLICATE"], None
                    name = canonical
                    if branch:
                        entries, decisions = _pull_requests(name, branch, read_gh)
                except ReadFailed:
                    # One unreadable repository is not kept (not shown public); a run of them is a broken reader.
                    recent = [d["codes"] for d in state["decisions"][-(READ_FAILURES_IN_A_ROW - 1):]]
                    if len(recent) == READ_FAILURES_IN_A_ROW - 1 and all(c == ["READ"] for c in recent):
                        raise
                    codes, branch, entries, decisions = ["READ"], None, [], []
            remaining()
            # Commit a complete repository scan as one checkpoint transaction.
            updated = copy.deepcopy(state)
            updated["decisions"].append({"rank": index + 1, "repository": name, "codes": codes})
            if branch:
                updated["repositories"].append({"rank": index + 1, "repository": name,
                                                 "rules": ["PUBLIC", "P2", "P3", "P4", "COHORTS"]})
                updated["entries"].extend(entries)
                updated["pr_decisions"].extend(decisions)
            updated["next_rank"] = index + 1
            state = updated
            save()
        state["complete"] = True
    except (_Deadline, subprocess.TimeoutExpired, TimeoutError):
        state["complete"] = False
    finally:
        save()
    return {key: value for key, value in state.items() if key not in {"ranking", "binding"}}


def main(argv=None, *, repository_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-cohort", type=Path, required=True)
    parser.add_argument("--selection-cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=float, default=WALL_SECONDS)
    args = parser.parse_args(argv)
    try:
        mine(output=args.output, checkpoint=args.checkpoint, development=args.development_cohort,
             selection_cohort=args.selection_cohort, wall_seconds=args.wall_seconds, repository_root=repository_root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        parser.exit(2, "pool input or read refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
