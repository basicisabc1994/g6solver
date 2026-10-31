# SPDX-License-Identifier: Apache-2.0
"""The completion loop: when a coding agent says it has finished, test the work with checks it never saw, send failures
back in the same session until the work holds or the stop rule fires, and say what was tested and what remains unknown.

It is the worked examples' arm L9 (``validation/worked_examples/run.py``), moved into the product. Its text is data
(``HARNESS``, read by ``harness``; a test holds it to ``run.py``'s), and a later trained harness needs no code: a file
with the fields ``trained`` holds, the parts ``TRAINED`` names, adds to those parts as ``run.py``'s ``trained`` does.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from g6.cegis import InputDomain
from g6.harness import verification_search as vs

HARNESS = Path(__file__).with_name("data") / "completion_harness.json"
#: The parts of the loop harness training can add to: never the sets, the scoring, the reply forms or the stop rule.
TRAINED = ("fixer", "scout", "repair", "critic")
MAX_REPAIRS, MAX_CROSS, MIN_RANDOM, MAX_SHOWN, MAX_CASE_CHARS = 3, 200, 300, 12, 900
SCOUT_CAP_USD, CRITIC_SECONDS = 2.0, 1500


def literal(text: str) -> str:
    """``text`` for a template that ``format`` fills."""
    return text.replace("{", "{{").replace("}", "}}")


@dataclass(frozen=True)
class Harness:
    """The loop's text: each part with what training added to it."""
    scout: str
    hidden_scouts: tuple[str, ...]
    fixer: str
    repair: str
    critic: str
    critic_note: str


def harness(path: Path = HARNESS) -> Harness:
    """The harness in ``path``. Training can only add to a part's instructions: anything else in ``trained`` is refused."""
    data = json.loads(path.read_text(encoding="utf-8"))
    added = data.get("trained") or {}
    odd = sorted(set(added) - set(TRAINED))
    if odd:
        raise ValueError(f"harness training cannot change {odd}")

    def scout(text: str) -> str:
        add = added.get("scout")
        return text.replace("5. Critique:", f"   {literal(add)}\n5. Critique:", 1) if add else text

    fixer, repair, critic = added.get("fixer"), added.get("repair"), added.get("critic")
    return Harness(scout(data["scout"]), tuple(scout(text) for text in data["hidden_scouts"]),
                   data["fixer"] + (f"\n\n{fixer}" if fixer else ""),
                   data["repair"] + (f"\n\n{literal(repair)}" if repair else ""),
                   data["critic"].replace("</task>", f"4. {critic}\n</task>", 1) if critic else data["critic"],
                   data["critic_note"])


@dataclass
class ProbeRun:
    """One run of a probe set: pytest's own count of each case, so no reading of its console can hide a failure."""
    exit: int | None
    results: dict[str, str]
    texts: dict[str, str]
    tail: str = ""

    @property
    def ran(self) -> int:
        return sum(v in ("passed", "failed") for v in self.results.values())

    @property
    def usable(self) -> bool:
        return self.exit in (0, 1) and self.ran > 0


def split_failures(now: ProbeRun, base: ProbeRun | None) -> tuple[list[str], list[str]]:
    """G6's plan rule 3 (``classify``) on each failing case: one that passed at the base and fails now is a counterexample
    on what worked before; any other is what a reader says the fix still owes."""
    regressions, owed = [], []
    for case in sorted(k for k, v in now.results.items() if v in ("failed", "error")):
        before = base.results.get(case) if base else None
        verdict = vs.classify("preserve", {"result": "failed", "detail": ""},
                              {"result": before, "detail": ""} if before else None)
        (regressions if verdict["result"] == vs.COUNTEREXAMPLE else owed).append(case)
    return regressions, owed


def failures_of(sets: Iterable[tuple[str, ProbeRun, ProbeRun | None]]) -> tuple[set[str], str]:
    """The failing ids of ``(label, run now, run at the base)``, and what the agent reads: regressions first, each case in
    its own words, ``MAX_SHOWN`` at most. A set that no longer runs is reported, never clean."""
    failing: set[str] = set()
    broke, regress, owed = [], [], []
    for label, now, base in sets:
        if not now.usable:
            failing.add(f"{label}:exit {now.exit}" if now.exit is not None else f"{label}:no exit")
            broke.append(f"- the {label} probes no longer run on your code:\n{now.tail[-MAX_CASE_CHARS:]}")
            continue
        lost, still = split_failures(now, base)
        failing |= {f"{label}:{case}" for case in [*lost, *still]}
        regress += [(label, case, now.texts.get(case, "")) for case in lost]
        owed += [(label, case, now.texts.get(case, "")) for case in still]
    parts, left = list(broke), MAX_SHOWN
    for heading, cases in (("Passed before your change and fail now:", regress),
                           ("Failed before your change and still fail:", owed)):
        if cases:
            shown = cases[:left]
            left -= len(shown)
            parts.append("\n".join([heading, *(f"- {label}:{case}\n{text[:MAX_CASE_CHARS]}" for label, case, text in shown),
                                    *([f"(and {len(cases) - len(shown)} more)"] if len(cases) > len(shown) else [])]))
    return failing, "\n\n".join(parts)


def with_critic(found: tuple[set[str], str], critic: Mapping[str, str], note: str) -> tuple[set[str], str]:
    """What fails, with the critic's rule, reasons and test before it if the critic's own test is among the failures."""
    failing, text = found
    if not any(case.startswith("critic:") for case in failing):
        return failing, text
    return failing, note.format(**{k: critic.get(k, "") for k in ("rule", "why", "test")}) + "\n\n" + text


def critic_report(said: str, *, proof: str = "") -> dict[str, str]:
    """The critic's reply: its rule, verdict, reasons and test. Only what follows any progress lines counts, and a test
    comes with a verdict of defect or not at all. ``proof``: the line it must quote from its pack, because a critic that
    could not read the pack still answers, and "none" from it looks like a clean review."""
    body = said[said.rfind("[codex]"):].split("\n", 1)[-1] if "[codex]" in said else said
    if proof and not re.search(rf"^READ:\s*{re.escape(proof)}\s*$", body, re.M):
        return {"verdict": "unread", "rule": "", "why": "", "test": "", "error": f"the reply does not quote {proof}"}
    verdict = re.search(r"^VERDICT:\s*(\w+)", body, re.M)
    rule = re.search(r"^RULE:\s*(.*?)(?=^VERDICT:)", body, re.M | re.S)
    why = re.search(r"^WHY:\s*(.*?)(?=^```|\Z)", body, re.M | re.S)
    test = re.search(r"^```python\n(.*?)^```", body, re.M | re.S)
    said_defect = bool(verdict) and verdict[1].lower() == "defect"
    return {"verdict": verdict[1].lower() if verdict else "unreadable", "rule": rule[1].strip() if rule else "",
            "why": why[1].strip() if why else "", "test": test[1].strip() if test and said_defect else "", "error": ""}


def scout_gate(raw: str, *, exit_code: int, collected: int, broken: int = 0, randoms: int | None = None,
               executed: int | None = None) -> list[str]:
    """What is wrong with a scout's probes, by G6's own count: ``InputDomain`` says how many combinations the declared
    dimensions make, up to ``MAX_CROSS``. Empty when nothing is."""
    wrong = []
    if executed == 0:
        wrong.append("no case of test_cross ran: a skipped case tests nothing")
    if randoms is not None and randoms < MIN_RANDOM:
        wrong.append(f"test_random has {randoms} of {MIN_RANDOM} generated inputs")
    if broken:
        wrong.append(f"{broken} probes fail with an error, not an assertion: they test nothing until they run")
    if exit_code not in (0, 1):
        wrong.append(f"the probes could not run (pytest exit {exit_code}): make them importable from the repository")
    elif exit_code == 0:
        wrong.append("every probe passes on the unfixed code, so none fails: reproduce the issue's symptom")
    try:
        dimensions = json.loads(raw).get("dimensions") or {}
        domain = InputDomain.of(dimensions, max_inputs=MAX_CROSS)
    except (ValueError, TypeError, AttributeError):
        dimensions, domain = {}, None
    if domain is None or len(dimensions) < 2:
        wrong.append('tree.json needs "dimensions" with at least two dimensions')
    elif collected < min(domain.size, MAX_CROSS):
        wrong.append(f"test_cross has {collected} of {min(domain.size, MAX_CROSS)} combinations of the dimensions")
    return wrong
