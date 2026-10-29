# SPDX-License-Identifier: Apache-2.0
"""Apply PLAN.md's selection rule, read-only: the candidate projects' rules P1, P2 and P4 from GitHub's API and the
PyPI download list, then each kept project's merged pull requests in the window, newest first, with the mechanical
parts of T1 to T4 decided here. The judgement T2 asks for (a bug with a clear expected behaviour) is a person's, made
on the issue texts this script saves; P3 is decided at each task's validation.

Writes ``candidates.json`` into the archive folder (outside the repository): per project its rules and, for each pull
request that passed the mechanical checks, the issue's title, labels and text. Nothing is written to any project:
only ``gh api`` and ``gh pr list`` reads.

    python -I validation/worked_examples/selection.py [--archive <folder>]

(Named ``selection`` so that it never stands in for the standard library's ``select`` module.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import wecommon as c  # noqa: E402

CANDIDATES = ("pydantic/pydantic", "psf/black", "pallets/click", "pallets/jinja", "encode/httpx", "encode/starlette",
              "fastapi/fastapi", "Textualize/rich", "networkx/networkx", "python-attrs/attrs", "pypa/packaging",
              "marshmallow-code/marshmallow", "pallets/flask", "psf/requests", "python-pillow/Pillow")
#: The package each candidate publishes on PyPI, for P1's download list.
PYPI_NAME = {"pydantic/pydantic": "pydantic", "psf/black": "black", "pallets/click": "click", "pallets/jinja": "jinja2",
             "encode/httpx": "httpx", "encode/starlette": "starlette", "fastapi/fastapi": "fastapi",
             "Textualize/rich": "rich", "networkx/networkx": "networkx", "python-attrs/attrs": "attrs",
             "pypa/packaging": "packaging", "marshmallow-code/marshmallow": "marshmallow", "pallets/flask": "flask",
             "psf/requests": "requests", "python-pillow/Pillow": "pillow"}
TOP_PYPI = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json"
STARS = 10_000
TOP_N = 100
PERMISSIVE = frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0"})
#: SWE-bench's 12 repositories (paper, Table 12); SWE-bench Verified is a subset of them.
SWE_BENCH = frozenset({"astropy/astropy", "django/django", "pallets/flask", "matplotlib/matplotlib", "pylint-dev/pylint",
                       "pytest-dev/pytest", "psf/requests", "scikit-learn/scikit-learn", "mwaskom/seaborn",
                       "sphinx-doc/sphinx", "sympy/sympy", "pydata/xarray"})
#: SWE-bench Multimodal's 17 repositories (arXiv 2410.03859), all JavaScript and TypeScript.
MULTIMODAL = frozenset({"GoogleChrome/lighthouse", "PrismJS/prism", "alibaba-fusion/next", "bpmn-io/bpmn-js",
                        "carbon-design-system/carbon", "eslint/eslint", "grommet/grommet", "highlightjs/highlight.js",
                        "openlayers/openlayers", "prettier/prettier", "quarto-dev/quarto-cli",
                        "scratchfoundation/scratch-gui", "Automattic/wp-calypso", "chartjs/Chart.js",
                        "diegomura/react-pdf", "markedjs/marked", "processing/p5.js"})
WINDOW = ("2026-06-01", "2026-10-29")
ISSUE_TEXT_CHARS = 6000


# --- the rules on one project ------------------------------------------------------------------------------------------
def project_rules(name: str, meta: dict[str, Any], top: Sequence[str]) -> dict[str, Any]:
    """P1, P2 and P4 for one candidate. ``meta`` is ``gh api repos/<name>``; ``top`` the PyPI names, most downloaded
    first. P3 is decided at the first task's validation."""
    stars = int(meta.get("stargazers_count") or 0)
    pypi = PYPI_NAME.get(name, "").lower()
    rank = next((i + 1 for i, p in enumerate(top) if p.lower() == pypi), None)
    p1 = stars >= STARS or (rank is not None and rank <= TOP_N)
    spdx = str((meta.get("license") or {}).get("spdx_id") or "")
    p2 = spdx in PERMISSIVE
    folded = {n.lower() for n in SWE_BENCH | MULTIMODAL}
    p4 = name.lower() not in folded and str(meta.get("full_name") or name).lower() not in folded
    why = []
    if not p1:
        why.append(f"P1: {stars} stars and PyPI rank {rank or 'not in the list'}")
    if not p2:
        why.append(f"P2: licence {spdx or 'unknown'} is not one P2 lists")
    if not p4:
        why.append("P4: a SWE-bench repository" if name.lower() in {n.lower() for n in SWE_BENCH}
                   else "P4: a SWE-bench Multimodal repository")
    return {"project": name, "full_name": str(meta.get("full_name") or name), "stars": stars, "pypi": pypi,
            "pypi_rank": rank, "licence": spdx,
            "default_branch": meta.get("default_branch"), "P1": p1, "P2": p2, "P4": p4,
            "kept": p1 and p2 and p4, "why": "; ".join(why) or "P1, P2 and P4 hold (P3 at validation)"}


# --- the mechanical task rules -----------------------------------------------------------------------------------------
OTHER_SOURCE = (".c", ".h", ".cc", ".cpp", ".hpp", ".pyx", ".pxd", ".pxi", ".rs", ".go", ".js", ".ts", ".java")


def is_test(path: str) -> bool:
    """T3's test file: a Python file under a ``test`` or ``tests`` folder, or named ``test_*.py``, ``*_test.py`` or
    ``conftest.py``."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if not name.endswith(".py"):
        return False
    return (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or any(p in ("test", "tests") for p in parts[:-1]))


def t3(files: Sequence[str]) -> tuple[bool, str]:
    """At least one Python source file that is not a test, at least one test file, and no source in another language."""
    other = [f for f in files if f.lower().endswith(OTHER_SOURCE)]
    if other:
        return False, f"T3: changes source in another language ({other[0]})"
    source = [f for f in files if f.endswith(".py") and not is_test(f)]
    tests = [f for f in files if is_test(f)]
    if not source:
        return False, "T3: no Python source file outside the tests"
    if not tests:
        return False, "T3: no test file added or changed"
    return True, ""


def t1(merged_at: str, base_ref: str, default_branch: str) -> tuple[bool, str]:
    day = (merged_at or "")[:10]
    if base_ref != default_branch:
        return False, f"T1: merged into {base_ref}, not {default_branch}"
    if not (WINDOW[0] <= day <= WINDOW[1]):
        return False, f"T1: merged {day}, outside {WINDOW[0]}..{WINDOW[1]}"
    return True, ""


def t4(commit: dict[str, Any], pr_files: Sequence[str]) -> tuple[str | None, str]:
    """The parent commit (``gh api repos/<name>/commits/<merge sha>``): the first parent of a two-parent merge commit;
    for a one-parent commit, its parent when the files it changes are the pull request's."""
    parents = [p.get("sha") for p in commit.get("parents") or []]
    if len(parents) == 2:
        return parents[0], ""
    if len(parents) != 1:
        return None, f"T4: the merge commit has {len(parents)} parents"
    changed = sorted(f.get("filename", "") for f in commit.get("files") or [])
    if changed != sorted(pr_files):
        return None, "T4: the merged commit's files are not the pull request's (a rebase of several commits)"
    return parents[0], ""


# --- reading GitHub (read-only) ---------------------------------------------------------------------------------------
Runner = Callable[[Sequence[str]], str]


def gh_runner(args: Sequence[str]) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {done.stderr.decode(errors='replace')[-300:]}")
    return done.stdout.decode("utf-8", errors="replace")


PR_FIELDS = "number,title,url,mergedAt,baseRefName,mergeCommit,closingIssuesReferences,files"


PR_LIMIT = 1000     # GitHub's search returns at most 1,000 results


def merged_prs(name: str, gh: Runner = gh_runner) -> list[dict[str, Any]]:
    """The pull requests merged in the window, newest first. A list as long as the limit may be cut short."""
    found = json.loads(gh(["pr", "list", "-R", name, "--state", "merged", "--limit", str(PR_LIMIT), "--search",
                           f"merged:{WINDOW[0]}..{WINDOW[1]}", "--json", PR_FIELDS]) or "[]")
    if len(found) >= PR_LIMIT:
        raise RuntimeError(f"{name}: {len(found)} pull requests, the search's limit; the list may be incomplete")
    return sorted(found, key=lambda pr: pr.get("mergedAt") or "", reverse=True)


def issue(name: str, number: int, gh: Runner = gh_runner) -> dict[str, Any]:
    got = json.loads(gh(["api", f"repos/{name}/issues/{number}"]))
    return {"number": got.get("number"), "title": got.get("title"), "url": got.get("html_url"),
            "labels": [lab.get("name") for lab in got.get("labels") or []], "state": got.get("state"),
            "is_pull_request": "pull_request" in got, "text": (got.get("body") or "")[:ISSUE_TEXT_CHARS],
            "text_chars": len(got.get("body") or "")}


def screen(name: str, rules: dict[str, Any], gh: Runner = gh_runner, *, want: int = 6) -> list[dict[str, Any]]:
    """The newest pull requests that pass T1, T3, T4 and T2's one-issue part, up to ``want`` of them, each with its
    issue for the person's T2 judgement; and every pull request passed over, with why."""
    rows: list[dict[str, Any]] = []
    passing = 0
    for pr in merged_prs(name, gh):
        row = {"pr": pr["number"], "url": pr.get("url"), "title": pr.get("title"), "merged_at": pr.get("mergedAt")}
        ok, why = t1(pr.get("mergedAt") or "", pr.get("baseRefName") or "", rules.get("default_branch") or "")
        closing = [i for i in pr.get("closingIssuesReferences") or []]
        files = [f.get("path", "") for f in pr.get("files") or []]
        if ok and len(closing) != 1:
            ok, why = False, f"T2: closes {len(closing)} issues"
        if ok:
            ok, why = t3(files)
        if ok:
            merge = (pr.get("mergeCommit") or {}).get("oid") or ""
            parent, why = t4(json.loads(gh(["api", f"repos/{name}/commits/{merge}"])), files)
            ok = parent is not None
            row.update(merge_commit=merge, parent=parent)
        if ok:
            row["issue"] = issue(name, int(closing[0]["number"]), gh)
            row["files"] = files
            passing += 1
        row["mechanical"] = "passed" if ok else why
        rows.append(row)
        if passing >= want:
            break
    return rows


def top_pypi() -> list[str]:
    with urllib.request.urlopen(TOP_PYPI, timeout=120) as reply:  # noqa: S310 - a fixed https address
        data = json.loads(reply.read().decode("utf-8"))
    return [row["project"] for row in data.get("rows", [])]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", type=Path, default=None)
    args = ap.parse_args(argv)
    root = c.archive_root(args.archive)
    top = top_pypi()
    projects = []
    for name in CANDIDATES:
        rules = project_rules(name, json.loads(gh_runner(["api", f"repos/{name}"])), top)
        if rules["kept"]:
            # the repository's current name: GitHub's search finds nothing under a name the repository was moved from
            rules["pull_requests"] = screen(rules["full_name"], rules)
        projects.append(rules)
        print(f"{name}: {'kept' if rules['kept'] else 'dropped'} ({rules['why']}); "
              f"{sum(r.get('mechanical') == 'passed' for r in rules.get('pull_requests', []))} to judge", flush=True)
    out = root / "selection" / "candidates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"read_at": c.now(), "window": WINDOW, "top_pypi": TOP_PYPI, "projects": projects},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out.name} in the archive's selection folder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
