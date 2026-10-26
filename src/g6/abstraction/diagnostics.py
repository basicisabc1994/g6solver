# SPDX-License-Identifier: Apache-2.0
"""Self-debugging diagnostics (the harness reasoning about its own reasoning).

Every verification decision the harness makes rests on estimates that can
silently go wrong. This module audits those decisions and reports findings
as machine-actionable items: each carries a severity, an explanation, and a
`remediation` naming the exact tool call an agent should issue next. The
diagnosis covers:

  INERT_MECHANISM     checked/provisional but its bounds never bind
                      (finite_bound_rate ~ 0 or slack >> span): soundness
                      earned vacuously; it can never prune, so its checked
                      status overstates its usefulness
  BOUND_MISCALIBRATED violation_rate > 0 on a non-rejected entry (should be
                      impossible if the pipeline is consistent — this is the
                      self-consistency check on the checker itself)
  STALE_ENTRY         trace grew past the warn/refuse thresholds since the
                      entry was evaluated
  WINNERS_CURSE       many candidates tried on a domain with thin margins:
                      the best entry's lcb is close to 0 relative to
                      selection pressure
  DEGENERATE_PRESENT  degenerate entries in the library (informational:
                      the defence held, but the proposer is wasting budget)
  NO_CHECKED          a domain has evaluations but no checked mechanism:
                      recommend CEGAR refinement of the best rejected entry
                      or more data
  META_UNDERPOWERED   lifted entries evaluated on a meta-domain with very
                      few records: meta-conclusions are noise until more
                      base-level abstractions have been evaluated
  LOW_DATA            domain trace too short for the gate's statistics to
                      mean much

The severity scale: "info" < "warn" < "act".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain import ConcreteDomain
from .library import Library, STALE_REFUSE, STALE_WARN, TOP_STATUS

MIN_TRACE_FOR_CONFIDENCE = 30
THIN_MARGIN = 0.02


@dataclass
class Finding:
    severity: str          # "info" | "warn" | "act"
    code: str
    subject: str           # entry_id or domain name
    detail: str
    remediation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code,
                "subject": self.subject, "detail": self.detail,
                "remediation": self.remediation}


@dataclass
class Diagnosis:
    findings: list[Finding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        sev_rank = {"act": 0, "warn": 1, "info": 2}
        ordered = sorted(self.findings, key=lambda f: sev_rank.get(f.severity, 9))
        return {"summary": self.summary,
                "n_findings": len(ordered),
                "findings": [f.to_dict() for f in ordered]}


def diagnose(library: Library, domains: dict[str, ConcreteDomain]) -> Diagnosis:
    d = Diagnosis()
    all_entries = list(library.entries.values())
    # Frontier discipline (remediation: zombie findings): superseded
    # entries are provenance, not decisions in force — auditing them
    # forever would bury live findings under resolved history.
    entries = [e for e in all_entries if e.superseded_by is None]
    by_domain: dict[str, list] = {}
    for e in entries:
        by_domain.setdefault(e.domain_name, []).append(e)

    # Spec drift from loaded state files (validated on load).
    for eid in getattr(library, "invalid_specs", []):
        d.findings.append(Finding(
            "act", "INVALID_SPEC", eid,
            "entry loaded from a state file carries a spec that violates "
            "current validation limits; it cannot be instantiated and its "
            "recorded scores should not be trusted or lifted.",
            {"tool": "evaluate_abstraction",
             "args": {"spec_json": "<corrected spec>"},
             "why": "re-propose within current limits; the old entry stays "
                    "for provenance only"}))

    # ---- per-entry audits ------------------------------------------------ #
    for e in entries:
        cal = e.gate.get("calibration", {}) or {}
        vr = cal.get("violation_rate")

        if e.status in (TOP_STATUS, "provisional"):
            saved = e.replay.get("evals_saved", 0)
            rg = e.replay.get("reorder_gain")
            no_pruning = saved == 0
            no_guidance = rg is None or rg <= 0
            if no_pruning and no_guidance:
                slack = cal.get("mean_slack")
                slack_txt = (f" (mean bound slack {slack:.3f} — too loose to "
                             f"ever undercut the incumbent)"
                             if slack is not None else "")
                d.findings.append(Finding(
                    "warn", "INERT_MECHANISM", e.entry_id,
                    f"status={e.status} but it neither pruned a single "
                    f"evaluation nor improved ordering in replay — its "
                    f"soundness was earned vacuously{slack_txt}. The status "
                    f"overstates usefulness.",
                    {"tool": "tighten_abstraction",
                     "args": {"entry_id": e.entry_id},
                     "why": "tighten binary-searches the smallest lipschitz "
                            "scale that stays robustly sound, turning "
                            "unused slack into pruning power — refine "
                            "cannot help here (no soundness cexs to drive "
                            "it)"}))
            if vr is not None and vr > 0:
                d.findings.append(Finding(
                    "act", "BOUND_MISCALIBRATED", e.entry_id,
                    f"self-consistency violation: status={e.status} yet "
                    f"prospective violation_rate={vr:.2%}. The checker's own "
                    f"pipeline disagrees with itself — treat this entry's "
                    f"advice as unsound until revalidated.",
                    {"tool": "revalidate", "args": {"entry_id": e.entry_id}}))

        if e.domain_name in domains:
            growth = e.staleness(domains[e.domain_name])
            if growth >= STALE_REFUSE:
                d.findings.append(Finding(
                    "act", "STALE_ENTRY", e.entry_id,
                    f"trace grew {growth:.0%} since evaluation "
                    f"({e.trace_len} -> {len(domains[e.domain_name].records)} "
                    f"records); pruning advice is refused until revalidation.",
                    {"tool": "revalidate", "args": {"entry_id": e.entry_id}}))
            elif growth > STALE_WARN and e.status in (TOP_STATUS, "provisional"):
                d.findings.append(Finding(
                    "warn", "STALE_ENTRY", e.entry_id,
                    f"trace grew {growth:.0%} since evaluation; advice still "
                    f"served with a staleness caveat.",
                    {"tool": "revalidate", "args": {"entry_id": e.entry_id}}))

    # ---- per-domain audits ------------------------------------------------ #
    pressure = library.selection_pressure()
    for name, es in by_domain.items():
        checked = [e for e in es if e.status == TOP_STATUS]
        k = pressure.get(name, len(es))

        if name in domains and len(domains[name].records) < MIN_TRACE_FOR_CONFIDENCE:
            d.findings.append(Finding(
                "warn", "LOW_DATA", name,
                f"only {len(domains[name].records)} records; gate statistics "
                f"and bootstrap intervals are weak below "
                f"~{MIN_TRACE_FOR_CONFIDENCE}.",
                {"tool": "record_evaluation",
                 "args": {"domain": name},
                 "why": "collect more evaluated configs before trusting "
                        "the library's decisions"}))

        if checked:
            best = max(checked, key=lambda e: e.robust.get("lcb", 0.0))
            lcb = best.robust.get("lcb", 0.0)
            if k >= 5 and lcb < THIN_MARGIN:
                d.findings.append(Finding(
                    "warn", "WINNERS_CURSE", name,
                    f"{k} candidates tried; the best checked entry "
                    f"({best.entry_id}) clears its selection-adjusted lower "
                    f"bound by only {lcb:+.4f}. The winner may be selection "
                    f"noise.",
                    {"tool": "evaluate_abstraction",
                     "args": {"spec_json": "<same spec as "
                              + best.entry_id + ">", "domain": name},
                     "why": "independent re-evaluation (fresh permutations, "
                            "stricter k) confirms or demotes the winner"}))
        elif es:
            provisional = [e for e in es if e.status == "provisional"]
            candidates = [e for e in es if e.status == "rejected"]
            rec = (max(candidates, key=lambda e: e.replay.get("evals_saved", 0))
                   if candidates else None)
            if provisional:
                best_p = max(provisional, key=lambda e: e.blp_score)
                d.findings.append(Finding(
                    "warn", "NO_CHECKED", name,
                    f"{len(es)} evaluations; sound candidates exist "
                    f"(e.g. {best_p.entry_id}) but none has demonstrated "
                    f"value at the selection-adjusted level.",
                    {"tool": "tighten_abstraction",
                     "args": {"entry_id": best_p.entry_id},
                     "why": "tighten the best provisional entry until its "
                            "bounds can bind, then re-evaluate"}))
            else:
                d.findings.append(Finding(
                    "act", "NO_CHECKED", name,
                    f"{len(es)} evaluations, none sound — the domain either "
                    f"lacks structure the gate accepts at tried scales, or "
                    f"candidates need refinement.",
                    {"tool": "refine_abstraction",
                     "args": {"entry_id": rec.entry_id} if rec else {},
                     "why": "CEGAR on the most aggressive rejected pruner "
                            "converges to the empirically honest scale, or "
                            "bottoms out cheaply — which is itself the answer "
                            "(no compressible structure at this scale)"}))

        n_degen = sum(1 for e in es if e.status == "degenerate")
        if n_degen:
            d.findings.append(Finding(
                "info", "DEGENERATE_PRESENT", name,
                f"{n_degen} degenerate (single-cell) candidates were caught "
                f"and clamped; the proposer is spending budget on trivial "
                f"structure.",
                {"tool": "list_combinators", "args": {},
                 "why": "revisit parameter ranges: radius/bins producing one "
                        "cell add description length without content"}))

    # ---- meta-level audit -------------------------------------------------- #
    for name, dom in domains.items():
        if name.startswith("meta(") and len(dom.records) < 8:
            lifted_here = [e.entry_id for e in entries if e.domain_name == name]
            if lifted_here:
                d.findings.append(Finding(
                    "warn", "META_UNDERPOWERED", name,
                    f"lifted entries {lifted_here} were evaluated on a "
                    f"meta-domain of only {len(dom.records)} records; "
                    f"meta-level conclusions are noise at this size.",
                    {"tool": "evaluate_abstraction", "args": {"domain": "<base>"},
                     "why": "evaluate more base-level abstractions first; "
                            "the meta-domain grows one record per evaluation"}))

    # ---- summary ------------------------------------------------------------ #
    statuses: dict[str, int] = {}
    for e in entries:
        statuses[e.status] = statuses.get(e.status, 0) + 1
    d.summary = {
        "n_entries": len(all_entries),
        "n_frontier": len(entries),
        "n_superseded": len(all_entries) - len(entries),
        "by_status": statuses,
        "selection_pressure": pressure,
        "n_act": sum(1 for f in d.findings if f.severity == "act"),
        "n_warn": sum(1 for f in d.findings if f.severity == "warn"),
        "healthy": not any(f.severity == "act" for f in d.findings),
    }
    return d
