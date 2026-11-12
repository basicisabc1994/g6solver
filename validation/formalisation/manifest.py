# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A row's fixture manifest, frozen before the row runs (design 15.7).

A manifest names the row, the supported fragments, the cases with their labels' author, the adapter and renderer
versions, what the environment had at freeze time, the numerical thresholds and the pass/stop rule. It is identified
by the digest of its canonical JSON. ``freeze`` writes it once and refuses to overwrite; a card names the digest of
the manifest it was run on, so a threshold changed after a run is another manifest and another card. A cohort that
informed tuning is retired (``retire``) and refused for a confirmatory run. Development and final cohorts are kept
apart (``disjoint``), and a case's labels are never written by the family that wrote the candidate.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from g6.core.ledger import canonical
from g6.harness.formalisation import FRAGMENTS

ROWS = ("proposal", "rendering", "correspondence", "archive_defects", "questions", "cost")
COHORTS = ("development", "final")
RETIRED = Path(__file__).parent / "fixtures" / "retired.json"


class Frozen(ValueError):
    """The manifest is frozen, malformed or retired: nothing is written or run."""


@dataclass(frozen=True)
class Case:
    case_id: str
    cohort: Literal["development", "final"]
    fragment: str
    requirement: str
    reference: str
    inputs: tuple[tuple[str, str], ...]
    result_sort: str
    label_author: str
    domain: tuple[str, ...] = ()
    #: An exhaustive finite table over the domain: ((inputs, expected result), ...).
    table: tuple[tuple[Mapping[str, Any], Any], ...] = ()
    #: Or the name of a separately written oracle.
    oracle: str = ""
    #: A reference implementation and wrong ones, for the correspondence row.
    code: str = ""
    wrong_code: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Case:
        return cls(**{**data, "inputs": tuple(tuple(pair) for pair in data["inputs"]),
                      "domain": tuple(data.get("domain", ())),
                      "table": tuple((dict(row[0]), row[1]) for row in data.get("table", ())),
                      "wrong_code": tuple(data.get("wrong_code", ()))})

    def to_dict(self) -> dict:
        return {**asdict(self), "inputs": [list(pair) for pair in self.inputs], "domain": list(self.domain),
                "table": [[dict(row[0]), row[1]] for row in self.table], "wrong_code": list(self.wrong_code)}


@dataclass(frozen=True)
class Manifest:
    row: Literal["proposal", "rendering", "correspondence", "archive_defects", "questions", "cost"]
    fragments: tuple[str, ...]
    cases: tuple[Case, ...]
    adapter_versions: Mapping[str, str]
    renderer_versions: Mapping[str, str]
    environment: Mapping[str, str]
    thresholds: Mapping[str, float]
    stop_rule: str
    #: Who writes the candidates this row scores ("g6:mutation", "model:<family>"): never a case's label author.
    candidate_authors: tuple[str, ...] = field(default=("g6:mutation",))

    def to_dict(self) -> dict:
        return {"row": self.row, "fragments": list(self.fragments), "cases": [c.to_dict() for c in self.cases],
                "adapter_versions": dict(self.adapter_versions), "renderer_versions": dict(self.renderer_versions),
                "environment": dict(self.environment), "thresholds": dict(self.thresholds),
                "stop_rule": self.stop_rule, "candidate_authors": list(self.candidate_authors)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Manifest:
        return cls(row=data["row"], fragments=tuple(data["fragments"]),
                   cases=tuple(Case.from_dict(c) for c in data["cases"]),
                   adapter_versions=dict(data["adapter_versions"]), renderer_versions=dict(data["renderer_versions"]),
                   environment=dict(data["environment"]), thresholds=dict(data["thresholds"]),
                   stop_rule=data["stop_rule"], candidate_authors=tuple(data["candidate_authors"]))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical(self.to_dict()).encode("ascii")).hexdigest()

    @property
    def cohort(self) -> str:
        found = {case.cohort for case in self.cases}
        return found.pop() if len(found) == 1 else ""


def _family(author: str) -> str:
    parts = author.split(":")
    return ":".join(parts[:2]) if parts[0] == "model" else author


def problems(manifest: Manifest) -> list[str]:
    """Everything that keeps a manifest from being frozen; empty when it may be."""
    out = []
    if manifest.row not in ROWS:
        out.append(f"row is one of {', '.join(ROWS)}")
    if not manifest.fragments:
        out.append("no supported fragment is named")
    for fragment in manifest.fragments:
        entry = FRAGMENTS.get(fragment)
        if entry is None:
            out.append(f"{fragment} is not a supported fragment")
        elif (manifest.adapter_versions.get(fragment) != entry["adapter"]
              or manifest.renderer_versions.get(fragment) != entry["renderer"]):
            out.append(f"{fragment}: the adapter and renderer versions are not the ones G6 has now")
    floor, ceiling = manifest.thresholds.get("min_right_acceptance"), manifest.thresholds.get("max_wrong_acceptance")
    if not isinstance(floor, int | float) or isinstance(floor, bool) or not 0 < floor <= 1:
        out.append("min_right_acceptance must be above zero (and at most 1): refusing everything must fail")
    if not isinstance(ceiling, int | float) or isinstance(ceiling, bool) or not 0 <= ceiling < 1:
        out.append("max_wrong_acceptance must be in [0, 1)")
    if not manifest.stop_rule.strip():
        out.append("no pass/stop rule")
    if not manifest.environment:
        out.append("the environment's availability at freeze time is not recorded")
    if not manifest.cases:
        out.append("no cases")
    if len({case.case_id for case in manifest.cases}) != len(manifest.cases):
        out.append("a case id is used twice")
    if not manifest.cohort and manifest.cases:
        out.append("a manifest holds one cohort: development and final cases are kept apart")
    candidates = {_family(author) for author in manifest.candidate_authors}
    if not candidates:
        out.append("no candidate author is named")
    for case in manifest.cases:
        if case.cohort not in COHORTS or case.fragment not in manifest.fragments:
            out.append(f"{case.case_id}: its cohort or fragment is not the manifest's")
        if not case.label_author.strip() or _family(case.label_author) in candidates:
            out.append(f"{case.case_id}: the label author ({case.label_author or 'none'}) is a candidate's author; "
                       "one model never writes both the candidate and its labels")
        if not case.table and not case.oracle:
            out.append(f"{case.case_id}: neither an exhaustive table nor a separately written oracle")
        names = [name for name, _ in case.inputs]
        if any(sorted(row[0]) != sorted(names) for row in case.table):
            out.append(f"{case.case_id}: a table row does not give exactly the case's inputs")
        if not case.requirement.strip() or not case.reference.strip():
            out.append(f"{case.case_id}: no requirement or no reference formula")
    return out


def disjoint(development: Manifest, final: Manifest) -> list[str]:
    """What a development and a final cohort share; empty when they are apart."""
    out = []
    if development.cohort != "development" or final.cohort != "final":
        out.append("the cohorts are not one development and one final")
    for what in ("case_id", "requirement", "reference"):
        shared = {getattr(c, what) for c in development.cases} & {getattr(c, what) for c in final.cases}
        out += [f"{what} shared by both cohorts: {one[:60]}" for one in sorted(shared)]
    return out


def freeze(manifest: Manifest, path: Path) -> str:
    """Write the manifest once, with its digest, and return the digest. A manifest with problems is refused, and so
    is any path that already holds one: a frozen manifest cannot change."""
    found = problems(manifest)
    if found:
        raise Frozen("the manifest cannot be frozen: " + "; ".join(found))
    path = Path(path)
    body = json.dumps({"sha256": manifest.sha256, "manifest": manifest.to_dict()}, indent=1, sort_keys=True) + "\n"
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as out:
            out.write(body)
    except FileExistsError:
        raise Frozen(f"{path.name} is already frozen; a changed manifest is a new file") from None
    return manifest.sha256


def load(path: Path) -> Manifest:
    """The frozen manifest at ``path``, refused when its bytes are not the ones its digest was taken over."""
    held = json.loads(Path(path).read_text(encoding="utf-8"))
    manifest = Manifest.from_dict(held["manifest"])
    if manifest.sha256 != held["sha256"]:
        raise Frozen(f"{Path(path).name} was changed after it was frozen")
    return manifest


def retired(registry: Path = RETIRED) -> dict[str, dict]:
    registry = Path(registry)
    return json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}


def retire(manifest_sha256: str, reason: str, *, registry: Path = RETIRED) -> None:
    """Record that a cohort informed tuning or routing: it is never used for a confirmatory run again."""
    if not reason.strip():
        raise Frozen("a cohort is retired with the reason")
    held = retired(registry)
    held.setdefault(manifest_sha256, {"reason": reason, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
    Path(registry).write_text(json.dumps(held, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def confirmatory(manifest: Manifest, *, registry: Path = RETIRED) -> None:
    """``Frozen`` unless ``manifest`` may decide a bar: a final cohort that was never retired."""
    if manifest.cohort != "final":
        raise Frozen("a confirmatory run uses a final cohort; a development cohort decides no bar")
    if manifest.sha256 in retired(registry):
        raise Frozen("this cohort was retired (" + retired(registry)[manifest.sha256]["reason"] + "); a confirmatory "
                     "run needs a fresh final cohort")


__all__ = ["COHORTS", "RETIRED", "ROWS", "Case", "Frozen", "Manifest", "confirmatory", "disjoint", "freeze", "load",
           "problems", "retire", "retired"]