# SPDX-License-Identifier: Apache-2.0
"""An independent PHQ-9 and GAD-7 total, written from the forms and the rule texts alone; it imports nothing.

Each form has its items (nine and seven) and one scale, 0 to 3 (Not at all, Several days, More than half the days,
Nearly every day), and its office-coding line adds the item scores. A PHQ-9 item 9 answer other than 0 gives the
crisis result whatever the total (-2); an item with no answer, an answer off the scale or more answers than items
gives no total (-1).
"""
ITEMS = {"phq9_total": 9, "gad7_total": 7}
SCALE = (0, 1, 2, 3)
REFUSED = -1
CRISIS = -2


def outcome(rule, answers):
    """The total, or the encoded crisis result or refusal, for one list of answers."""
    if rule == "phq9_total" and len(answers) >= 9 and answers[8] is not None and answers[8] != 0:
        return CRISIS
    if len(answers) != ITEMS[rule]:
        return REFUSED
    total = 0
    for answer in answers:
        if type(answer) is not int or answer not in SCALE:
            return REFUSED
        total += answer
    return total


def evaluate(rule, inputs):
    answers = list(inputs["answers"])
    total = outcome(rule, answers)
    if "raise" not in inputs:
        return total
    raised = list(answers)
    raised[inputs["raise"]] = raised[inputs["raise"]] + 1
    return {"total": total, "raised": outcome(rule, raised)}
