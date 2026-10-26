# SPDX-License-Identifier: Apache-2.0
"""Demo 4 — a coding agent discovering and composing G6.

The calls an agent makes over MCP (``g6_find`` → ``g6_describe`` → ``g6_invoke``), scripted, for a domain G6 has no
ready-made pipeline for: "check an email to a customer before the agent sends it". The agent describes each need in
plain words, picks the component discovery ranks first, reads its ops, and composes two calls; the overall label is the
weakest of the two. Each step prints what the agent would see.

This script shows the path. The acceptance test for the demo is different: a fresh Claude Code or Codex session, given
only the README, doing the same thing unaided (``claude mcp add g6 -- g6-mcp``).

    python examples/demo_4_agent_discovery.py
"""
from __future__ import annotations

import json

from g6.core import discovery
from g6.mcp import invoke_reply

EMAIL = "Refunds are issued within 14 days of a return. Orders over $50 ship free. Every order includes a gift."
SOURCES = {"refunds.md": "Refunds are issued within 14 days of a return.",
           "shipping.md": "Orders over $50 ship free. Standard delivery takes 3 to 5 business days."}
_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}


def _need(words: str) -> str:
    hits = discovery.find(words, limit=3)
    print(f'g6_find("{words}")')
    for h in hits:
        print(f"  {h['component']:<22} score {h['score']:<6} matched {h['matched']}")
    chosen = hits[0]["component"]
    info = discovery.describe(chosen)
    print(f'g6_describe("{chosen}") -> maturity {info.get("maturity")}, ops {info.get("ops")}')
    return chosen


def _decision(reply: dict) -> str:
    result = reply.get("result") or {}
    return str(result.get("decision") or (result.get("gate_decision") or {}).get("decision") or "?")


def _reasons(reply: dict) -> list[str]:
    result = reply.get("result") or {}
    codes = result.get("reason_codes") or (result.get("gate_decision") or {}).get("reason_codes") or []
    return [c for c in codes if c != "G6_E_GATE_OK"]


def _invoke(component: str, op: str, params: dict) -> dict:
    reply = invoke_reply(component, op, json.dumps(params))
    card = reply.get("warning_card") or {}
    print(f'g6_invoke("{component}", "{op}") -> {reply["completion_state"]} ({card.get("code", "-")})')
    return reply


def main() -> int:
    print("Task: check an email to a customer before the agent sends it.\n")
    grounding = _need("are the claims in this text backed by my documents")
    claims = _invoke(grounding, "check_claims", {"text": EMAIL, "sources": SOURCES})
    for c in claims["result"]["checks"]:
        print(f"  - {c['status']:<9} {c['claim']!r}")
    print()
    gate = _need("should my agent send this email to a customer")
    action = {"action_type": "send_email", "target_resource": "dana@example.com", "reversibility": "irreversible",
              "blast_radius": "external_party", "external_side_effect": True}
    decided = _invoke(gate, "evaluate", {"action": action})
    print(f"  decision: {_decision(decided)}; reasons {_reasons(decided)}")
    print("The email is irreversible and leaves the company, so the gate wants a verification plan and the context the"
          " agent is acting in. The agent supplies them and asks again:")
    planned = {**action,
               "verification_contract": {"expected_result": "the customer receives one correct email",
                                         "observation_window": "1 hour",
                                         "leading_indicators": ["send accepted by the mail server"],
                                         "lagging_indicators": ["no bounce or complaint within an hour"],
                                         "monitor": "the outbox and the support inbox",
                                         "verification_time": "1 hour after sending",
                                         "stop_threshold": "a bounce or a complaint",
                                         "rollback_procedure": "send a correction and tell the support lead",
                                         "model_update_rule": "a complaint adds a check for the claim it disputes"},
               "coordination_context": {"proposed": {"action_id": "email-dana", "objective": "answer a refund question",
                                                     "resource": "dana@example.com"}, "active": []},
               "world_state": {"variables": [], "horizons_evaluated": ["1 hour", "1 week"]}}
    decided = _invoke(gate, "evaluate", {"action": planned})
    print(f"  decision: {_decision(decided)}; reasons {_reasons(decided)}")
    print()
    overall = max((claims["completion_state"], decided["completion_state"]), key=_RANK.__getitem__)
    print(f"Composed label (the weakest of the two): {overall}")
    print("The agent reports both labels and their reasons to its user, and does not send while either is "
          "blocked-escalated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
