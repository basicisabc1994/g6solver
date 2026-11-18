# SPDX-License-Identifier: Apache-2.0
"""The deterministic risk vocabulary shared by every semantic risk floor.

Legacy match modes (substring, word forms, negation) remain with their consumers.
Tier conflicts resolve upward; context/safety suggestion words alone are R1.
"""
from __future__ import annotations

import re

_LEGACY_TIERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("R3", ("irreversible", "clinical decision", "legal filing", "financial transaction", "destructive", "credential",
            "production access", "delete production", "delete data", "delete customer", "drop table",
            "drop database"),
     "critical or irreversible action terms detected"),
    ("R2", ("legal", "medical", "clinical", "financial", "security", "compliance", "production", "public claim",
            "hiring", "tax", "investment", "send", "delete", "payment", "deploy"),
     "high-stakes domain or public/regulated decision terms detected"),
    ("R1", ("internal", "prototype", "project plan", "analysis", "deployment", "code", "engineering"),
     "moderate-stakes internal, code, deployment, or analysis task"),
    ("R0", ("brainstorm", "style", "summary", "draft"), "low-stakes draft, summary, or brainstorming task"),
)

ACCESS_TERMS: tuple[str, ...] = ("password", "credential", "private key", "production access")

CONTEXT_TERMS: tuple[str, ...] = ("missing context", "unknown api", "unavailable file", "attached document")

DESTRUCTIVE_TERMS: tuple[str, ...] = ("drop table", "wipe", "destroy", "purge", "rm rf", "force push")

SAFETY_WORDS = ("safe", "should i", "should we", "can i", "can we", "risky", "dangerous", "allowed", "okay to",
                "ok to", "fine to", "is it ok", "production", "prod", "live", "delete", "rm", "overwrite", "reset",
                "push", "commit", "merge", "pay", "migrate", "migration", "drop", "deploy", "send", "publish", "wipe",
                "erase", "truncate", "destroy", "terminate", "remove", "upload", "email", "transfer", "ship",
                "release", "roll back", "rollback", "revoke", "rotate", "tear down", "teardown", "kill", "refund",
                "charge", "shut down", "shutdown", "rewrite history", "force")

SAFETY_VERBS = frozenset({"delete", "overwrite", "reset", "push", "commit", "merge", "pay", "migrate", "drop",
                          "deploy", "send", "publish", "wipe", "erase", "truncate", "destroy", "terminate", "remove",
                          "upload", "email", "transfer", "ship", "release", "revoke", "rotate", "kill", "refund",
                          "charge", "force"})

_IRREGULAR = {"pay": ("paid",), "send": ("sent",), "safe": ("safely",), "migration": ("migrations",),
              "roll back": ("rolls back", "rolled back", "rolling back"),
              "tear down": ("tears down", "tore down", "torn down", "tearing down"),
              "shut down": ("shuts down", "shutting down"),
              "rewrite history": ("rewrote history", "rewriting history", "rewrite the history",
                                  "rewrite the git history", "rewrite git history")}

_RISK_FORMS: dict[str, str] = {
    "auth": r"(?:re|un)?auth(?:n|z|ed)?",
    "authentication": r"(?:re|un)?authenticat(?:e|ed|es|ing|ion|ions)",
    "authorization": r"(?:re|un|de)?authori[sz](?:e|ed|es|ing|ation|ations)",
    "oauth": r"oauth\d*",
    "credential": r"credentials?",
    "secret": r"secrets?",
    "key material": r"(?:signing|private|api|access|encryption)[- ]keys?",
    "deploy": r"(?:re|un)?deploy(?:s|ed|ing|ment|ments)?",
    "production": r"production(?:i[sz](?:e|ed|es|ing))?",
    "medical": r"(?:bio)?medical(?:ly)?",
    "dosage": r"dosages?",
    "legal": r"legal(?:ly)?",
    "payment": r"(?:re)?payments?",
    "security": r"security",
    "destructive data operation": r"(?:drop|truncate)\s+(?:the\s+)?(?:\w+\s+)?(?:tables?|databases?|schemas?)"
                                  r"|truncat(?:e|es|ed|ing)|delet(?:e|es|ed|ing)\s+(?:all\s+)?(?:the\s+)?"
                                  r"(?:data|records|rows|users|customers|accounts)",
    "rm -rf": r"rm\s+-(?:rf|fr|r\s+-f)",
    "data migration": r"(?:schema|database|db|data)\s+migrations?|migrat(?:e|es|ed|ing)\s+(?:the\s+)?"
                      r"(?:schema|database|db|data)",
    "force push": r"force[- ]?push(?:es|ed|ing)?|push\s+(?:--force|-f)",
}


def _union():
    terms = {}
    for tier, words, _ in _LEGACY_TIERS:
        for word in words:
            terms[word] = max(tier, terms.get(word, "R0"))
    for tier, words in (("R3", ACCESS_TERMS + DESTRUCTIVE_TERMS), ("R1", CONTEXT_TERMS + SAFETY_WORDS),
                        ("R2", tuple(_RISK_FORMS)),
                        ("R3", ("key material", "secret", "destructive data operation", "rm -rf", "force push"))):
        for word in words:
            terms[word] = max(tier, terms.get(word, "R0"))
    return tuple((tier, tuple(sorted(w for w, t in terms.items() if t == tier)),
                  next(reason for t, _, reason in _LEGACY_TIERS if t == tier))
                 for tier in ("R3", "R2", "R1", "R0"))


TIER_TERMS = _union()
RISK_FORMS = _RISK_FORMS
SAFETY_IRREGULAR = _IRREGULAR
RISK_PATTERNS = {term: re.compile(rf"(?<![a-z0-9])(?:{form})(?![a-z])") for term, form in RISK_FORMS.items()}
RISK_TIERS = {term: tier for tier, terms, _ in TIER_TERMS for term in terms}
_SUBSTRINGS = frozenset(word for _, terms, _ in _LEGACY_TIERS for word in terms)
_WORDS = {term: re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
          for term in RISK_TIERS if term not in _SUBSTRINGS}


def floor_signals(text: str) -> tuple[tuple[str, str], ...]:
    """The union's literal and existing regex-form signals, strongest first."""
    lowered = text.lower()
    found = {(tier, term) for tier, terms, _ in TIER_TERMS for term in terms
             if (term in lowered if term in _SUBSTRINGS else _WORDS[term].search(lowered))}
    found.update((RISK_TIERS[term], term) for term, pattern in RISK_PATTERNS.items() if pattern.search(lowered))
    return tuple(sorted(found, key=lambda item: (-int(item[0][1]), item[1])))
