# SPDX-License-Identifier: Apache-2.0
"""Bundle: Plan pre-mortem — imagine the plan has failed, list why, and how you would see it coming.

Needs a model. The model proposes failure stories; G6 then holds each one to a structure (an early
warning sign you can observe and a cheap test you can run this month) and checks every statement the
model makes about *your plan* against the plan's own text, marking it "from your plan" or "assumption".
Risks are ideas, not findings, so the best label is ``qualified-draft``.
"""
from __future__ import annotations

from typing import Any

from g6.bundles import Bundle, BundleResult, Field

SYSTEM = (
    "You run a pre-mortem. Assume the plan below was carried out and failed badly one year from now. "
    "List the 5 to 8 most likely reasons. Reply with one JSON object: "
    '{"risks": [{"risk": "<what went wrong>", "plan_fact": "<the sentence of the plan this risk is about, '
    'quoted exactly, or empty>", "early_signal": "<an observable sign within 3 months>", '
    '"cheap_test": "<a test costing under a week that would reveal it>", "severity": "high|medium|low"}]}'
)
_SEVERITY = {"high": 0, "medium": 1, "low": 2}


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.core.llm import LLMError
    from g6.grounding import Passage, check_claim
    from g6.reasoning import first_json_object

    plan = inputs.get("plan", "").strip()
    if len(plan.split()) < 20:
        return BundleResult("blocked-escalated", "Describe the plan in at least a few sentences (20+ words).")
    if llm is None:
        return BundleResult("blocked-escalated", "Choose an AI model first (AI model panel above).")
    try:
        reply = llm.complete(SYSTEM, f"PLAN:\n{plan}", max_tokens=2500)
        risks = first_json_object(reply).get("risks")
    except LLMError as exc:
        return BundleResult("blocked-escalated", f"The AI model could not answer: {exc}")
    except ValueError:
        return BundleResult("blocked-escalated", "The AI model's reply was not in the expected format; try again.")
    if not isinstance(risks, list) or not risks:
        return BundleResult("blocked-escalated", "The AI model returned no risks; try again or add detail.")
    source = [Passage(plan, "your plan")]
    rows, incomplete = [], 0
    for item in risks[:12]:
        if not isinstance(item, dict) or not str(item.get("risk", "")).strip():
            continue
        signal, test = str(item.get("early_signal", "")).strip(), str(item.get("cheap_test", "")).strip()
        fact = str(item.get("plan_fact", "")).strip()
        if fact:
            check = check_claim(fact, source)
            basis = "from your plan" if check.status in ("quoted", "supported") else \
                "assumption (not in your plan)" if check.status != "conflict" else "contradicts your plan"
        else:
            basis = "general risk"
        if not signal or not test:
            incomplete += 1
        severity = str(item.get("severity", "")).lower()
        rows.append({
            "risk": str(item["risk"]).strip(),
            "about your plan": f"{basis}: “{fact}”" if fact else basis,
            "early warning sign": signal or "MISSING — ask: how would you notice this early?",
            "cheap test": test or "MISSING — ask: what quick test would reveal this?",
            "severity": severity if severity in _SEVERITY else "unrated",
        })
    if not rows:
        return BundleResult("blocked-escalated", "The AI model returned no usable risks; try again.")
    rows.sort(key=lambda r: _SEVERITY.get(r["severity"], 3))
    state = "blocked-escalated" if incomplete == len(rows) else "qualified-draft"
    return BundleResult(
        state,
        f"{len(rows)} ways this plan could fail; {len(rows) - incomplete} come with a sign to watch and a test to run.",
        rows,
        ["These are possibilities proposed by an AI model, not findings. Use them to decide what to test first.",
         "'From your plan' means the risk refers to something your plan actually says; 'assumption' means the "
         "model said something about your plan that your text does not contain — check it."],
    )


BUNDLE = Bundle(
    id="premortem",
    title="Plan pre-mortem",
    summary="Imagine your plan failed: see the likely reasons, the early warning signs and a cheap test for each.",
    fields=(Field("plan", "Your plan",
                  placeholder="We will launch a meal-kit subscription in Brisbane in March, priced at $79/week, "
                              "marketed on Instagram, delivered by two contract drivers...",
                  help="A few paragraphs: what, for whom, price, how you will reach people, who does the work, "
                       "money and timeline."),),
    run=run,
    needs_model=True,
)
