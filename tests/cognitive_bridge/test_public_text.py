# SPDX-License-Identifier: Apache-2.0
"""The bridge's words claim no more than its code: the checker that runs in this process is not called independent,
and its public text (docs, skill, examples, the package's own strings) names no tool that wrote it, uses none of the
words G6 keeps for what a check cannot give, and names ``verified`` only as G6's state, in backticks."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import g6.cognitive_bridge as bridge

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = sorted([*(ROOT / "docs" / "cognitive_bridge").glob("*.md"),
                 *(ROOT / "examples" / "cognitive_bridge").glob("*.*"),
                 *(ROOT / "src" / "g6" / "cognitive_bridge").rglob("*.py"),
                 ROOT / "src" / "g6" / "cognitive_bridge" / "skill" / "SKILL.md"])
#: The tool that wrote the PR's first draft, and the words G6 keeps out of what it says of a check (the verb for
#: establishing beyond doubt, in each form, its noun, and the maturity word for untried work).
BARRED = re.compile(r"(?i)chatgpt|\bpro(?:v(?:e|ed|en|es|ing|ably)|ofs?)\b|experiment(?:al)")
#: ``verified`` said of something, rather than G6's state or field named as a token (in backticks or quotes, as the code
#: names the key it drops); G6's own warning card carries the flag ``"verified": false`` (INV-ENV-2), which says the
#: opposite, so the example dossier's cards may show it.
BARE_VERIFIED = re.compile(r'(?<![`"\'])\bverified\b(?![`"\'])')


def test_the_checker_that_runs_in_process_is_named_for_what_it_is():
    # 9ebfbbe called it IndependentAssuranceEvaluator, while its own docstring said it cannot attest its isolation.
    assert not hasattr(bridge, "IndependentAssuranceEvaluator")
    doc = inspect.getdoc(bridge.InProcessAssuranceEvaluator) or ""
    assert "in this Python process" in doc and "does not isolate" in doc


def test_the_public_text_claims_no_more_than_the_code():
    # 9ebfbbe's README said "No repository changes were made by this ChatGPT run", and its text used the barred words
    # and said "verified" of its own results.
    assert PUBLIC
    hits = [f"{path.relative_to(ROOT).as_posix()}:{n}: {match.group(0)}"
            for path in PUBLIC for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            for pattern in (BARRED, BARE_VERIFIED) for match in pattern.finditer(line)]
    assert hits == []
