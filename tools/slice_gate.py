# SPDX-License-Identifier: Apache-2.0
"""The eight-question gate on a slice brief (design 17.1; docs/product_global_specs/slice_gate.md).

A brief answers a question under a ``### Gate: <name>`` heading. ``unanswered`` names every question the brief
leaves without an answer; an empty list means the brief may go out. These are the builder's questions: nothing
here asks the person anything.

    python tools/slice_gate.py <brief.md> [...]     exit 1 and the names, when a brief leaves a question open
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

QUESTIONS = ("outcome", "constraint_hypothesis", "observations_for_and_against", "simpler_alternative",
             "removes", "checked_by", "value_measured_by", "constraint_added")
# the questions as the design words them; an answer that only repeats one is not an answer
QUESTION_TEXT = {
    "outcome": "Which outcome does it improve?",
    "constraint_hypothesis": "Which workflow constraint hypothesis does it address?",
    "observations_for_and_against": "What observations support it, and what could count against it?",
    "simpler_alternative": "Why is it better than a simpler alternative?",
    "removes": "What effort, risk, delay, cost or uncertainty does it remove?",
    "checked_by": "How is its working checked?",
    "value_measured_by": "How is end-to-end value measured?",
    "constraint_added": "Does it add a constraint of its own?",
}
_PLACEHOLDERS = frozenset({"", "na", "none", "tbd", "todo", "unknown", "nothing", "pending"})
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_GATE = re.compile(r"^gate:\s*(\S+)$", re.IGNORECASE)
_HYPOTHESIS = re.compile(r"^\W*hypothesis\s*:(.*)$", re.IGNORECASE | re.DOTALL)
_PLANNED = re.compile(r"^\W*planned check\s*:", re.IGNORECASE | re.MULTILINE)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _said(text: str, name: str) -> bool:
    """Whether ``text`` says something: not empty, not a placeholder, not the question handed back."""
    words = _words(text)
    return (words.replace(" ", "") not in _PLACEHOLDERS
            and words not in {_words(QUESTION_TEXT[name]), _words(name)})


def _answers(brief_text: str) -> dict[str, list[str]]:
    """Each question's answers: the text from its heading to the next heading of any level."""
    found: dict[str, list[str]] = {}
    name, lines = "", []
    for line in [*brief_text.splitlines(), "# end"]:
        heading = _HEADING.match(line)
        if not heading:
            lines.append(line)
            continue
        if name:
            found.setdefault(name, []).append("\n".join(lines))
        gate = _GATE.match(heading.group(1))
        name, lines = (gate.group(1).lower() if gate else ""), []
    return found


def _answered(text: str, name: str) -> bool:
    hypothesis = _HYPOTHESIS.match(text.strip())
    if not hypothesis:
        return _said(text, name)
    parts = _PLANNED.split(hypothesis.group(1), maxsplit=1)   # an unknown answer is a hypothesis with its check
    return len(parts) == 2 and _said(parts[0], name) and _said(parts[1], name)


def unanswered(brief_text: str) -> list[str]:
    """The names from QUESTIONS with no answer in ``brief_text``; ``[]`` means the brief may go out."""
    answers = _answers(brief_text)
    return [name for name in QUESTIONS
            if not answers.get(name) or not all(_answered(text, name) for text in answers[name])]


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__)
        return 2
    open_ = 0
    for path in paths:
        missing = unanswered(Path(path).read_text(encoding="utf-8-sig"))
        if missing:
            open_ += 1
            print(f"{path}: unanswered: {', '.join(missing)}")
    return 1 if open_ else 0


if __name__ == "__main__":
    raise SystemExit(main())
