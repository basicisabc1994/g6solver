# SPDX-License-Identifier: Apache-2.0
"""Round 2's one G6-side change (PLAN.md section 9, "Round 2: G6 with its own model"), made to the image's copy of G6
before it is installed, never to G6's repository.

At 9ad134d only the caller's ``search`` argument switches the proposer on. This change fixes the proposer's settings in
``g6.harness.verification_search.search_config`` at the code's own defaults with ``propose`` on, so every review asks
G6's model for checks whatever the agent passes; every other ``search`` setting stays as the caller gives it. Each
anchor must be found exactly once, or nothing is written and the script fails.

    python3 round2_g6.py <path to src/g6/harness/verification_search.py>
"""
from __future__ import annotations

import sys
from pathlib import Path

FORCED = ('#: Worked examples, round 2 (PLAN.md section 9): the proposer on for every review, at the code\'s own default\n'
          '#: limits, whatever the caller passes.\n'
          'WORKED_EXAMPLES_ROUND2 = {"propose": True, "proposer": PROPOSER_TEMPLATES_FIRST, "max_model_calls": 10,\n'
          '                          "max_input_chars": 60_000, "max_output_tokens": 4_096, "max_context_chars": 24_000}\n'
          '\n\n')
EDITS = (
    ("def search_config(value) -> dict:\n", FORCED + "def search_config(value) -> dict:\n"),
    ("    if value is None:\n        return settings\n",
     "    if value is None:\n        return {**settings, **WORKED_EXAMPLES_ROUND2}\n"),
    ("    return {**settings, **value}\n", "    return {**settings, **value, **WORKED_EXAMPLES_ROUND2}\n"),
)


def patch(text: str) -> str:
    """``text`` with the three edits, or ``ValueError`` when an anchor is not found exactly once."""
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"anchor found {text.count(old)} times, not once: {old.splitlines()[0]!r}")
        text = text.replace(old, new)
    return text


def main(argv: list[str]) -> int:
    target = Path(argv[1])
    data = target.read_bytes().decode("utf-8")
    try:
        patched = patch(data)
    except ValueError as exc:
        print(f"round2_g6: {exc}", file=sys.stderr)
        return 1
    target.write_bytes(patched.encode("utf-8"))
    print("round2_g6: the proposer is on for every review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
