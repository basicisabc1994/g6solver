# SPDX-License-Identifier: Apache-2.0
"""The tick's own root cause analysis: why a tick adopted or refused what it did, read from its records (loop plan Part 24).

``method_rca`` reads how tuning went; this reads what the tick's evidence says about the change it raced and the gate it met,
which is what a person did by hand for stage 3's refused tick. Each cause is a plain-code measure with a stated line, so nothing
here is a model's opinion:

* ``threshold_move``: on the held-out split, every win of the candidate is on one label and at least 80% of its losses are on
  another. It traded one error for another, which is not a gain, and the method should score both kinds of error.
* ``split_mix_skew``: the labels of the cases the base failed while tuning differ from those of the cases it failed on the
  held-out split by more than 25 points, so the tuning split taught the wrong lesson. A person cuts the splits by label too.
* ``race_noise``: the race that chose the champion chose it on noise, its margin over the base not clear of a one-sided sign test
  at 0.05, or it dropped a candidate as good as the one it kept on the cases both ran. No held-out evaluation should be spent
  on such a margin.
* ``edit_space_short``: every candidate changed only wording or examples, and none was adopted. The failures may need an action
  the profile cannot express, which only a person can add, as a new version of the domain.
* ``gate_underpowered``: the gate refused a candidate that did not lose, with less than an even chance of seeing a 10-point
  gain. A person grows the held-out split before the next real evaluation. A candidate that lost is not refused for want of
  cases.

Every remedy is a person's (``WHO``), so the analysis informs; it changes nothing by itself, and nothing of it reaches a proposer,
as it reads the held-out split. The order above is the order causes are given in: the first explains a refusal most directly. Each cause is also a prediction
(``lessons.Prediction``), that with its remedy applied its measure is back across its line at the next tick, and ``scored``
scores it there. ``analysis_is_the_constraint`` says when two ticks in a row scored a remedy that did not remove its cause: then
the analysis itself is what limits the loop, the reflexive question of Part 20 made a count.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.metaharness.lessons import FAILED, HELD, OPEN, Prediction, score
from g6.reasoning.compare import sign_test_p

CAUSES = ("threshold_move", "split_mix_skew", "race_noise", "edit_space_short", "gate_underpowered")
#: The profile fields that change only what a model is told, never what the harness does.
WORDING = frozenset({"prompt", "prompt_layout", "skill_text", "hints", "exemplars"})
TRADE, SKEW, NOISE, POWER = 0.8, 0.25, 0.05, 0.5
_JUST_UNDER = 1e-9
#: Who applies each remedy. Each is a person's for now. Scoring candidates on both kinds of error, or spending no held-out
#: evaluation on a noisy race, changes the method a person started the loop with. Telling proposers which error rose would hand
#: them a held-out result, which the loop keeps from them (``loop._exploit``). The splits and the domain are a person's already.
WHO = dict.fromkeys(("threshold_move", "split_mix_skew", "race_noise", "edit_space_short", "gate_underpowered"), "person")
#: What a person changes to apply each remedy, among the parts of ``TickRecord.enrolment``: the remedy counts as applied once
#: one of these has changed, and a change to another part says nothing of it.
APPLIED_BY = {"threshold_move": ("method",), "split_mix_skew": ("cut",), "race_noise": ("method", "cut"),
              "edit_space_short": ("domain",), "gate_underpowered": ("cut",)}
_PLACES = 9      # a measure is compared at this many decimal places, so a share of exactly 25 points is 25 points


@dataclass(frozen=True)
class TickRecord:
    """What one tick's records say, as ``tick_rca`` reads it. ``labels``: each case's label. ``tuning_failed``: the tuning cases
    the base failed. ``held_out``: each held-out case's (id, base right, candidate right), empty when none was evaluated.
    ``races``: each race's ``winner`` and ``ranking`` rows (``candidate``, ``wins``, ``losses``, ``n``, ``dropped_at_block``,
    ``net_by_block``). ``candidates``: each raced candidate's changed ``fields``. ``power``: the held-out report's, by gain.
    ``enrolment``: the digest of each part of what a person decides (``method``; ``cut``; ``domain``, what a profile may
    express), each moving only when they change it, so a person's remedy counts as applied once its part has (``APPLIED_BY``)."""

    outcome: str
    labels: Mapping[str, str] = field(default_factory=dict)
    tuning_failed: Sequence[str] = ()
    held_out: Sequence[tuple[str, bool, bool]] = ()
    races: Sequence[Mapping[str, Any]] = ()
    candidates: Sequence[Mapping[str, Any]] = ()
    power: Mapping[str, float] = field(default_factory=dict)
    enrolment: Mapping[str, str] = field(default_factory=dict)
    champion: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "enrolment", _parts(self.enrolment))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TickRecord:
        """From the shape a trimmed tick's records take (the fixture of stage 3's tick has it)."""
        report = data.get("report") or {}
        return cls(outcome=str(data.get("outcome", "")), labels=dict(data.get("labels") or {}),
                   tuning_failed=tuple(data.get("tuning_failed") or ()),
                   held_out=tuple((c["case"], bool(c["base"]), bool(c["candidate"])) for c in report.get("cases") or ()),
                   races=tuple({"winner": s.get("winner"), "ranking": list(s.get("ranking") or ())}
                               for s in data.get("selection") or ()),
                   candidates=tuple(data.get("candidates") or ()), power=dict(report.get("power") or {}),
                   enrolment=dict(data.get("enrolment") or {}), champion=str(data.get("champion") or ""))


@dataclass(frozen=True)
class TickCause:
    code: str
    summary: str
    measure: float
    check: str
    remedy: str
    who: str
    prediction: Prediction
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TickDiagnosis:
    causes: tuple[TickCause, ...]
    outcome: str
    enrolment: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "enrolment": dict(self.enrolment),
                "causes": [{"code": c.code, "summary": c.summary, "measure": c.measure, "check": c.check, "remedy": c.remedy,
                            "who": c.who, "expected": c.prediction.expected, "evidence": c.evidence} for c in self.causes]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TickDiagnosis:
        """A diagnosis as a cycle's row keeps it. A cause this code does not know is left out, as nothing could score it."""
        enrolment = _parts(data.get("enrolment"))
        causes = tuple(TickCause(c["code"], c["summary"], float(c["measure"]), c["check"], c["remedy"], c["who"],
                                 Prediction(c["code"], c["code"], float(c["measure"]), float(c["expected"])),
                                 dict(c.get("evidence") or {}))
                       for c in data.get("causes") or () if c.get("code") in _RULES)
        return cls(causes, str(data.get("outcome", "")), enrolment)


def _parts(enrolment: Any) -> dict[str, str]:
    """``enrolment`` by part. One digest, as a row written before the parts were kept holds, stands for every part."""
    if isinstance(enrolment, str):
        return dict.fromkeys(("method", "cut", "domain"), enrolment)
    return {str(k): str(v) for k, v in dict(enrolment or {}).items()}


def _shares(cases: Iterable[str], labels: Mapping[str, str]) -> dict[str, float]:
    counts = Counter(labels.get(case, "unknown") for case in cases)
    total = sum(counts.values())
    return {label: count / total for label, count in counts.items()} if total else {}


def _trade(record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    wins = [case for case, base, candidate in record.held_out if candidate and not base]
    losses = [case for case, base, candidate in record.held_out if base and not candidate]
    if not record.held_out or any(case not in record.labels for case in (*wins, *losses)):
        return None, {}               # a case with no label tells nothing of which error moved
    won, lost = Counter(record.labels[c] for c in wins), Counter(record.labels[c] for c in losses)
    evidence = {"wins": dict(won), "losses": dict(lost)}
    if not wins or not losses or len(won) != 1:
        return 0.0, evidence
    label = next(iter(won))
    return max((count for other, count in lost.items() if other != label), default=0) / len(losses), evidence


def _skew(record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    unseen_failed = [case for case, base, _ in record.held_out if not base]
    if any(case not in record.labels for case in (*record.tuning_failed, *unseen_failed)):
        return None, {}               # a case with no label tells nothing of the mix
    tuned, unseen = _shares(record.tuning_failed, record.labels), _shares(unseen_failed, record.labels)
    if not tuned or not unseen:
        return None, {}
    gap = max(abs(tuned.get(label, 0.0) - unseen.get(label, 0.0)) for label in {*tuned, *unseen})
    return gap, {"tuning": {k: round(v, 3) for k, v in sorted(tuned.items())},
                 "held_out": {k: round(v, 3) for k, v in sorted(unseen.items())}}


def _net(row: Mapping[str, Any]) -> int:
    return int(row.get("wins", 0)) - int(row.get("losses", 0))


def _level_with(row: Mapping[str, Any], winner: Mapping[str, Any]) -> bool | None:
    """Whether a dropped ``row`` did as well as ``winner`` on the cases both ran, or None when the rows cannot say. Every live
    candidate runs every case of a block, so a row dropped after block b shared blocks 1 to b with the winner: its net is
    compared with the winner's after that block, or with the winner's whole net when both ran as many cases."""
    block, nets = row.get("dropped_at_block"), winner.get("net_by_block") or ()
    if isinstance(block, int) and 1 <= block <= len(nets):
        return _net(row) >= int(nets[block - 1])
    if row.get("n") is not None and row.get("n") == winner.get("n"):
        return _net(row) >= _net(winner)
    return None


def _noise(record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    chosen = [race for race in record.races if race.get("winner")]
    race = next((r for r in reversed(chosen) if record.champion and r["winner"] == record.champion),   # the one that chose it last
                chosen[-1] if chosen else None)
    if race is None:
        return None, {}
    rows = race.get("ranking") or []
    winner = next((row for row in rows if row.get("candidate") == race["winner"]), None)
    if winner is None:
        return None, {}
    level = [_level_with(row, winner) for row in rows if row is not winner and row.get("dropped_at_block") is not None]
    tie_dropped = any(found is True for found in level)
    p = sign_test_p(int(winner.get("wins", 0)), int(winner.get("losses", 0)))
    return (1.0 if tie_dropped else p), {"margin": _net(winner), "p_one_sided": round(p, 6), "tie_dropped": tie_dropped,
                                         "ties_unmeasured": sum(found is None for found in level)}


def _structural(record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    if not record.candidates:
        return None, {}
    fields = sorted({str(f) for c in record.candidates for f in c.get("fields") or ()})
    return float(sum(bool(set(c.get("fields") or ()) - WORDING) for c in record.candidates)), {"fields": fields}


def _power(record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    if not record.held_out or "gain_0.10" not in record.power:
        return None, {}
    wins = sum(candidate and not base for _, base, candidate in record.held_out)
    losses = sum(base and not candidate for _, base, candidate in record.held_out)
    return float(record.power["gain_0.10"]), {"wins": wins, "losses": losses, "n": len(record.held_out)}


#: Each cause: its measure, whether the measure names it, the value its prediction expects, and what is said of it.
_RULES: dict[str, tuple[Callable[[TickRecord], tuple[float | None, dict[str, Any]]], Callable[[float, TickRecord, dict], bool],
                        float, str, str, str]] = {
    "threshold_move": (_trade, lambda m, r, e: m >= TRADE, TRADE - _JUST_UNDER,
                       "every held-out win is on one label and {m:.0%} of the losses are on another: a trade between errors",
                       "the next held-out evaluation's wins and losses do not fall on opposite labels",
                       "score candidates on both kinds of error, missed defects weighted, and tell proposers which error rose"),
    "split_mix_skew": (_skew, lambda m, r, e: m > SKEW, SKEW,
                       "the base's tuning failures and its held-out failures differ by {m:.0%} in their labels",
                       "after a new cut, the two mixes are within 25 points",
                       "cut the splits by label as well as by group, and enrol again"),
    "race_noise": (_noise, lambda m, r, e: m > NOISE, NOISE,
                   "the champion's race was not clear of noise (one-sided sign test {m:.2f}, or a tie was dropped)",
                   "the next champion's race margin is clear of a one-sided sign test at 0.05, with no tie dropped",
                   "spend no held-out evaluation on a race margin that noise explains; a larger selection split"),
    "edit_space_short": (_structural, lambda m, r, e: m == 0 and r.outcome != "adopted", 1.0,
                         "every candidate changed only wording or examples, and none was adopted",
                         "a later candidate changes what the harness does, not only what it says",
                         "a person reads the failure records for an action the profile cannot express, and adds it as a new "
                         "version of the domain"),
    "gate_underpowered": (_power, lambda m, r, e: m < POWER and r.outcome != "adopted" and e["wins"] >= e["losses"], POWER,
                          "the gate refused a candidate that did not lose, with {m:.0%} power to see a 10-point gain",
                          "the next real evaluation has at least an even chance of seeing a 10-point gain",
                          "grow the held-out split before the next real evaluation"),
}


def tick_rca(record: TickRecord) -> TickDiagnosis:
    """The causes ``record`` shows, in ``CAUSES`` order."""
    found = []
    for code in CAUSES:
        measure, names, expected, summary, check, remedy = _RULES[code]
        value, evidence = _measured(code, record)
        if value is None or not names(value, record, evidence):
            continue
        found.append(TickCause(code, summary.format(m=value), value, check, remedy, WHO[code],
                               Prediction(code, code, value, expected), evidence))
    return TickDiagnosis(tuple(found), record.outcome, dict(record.enrolment))


def _measured(code: str, record: TickRecord) -> tuple[float | None, dict[str, Any]]:
    value, evidence = _RULES[code][0](record)
    return (None if value is None else round(value, _PLACES)), evidence


def scored(before: TickDiagnosis, now: TickRecord) -> list[tuple[str, str]]:
    """Each cause of ``before`` scored on a later tick, ``now``: ``held`` when its measure is back across its line, ``failed``
    when it is not, and ``open`` when ``now`` cannot measure it or a person's remedy was not applied (nothing a person decides
    has changed), as a remedy nobody applied tells nothing of the analysis."""
    out = []
    for cause in before.causes:
        parts = APPLIED_BY.get(cause.code, ())
        if cause.who == "person" and all(now.enrolment.get(p) == before.enrolment.get(p) for p in parts):
            out.append((cause.code, OPEN))
            continue
        value, _ = _measured(cause.code, now)
        out.append((cause.code, score(cause.prediction, value)))
    return out


def analysis_is_the_constraint(history: Sequence[Sequence[str]]) -> bool:
    """Whether the last two ticks that decided anything each scored a remedy that failed. ``history``: each tick's outcomes from
    ``scored``, oldest first. A tick that decided nothing is skipped, and one with any failed remedy counts as failing."""
    decided = [FAILED in outcomes for outcomes in history if any(o in (HELD, FAILED) for o in outcomes)]
    return len(decided) >= 2 and decided[-1] and decided[-2]


__all__ = ["CAUSES", "TickCause", "TickDiagnosis", "TickRecord", "WORDING", "analysis_is_the_constraint", "scored", "tick_rca"]
