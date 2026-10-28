# SPDX-License-Identifier: Apache-2.0
"""V10's study driver over patches (docs/validation/patch_review_search/PROTOCOL.md, "For all arms of this study" and
"Reruns", with the attribution rule under "Arms").

**Order** (``study``). The non-empty patches of V9's draw, in draw order; the empty and non-applying ones are
counted and recorded with their reason first (``excluded``), never dropped. On each, arms C, T and E run, all three on
the patch before the next; arm A is V9's arm 3 result on the patch, reused, never rerun. When every patch is done,
every finding of C, T and E is rerun (``rerun``, before any label is read). Every arm's output, every retry and every
rerun result goes into an append-only record (``Record``), each entry's SHA-256 chained to the one before it.

**The arms are injected.** G6's arms C and T are callables, ``arm(patch, *, search, share_left, meter)``, given the arm's
search configuration as data (``SEARCH``: C free-form checks only, T templates and test adequacy). G6's review takes
it as ``search={"templates": bool, ...}`` (VS-3), and its proposer comes with VS-5; this driver never calls G6 itself.
Each returns its own review's disposition by arm A's rule (``own``), its model calls, what it spent (``spent_cents``),
its reason codes, whether its deadline was reached during the search stage, its findings (reproductions ``{file, text,
node}``) and, when the proposer failed, G6's reason. Arm E is ``arm_e(patch, *, c_calls, t_calls, share_left, meter)``,
around ``arm_e.arm_e``.

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
import hashlib
import json
from pathlib import Path

from patch_review_utility.dispositions import CATEGORIES, ERROR, make, tally

ARMS = ("C", "T", "E")
#: Each search arm's configuration, as G6's review takes it (``search``): C, free-form checks only; T, templates first
#: (and the formal path where a template uses it), free-form checks where no template applies, and test adequacy.
SEARCH = {"C": {"templates": False}, "T": {"templates": True, "test_adequacy": True}}
INFRASTRUCTURE, SPEND_SHARE, TOOL_ERROR = "infrastructure_before_first_call", "spend_share_reached", "tool_error"
ARM_A = "arm_a_without_disposition"
#: The reason codes that attribute a difference from arm A to search.
SEARCH_REASONS = frozenset({"unconfirmed_counterexample", "unconfirmed_failure"})
TO_SEARCH, BETWEEN_RUNS = "search", "difference between runs"
#: Why a patch of the draw is left out before the driver: V9's states for it (``trees.apply``; V9's ``run.py`` keeps
#: each such task's row, with its state in ``patch``).
EXCLUDED = ("empty", "not_applying")


class InfrastructureFailure(Exception):
    """An arm's run that failed from infrastructure before its first model call."""


#: A meter's reading before anything ran.
NOTHING = {"calls": 0, "characters": 0, "cents": 0.0}


class Meter:
    """What an arm's model calls have cost on one patch, counted as each call happens: calls, characters and cents.
    The driver gives each arm run a fresh one (``meters``) and reads it after the arm returns and after it raises.
    VS-5's MeteredCaller will be such a meter: anything with ``reading()`` giving those three counts."""

    def __init__(self):
        self._found = dict(NOTHING)

    def add(self, *, calls: int = 1, characters: int = 0, cents: float = 0.0) -> None:
        if calls < 0 or characters < 0 or cents < 0:
            raise ValueError("a meter only counts up")
        self._found = {"calls": self._found["calls"] + int(calls),
                       "characters": self._found["characters"] + int(characters),
                       "cents": self._found["cents"] + float(cents)}

    def reading(self) -> dict:
        return dict(self._found)


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


def _canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


class Record:
    """An append-only record: each entry ``{seq, kind, sha256, previous, chain, data}``, where ``sha256`` is its data's
    and ``chain`` the SHA-256 of the previous chain and that; written as JSON lines to ``path``, which must be new."""

    def __init__(self, path: str | Path | None = None):
        self.path, self.entries, self.final = (Path(path) if path else None), [], "0" * 64
        if self.path is not None:
            with open(self.path, "x", encoding="utf-8"):
                pass

    def add(self, kind: str, data: dict) -> str:
        digest = hashlib.sha256(_canonical(data)).hexdigest()
        chain = hashlib.sha256((self.final + digest).encode("ascii")).hexdigest()
        entry = {"seq": len(self.entries), "kind": kind, "sha256": digest, "previous": self.final, "chain": chain,
                 "data": data}
        self.entries.append(entry)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical(entry).decode("utf-8") + "\n")
        self.final = chain
        return chain


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
          excluded: list[dict], meters=lambda arm: Meter()) -> dict:
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
    for item in excluded:
        record.add("excluded", dict(item))
    rows, retries, failures, differences = [], [], [], []

    def once(patch: dict, arm: str, call) -> tuple[dict | None, int, str, dict]:
        """The arm's reply, the attempts made, the tool error's kind when there is no usable reply, and the meter's
        reading. The arm is charged what its meter counted, read after it returns and after it raises."""
        if not spending.left(arm):
            return None, 0, SPEND_SHARE, dict(NOTHING)
        meter = meters(arm)

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
                except Exception as exc:  # a tool error is counted, never dropped
                    reply, kind = {"error": f"{type(exc).__name__}: {exc}"}, TOOL_ERROR
                    break
        finally:
            reading = meter.reading()
            spending.charge(arm, reading["cents"])
        reported = {"cents": (reply or {}).get("spent_cents"),
                    "calls": (reply or {}).get("model_calls", ((reply or {}).get("calls") or {}).get("made"))}
        if any(isinstance(reported[key], (int, float)) and abs(float(reported[key]) - reading[key]) > 1e-6
               for key in ("cents", "calls")):
            found = {"patch": patch["id"], "arm": arm, "metered_cents": reading["cents"],
                     "reported_cents": reported["cents"], "metered_calls": reading["calls"],
                     "reported_calls": reported["calls"]}
            differences.append(found)
            record.add("spending_difference", found)
        return reply, attempt, kind, reading

    def errored(kind: str, reply: dict | None) -> dict:
        said = {SPEND_SHARE: "the arm's share of the spending cap was spent before this patch",
                INFRASTRUCTURE: "the run failed from infrastructure before its first model call, twice"}.get(kind, "")
        return make(ERROR, (said or (reply or {}).get("error") or "no disposition")[:300], error_kind=kind)

    for patch in patches:
        arm_a, arms = patch["arm_a"], {}
        for arm in ("C", "T"):
            search, run = copy.deepcopy(SEARCH[arm]), arm_c if arm == "C" else arm_t
            reply, attempts, kind, reading = once(patch, arm, lambda share_left, meter, run=run, search=search: run(
                patch, search=search, share_left=share_left, meter=meter))
            if not kind and not _valid((reply or {}).get("own")):
                kind = TOOL_ERROR
            reply = reply or {}
            own = errored(kind, reply) if kind else reply["own"]
            arms[arm] = {"disposition": _search_disposition(arm_a, own), "own": own, "attempts": attempts,
                         "model_calls": reading["calls"], "meter": reading, "spent_cents": reply.get("spent_cents"),
                         "reasons": list(reply.get("reasons") or []), "findings": list(reply.get("findings") or []),
                         "attribution": _attribution(arm_a, own, reply),
                         "proposer_failure": reply.get("proposer_failure")}
            if reply.get("proposer_failure"):
                failures.append({"patch": patch["id"], "arm": arm, "reason": reply["proposer_failure"],
                                 "category": arms[arm]["disposition"]["category"]})
        reply, attempts, kind, reading = once(patch, "E", lambda share_left, meter: arm_e(
            patch, c_calls=arms["C"]["model_calls"], t_calls=arms["T"]["model_calls"], share_left=share_left,
            meter=meter))
        if not kind and not _valid((reply or {}).get("disposition")):
            kind = TOOL_ERROR
        reply = reply or {}
        arms["E"] = {**{key: value for key, value in reply.items() if key not in ("disposition", "findings")},
                     "disposition": errored(kind, reply) if kind else reply["disposition"], "attempts": attempts,
                     "model_calls": reading["calls"], "meter": reading, "findings": list(reply.get("findings") or [])}
        for arm in ARMS:
            record.add("arm", {"patch": patch["id"], "arm": arm, "output": arms[arm]})
        rows.append({"patch": patch["id"], "draw_index": patch["draw_index"], "arm_a": arm_a, "arms": arms})

    reruns = []
    for patch, row in zip(patches, rows):
        for arm in ARMS:
            for reproduction in row["arms"][arm]["findings"]:
                found = {"patch": patch["id"], "arm": arm, "node": reproduction.get("node"),
                         "result": rerun(patch, arm, reproduction)}
                reruns.append(found)
                record.add("rerun", found)
    counts = {reason: sum(item["reason"] == reason for item in excluded) for reason in EXCLUDED}
    return {"patches": rows, "excluded": {"counts": counts, "patches": [dict(item) for item in excluded]},
            "reruns": reruns, "retries": retries, "proposer_failures": failures, "spending_differences": differences,
            "tallies": {arm: tally([row["arms"][arm]["disposition"] for row in rows]) for arm in ARMS},
            "spending": spending.report(), "record": record.final}
