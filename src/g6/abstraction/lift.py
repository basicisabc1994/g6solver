# SPDX-License-Identifier: Apache-2.0
"""The Lift functor (framework section 7).

The outer problem is itself a concrete domain C1: 'configs' are abstraction
specs featurised into a numeric vector, and the 'objective' is the recorded
blp_score of the corresponding library entry. Any trace-generic mechanism
therefore applies at the meta level unchanged — e.g. a Quotient over spec
space is meta-Tabu: 'do not propose an abstraction similar to one already
evaluated'. Applicability is the checkable syntactic condition
signature == "trace-generic" (Proposition-4 safety is inherited because the
meta objective is grounded in level-0 replay scores, which are grounded in
recorded concrete f values; no level evaluates itself).
"""
from __future__ import annotations

from typing import Any

from .abstractions import Abstraction, TRACE_GENERIC
from .domain import ConcreteDomain
from .library import Library

_FEATURES = ("radius", "lipschitz", "bins", "arms")


def _featurise(spec: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {"depth": 0.0}
    kinds = {"quotient": 1.0, "project": 2.0, "sufficient": 3.0, "product": 4.0}

    def walk(s: dict[str, Any], depth: int) -> None:
        flat["depth"] = max(flat["depth"], float(depth))
        flat[f"kind_{s.get('kind')}"] = flat.get(f"kind_{s.get('kind')}", 0.0) + 1.0
        flat["kind_code"] = kinds.get(s.get("kind", ""), 0.0)
        for f in _FEATURES:
            if f in s:
                flat[f] = float(s[f])
        for side in ("left", "right"):
            if side in s:
                walk(s[side], depth + 1)

    walk(spec, 1)
    for f in _FEATURES:
        flat.setdefault(f, 0.0)
    for k in kinds:
        flat.setdefault(f"kind_{k}", 0.0)
    return flat


def meta_domain(library: Library, domain_name: str | None = None) -> ConcreteDomain:
    rows = []
    for e in library.entries.values():
        if domain_name and e.domain_name != domain_name:
            continue
        row = _featurise(e.spec)
        row["objective"] = e.blp_score
        rows.append(row)
    if len(rows) < 4:
        raise ValueError(
            f"need >= 4 evaluated abstractions to build the meta-domain "
            f"(have {len(rows)}); evaluate more candidates first")
    return ConcreteDomain.from_rows(rows, objective="objective",
                                    maximise=True, name="meta(" + (domain_name or "all") + ")")


def referenced_dims(spec: dict) -> set[str]:
    """Dimension names a spec reads (``dims`` / ``dim``), including inside compositions."""
    dims: set[str] = set(spec.get("dims") or ())
    if spec.get("dim"):
        dims.add(spec["dim"])
    for child in ("left", "right"):
        if isinstance(spec.get(child), dict):
            dims |= referenced_dims(spec[child])
    return dims


def liftable(abstraction: Abstraction, meta: ConcreteDomain | None = None) -> bool:
    """Structurally eligible (trace-generic), and — when ``meta`` is given — every dimension the
    spec names exists in that meta-domain (audit 001, F-08: a trace-generic ``Sufficient(dim="lr")``
    was reported liftable although the meta-domain has no ``lr`` column, so the lift then failed)."""
    if abstraction.signature != TRACE_GENERIC:
        return False
    return meta is None or referenced_dims(abstraction.spec()) <= set(meta.params)


def lift(abstraction: Abstraction, library: Library,
         domain_name: str | None = None) -> tuple[Abstraction, ConcreteDomain]:
    if not liftable(abstraction):
        raise ValueError(
            "mechanism is domain-specific (signature != trace-generic); "
            "the Lift functor is undefined for it")
    meta = meta_domain(library, domain_name)
    missing = referenced_dims(abstraction.spec()) - set(meta.params)
    if missing:
        raise ValueError(f"spec names dimensions absent from the meta-domain: {sorted(missing)}; "
                         "supply a spec over meta features instead")
    from .abstractions import from_spec
    lifted = from_spec(abstraction.spec())
    lifted.fit(meta)
    return lifted, meta
