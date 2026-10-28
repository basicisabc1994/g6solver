# SPDX-License-Identifier: Apache-2.0
"""V10's study driver over patches (docs/validation/patch_review_search/PROTOCOL.md, "For all arms of this study" and
"Reruns", with the attribution rule under "Arms").

**Order** (``study``). The non-empty patches of V9's draw, in draw order; the empty and non-applying ones are
counted and recorded with their reason first (``excluded``), never dropped. On each, arms C, T and E run, all three on
the patch before the next; arm A is V9's arm 3 result on the patch, reused, never rerun. When every patch is done,
every finding of C, T and E is rerun (``rerun``, before any label is read), and so is every finding of arm A the patch
carries (``arm_a_findings``: its failed stated checks, as reproductions). Each arm run is timed, wall-clock and
processor seconds (children included). Every arm's output, every retry and every
rerun result goes into an append-only record (``Record``), each entry's SHA-256 chained to the one before it.

**The arms are injected.** G6's arms C and T are callables, ``arm(patch, *, search, share_left, meter)``, given the arm's
search configuration as data (``SEARCH``: C free-form checks only, T templates and test adequacy). G6's review takes
it as ``search={"templates": bool, ...}`` (VS-3), and its proposer comes with VS-5; this driver never calls G6 itself.
Each returns its own review's disposition by arm A's rule (``own``), its model calls, what it spent (``spent_cents``),
its reason codes, whether its deadline was reached during the search stage, its findings (reproductions ``{file, text,
node}``) and, when the proposer failed, G6's reason. Arm E is ``arm_e(patch, *, c_calls, t_calls, share_left, meter)``,
around ``arm_e.arm_e``.

**C and T in a child process.** At a study level (``children_only``) arms C and T are ``ChildArm``s: each run is a
fresh isolated interpreter (``child.py``), killed with its process tree at the arm's limit plus its grace, so a hung
review cannot stall the study. A killed run is a time-out, a tool error marked as shaped by the limit, and is charged
what its meter (a ``FileMeter`` the child writes as it calls) counted before the kill. The child gets the study's
environment without the research flag. A plain callable runs in this process, for development fixtures.

**One run each.** A run that failed from infrastructure before its first model call (the arm raises
``InfrastructureFailure``, and its meter counted no call) is retried once, and the retry is logged; a second such
failure is a tool error. The same failure after a model call, any other exception, or a reply without a disposition, is
a tool error, never retried. Every tool error is counted, never dropped.

**Spending.** Each of C, T and E runs under a third of the study's spending cap (``Spending``). Every arm run gets a
fresh meter (``Meter``; VS-5's MeteredCaller will be one), which counts calls, characters and cents as each model call
happens; the driver charges the arm what its meter counted, read after the arm returns and after it raises, so a run
that fails after its calls is charged for them. What the arm says it spent (``spent_cents``, and its calls) is a
cross-check: a difference is logged. Each arm is asked before every model call whether its share, less what its meter
has counted on the patch so far, still allows one (``share_left``). A patch reached after an arm's share is spent is a
counted tool error for that arm, and the arm is not called.

**Dispositions.** C and T accept when arm A accepts and their own review accepts by arm A's rule; their own review's
tool error is theirs, arm A's leaves them none (``arm_a_without_disposition``), and when arm A does not accept they
take its category. A patch that arm A accepted and a search arm did not is attributed to search only when that arm's
reasons include ``unconfirmed_counterexample`` or ``unconfirmed_failure``, or its deadline was reached during the search
stage; otherwise it is a difference between runs. A patch on which a search arm's proposer failed is listed, with G6's
reason, beside its disposition.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from g6.core import component_process
from patch_review_search.metering import NOTHING, FileMeter, InfrastructureFailure, Meter
from patch_review_search import report_check
from patch_review_search.record import Record
from patch_review_utility.arms import KILL_GRACE_S, RESEARCH_FLAG, ROOTS
from patch_review_utility.dispositions import CATEGORIES, ERROR, make, tally

__all__ = ["NOTHING", "ChildArm", "FileMeter", "InfrastructureFailure", "Meter", "Record", "Spending", "study"]
CHILD = Path(__file__).with_name("child.py")

ARMS = ("C", "T", "E")
#: Each search arm's configuration, as G6's review takes it (``search``): C, free-form checks only; T, templates first
#: (and the formal path where a template uses it), free-form checks where no template applies, and test adequacy.
SEARCH = {"C": {"templates": False}, "T": {"templates": True, "test_adequacy": True}}
INFRASTRUCTURE, SPEND_SHARE, TOOL_ERROR = "infrastructure_before_first_call", "spend_share_reached", "tool_error"
#: A child arm stopped at its limit: a time-out, the kind arm E gives one (``arm_e.TIME_OUT``).
TIME_OUT = "time_out"
#: A search arm's report that failed the report check (``report_check``): a tool error for that patch.
REPORT_CHECK = "report_check_failed"
ARM_A = "arm_a_without_disposition"
#: The reason codes that attribute a difference from arm A to search.
SEARCH_REASONS = frozenset({"unconfirmed_counterexample", "unconfirmed_failure"})
TO_SEARCH, BETWEEN_RUNS = "search", "difference between runs"
#: Why a patch of the draw is left out before the driver: V9's states for it (``trees.apply``; V9's ``run.py`` keeps
#: each such task's row, with its state in ``patch``).
EXCLUDED = ("empty", "not_applying")


@dataclass(frozen=True)
class ChildArm:
    """Arm C or T run in a child process of its own, as V9 runs arms 3 and 4 (``arms.in_child``): ``python -I
    child.py``, through G6's ``component_process.run_process``, in a call window of ``limit_s`` (the arm's wall-clock
    limit, V9's arm 3's) plus ``grace_s``, so a hung review cannot stall the study: at the window's end the child is
    killed with its whole process tree. ``spec`` is the arm, ``module:function``, imported in the child from ``roots``
    (with the study's folder and G6's)."""
    spec: str
    limit_s: float
    roots: tuple = ()
    grace_s: float = KILL_GRACE_S


class ChildTimedOut(Exception):
    """A child arm stopped at its limit, with no reply."""


def _in_child(arm: ChildArm, patch: dict, *, search: dict, share_cents: float, meter: FileMeter, folder: Path) -> dict:
    """The child arm's reply, or ``InfrastructureFailure`` (it said so), ``ChildTimedOut`` (killed at its limit), or
    ``RuntimeError`` (it raised, or ended with no reply)."""
    job, reply, log = folder / "job.json", folder / "reply.json", folder / "child.log"
    job.write_text(json.dumps({"spec": arm.spec, "patch": patch, "search": search, "share_cents": share_cents,
                               "meter": str(meter.path), "roots": [*map(str, arm.roots), *ROOTS]}, default=str),
                   encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != RESEARCH_FLAG}
    argv = [sys.executable, "-I", str(CHILD), str(job), str(reply)]
    try:
        with open(log, "wb") as sink, component_process.window(arm.limit_s + arm.grace_s):
            code = component_process.run_process(argv, env=env, stdout=sink, stderr=subprocess.STDOUT)
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise   # an operator stop ends the study, never one arm's result
        raise ChildTimedOut(f"the arm was stopped at its wall-clock limit of {arm.limit_s} seconds (and "
                            f"{arm.grace_s} more), with no reply") from exc
    if not reply.is_file():
        said = log.read_text(encoding="utf-8", errors="replace")[-600:]
        raise RuntimeError(f"the arm's process ended with exit code {code} and no reply: {said}")
    found = json.loads(reply.read_text(encoding="utf-8"))
    if "infrastructure" in found:
        raise InfrastructureFailure(found["infrastructure"])
    if "error" in found:
        raise RuntimeError(found["error"])
    return found["reply"]


class Spending:
    """The study's spending cap, in cents, a third for each of arms C, T and E."""

    def __init__(self, cap_cents: float):
        if not cap_cents > 0:
            raise ValueError("the spending cap must be above zero")
        self.cap, self.share = float(cap_cents), float(cap_cents) / len(ARMS)
        self.spent = dict.fromkeys(ARMS, 0.0)

    def left(self, arm: str) -> bool:
        return self.spent[arm] < self.share

    def charge(self, arm: str, cents) -> None:
        cents = float(cents or 0)
        if cents < 0:
            raise ValueError("an arm cannot spend less than nothing")
        self.spent[arm] += cents

    def report(self) -> dict:
        return {"cap_cents": self.cap, "share_cents": self.share, "spent_cents": dict(self.spent)}


def _valid(disposition) -> bool:
    return isinstance(disposition, dict) and disposition.get("category") in CATEGORIES


def _search_disposition(arm_a: dict, own: dict) -> dict:
    if own["category"] == ERROR:
        return dict(own)
    if arm_a["category"] == ERROR:
        return make(ERROR, f"arm A has no disposition: {arm_a.get('detail', '')}"[:300], error_kind=ARM_A)
    if not arm_a["accepted"]:
        return make(arm_a["category"], "arm A did not accept", time_or_cap=bool(arm_a.get("time_or_cap_reached")))
    return dict(own)


def _attribution(arm_a: dict, own: dict, reply: dict) -> str | None:
    if not (arm_a.get("accepted") and _valid(own) and not own["accepted"]):
        return None
    codes = {str(code).rpartition(":")[2] for code in reply.get("reasons") or []}
    return TO_SEARCH if codes & SEARCH_REASONS or reply.get("deadline_in_search") else BETWEEN_RUNS


def study(patches: list[dict], *, arm_c, arm_t, arm_e, spending: Spending, record: Record, rerun,
          excluded: list[dict], meters=lambda arm: Meter(), children_only: bool = False) -> dict:
    """Arms C, T and E on every patch in draw order, then the reruns of every finding (the module docstring).
    ``excluded``: the patches of the draw V9 found empty or not applying (``{id, draw_index, reason}``), each counted
    and recorded with its reason before any arm runs, never dropped."""
    order = [patch.get("draw_index") for patch in patches]
    if any(not isinstance(index, int) for index in order) or order != sorted(set(order)):
        raise ValueError("the patches must come in draw order, each with its own draw_index")
    if not all(_valid(patch.get("arm_a")) for patch in patches):
        raise ValueError("every patch needs arm A's disposition (V9's arm 3 result on it)")
    left_out = [item.get("draw_index") for item in excluded]
    if any(not isinstance(index, int) for index in left_out) or len(set(left_out) | set(order)) != len(
            left_out) + len(order):
        raise ValueError("every excluded patch needs a draw index of its own, used by no other patch")
    if any(item.get("reason") not in EXCLUDED for item in excluded):
        raise ValueError(f"an excluded patch's reason must be one of V9's: {', '.join(EXCLUDED)}")
    if children_only and not (isinstance(arm_c, ChildArm) and isinstance(arm_t, ChildArm)):
        raise ValueError("at a study level, arms C and T run each in a child process of their own (ChildArm)")
    for item in excluded:
        record.add("excluded", dict(item))
    rows, retries, failures, differences = [], [], [], []

    def once(patch: dict, arm: str, call, meter=None) -> tuple[dict | None, int, str, dict, dict]:
        """The arm's reply, the attempts made, the tool error's kind when there is no usable reply, the meter's
        reading, and the run's wall-clock and processor seconds (its process tree's, children included). The arm is
        charged what its meter counted, read after it returns and after it raises."""
        if not spending.left(arm):
            return None, 0, SPEND_SHARE, dict(NOTHING), {"seconds": 0.0, "processor_seconds": 0.0}
        meter = meter if meter is not None else meters(arm)
        started, cpu = time.monotonic(), os.times()

        def share_left() -> bool:   # what the arm has spent so far on this patch counts against its share
            return spending.spent[arm] + meter.reading()["cents"] < spending.share

        reply, attempt, kind = None, 0, ""
        try:
            for attempt in (1, 2):
                try:
                    reply = call(share_left, meter)
                    break
                except InfrastructureFailure as exc:
                    if meter.reading()["calls"]:   # not before its first model call: a tool error, not retried
                        reply, kind = {"error": f"after a model call: {exc}"}, TOOL_ERROR
                        break
                    if attempt == 2:
                        reply, kind = {"error": str(exc)}, INFRASTRUCTURE
                        break
                    retry = {"patch": patch["id"], "arm": arm, "attempt": attempt, "why": str(exc)[:300]}
                    retries.append(retry)
                    record.add("retry", retry)
                except ChildTimedOut as exc:
                    reply, kind = {"error": str(exc)}, TIME_OUT
                    break
                except Exception as exc:  # a tool error is counted, never dropped
                    reply, kind = {"error": f"{type(exc).__name__}: {exc}"}, TOOL_ERROR
                    break
        finally:
            reading = meter.reading()
            spending.charge(arm, reading["cents"])
            now = os.times()
            timing = {"seconds": round(time.monotonic() - started, 3),
                      "processor_seconds": round(sum(now[:4]) - sum(cpu[:4]), 3)}
        reported = {"cents": (reply or {}).get("spent_cents"),
                    "calls": (reply or {}).get("model_calls", ((reply or {}).get("calls") or {}).get("made"))}
        if any(isinstance(reported[key], (int, float)) and abs(float(reported[key]) - reading[key]) > 1e-6
               for key in ("cents", "calls")):
            found = {"patch": patch["id"], "arm": arm, "metered_cents": reading["cents"],
                     "reported_cents": reported["cents"], "metered_calls": reading["calls"],
                     "reported_calls": reported["calls"]}
            differences.append(found)
            record.add("spending_difference", found)
        return reply, attempt, kind, reading, timing

    def errored(kind: str, reply: dict | None) -> dict:
        said = {SPEND_SHARE: "the arm's share of the spending cap was spent before this patch",
                INFRASTRUCTURE: "the run failed from infrastructure before its first model call, twice"}.get(kind, "")
        return make(ERROR, (said or (reply or {}).get("error") or "no disposition")[:300], error_kind=kind,
                    time_or_cap=kind == TIME_OUT)

    def search_arm(patch: dict, arm: str) -> tuple[dict | None, int, str, dict, dict]:
        search, run = copy.deepcopy(SEARCH[arm]), arm_c if arm == "C" else arm_t
        if not isinstance(run, ChildArm):
            return once(patch, arm, lambda share_left, meter: run(patch, search=search, share_left=share_left,
                                                                  meter=meter))
        with tempfile.TemporaryDirectory(prefix="v10-arm-") as folder:
            meter = FileMeter(Path(folder) / "meter.jsonl")
            return once(patch, arm, lambda share_left, meter: _in_child(
                run, patch, search=search, share_cents=spending.share - spending.spent[arm], meter=meter,
                folder=Path(folder)), meter=meter)

    for patch in patches:
        arm_a, arms = patch["arm_a"], {}
        for arm in ("C", "T"):
            reply, attempts, kind, reading, timing = search_arm(patch, arm)
            if not kind and not _valid((reply or {}).get("own")):
                kind = TOOL_ERROR
            reply = reply or {}
            checked = (report_check.check(reply["report"], list(patch.get("contract_ids") or []))
                       if not kind and reply.get("report") is not None else None)
            if checked is not None and not checked["complete"]:
                kind, reply = REPORT_CHECK, {**reply, "error": "the report failed the report check: " + "; ".join(
                    [*checked["missing"], *(f"{c['check']} ({c['provenance']}) is listed as passing {c['requirement']}"
                                            for c in checked["model_originated_passing"]),
                     *checked["formal_problems"]])}
            own = errored(kind, reply) if kind else reply["own"]
            arms[arm] = {"disposition": _search_disposition(arm_a, own), "own": own, "attempts": attempts,
                         "model_calls": reading["calls"], "meter": reading, **timing,
                         "spent_cents": reply.get("spent_cents"),
                         "reasons": list(reply.get("reasons") or []), "findings": list(reply.get("findings") or []),
                         "attribution": _attribution(arm_a, own, reply),
                         "proposer_failure": reply.get("proposer_failure"),
                         # whether the search stage ran a check to a pass or a fail (feasibility criterion 3), as
                         # G6's report says it once VS-5 settles the field
                         "search_stage": reply.get("search_stage"),
                         # G6's report itself, and the report check of it (feasibility criterion 4)
                         "report": reply.get("report"), "report_check": checked}
            if reply.get("proposer_failure"):
                failures.append({"patch": patch["id"], "arm": arm, "reason": reply["proposer_failure"],
                                 "category": arms[arm]["disposition"]["category"]})
        reply, attempts, kind, reading, timing = once(patch, "E", lambda share_left, meter: arm_e(
            patch, c_calls=arms["C"]["model_calls"], t_calls=arms["T"]["model_calls"], share_left=share_left,
            meter=meter))
        if not kind and not _valid((reply or {}).get("disposition")):
            kind = TOOL_ERROR
        reply = reply or {}
        arms["E"] = {**{key: value for key, value in reply.items() if key not in ("disposition", "findings")},
                     "disposition": errored(kind, reply) if kind else reply["disposition"], "attempts": attempts,
                     "model_calls": reading["calls"], "meter": reading, **timing,
                     "findings": list(reply.get("findings") or [])}
        for arm in ARMS:
            record.add("arm", {"patch": patch["id"], "arm": arm, "output": arms[arm]})
        rows.append({"patch": patch["id"], "draw_index": patch["draw_index"], "arm_a": arm_a, "arms": arms})

    reruns = []
    for patch, row in zip(patches, rows):
        found_by = {"A": list(patch.get("arm_a_findings") or []),
                    **{arm: row["arms"][arm]["findings"] for arm in ARMS}}
        for arm, findings in found_by.items():
            for reproduction in findings:
                found = {"patch": patch["id"], "arm": arm, "node": reproduction.get("node"),
                         "result": rerun(patch, arm, reproduction)}
                reruns.append(found)
                record.add("rerun", found)
    counts = {reason: sum(item["reason"] == reason for item in excluded) for reason in EXCLUDED}
    return {"patches": rows, "excluded": {"counts": counts, "patches": [dict(item) for item in excluded]},
            "reruns": reruns, "retries": retries, "proposer_failures": failures, "spending_differences": differences,
            "tallies": {arm: tally([row["arms"][arm]["disposition"] for row in rows]) for arm in ARMS},
            "spending": spending.report(), "record": record.final}
