# SPDX-License-Identifier: Apache-2.0
"""Bundle: Code review with receipts — an AI reviewer's findings, each checked against the code you pasted.

Needs a model. The reviewer must quote the exact line for every finding. G6 then confirms the quote
really is in your code (and where), and marks findings it cannot locate as "unconfirmed" — reviewers
invent line numbers and code surprisingly often. A located finding is still an opinion about the code,
so the best label is ``qualified-draft``; nothing is run.
"""
from __future__ import annotations

from typing import Any

from g6.bundles import Bundle, BundleResult, Field

SYSTEM = (
    "You are a careful security and correctness reviewer. Review the code. Report only real problems "
    "(injection, unsafe deserialisation, secrets in code, missing input validation, race conditions, "
    "unhandled errors, logic bugs). Reply with one JSON object: "
    '{"findings": [{"title": "<short>", "quote": "<one line copied exactly from the code>", '
    '"why": "<what can go wrong>", "fix": "<concrete change>", "severity": "high|medium|low"}]}. '
    'If there are none, reply {"findings": []}.'
)
_SEVERITY = {"high": 0, "medium": 1, "low": 2}
_MAX_CODE = 60_000


def locate(quote: str, code: str) -> int | None:
    """1-based line of ``quote`` in ``code`` (whitespace-insensitive, whole quote within one line)."""
    want = " ".join(quote.split())
    if len(want) < 6:
        return None
    for n, line in enumerate(code.splitlines(), 1):
        if want in " ".join(line.split()):
            return n
    return None


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.core.llm import LLMError
    from g6.reasoning import first_json_object

    code = inputs.get("code", "")
    if not code.strip():
        return BundleResult("blocked-escalated", "Paste the code to review.")
    if len(code) > _MAX_CODE:
        return BundleResult("blocked-escalated", f"Paste at most {_MAX_CODE:,} characters (one file or a few).")
    if llm is None:
        return BundleResult("blocked-escalated", "Choose an AI model first (AI model panel above).")
    try:
        findings = first_json_object(llm.complete(SYSTEM, code, max_tokens=3000)).get("findings")
    except LLMError as exc:
        return BundleResult("blocked-escalated", f"The AI model could not review the code: {exc}")
    except ValueError:
        return BundleResult("blocked-escalated", "The AI model's reply was not in the expected format; try again.")
    if not isinstance(findings, list):
        return BundleResult("blocked-escalated", "The AI model's reply had no findings list; try again.")
    rows = []
    for f in findings[:30]:
        if not isinstance(f, dict) or not str(f.get("title", "")).strip():
            continue
        line = locate(str(f.get("quote", "")), code)
        severity = str(f.get("severity", "")).lower()
        rows.append({
            "finding": str(f["title"]).strip(),
            "where": f"line {line}" if line else "unconfirmed — quote not found in your code",
            "why": str(f.get("why", "")).strip() or "—",
            "suggested fix": str(f.get("fix", "")).strip() or "—",
            "severity": severity if severity in _SEVERITY else "unrated",
            "label": "qualified-draft" if line else "blocked-escalated",
        })
    rows.sort(key=lambda r: (r["label"] != "qualified-draft", _SEVERITY.get(r["severity"], 3)))
    located = sum(r["label"] == "qualified-draft" for r in rows)
    notes = ["An AI review finds some problems and misses others; no findings does not mean the code is safe.",
             "'Unconfirmed' findings quote code that is not in what you pasted — treat them as likely invented."]
    if not rows:
        return BundleResult("qualified-draft", "The reviewer reported no problems.", [], notes)
    return BundleResult(
        "qualified-draft" if located else "blocked-escalated",
        f"{len(rows)} finding(s); {located} located in your code, {len(rows) - located} unconfirmed.",
        rows, notes,
    )


BUNDLE = Bundle(
    id="code-review",
    title="Code review with receipts",
    summary="An AI security and bug review where every finding must point at a real line of your code.",
    fields=(Field("code", "Your code", placeholder="def login(user, pw):\n    q = f\"SELECT * FROM users WHERE name='{user}'\"\n    ...",
                  help="Paste one file or a few functions. Nothing is run."),),
    run=run,
    needs_model=True,
)
