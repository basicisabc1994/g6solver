# SPDX-License-Identifier: Apache-2.0
"""Round 2's G6-side change (PLAN.md section 9, "Round 2: G6 with its own model"), made to the image's copy of G6
before it is installed, never to G6's repository.

At 9ad134d only the caller's ``search`` argument switches the proposer on. Two edits make every review ask G6's model
for checks, whatever the agent passes:

* ``g6.harness.verification_search.search_config`` fixes the proposer's settings at the code's own defaults with
  ``propose`` on; every other ``search`` setting stays as the caller gives it;
* ``g6.harness.patch_review._inputs`` builds the search configuration for every review (at 9ad134d it builds none
  when the caller passes neither ``search`` nor template instances, and then no proposer runs).

Each anchor must be found exactly once, or nothing is written and the script fails.

    python3 round2_g6.py <G6's src/g6/harness folder>
"""
from __future__ import annotations

import sys
from pathlib import Path

FORCED = ('#: Worked examples, round 2 (PLAN.md section 9): the proposer on for every review, at the code\'s own default\n'
          '#: limits, whatever the caller passes.\n'
          'WORKED_EXAMPLES_ROUND2 = {"propose": True, "proposer": PROPOSER_TEMPLATES_FIRST, "max_model_calls": 10,\n'
          '                          "max_input_chars": 60_000, "max_output_tokens": 4_096, "max_context_chars": 24_000}\n'
          '\n\n')
SEARCH_EDITS = (
    ("def search_config(value) -> dict:\n", FORCED + "def search_config(value) -> dict:\n"),
    ("    if value is None:\n        return settings\n",
     "    if value is None:\n        return {**settings, **WORKED_EXAMPLES_ROUND2}\n"),
    ("    return {**settings, **value}\n", "    return {**settings, **value, **WORKED_EXAMPLES_ROUND2}\n"),
)
REVIEW_EDITS = (
    ("    return stated, assumed, unmapped, proposed, instances, config\n",
     "    from g6.harness import verification_search   # worked examples, round 2: a search configuration for every review\n"
     "    config = config or verification_search.search_config(None)\n"
     "    return stated, assumed, unmapped, proposed, instances, config\n"),
)
FILES = (("verification_search.py", SEARCH_EDITS), ("patch_review.py", REVIEW_EDITS))


def patch(text: str, edits=SEARCH_EDITS) -> str:
    """``text`` with ``edits``, or ``ValueError`` when an anchor is not found exactly once."""
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f"anchor found {text.count(old)} times, not once: {old.splitlines()[0]!r}")
        text = text.replace(old, new)
    return text


def main(argv: list[str]) -> int:
    folder = Path(argv[1])
    try:
        patched = {name: patch((folder / name).read_bytes().decode("utf-8"), edits) for name, edits in FILES}
    except (OSError, ValueError) as exc:
        print(f"round2_g6: {exc}", file=sys.stderr)
        return 1
    for name, text in patched.items():   # written only once every edit has its anchor
        (folder / name).write_bytes(text.encode("utf-8"))
    print("round2_g6: the proposer is on for every review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
