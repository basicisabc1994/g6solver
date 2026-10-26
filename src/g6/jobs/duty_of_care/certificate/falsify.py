# SPDX-License-Identifier: Apache-2.0
"""Seeded answer vectors for the PHQ-9 and GAD-7 totals, valid and not, and the invariants the forms and rules state.

Inputs: every item 0 to 3 (the PHQ-9's item 9 mostly 0, so the sum itself is exercised); one item with no answer or
one answer short; one answer off the scale; one answer too many; and, for most inputs, one answered item to raise by
one. Invariants: the total is the item sum, inside 0 to 27 (PHQ-9) or 0 to 21 (GAD-7); raising one item by one
raises the total by one while both answer lists are scored; any PHQ-9 item 9 answer other than 0 gives the crisis
result (-2) whatever the total; anything else gives no total (-1).
"""
import random

ITEMS = {"phq9_total": 9, "gad7_total": 7}
MAXIMUM = {"phq9_total": 27, "gad7_total": 21}
SCALE = (0, 1, 2, 3)
REFUSED = -1
CRISIS = -2


def generate(rule, seed, count):
    rng = random.Random(seed)
    items = ITEMS[rule]
    out = []
    for index in range(count):
        kind = index % 5
        answers = [rng.choice(SCALE) for _ in range(items)]
        if rule == "phq9_total" and kind != 4:
            answers[8] = 0
        if kind == 1 and rng.randrange(2):
            answers[rng.randrange(items)] = None
        elif kind == 1:
            answers = answers[:-1]
        elif kind == 2:
            answers[rng.randrange(items)] = rng.choice([4, 5, 7, -1])
        elif kind == 3:
            answers = answers + [rng.choice(SCALE)]
        case = {"answers": answers}
        answered = [i for i, a in enumerate(answers) if type(a) is int]
        if answered and rng.randrange(4):
            case["raise"] = rng.choice(answered)
        out.append(case)
    return out


def _kind(rule, answers):
    if rule == "phq9_total" and len(answers) >= 9 and answers[8] is not None and answers[8] != 0:
        return "crisis"
    scored = len(answers) == ITEMS[rule] and all(type(a) is int and a in SCALE for a in answers)
    return "sum" if scored else "refused"


def _holds(rule, answers, observed):
    kind = _kind(rule, answers)
    if kind == "crisis":
        return observed == CRISIS
    if kind == "refused":
        return observed == REFUSED
    return observed == sum(answers) and 0 <= observed <= MAXIMUM[rule]


def invariant(rule, inputs, value):
    answers = list(inputs["answers"])
    if "raise" not in inputs:
        return _holds(rule, answers, value)
    raised = list(answers)
    raised[inputs["raise"]] = raised[inputs["raise"]] + 1
    if not (_holds(rule, answers, value["total"]) and _holds(rule, raised, value["raised"])):
        return False
    if _kind(rule, answers) == _kind(rule, raised) == "sum":
        return value["raised"] == value["total"] + 1
    return True
