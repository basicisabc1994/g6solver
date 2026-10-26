# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""An episode as an append-only, hash-chained record (ECHA spec 4.2).

Events hold ids, hashes, codes and counts: never a probe's input, a counterexample or any text. Each event carries the
hash of the one before it, so an edited, removed or reordered event shows. A forecast is a prospective statement, so it
is refused once the outcome is written (it is never backfilled), and the outcome must be preceded by a forecast or by
the explicit statement that none was available. The outcome is one of the existing wire states; no alias is accepted.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .flag import require_research
from .records import SCHEMA, digest, hex64_of, text_of, texts_of

EVENT_KINDS = ("plan", "action", "activation_attempt", "tool_result", "evidence_received", "repair", "adjudication",
               "forecast", "outcome", "incident")
OUTCOME_STATES = ("verified", "qualified-draft", "blocked-escalated", "not_recorded")
HEADER_KEYS = ("episode_id", "task_id", "task_family", "repository_or_group_id", "split_id", "standard_hash",
               "executor_version", "harness_version", "oracle_version", "component_versions", "model_id",
               "model_configuration", "seed", "cost_budget", "independent_oracle_access")
_TEXT_LIMIT = 200
_LIST_LIMIT = 50


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= _TEXT_LIMIT)


def _detail(detail: Any) -> dict:
    """An event's detail: short scalars and short lists of them, so it can hold an id or a count but not content."""
    if not isinstance(detail, Mapping):
        raise ValueError("detail must be a mapping of short values")  # noqa: TRY004
    out = {}
    for key, value in detail.items():
        ok = _scalar(value) or (isinstance(value, (list, tuple)) and len(value) <= _LIST_LIMIT
                                and all(_scalar(item) for item in value))
        if not isinstance(key, str) or not key or not ok:
            raise ValueError(f"detail {key!r} must be a short value (an id, a code, a count or a short list of them), "
                             f"never text or a nested structure")
        out[key] = list(value) if isinstance(value, (list, tuple)) else value
    return out


def _event(seq: int, kind: str, detail: dict, prev: str) -> dict:
    return {"seq": seq, "kind": kind, "detail": detail, "prev": prev,
            "sha256": digest({"seq": seq, "kind": kind, "detail": detail, "prev": prev})}


def verify_episode_body(body: Mapping) -> list[str]:
    """What is wrong with a stored episode: its header against its genesis, its chain, its head and its outcome."""
    problems = []
    try:
        header = {key: body[key] for key in HEADER_KEYS}
    except KeyError as exc:
        return [f"episode header is missing {exc.args[0]}"]
    prev = digest(header)
    if body.get("genesis") != prev:
        problems.append("episode genesis does not match its header")
    events = body.get("events") or []
    for i, event in enumerate(events):
        if event.get("seq") != i or event.get("prev") != prev:
            problems.append(f"episode event {i}: out of order or not linked to the event before it")
        if _event(i, event.get("kind"), event.get("detail"), event.get("prev"))["sha256"] != event.get("sha256"):
            problems.append(f"episode event {i}: its hash does not match its content")
        prev = event.get("sha256")
    if body.get("head") != prev:
        problems.append("episode head does not match its last event")
    kinds = [e.get("kind") for e in events]
    if "outcome" not in kinds:
        problems.append("episode has no outcome event")
    elif "forecast" not in kinds[:kinds.index("outcome")]:
        problems.append("episode outcome is not preceded by a forecast")
    outcome = next((e for e in events if e.get("kind") == "outcome"), None)
    if outcome is not None and (outcome.get("detail") or {}).get("state") != body.get("outcome_state"):
        problems.append("episode outcome_state does not match its outcome event")
    return problems + _builder_rules(body, events, kinds)


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _builder_rules(body: Mapping, events: list, kinds: list) -> list[str]:
    """Every rule ``Episode`` applies while it builds, applied again to a stored episode, so a record made by hand, or
    edited and re-sealed, cannot carry what the builder would have refused."""
    found = []
    if body.get("schema_version") != SCHEMA:
        found.append(f"episode schema_version is not {SCHEMA}")
    if body.get("outcome_state") not in OUTCOME_STATES:
        found.append(f"episode outcome_state is not one of {OUTCOME_STATES} (no alias is accepted)")
    if not isinstance(body.get("independent_oracle_access"), bool):
        found.append("episode independent_oracle_access must be true or false")
    for name in ("run_cost", "cost_budget"):
        if body.get(name) is not None and not _finite(body.get(name)):
            found.append(f"episode {name} must be a finite number of at least 0, or None")
    if not _finite(body.get("latency_seconds")):
        found.append("episode latency_seconds must be a finite number of at least 0")
    artifact = body.get("candidate_artifact_hash")
    if artifact != "" and not (isinstance(artifact, str) and len(artifact) == 64
                               and all(c in "0123456789abcdef" for c in artifact)):
        found.append("episode candidate_artifact_hash must be empty or a sha256 hex digest")
    if kinds.count("outcome") > 1:
        found.append("episode has more than one outcome event")
    if "outcome" in kinds and "forecast" in kinds[kinds.index("outcome") + 1:]:
        found.append("episode has a forecast after its outcome (a forecast is prospective)")
    for i, event in enumerate(events):
        if event.get("kind") not in EVENT_KINDS:
            found.append(f"episode event {i}: kind is not one of {EVENT_KINDS}")
        try:
            _detail(event.get("detail"))
        except ValueError as exc:
            found.append(f"episode event {i}: {exc}")
    return found


@dataclass(frozen=True)
class EpisodeRecord:
    """A finished episode (spec 4.2): the header, the chained events, and what the outcome rests on."""

    header: Mapping[str, Any]
    genesis: str
    events: tuple[dict, ...]
    head: str
    candidate_artifact_hash: str
    outcome_state: str
    evidence_refs: tuple[str, ...]
    known_unknowns: tuple[str, ...]
    run_cost: float | None
    latency_seconds: float
    schema_version: str = SCHEMA

    @property
    def episode_id(self) -> str:
        return self.header["episode_id"]

    @property
    def standard_hash(self) -> str:
        return self.header["standard_hash"]

    @property
    def independent_oracle_access(self) -> bool:
        return self.header["independent_oracle_access"]

    def to_dict(self) -> dict:
        return {**dict(self.header), "genesis": self.genesis, "events": [dict(e) for e in self.events],
                "head": self.head, "candidate_artifact_hash": self.candidate_artifact_hash,
                "outcome_state": self.outcome_state, "evidence_refs": list(self.evidence_refs),
                "known_unknowns": list(self.known_unknowns), "run_cost": self.run_cost,
                "latency_seconds": self.latency_seconds, "schema_version": self.schema_version}


class Episode:
    """The builder of one episode. Append events in order; ``freeze`` closes it with its outcome."""

    def __init__(self, *, episode_id: str, task_id: str, task_family: str, repository_or_group_id: str, split_id: str,
                 standard_hash: str, executor_version: str, harness_version: str, oracle_version: str,
                 component_versions: Mapping[str, str], model_id: str, model_configuration: Mapping[str, Any],
                 cost_budget: float | None = None, seed: int | None = None,
                 independent_oracle_access: bool = False) -> None:
        require_research()
        for name, value in (("episode_id", episode_id), ("task_id", task_id), ("task_family", task_family),
                            ("repository_or_group_id", repository_or_group_id), ("split_id", split_id),
                            ("executor_version", executor_version), ("harness_version", harness_version),
                            ("oracle_version", oracle_version), ("model_id", model_id)):
            text_of(name, value)
        hex64_of("standard_hash", standard_hash)
        if not isinstance(independent_oracle_access, bool):
            raise ValueError("independent_oracle_access must be true or false")  # noqa: TRY004
        if cost_budget is not None and (isinstance(cost_budget, bool) or not isinstance(cost_budget, (int, float))
                                        or cost_budget < 0):
            raise ValueError("cost_budget must be a number of at least 0, or None")
        self._header = {"episode_id": episode_id, "task_id": task_id, "task_family": task_family,
                        "repository_or_group_id": repository_or_group_id, "split_id": split_id,
                        "standard_hash": standard_hash, "executor_version": executor_version,
                        "harness_version": harness_version, "oracle_version": oracle_version,
                        "component_versions": {text_of("component_versions", k): text_of("component_versions", v)
                                               for k, v in dict(component_versions).items()},
                        "model_id": model_id, "model_configuration": _detail(model_configuration), "seed": seed,
                        "cost_budget": cost_budget, "independent_oracle_access": independent_oracle_access}
        self._genesis = digest(self._header)
        self._events: list[dict] = []
        self._frozen = False

    def append(self, kind: str, detail: Mapping[str, Any]) -> dict:
        if self._frozen:
            raise ValueError("episode is frozen")
        if kind not in EVENT_KINDS:
            raise ValueError(f"kind must be one of {EVENT_KINDS}")
        kinds = [e["kind"] for e in self._events]
        if kind == "forecast" and "outcome" in kinds:
            raise ValueError("a forecast is prospective: it cannot be written after the outcome")
        if kind == "outcome":
            if "outcome" in kinds:
                raise ValueError("the outcome is written once")
            if "forecast" not in kinds:
                raise ValueError("the outcome needs a forecast, or a stated absence of one, before it (forecast)")
        prev = self._events[-1]["sha256"] if self._events else self._genesis
        event = _event(len(self._events), kind, _detail(detail), prev)
        self._events.append(event)
        return dict(event)

    def freeze(self, *, candidate_artifact_hash: str, outcome_state: str, evidence_refs: tuple[str, ...] | list[str],
               known_unknowns: tuple[str, ...] | list[str], run_cost: float | None,
               latency_seconds: float) -> EpisodeRecord:
        if outcome_state not in OUTCOME_STATES:
            raise ValueError(f"outcome_state must be one of the wire states {OUTCOME_STATES}")
        if candidate_artifact_hash != "":
            hex64_of("candidate_artifact_hash", candidate_artifact_hash)
        if self._frozen:
            raise ValueError("episode is frozen")
        if run_cost is not None and not _finite(run_cost):
            raise ValueError("run_cost must be a finite number of at least 0, or None when it was not measured")
        if not _finite(latency_seconds):
            raise ValueError("latency_seconds must be a finite number of at least 0")
        refs, unknowns = texts_of("evidence_refs", list(evidence_refs)), texts_of("known_unknowns", list(known_unknowns))
        outcome = next((e for e in self._events if e["kind"] == "outcome"), None)
        if outcome is None:
            self.append("outcome", {"state": outcome_state})
        elif outcome["detail"].get("state") != outcome_state:
            raise ValueError("outcome_state does not match the outcome event already written")
        self._frozen = True
        return EpisodeRecord(header=copy.deepcopy(self._header), genesis=self._genesis,
                             events=tuple(copy.deepcopy(e) for e in self._events),
                             head=self._events[-1]["sha256"], candidate_artifact_hash=candidate_artifact_hash,
                             outcome_state=outcome_state, evidence_refs=refs, known_unknowns=unknowns,
                             run_cost=run_cost, latency_seconds=latency_seconds)
