# SPDX-License-Identifier: Apache-2.0
"""Pure decision projection from component-owned, operation-specific declarations.

``component`` is the producing module (or its declaration mapping). No environment is read,
and no key is interpreted unless that operation declares its path. Bound producers' declarations
live in the identity-keyed companion table; module-owned declarations take precedence.
Paths are tuples of exact keys; ``*`` reduces only a declared list position. Unknown words
and missing fields remain unknown. This adds no reliability promotion or permission grant.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Any

STATUSES = frozenset({"asserted", "observed_by_g6", "verified_within_scope"})


def _at(value: Any, path: tuple[str, ...]) -> list[Any]:
    if not path:
        return [value]
    key, *rest = path
    if key == "*":
        return [found for child in value for found in _at(child, tuple(rest))] if type(value) is list else []
    if type(value) is dict and key in value:
        return _at(value[key], tuple(rest))
    return []


def summarise(component: ModuleType | Mapping | Any, op: str, output: Any) -> dict[str, Any]:
    """Summarise exactly the producer's declared output positions, never arbitrary nested data."""
    if isinstance(component, Mapping):
        declarations = component
    elif hasattr(component, "__dict__") and "__g6_decision__" in vars(component):
        declarations = vars(component)["__g6_decision__"]
    elif isinstance(component, ModuleType):
        from g6.core.decision_declarations import DECISIONS

        declarations = DECISIONS.get(component, {})
    else:
        declarations = {}
    spec = declarations.get(op) if isinstance(declarations, Mapping) else None
    out = {"may_proceed": None, "declared": isinstance(spec, Mapping), "evidence_status": "asserted",
           "action_authorization": "not_requested"}
    if not isinstance(spec, Mapping):
        return out
    values = _at(output, tuple(spec["field"]))
    known = spec.get("values", {})
    decisions = [known.get(value) if isinstance(value, str) else None for value in values]
    if False in decisions:
        out["may_proceed"] = False
    elif decisions and all(d is True for d in decisions):
        out["may_proceed"] = True
    statuses = _at(output, tuple(spec["evidence_field"])) if "evidence_field" in spec else []
    if statuses and all(type(s) is str and s in STATUSES for s in statuses):
        rank = {"asserted": 0, "observed_by_g6": 1, "verified_within_scope": 2}
        out["evidence_status"] = min(statuses, key=rank.__getitem__)
    # A component must explicitly declare authorization meanings separately from proceed meanings.
    authorization = spec.get("authorization", {})
    grants = [authorization.get(v, "not_requested") if isinstance(v, str) else "not_requested" for v in values]
    if "refused" in grants:
        out["action_authorization"] = "refused"
    elif "requires_person" in grants:
        out["action_authorization"] = "requires_person"
    elif grants and all(g == "allowed" for g in grants):
        out["action_authorization"] = "allowed"
    return out
