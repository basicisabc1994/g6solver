# SPDX-License-Identifier: Apache-2.0
"""Demo 2 — error detection and evidence-based revision.

An agent asks G6 whether its draft answer is backed by the sources it has, through the same ``g6_invoke`` reply an
MCP client gets. With only the shipping policy to hand, the refund sentences are not backed: the reply is
``blocked-escalated``, names the sentences and the words no source contains, and says what a person should do. Once the
refund policy is supplied the same draft is a ``qualified-draft``. A third run shows a source that contradicts the
draft (a different figure) is a conflict, not support.

    python examples/demo_2_evidence_revision.py
"""
from __future__ import annotations

import json

from g6.mcp import invoke_reply

DRAFT = "Refunds are issued within 14 days of a return. Orders over $50 ship free."
SHIPPING = {"shipping.md": "Orders over $50 ship free. Standard delivery takes 3 to 5 business days."}
REFUNDS = {"refunds.md": "Refunds are issued within 14 days of a return. Store credit never expires."}
WRONG = {"refunds.md": "Refunds are issued within 30 days of a return."}


def _show(title: str, sources: dict[str, str]) -> None:
    reply = invoke_reply("grounding", "check_claims", json.dumps({"text": DRAFT, "sources": sources}))
    print(title)
    print(f"  sources:     {sorted(sources)}")
    print(f"  label:       {reply['completion_state']}")
    card = reply.get("warning_card") or {}
    print(f"  code:        {card.get('code', '-')}")
    for check in reply["result"]["checks"]:
        line = f"  - {check['status']:<10} {check['claim']!r}"
        if check["status"] in ("not_found", "partial") and check["missing_terms"]:
            line += f"  (word stems no source has: {', '.join(check['missing_terms'])})"
        if check["status"] == "conflict":
            line += f"  ({'; '.join(check['conflicts'])})"
        print(line)
    checks = reply["result"]["checks"]
    missing = [c["claim"] for c in checks if c["status"] in ("not_found", "partial")]
    contradicted = [c["claim"] for c in checks if c["status"] == "conflict"]
    actions = ([f"supply a source that states {missing}, or remove those sentences"] if missing else []) + (
        [f"a source contradicts {contradicted}: find out which is right before using it"] if contradicted else [])
    print("  next action: " + ("; ".join(actions) or "none needed for support; backed is not proof the source is right"))
    print()


def main() -> int:
    _show("1. Only the shipping policy is available", SHIPPING)
    _show("2. The refund policy is supplied", {**SHIPPING, **REFUNDS})
    _show("3. A source with a different figure", {**SHIPPING, **WRONG})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
