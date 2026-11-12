# SPDX-License-Identifier: Apache-2.0
"""The frozen decision manifest for section 17's measurements (design 17.5 "Before any run").

A measurement may run only under a manifest that is complete and frozen: its cases and labels, the unit and every
eligible start, the baseline and comparison, the agent, information and budget policy, the accepted outcome and
preservation checks, the clocks, how failed, abandoned, missing and censored starts are handled, disclosures, costs,
and, for each row, every threshold with its value, unit, denominator, sample requirement and uncertainty rule.

- ``problems`` lists what is missing; anything missing blocks the run.
- ``freeze`` gives the manifest's digest, and refuses while there is a problem.
- ``decide`` gives each row ``admit``, ``repair`` or ``hold`` from results, under the frozen digest only. Too little
  evidence is ``hold``, never a pass. No start leaves a denominator. Thresholds are read from the manifest, never from
  results. Simulated sessions can fail a row; they can never admit one. A threshold marked ``on_fail: hold`` (an
  agreement bar: without it the labels cannot be read either way) holds its row instead of sending it to repair.

Each row needs a usefulness minimum above zero, so a policy that refuses everything cannot pass.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from g6.core import binomial
from g6.core.ledger import digest

VERSION = "workflow-manifest.v1"
ROWS = ("diagnosis", "suggestions", "value", "self_friction")
REQUIRED = ("cases", "labels", "unit", "eligible_starts", "baseline", "comparison", "agent_policy",
            "information_policy", "budget_policy", "accepted_outcome", "preservation_checks", "start_clock",
            "finish_clock", "handling", "disclosures", "costs", "rows")
HANDLING = ("failed", "abandoned", "missing", "censored")
THRESHOLD_FIELDS = ("name", "value", "unit", "denominator", "sample_requirement", "uncertainty_rule")
DECISIONS = ("admit", "repair", "hold")
ROLES = ("usefulness", "error", "burden", "contract")
DIRECTIONS = ("at_least", "at_most")
ALPHA = 0.05
#: How a threshold is compared: on the point estimate; or on the exact binomial bound from ``g6.core.binomial`` at
#: one-sided level ``ALPHA`` (a floor passes when the lower bound clears it; a maximum fails only when the lower bound
#: is above it, and is undecided in between).
UNCERTAINTY_RULES = ("point", "core.binomial.lower_bound", "core.binomial.interval")
#: The level each gate's admitted row raises the view to (``view.LEVELS``); a gate not listed raises nothing.
ADMITS = {"G2": ("self_friction", "narrow"), "G3": ("diagnosis", "diagnosis"),
          "G4": ("suggestions", "suggestions")}     # gate: (the row that decides it, the level it admits)
BASE_LEVEL = "narrow"
SIMULATED = "simulated sessions can fail a gate but never pass usefulness: evidence_pending"


class ManifestRefused(ValueError):
    """A manifest that may not be frozen, or results that may not be decided under it."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _stated(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def problems(manifest: Mapping) -> list[str]:
    """Everything that blocks this manifest; ``[]`` means it may be frozen."""
    if not isinstance(manifest, Mapping):
        return ["the manifest is not a mapping"]
    found = [f"missing field: {name}" for name in REQUIRED if not _stated(manifest.get(name))]
    if manifest.get("version") != VERSION:
        found.append(f"version must be {VERSION}")
    handling = manifest.get("handling") if isinstance(manifest.get("handling"), Mapping) else {}
    found += [f"handling does not say what happens to {name} starts" for name in HANDLING if not _stated(handling.get(name))]
    cases, tuned = manifest.get("cases"), manifest.get("tuned_on") or []
    listed = cases.get("ids") if isinstance(cases, Mapping) else cases
    if isinstance(listed, list) and set(map(str, listed)) & set(map(str, tuned)):
        found.append("a case used to tune a policy is among the evaluation cases")
    rows = manifest.get("rows") if isinstance(manifest.get("rows"), Mapping) else {}
    for name, row in rows.items():
        thresholds = row.get("thresholds") if isinstance(row, Mapping) else None
        if name not in ROWS:
            found.append(f"unknown row: {name}")
        if not isinstance(thresholds, list) or not thresholds:
            found.append(f"row {name} has no threshold")
            continue
        if not _stated(row.get("decision")):
            found.append(f"row {name} does not say what its decision admits")
        for number, one in enumerate(thresholds, 1):
            where = f"row {name}, threshold {one.get('name') if isinstance(one, Mapping) and one.get('name') else number}"
            if not isinstance(one, Mapping):
                found.append(f"{where} is not a mapping")
                continue
            found += [f"{where} has no {field}" for field in THRESHOLD_FIELDS if not _stated(one.get(field))
                      and not (field == "value" and one.get(field) == 0)]
            if "value" in one and not _number(one["value"]):
                source = one.get("source") or "its value"
                found.append(f"{where}: {source} is not defined as a number")
            if _stated(one.get("sample_requirement")) and not (type(one["sample_requirement"]) is int
                                                               and one["sample_requirement"] > 0):
                found.append(f"{where}: the sample requirement is a whole number above zero")
            if _stated(one.get("uncertainty_rule")) and one["uncertainty_rule"] not in UNCERTAINTY_RULES:
                found.append(f"{where}: unknown uncertainty rule")
            if one.get("role") not in ROLES or one.get("direction") not in DIRECTIONS:
                found.append(f"{where} needs a role ({', '.join(ROLES)}) and a direction ({', '.join(DIRECTIONS)})")
        useful = [one for one in thresholds if isinstance(one, Mapping) and one.get("role") == "usefulness"
                  and one.get("direction") == "at_least" and _number(one.get("value")) and one["value"] > 0]
        if not useful:
            found.append(f"row {name} has no usefulness minimum above zero, so refusing everything could pass")
    return found


def freeze(manifest: Mapping) -> str:
    """The digest a run is bound to. ``ManifestRefused`` while anything is missing."""
    found = problems(manifest)
    if found:
        raise ManifestRefused("the manifest is not complete: " + "; ".join(found[:8]))
    return digest(manifest)


def _measure(threshold: Mapping, measured: Any, starts: int) -> tuple[str, str]:
    """One threshold against one measure: (``pass`` | ``fail`` | ``undecided`` | ``too_few``, why)."""
    name, bar, need = threshold["name"], threshold["value"], threshold["sample_requirement"]
    if not isinstance(measured, Mapping):
        return "too_few", f"{name}: not measured"
    if "count" in measured:
        count, total = measured.get("count"), measured.get("n")
        if type(count) is not int or type(total) is not int or not 0 <= count <= total:
            raise ManifestRefused(f"{name}: a count and its denominator are whole numbers")
        if threshold["denominator"] == "eligible_starts" and total != starts:
            raise ManifestRefused(f"{name}: its denominator is every eligible start ({starts}), and {total} were counted; "
                                  "failed, abandoned, missing and censored starts stay in")
        if total < need:
            return "too_few", f"{name}: {total} of the {need} needed"
        point = count / total
        if threshold["uncertainty_rule"] == "core.binomial.lower_bound":
            bound = binomial.lower_bound(count, total, ALPHA)
            return ("pass" if bound >= bar else "undecided" if point >= bar else "fail",
                    f"{name}: lower bound {bound:.3f} against {bar}")
        if threshold["uncertainty_rule"] == "core.binomial.interval":
            low, high = binomial.lower_bound(count, total, ALPHA), binomial.upper_bound(count, total, ALPHA)
            if threshold["direction"] == "at_most":
                verdict = "pass" if point <= bar else "fail" if low > bar else "undecided"
            else:
                verdict = "pass" if point >= bar else "fail" if high < bar else "undecided"
            return verdict, f"{name}: {count} of {total} ({low:.3f} to {high:.3f}) against {bar}"
    else:
        point, total = measured.get("value"), measured.get("n", 0)
        if not _number(point) or type(total) is not int:
            return "too_few", f"{name}: not measured"
        if total < need:
            return "too_few", f"{name}: {total} of the {need} needed"
    ok = point >= bar if threshold["direction"] == "at_least" else point <= bar
    return ("pass" if ok else "fail"), f"{name}: {point:g} against {bar}"


def decide(manifest: Mapping, frozen_sha256: str, results: Mapping) -> dict:
    """Each row's decision under the frozen manifest. ``results``: ``{"simulated": bool, "starts": {"eligible": n,
    "failed": n, "abandoned": n, "missing": n, "censored": n}, "rows": {row: {measure name: {"count", "n"} or
    {"value", "n"}}}}``. ``ManifestRefused`` when the manifest is not the frozen one or a start left a denominator."""
    if freeze(manifest) != frozen_sha256:
        raise ManifestRefused("these results were not gathered under this manifest: its digest is not the frozen one")
    starts = results.get("starts") if isinstance(results.get("starts"), Mapping) else {}
    if type(starts.get("eligible")) is not int or any(type(starts.get(name, 0)) is not int for name in HANDLING):
        raise ManifestRefused("results state every eligible start and how many failed, were abandoned, are missing or "
                              "were censored")
    if sum(starts.get(name, 0) for name in HANDLING) > starts["eligible"]:
        raise ManifestRefused("more starts are accounted for than were eligible")
    simulated, decided = bool(results.get("simulated")), {}
    for name, row in manifest["rows"].items():
        measures = (results.get("rows") or {}).get(name) or {}
        # a threshold reads the measure it names (several thresholds may read one measure: a bar and its floor)
        verdicts = [(*_measure(one, measures.get(one.get("measure", one["name"])), starts["eligible"]), one)
                    for one in row["thresholds"]]
        why = [said for _verdict, said, _one in verdicts]
        if any(verdict == "fail" and one.get("on_fail") != "hold" for verdict, _said, one in verdicts):
            decision = "repair"
        elif any(verdict in ("too_few", "undecided", "fail") for verdict, _said, _one in verdicts):
            decision = "hold"
        elif simulated:
            decision, why = "hold", [SIMULATED, *why]
        else:
            decision = "admit"
        decided[name] = {"decision": decision, "why": why}
    return {"manifest_sha256": frozen_sha256, "simulated": simulated, "rows": decided}


def admitted_level(decisions: Mapping) -> str:
    """The view level the recorded gate results admit: the base level, raised by each gate in ``ADMITS`` whose row
    was admitted on real (not simulated) results, in order, with none skipped."""
    level = decisions.get("base_level", BASE_LEVEL)
    entries = [e for e in decisions.get("entries") or [] if isinstance(e, Mapping)]
    for gate, (row, raised) in ADMITS.items():
        runs = [e for e in entries if e.get("gate") == gate and e.get("row") == row]
        if not runs or runs[-1].get("decision") != "admit" or runs[-1].get("simulated"):
            break
        level = raised
    return level


__all__ = ["ADMITS", "ALPHA", "BASE_LEVEL", "DECISIONS", "DIRECTIONS", "HANDLING", "ManifestRefused", "REQUIRED",
           "ROLES", "ROWS", "SIMULATED", "THRESHOLD_FIELDS", "UNCERTAINTY_RULES", "VERSION", "admitted_level", "decide",
           "freeze", "problems"]
