# SPDX-License-Identifier: Apache-2.0
"""One op's card: what its contract declares and what its component says about it (audit 016 §11: the archive's
``op_catalog``, rung 4).

The contracts are ``ParamContract`` files (``g6.workforce.capabilities``); by default the ones the library ships, and a
caller may pass others (``load_contracts(directory)``). A component's own op list and ``help`` lines come from its block,
through ``g6.core.discovery``. Nothing is ranked, inferred or measured here: a purpose is the component's own ``help``
line for the op, else empty, and an op that neither a contract nor its component's block names has no card.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from g6.core import discovery
from g6.core.registry import get_registry
from g6.navigator.authority import Authority, measured, shipped_authority
from g6.workforce.capabilities import ParamContract, load_contracts
from g6.workforce.economics import format_money

Contracts = Mapping[tuple[str, str], ParamContract]
PURPOSE_CHARS = 160
#: A card's budget in characters (400 tokens of four), as the archive's rung 4 had it.
CARD_CHARS = 1600


def _table(contracts: Contracts | None) -> Contracts:
    return load_contracts() if contracts is None else contracts


def contract_for(component: str, op: str, *, contracts: Contracts | None = None) -> ParamContract | None:
    """The op's ParamContract, or None (a lookup in the loaded contracts: no path is built from a name)."""
    return _table(contracts).get((str(component), str(op)))


def listed_ops(component: str) -> frozenset[str]:
    """The ops the component's block lists for itself (its ``ops`` answer); empty for one with no block."""
    return frozenset(discovery.ops(component))


def ops_of(component: str, *, contracts: Contracts | None = None) -> frozenset[str]:
    """Every op a contract declares on ``component`` or its block lists (empty: none)."""
    declared = {op for name, op in _table(contracts) if name == component}
    return frozenset(declared) | listed_ops(component)


def purpose(component: str, op: str) -> str:
    """The component's own ``help`` line for ``op`` (at most PURPOSE_CHARS), or "" when it gives none."""
    result = get_registry().invoke(component, "help")
    if result.__class__.__name__ != "Success" or not isinstance(result.unwrap(), dict):
        return ""
    for line in result.unwrap().get("help") or []:
        text = str(line)
        if text.split("(", 1)[0].split(":", 1)[0].strip() == op:
            return text[:PURPOSE_CHARS]
    return ""


def _maturity(component: str) -> str | None:
    """What the component declares (its block's maturity, else its package's); never a constant (audit 016, NAV-014)."""
    info = get_registry().describe(component)
    return info.get("maturity") if info.get("available") else None


def _cost(contract: ParamContract) -> str:
    ceiling = contract.cost_ceiling
    if ceiling is None:
        return "unknown"
    if ceiling.amount_minor_units == 0:
        return "free"
    return "up to " + format_money(ceiling.amount_minor_units, ceiling.currency)


def card(component: str, op: str, *, contracts: Contracts | None = None,
         authority: Authority | None = None) -> dict[str, Any] | None:
    """One op's card within CARD_CHARS: purpose, maturity, authority class (None when unmeasured) and, from its
    contract, the required and optional parameters, side effect, cost and a known-good example. None when neither a
    contract nor the component's block names the op."""
    table = _table(contracts)
    contract = table.get((component, op))
    if contract is None and op not in listed_ops(component):
        return None
    read = authority or shipped_authority(table)
    out: dict[str, Any] = {"component": component, "op": op, "purpose": purpose(component, op),
                           "maturity": _maturity(component), "authority": measured(component, op, read)}
    if contract is None:
        out["contract"] = None
        out["note"] = "no parameter contract: the component's typed input checks a call when it runs"
        return out
    out.update(side_effect=contract.side_effect, model_free=contract.model_free, cost=_cost(contract),
               required=dict(contract.required), optional=dict(contract.optional),
               example=dict(contract.probe_params), contract_sha256=contract.sha256)
    return _fit(out)


def _fit(found: dict[str, Any]) -> dict[str, Any]:
    """The card within CARD_CHARS: the example goes first, then the optional parameters' types, then their names."""
    if len(json.dumps(found)) <= CARD_CHARS:
        return found
    found = {**found, "example": None, "example_note": "too long to show here: send the required parameters"}
    if len(json.dumps(found)) <= CARD_CHARS:
        return found
    names = sorted(found.get("optional") or {})
    found = {**found, "optional": names}
    while names and len(json.dumps(found)) > CARD_CHARS:
        names = names[:-1]
        found = {**found, "optional": names, "optional_note": "more optional parameters: the contract lists them all"}
    return found


__all__ = ["CARD_CHARS", "PURPOSE_CHARS", "Contracts", "card", "contract_for", "listed_ops", "ops_of", "purpose"]
