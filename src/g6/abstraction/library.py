# SPDX-License-Identifier: Apache-2.0
"""The abstraction library and the robust (BLP) objective.

Status lattice (remediation: silent failure modes become named states):

  rejected     gate failed, or any regret event on any bootstrap ordering
               — unsound mechanisms cannot score positively, ever
  degenerate   sound but structurally trivial (single cell): gaming MDL
               with a vacuous quotient earns this label, not a win
  provisional  robustly sound, but the selection-adjusted lower confidence
               bound of the benefit is not positive — usable for advice
               with a caveat, not yet promotable
  checked      robustly sound AND valuable at the Bonferroni-adjusted level
               (the more candidates tried on this domain, the stricter);
               the module's own top status, never a G6 ``verified``

Entries stamp the trace length at evaluation time; staleness (trace growth
since) is a first-class, queryable property. The library persists to JSON;
a state file written when the top status was called ``verified`` loads as
``checked``.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .abstractions import Abstraction, SpecError, from_spec
from .domain import ConcreteDomain, ParamSpec, Record
from .gate import GateReport, ReplayReport, replay, run_gate
from .mdl import MDLReport, score as mdl_score
from .robustness import RobustReport, bootstrap_replay

STALE_WARN = 0.25    # trace grew >25% since evaluation: warn
STALE_REFUSE = 1.00  # trace grew >100%: refuse pruning advice until revalidated
TOP_STATUS = "checked"  # the module's own top status; state files from before the rename say "verified"


@dataclass
class LibraryEntry:
    entry_id: str
    spec: dict[str, Any]
    domain_name: str
    gate: dict[str, Any]
    replay: dict[str, Any]
    robust: dict[str, Any]
    mdl: dict[str, Any]
    blp_score: float
    status: str
    trace_len: int
    created: float = field(default_factory=time.time)
    parent: str | None = None
    notes: str = ""
    superseded_by: str | None = None
    spec_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("entry_id", "spec", "domain_name", "gate", "replay",
                 "robust", "mdl", "blp_score", "status", "trace_len",
                 "created", "parent", "notes", "superseded_by",
                 "spec_hash")}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LibraryEntry":
        return cls(**d)

    def staleness(self, domain: ConcreteDomain) -> float:
        if self.trace_len <= 0:
            return float("inf")
        return (len(domain.records) - self.trace_len) / self.trace_len


class Library:
    def __init__(self, mu: float = 0.5, kappa: float = 0.5,
                 k_permutations: int = 12) -> None:
        self.entries: dict[str, LibraryEntry] = {}
        self.mu = mu
        self.kappa = kappa
        self.k_permutations = k_permutations
        self._counter = 0
        self._evals_per_domain: dict[str, int] = {}
        self.invalid_specs: list[str] = []  # populated on load()
        # Entries restored from a state file. Their status and evidence were read, not
        # re-established, so they never authorise pruning (audit 001, F-03); `revalidate`
        # re-evaluates the spec and produces a fresh, trusted entry.
        self.loaded_unverified: set[str] = set()

    def _next_id(self, kind: str) -> str:
        self._counter += 1
        return f"{kind}-{self._counter:03d}"

    # ------------------------------------------------------------------ #
    @staticmethod
    def spec_hash(spec: dict[str, Any]) -> str:
        return hashlib.sha1(
            json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]

    def evaluate(self, abstraction: Abstraction, domain: ConcreteDomain,
                 budget: int | None = None, parent: str | None = None,
                 notes: str = "", force: bool = False,
                 supersedes: str | None = None) -> LibraryEntry:
        # Dedup guard (remediation: duplicate-evaluation inflation).
        # Re-evaluating an identical spec on an unchanged trace wastes
        # budget AND inflates the Bonferroni k for every other candidate,
        # making honest mechanisms harder to verify. Return the existing
        # entry instead — the harness's own Tabu principle, applied to the
        # harness itself.
        h = self.spec_hash(abstraction.spec())
        if not force:
            for e in self.entries.values():
                if (e.spec_hash == h and e.domain_name == domain.name
                        and e.trace_len == len(domain.records)
                        and e.superseded_by is None):
                    return e

        # Selection pressure counter: feeds the Bonferroni adjustment.
        self._evals_per_domain[domain.name] = (
            self._evals_per_domain.get(domain.name, 0) + 1)
        k_tried = self._evals_per_domain[domain.name]

        g: GateReport = run_gate(abstraction, domain)
        r: ReplayReport = replay(abstraction, domain, budget=budget)
        rob: RobustReport = bootstrap_replay(
            abstraction, domain, budget=budget,
            k_permutations=self.k_permutations, mu=self.mu,
            n_candidates_tried=k_tried)
        m: MDLReport = mdl_score(abstraction, domain)

        mdl_term = m.net_bits_saved / max(1.0, m.raw_bits)
        blp = rob.mean_benefit + self.kappa * mdl_term

        fitted = from_spec(abstraction.spec()).fit(domain)
        degenerate = fitted.n_cells() <= 1

        sound = g.passed and rob.sound_robust
        if not sound:
            status = "rejected"
            blp = min(blp, 0.0)
        elif degenerate:
            status = "degenerate"
            blp = min(blp, 0.0)  # trivial structure cannot win on MDL alone
        elif rob.valuable:
            status = TOP_STATUS
        else:
            status = "provisional"

        entry = LibraryEntry(
            entry_id=self._next_id(abstraction.KIND),
            spec=abstraction.spec(), domain_name=domain.name,
            gate=g.to_dict(), replay=r.to_dict(), robust=rob.to_dict(),
            mdl=m.to_dict(), blp_score=round(blp, 6), status=status,
            trace_len=len(domain.records), parent=parent, notes=notes,
            spec_hash=h)
        self.entries[entry.entry_id] = entry
        if supersedes and supersedes in self.entries:
            # Lineage (remediation: zombie findings). A refined/tightened/
            # revalidated child supersedes its parent so diagnostics audit
            # the frontier, not the graveyard. Provenance is kept.
            self.entries[supersedes].superseded_by = entry.entry_id
        return entry

    # ------------------------------------------------------------------ #
    def get(self, entry_id: str) -> LibraryEntry:
        if entry_id not in self.entries:
            raise KeyError(f"no library entry {entry_id!r}; "
                           f"known: {sorted(self.entries)}")
        return self.entries[entry_id]

    def instantiate(self, entry_id: str) -> Abstraction:
        if entry_id in self.invalid_specs:
            raise SpecError(f"entry {entry_id} has a spec that fails current validation; it cannot be used")
        return from_spec(self.get(entry_id).spec)

    def leaderboard(self, domain_name: str | None = None,
                    include_superseded: bool = True) -> list[dict[str, Any]]:
        rows = [e for e in self.entries.values()
                if (domain_name is None or e.domain_name == domain_name)
                and (include_superseded or e.superseded_by is None)]
        order = {TOP_STATUS: 0, "provisional": 1, "degenerate": 2, "rejected": 3}
        rows.sort(key=lambda e: (order.get(e.status, 9), -e.blp_score))
        return [{"entry_id": e.entry_id, "kind": e.spec.get("kind"),
                 "domain": e.domain_name, "status": e.status,
                 "blp_score": e.blp_score,
                 "benefit_lcb": e.robust.get("lcb"),
                 "regrets_bootstrap": e.robust.get("total_regret_events"),
                 "compression": e.mdl.get("compression"),
                 "trace_len": e.trace_len, "parent": e.parent,
                 "superseded_by": e.superseded_by}
                for e in rows]

    def promotable(self) -> list[str]:
        """Combinator kinds at the top status (``checked``) on >= 2 distinct domains."""
        seen: dict[str, set[str]] = {}
        for e in self.entries.values():
            if e.status == TOP_STATUS:
                seen.setdefault(str(e.spec.get("kind")), set()).add(e.domain_name)
        return [k for k, doms in seen.items() if len(doms) >= 2]

    def selection_pressure(self) -> dict[str, int]:
        return dict(self._evals_per_domain)

    # ------------------------------------------------------------------ #
    # Persistence (remediation: state loss)
    # ------------------------------------------------------------------ #
    def to_state(self, domains: dict[str, ConcreteDomain]) -> dict[str, Any]:
        return {
            "version": 1,
            "library": {
                "mu": self.mu, "kappa": self.kappa,
                "k_permutations": self.k_permutations,
                "counter": self._counter,
                "evals_per_domain": self._evals_per_domain,
                "entries": [e.to_dict() for e in self.entries.values()],
            },
            "domains": {
                name: {
                    "objective_name": d.objective_name,
                    "maximise": d.maximise,
                    "params": {p: {"kind": s.kind, "low": s.low,
                                   "high": s.high,
                                   "choices": list(s.choices) if s.choices else None}
                               for p, s in d.params.items()},
                    "records": [{"config": r.config, "objective": r.objective}
                                for r in d.records],
                } for name, d in domains.items()
            },
        }

    def save(self, path: str, domains: dict[str, ConcreteDomain]) -> None:
        """Atomic write: serialise to a sibling temp file, then rename.
        A crash mid-write leaves the previous state intact instead of a
        truncated JSON file."""
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(self.to_state(domains), fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> tuple["Library", dict[str, ConcreteDomain]]:
        with open(path) as fh:
            state = json.load(fh)
        if state.get("version") != 1:
            raise ValueError(f"unsupported state version "
                             f"{state.get('version')!r}; expected 1")
        lib_s = state["library"]
        lib = cls(mu=lib_s["mu"], kappa=lib_s["kappa"],
                  k_permutations=lib_s.get("k_permutations", 12))
        lib._counter = lib_s["counter"]
        lib._evals_per_domain = dict(lib_s.get("evals_per_domain", {}))
        for ed in lib_s["entries"]:
            e = LibraryEntry.from_dict(ed)
            if e.status == "verified":  # the top status's name before the rename
                e.status = TOP_STATUS  # and, like every loaded entry, untrusted below
            lib.entries[e.entry_id] = e
            lib.loaded_unverified.add(e.entry_id)
            # Spec drift guard: state files written under older limits must
            # not smuggle specs past current validation. Invalid entries are
            # kept for provenance but flagged; diagnose surfaces them.
            try:
                from_spec(e.spec)
            except SpecError:
                lib.invalid_specs.append(e.entry_id)
        domains: dict[str, ConcreteDomain] = {}
        for name, ds in state.get("domains", {}).items():
            params = {p: ParamSpec(name=p, kind=v["kind"], low=v["low"],
                                   high=v["high"],
                                   choices=tuple(v["choices"]) if v["choices"] else None)
                      for p, v in ds["params"].items()}
            recs = [Record(config=r["config"], objective=r["objective"], index=i)
                    for i, r in enumerate(ds["records"])]
            domains[name] = ConcreteDomain(
                params=params, records=recs,
                objective_name=ds["objective_name"],
                maximise=ds["maximise"], name=name)
        return lib, domains
