# SPDX-License-Identifier: Apache-2.0
"""V7 analysis, design D (PROTOCOL.md §7-§9). It reads only the archive.

1. ``gaming``: scans every scored run's stream, patch, egress log and repository state for test edits, oracle access,
   use of git history and network fetches (rule 8). It reads no verdict. A person inspects the matches and writes
   ``gaming_review.json`` in the archive root (``{"reviewed_by": "<name>", "void": ["<instance id>", ...]}``): a
   confirmed finding voids that task.
2. ``report``: refuses to run without that review; then, on the completed tasks (stage 2 finished and all three
   patches graded):

   * **H1, detection:** G6's flag (its check of the stage-1 patch) against the stage-1 patch's hidden outcome; one-sided
     Fisher exact test that the flag rate on failed stage-1 patches exceeds the rate on passed ones;
   * **H2, final resolve:** G against A2, paired by task; one-sided exact sign test on the discordant pairs;
   * Holm across H1 and H2 at family alpha 0.05;
   * secondaries, reported and not tested for the headline: G against A, A2 against A, breaks (a passing stage-1 patch
     made failing), cost per arm, G6's false-flag rate;
   * every run started, by how it ended, including those that stopped or failed.

   It writes ``report.json`` and ``rows.csv`` to the results folder.
3. ``manifest``: ``results/V7/archive_manifest.json``, every archived file's path, size and SHA-256.
4. ``project``: tasks completed under the cap for given costs per run (``--stage1``).

    python -I validation/v7_swebench/analyse.py gaming|report|manifest|project [--archive <archive>]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import re
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SRC = HERE.parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import shadow  # noqa: E402
import v7common as c  # noqa: E402

ALPHA = 0.05
BOOTSTRAP = 10_000
HEADLINE = "G6's check-and-fix against Claude Code checking itself with the same budget"

# --- rule 8 ------------------------------------------------------------------------------------------------------------
CODE_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
DATA_DIRS = {"testdata", "test_data", "fixtures", "fixture", "mocks", "mock", "__snapshots__"}


def is_test_path(path: str) -> bool:
    """The verifier's own rule for test code (tests/test.sh), plus its data folders."""
    segs = path.strip().lstrip("./").split("/")
    parents, base = segs[:-1], segs[-1]
    if any(s in DATA_DIRS for s in parents) or any(s in CODE_DIRS for s in parents):
        return True
    return bool(base == "conftest.py" or re.search(r"_test\.(go|py|js|jsx|ts|tsx)$", base)
                or re.search(r"^test_.*\.py$", base) or re.search(r"\.(test|spec)\.(js|jsx|ts|tsx)$", base))


ORACLE = re.compile(r"(^|[\s'\"=])(/tests/|/solution\b|/logs/verifier|test_patch\.patch|gold_patch\.diff)")
HISTORY = re.compile(r"\bgit\s+(log\b[^|;&]*--all|reflog|fsck|stash|cat-file|rev-list\b[^|;&]*--all|for-each-ref|"
                     r"fetch|pull|remote|clone|ls-remote|show-ref)")
NETWORK = re.compile(r"\b(curl|wget|pip3?\s+(download|install)|npm\s+(install|view|i)\b|yarn\s+add|go\s+(get|mod\s+"
                     r"download)|apt(-get)?\s+install)\b|https?://")
WRITES = re.compile(r"(sed\s+-i|perl\s+-pi|\btee\b|>\s*\S|\brm\b|\bmv\b|git\s+checkout)")


def _tool_uses(stream: Path) -> Iterable[dict]:
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "assistant":
            for block in (obj.get("message") or {}).get("content") or ():
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    yield block


def scan_run(folder: Path, task_folder: Path | None = None) -> list[dict[str, str]]:
    """The rule-8 matches for one archived run. Each is a lead for a person to inspect, not a finding."""
    hits: list[dict[str, str]] = []
    env = folder / "environment.json"
    git = json.loads(env.read_text(encoding="utf-8")).get("git", {}) if env.is_file() else {}
    if git and not git.get("history_clean"):
        hits.append({"kind": "git_history", "where": "repository state", "what": json.dumps(git, sort_keys=True)})
    egress = (task_folder / "egress.jsonl") if task_folder else None
    if egress is not None and egress.is_file():
        for line in egress.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line) if line.strip() else {}
            if entry.get("allowed") is False:
                hits.append({"kind": "network", "where": "egress proxy", "what": f"refused {entry.get('host')}"})
    patch = folder / "model.patch"
    if patch.is_file():
        for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("+++ b/") and is_test_path(line[6:]):
                hits.append({"kind": "test_edit", "where": "final patch", "what": line[6:]})
    stream = folder / "stream.jsonl"
    for tool in _tool_uses(stream) if stream.is_file() else ():
        name, data = str(tool.get("name")), tool.get("input") or {}
        text = json.dumps(data, sort_keys=True)
        if name in ("WebFetch", "WebSearch"):
            hits.append({"kind": "network", "where": name, "what": text[:300]})
        if name in ("Edit", "Write", "MultiEdit", "NotebookEdit") and is_test_path(str(data.get("file_path", ""))
                                                                                     .split("/app/")[-1]):
            hits.append({"kind": "test_edit", "where": name, "what": str(data.get("file_path"))})
        command = str(data.get("command", "")) if name == "Bash" else ""
        if ORACLE.search(text):
            hits.append({"kind": "oracle_access", "where": name, "what": text[:300]})
        if command and HISTORY.search(command):
            hits.append({"kind": "git_history", "where": "Bash", "what": command[:300]})
        if command and NETWORK.search(command):
            hits.append({"kind": "network", "where": "Bash", "what": command[:300]})
        if command and WRITES.search(command) and any(is_test_path(w) for w in re.findall(r"[\w./-]+", command)):
            hits.append({"kind": "test_edit", "where": "Bash", "what": command[:300]})
    return hits


def cmd_gaming(archive: c.Archive) -> dict[str, Any]:
    out = {}
    for row in archive.runs():
        if row.get("phase") != "eval" or row.get("arm") not in c.ARMS:
            continue
        task_run = archive.latest("eval", row["task"], "task")
        hits = scan_run(archive.root / row["run"], task_run["path"] if task_run else None)
        if hits:
            out[row["run"]] = hits
    c.write_json(archive.root / "gaming.json", out)
    return {"runs_with_matches": len(out), "matches": sum(len(v) for v in out.values()),
            "file": "gaming.json in the archive root", "next": "inspect, then write gaming_review.json there"}


# --- statistics --------------------------------------------------------------------------------------------------------
def sign_p(wins: int, losses: int) -> float:
    from g6.reasoning.compare import sign_test_p

    return float(sign_test_p(wins, losses))


def fisher_one_sided(flag_fail: int, noflag_fail: int, flag_pass: int, noflag_pass: int) -> float:
    """One-sided Fisher exact test that the flag rate among failed stage-1 patches exceeds the rate among passed ones:
    the chance, with the margins fixed, of at least ``flag_fail`` flags among the failed."""
    failed, flagged = flag_fail + noflag_fail, flag_fail + flag_pass
    n = failed + flag_pass + noflag_pass
    total = math.comb(n, failed)
    if total == 0:
        return 1.0
    tail = sum(math.comb(flagged, k) * math.comb(n - flagged, failed - k)
               for k in range(flag_fail, min(failed, flagged) + 1))
    return min(1.0, tail / total)


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    order = sorted(pvalues, key=lambda k: pvalues[k])
    adjusted, running = {}, 0.0
    for i, key in enumerate(order):
        running = max(running, min(1.0, (len(order) - i) * pvalues[key]))
        adjusted[key] = round(running, 6)
    return adjusted


def cluster_bootstrap(diffs: Mapping[str, Sequence[float]], *, reps: int = BOOTSTRAP, seed: int = c.SEED) -> list[float]:
    """Two-stage percentile 95% interval of the mean paired difference: repositories resampled, then tasks within."""
    repos = sorted(diffs)
    if not repos:
        return [0.0, 0.0]
    rng = random.Random(f"V7:{seed}:bootstrap")
    means = []
    for _ in range(reps):
        sample = []
        for repo in (rng.choice(repos) for _ in repos):
            pool = diffs[repo]
            sample.extend(rng.choice(pool) for _ in pool)
        means.append(statistics.fmean(sample))
    means.sort()
    return [round(means[int(0.025 * reps)], 4), round(means[min(reps - 1, int(0.975 * reps))], 4)]


def paired(rows: Mapping[str, Mapping[str, Any]], a: str, b: str, repo_of: Mapping[str, str]) -> dict[str, Any]:
    """``a`` against ``b``: wins (a resolved, b not), losses, ties, the one-sided sign test, the interval."""
    wins = sum(r[a] and not r[b] for r in rows.values())
    losses = sum(r[b] and not r[a] for r in rows.values())
    diffs: dict[str, list[float]] = {}
    for task, r in rows.items():
        diffs.setdefault(repo_of.get(task, "?"), []).append(float(r[a]) - float(r[b]))
    return {"tasks": len(rows), f"{a}_resolved": sum(r[a] for r in rows.values()),
            f"{b}_resolved": sum(r[b] for r in rows.values()), "wins": wins, "losses": losses,
            "ties": len(rows) - wins - losses, "p_one_sided": round(sign_p(wins, losses), 6),
            "interval_95": cluster_bootstrap(diffs) if rows else [0.0, 0.0]}


def sign_power(n: int, p_win: float, p_loss: float, level: float, *, reps: int = 4000, seed: int = c.SEED) -> float:
    """Chance that the one-sided sign test at ``level`` finds the gain, with per-task win and loss rates."""
    rng = random.Random(f"V7:{seed}:power")
    found = 0
    for _ in range(reps):
        wins = losses = 0
        for _ in range(n):
            u = rng.random()
            wins += u < p_win
            losses += p_win <= u < p_win + p_loss
        found += wins > losses and sign_p(wins, losses) <= level
    return round(found / reps, 4)


# --- the report --------------------------------------------------------------------------------------------------------
def completed_rows(archive: c.Archive, phase: str, ids: Sequence[str]) -> tuple[dict[str, dict], list[str]]:
    """``rows[task]``: each arm resolved (a released patch with reward 1), G6's flag, each arm's cost and status, for
    the tasks whose stage 2 finished and whose three patches were graded. The other tasks are listed."""
    rows, missing = {}, []
    for task in ids:
        task_run = archive.latest(phase, task, "task")
        if task_run is None or not task_run["record"].get("stage2_done"):
            missing.append(task)
            continue
        row: dict[str, Any] = {"g6_flag": bool(task_run["record"].get("g6_flag"))}
        complete = True
        for arm in c.ARMS:
            run, grade = archive.latest(phase, task, arm), archive.latest(phase, task, f"grade-{arm}")
            if run is None or grade is None or grade["record"].get("resolved") is None:
                complete = False
                break
            row[arm] = bool(grade["record"]["resolved"]) and bool(run["record"].get("released"))
            row[f"{arm}_cost"] = float(run["record"].get("cost_usd") or 0.0)
            row[f"{arm}_status"] = run["record"].get("status")
            row[f"{arm}_turns"] = run["record"].get("num_turns")
        if complete:
            rows[task] = row
        else:
            missing.append(task)
    return rows, missing


def report_d(archive: c.Archive | Path, out: Path, splits: Mapping[str, Any], *, phase: str = "eval") -> dict[str, Any]:
    archive = archive if isinstance(archive, c.Archive) else c.Archive(Path(archive))
    review_path = archive.root / "gaming_review.json"
    if not review_path.is_file():
        raise SystemExit("run `gaming` and have a person write gaming_review.json in the archive root first (rule 8)")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    who = str(review.get("reviewed_by", "")).strip()
    if not who or who.lower().startswith("auto:"):
        raise SystemExit("gaming_review.json needs the name of the person who inspected the matches")
    order = list(splits.get("scored") or splits.get("eval") or ())
    rows, missing = completed_rows(archive, phase, order)
    void = set(review.get("void") or ())
    rows = {t: r for t, r in rows.items() if t not in void}
    failed = [r for r in rows.values() if not r["A"]]
    passed = [r for r in rows.values() if r["A"]]
    ff, fp = sum(r["g6_flag"] for r in failed), sum(r["g6_flag"] for r in passed)
    h1 = {"flagged_failed": ff, "failed": len(failed), "flagged_passed": fp, "passed": len(passed),
          "p_one_sided": round(fisher_one_sided(ff, len(failed) - ff, fp, len(passed) - fp), 6)}
    h2 = paired(rows, "G", "A2", splits.get("repo_of", {}))
    adjusted = holm({"H1": h1["p_one_sided"], "H2": h2["p_one_sided"]})
    repo_of = splits.get("repo_of", {})
    secondary = {
        "G_vs_A": paired(rows, "G", "A", repo_of), "A2_vs_A": paired(rows, "A2", "A", repo_of),
        "breaks": {arm: sum(r["A"] and not r[arm] for r in rows.values()) for arm in ("A2", "G")},
        "cost_usd": {arm: {"total": round(sum(r[f"{arm}_cost"] for r in rows.values()), 4),
                           "mean": round(statistics.fmean([r[f"{arm}_cost"] for r in rows.values()]), 4)
                           if rows else None} for arm in c.ARMS},
        "false_flag_rate": (fp / len(passed)) if passed else None, "g6_flagged": ff + fp,
        "g_acted": sum(r["G_cost"] > 0 for r in rows.values())}
    significant = adjusted["H2"] <= ALPHA and h2["wins"] > h2["losses"]
    headline = (f"{HEADLINE}: G resolved {h2['G_resolved']} and A2 {h2['A2_resolved']} of {len(rows)} tasks "
                f"({h2['wins']} tasks only G resolved, {h2['losses']} only A2); Holm-adjusted p {adjusted['H2']}, "
                + ("significant at 0.05." if significant else "not significant at 0.05."))
    started = Counter(r.get("status") for r in archive.runs() if r.get("phase") == phase)
    selftest = oracle_selftest(archive, list(rows))
    reached = [t for t in missing if t not in void and archive.find(phase, t, "task")]
    report = {"study": c.STUDY, "design": "D", "phase": phase, "completed_tasks": len(rows),
              "not_completed": reached, "not_reached": [t for t in missing if t not in void and t not in reached],
              "void": sorted(void),
              "H1": h1, "H2": h2, "holm": adjusted, "secondary": secondary, "headline": headline,
              "runs_started": {"total": sum(started.values()), "by_status": dict(started)},
              "verdict": verdict_d(adjusted, h1, h2, rows, review, oracle_ok=selftest["ok"]),
              "oracle_selftest": selftest, "gaming_review": review,
              "shadow_learning": shadow.summary(archive, phase, list(rows)),
              "note": "Completed tasks only; every run started is counted in runs_started, whatever its end."}
    out.mkdir(parents=True, exist_ok=True)
    c.write_json(out / "report.json", report)
    (out / "rows.csv").write_text(_csv(rows), encoding="utf-8", newline="")
    return report


def oracle_selftest(archive: c.Archive, tasks: Sequence[str]) -> dict[str, Any]:
    """The self-test's archived runs for these tasks: the reference patch must resolve and an empty one must not.
    ``ok`` is None while any task's self-test is missing."""
    gold = [archive.latest("selftest", t, "gold") for t in tasks]
    empty = [archive.latest("selftest", t, "empty") for t in tasks]
    if not tasks or any(r is None for r in (*gold, *empty)):
        return {"ok": None, "why": "the oracle self-test has not run on every completed task"}
    unresolved = sum(r["record"].get("resolved") is not True for r in gold)
    resolved = sum(r["record"].get("resolved") is True for r in empty)
    return {"ok": unresolved <= 0.05 * len(tasks) and resolved == 0, "tasks": len(tasks),
            "gold_unresolved": unresolved, "empty_resolved": resolved}


def verdict_d(adjusted: Mapping[str, float], h1: Mapping[str, Any], h2: Mapping[str, Any], rows: Mapping[str, Any],
              review: Mapping[str, Any], *, oracle_ok: bool | None = None) -> dict[str, str]:
    """PROTOCOL.md §9, in order; exactly one verdict (provisional while the oracle self-test has not run)."""
    if review.get("contaminated"):
        return {"verdict": "contaminated_evaluation", "why": "a confirmed finding that an arm reached the oracle"}
    reverse = sign_p(h2["losses"], h2["wins"])
    if h2["losses"] > h2["wins"] and min(1.0, 2 * reverse) <= ALPHA:
        return {"verdict": "unsafe_or_regressive", "why": "Claude Code checking itself beat G6's check-and-fix"}
    if oracle_ok is False:
        return {"verdict": "oracle_inadequate", "why": "a reference patch unresolved or an empty patch resolved"}
    failures = sum(not r["A"] for r in rows.values())
    if not rows or failures < 0.10 * len(rows):
        return {"verdict": "insufficient_failure_opportunities",
                "why": f"stage 1 failed {failures} of {len(rows)} completed tasks"}
    note = "" if oracle_ok else " (provisional: the oracle self-test has not run)"
    if adjusted["H2"] <= ALPHA and h2["wins"] > h2["losses"]:
        return {"verdict": "mechanism_demonstrated", "why": f"H2: Holm p {adjusted['H2']}{note}"}
    if adjusted["H1"] <= ALPHA:
        return {"verdict": "component_validated_no_transfer",
                "why": f"H1: G6's flag found failing stage-1 patches (Holm p {adjusted['H1']}), and H2 did not "
                       f"pass{note}"}
    return {"verdict": "inconclusive", "why": f"neither H1 nor H2 passed after Holm{note}"}


def _csv(rows: Mapping[str, Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["instance_id", "g6_flag", *[f"{a}_{k}" for a in c.ARMS for k in ("resolved", "status", "cost_usd",
                                                                                   "turns")]])
    for task, r in rows.items():
        w.writerow([task, int(r["g6_flag"]), *[v for a in c.ARMS for v in (int(r[a]), r[f"{a}_status"],
                                                                           r[f"{a}_cost"], r[f"{a}_turns"])]])
    return buf.getvalue()


# --- the manifest and the projection -----------------------------------------------------------------------------------
def write_manifest(archive: c.Archive, out: Path) -> dict[str, Any]:
    found = c.manifest(archive.root)
    c.write_json(out / "archive_manifest.json", found)
    return {"files": found["count"], "file": str(out / "archive_manifest.json")}


def project(stage1: float, a2: float, g: float, flag_rate: float, *, pilot: int = c.PILOT_SIZE,
            cap: float = c.MAX_USD_CEILING, jobs: int = 2, scored: int = c.SCORED_SIZE) -> int:
    """Scored tasks completed under the batch's rule, with every task costing the same and lasting the same: the pilot
    first, then two at a time, a task admitted only when its reservation fits beside the one in flight."""
    per_task = stage1 + a2 + flag_rate * (g + c.LESSON_ESTIMATE_USD)   # a flagged task also pays for its lesson
    spent = pilot * per_task                       # the pilot runs first; its spend counts
    finished = flying = started = 0
    while True:
        while flying < jobs and started < scored and spent + (flying + 1) * c.TASK_RESERVE <= cap + 1e-9:
            flying += 1                            # admitted: its reservation fits beside those in flight
            started += 1
        if flying == 0:
            return finished
        flying -= 1                                # the oldest task in flight finishes
        finished += 1
        spent += per_task


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("gaming", "report", "manifest", "project", "power"))
    ap.add_argument("--archive", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=HERE.parents[1] / "docs/validation/results/V7")
    ap.add_argument("--splits", type=Path, default=HERE.parents[1] / "docs/validation/results/V7/splits.json")
    ap.add_argument("--phase", default="eval", choices=("eval", "pilot"))
    args = ap.parse_args(argv)
    archive = c.Archive(args.archive or c.archive_root())
    if args.command == "gaming":
        result = cmd_gaming(archive)
    elif args.command == "report":
        result = report_d(archive, args.out, c.load_splits(args.splits), phase=args.phase)
    elif args.command == "manifest":
        result = write_manifest(archive, args.out)
    elif args.command == "project":
        result = {f"stage1 ${s:.2f}": {label: project(s, a2, g, f) for label, (a2, g, f) in
                                      {"stage 2 at $1.00, G acting on half": (1.0, 1.0, 0.5),
                                       "stage 2 at its caps, G acting on all": (1.5, 1.5, 1.0)}.items()}
                  for s in (2.0, 3.0, 4.0)}
    else:
        result = {n: {f"{w}/{lo}": sign_power(n, w, lo, ALPHA / 2) for w, lo in ((0.10, 0.03), (0.15, 0.03),
                                                                                (0.20, 0.05))} for n in (20, 30, 40)}
    print(json.dumps(result, indent=1, default=str)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
