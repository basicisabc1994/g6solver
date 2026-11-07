# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Measuring the question slice (G6 0.3 design, section 5, row "A, the slice"; slice 3, items 6 and 7). Offline: nothing
here calls a model, starts an agent or a container, or reaches the network. It counts from records.

The simulated maintainer (``simulated_answer``) is for measurement only. It is deterministic: it runs the question's
scenario on the gold fix, as the example.v1 check of each outcome (``outcome_instances``), through the runner it is
given, and picks the outcome whose check passes there (exactly one), else ``neither``. It is recorded as
``answered_by="simulated:maintainer"``, never ``person:``, and is never normative in the product: slice 2's ``Answer``
refuses it, so no store takes it and no review enforces it. It knows the gold fix, so it is an upper bound on what a
person's answers can give, and every report says so (``UPPER_BOUND``).

``measure`` counts, for a cohort of cases, each with its denominator:

- unnecessary questions: asked where the task already decided the behaviour (the case's frozen ``task_decides``
  annotation, or the answer "the task already says");
- missed questions: a wrong final patch, with no confirming answer, that the case's frozen ``catchable`` annotation
  says a question would have caught;
- harmful answers: a confirming answer whose outcome is not the one the gold fix gives (``gold_option``) and a wrong
  final patch;
- the person's time: the dialog's seconds, for answers a person gave (``person:``) only.

The real measurement runs after this change's review loop, registered in PLAN.md before it runs, by the owner's rules
on spending; it needs a frozen cohort with these annotations and a runner for the gold fix.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from statistics import median
from typing import Any

SIMULATED = "simulated:maintainer"
UPPER_BOUND = ("answers from the simulated maintainer, which knows the gold fix: an upper bound on what a person's "
               "answers can give, never a person's answers and never normative in the product")
CONFIRMING = ("A", "B")


def outcome_instances(question: Mapping[str, Any]) -> dict[str, dict]:
    """Each outcome's example.v1 instance for the question's scenario: what ``simulated_answer``'s runner runs on the
    gold fix, one per outcome label (data only; template ``example.v1``, provenance ``stated``)."""
    found = {}
    for label, outcome in question["outcomes"].items():
        case = {"args": question["args"], **({"kwargs": question["kwargs"]} if question.get("kwargs") else {}),
                **outcome}
        found[label] = {"id": f"simulated-{label}", "template": "example.v1",
                        "requirement_id": question["requirement_id"], "intent": "change",
                        "bindings": {"function": question["function"], "cases": [case]}}
    return found


def simulated_answer(question: Mapping[str, Any], passes_on_gold: Callable[[dict], bool]) -> dict:
    """The simulated maintainer's answer: the outcome whose example.v1 check passes on the gold fix
    (``passes_on_gold``, the runner: a confined run there in the real measurement, a fake in tests), when exactly one
    does, else ``neither``. Recorded as ``SIMULATED``."""
    passing = [label for label, one in outcome_instances(question).items() if passes_on_gold(one)]
    return {"option": passing[0] if len(passing) == 1 else "neither", "answered_by": SIMULATED,
            "note": UPPER_BOUND}


def _rate(count: int, of: int) -> dict:
    return {"count": count, "of": of}


def measure(cases: Iterable[Mapping[str, Any]]) -> dict:
    """The slice's counts over ``cases`` (the module's docstring). Each case: ``case`` (its name), ``asked`` (whether
    G6 asked), ``answer`` (``{option, answered_by, seconds?}`` or None), ``gold_option`` (the option the gold fix
    gives, or None), ``task_decides`` and ``catchable`` (frozen annotations) and ``final_correct`` (the final patch
    passed the maintainers' tests)."""
    rows = list(cases)
    asked = [row for row in rows if row.get("asked")]
    answered = [row for row in asked if row.get("answer")]
    unnecessary = [row["case"] for row in asked if row.get("task_decides")
                   or (row.get("answer") or {}).get("option") == "task_already_says"]
    confirming = [row for row in answered if row["answer"].get("option") in CONFIRMING]
    wrong = [row for row in rows if not row.get("final_correct")]
    missed = [row["case"] for row in wrong if row.get("catchable") and row not in confirming]
    harmful = [row["case"] for row in confirming if not row.get("final_correct")
               and row.get("gold_option") is not None and row["answer"]["option"] != row["gold_option"]]
    seconds = [float(row["answer"]["seconds"]) for row in answered
               if str(row["answer"].get("answered_by", "")).startswith("person:") and "seconds" in row["answer"]]
    simulated = sum(row["answer"].get("answered_by") == SIMULATED for row in answered)
    return {"cases": len(rows), "asked": _rate(len(asked), len(rows)),
            "unnecessary": {**_rate(len(unnecessary), len(asked)), "cases": unnecessary},
            "missed": {**_rate(len(missed), len(wrong)), "cases": missed},
            "harmful": {**_rate(len(harmful), len(confirming)), "cases": harmful},
            "person_time": {"answers": len(seconds), "seconds_total": round(sum(seconds), 3),
                            "seconds_median": round(median(seconds), 3) if seconds else None},
            "answered_by_simulated_maintainer": simulated,
            "note": UPPER_BOUND if simulated else "answers a person gave in the client's dialog",
            "model_calls": 0}


__all__ = ["SIMULATED", "UPPER_BOUND", "measure", "outcome_instances", "simulated_answer"]
