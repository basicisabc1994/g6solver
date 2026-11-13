# SPDX-License-Identifier: Apache-2.0
"""The managed Elasticsearch writer (design 12.3): typed create, upsert and removal of canonical passage records in
the one index G6 manages for a project, with per-item acknowledgements.

The writer assumes the server can stop at any byte. So: the target is pinned before the first write (the alias must
resolve to exactly one index whose mapping is G6's) and checked again before each batch; a batch holds at most
:data:`MAX_BATCH_ITEMS` records and :data:`MAX_BATCH_BYTES`; each record's document id is its canonical passage id;
the bulk reply is read item by item, because an HTTP success does not stand for every item; an item whose outcome
is unknown (the connection dropped, the reply was cut) is sent again to the same id and the same index, which is
idempotent; and nothing counts as written until :func:`read_back` found it by ``_mget``.

The action gate is asked at each write call site and its decision is honoured: a block writes nothing and returns
the dry run. Removal takes passage ids from recorded membership only and needs a :class:`DeletionConsent`; a key or
a spend approval is not deletion consent. No caller names an index: the target comes from :func:`pin`.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import outside
from g6.knowledge._index import IndexRefused
from g6.knowledge.ctx_elastic import elastic, managed

MAX_BATCH_ITEMS = 200
MAX_BATCH_BYTES = 4_000_000
MAX_ATTEMPTS = 3
TARGET_REFUSED = "G6_E_KNOWLEDGE_ELASTIC_TARGET"
HELD = "G6_E_KNOWLEDGE_ELASTIC_HELD"
#: The gate decisions that let a write go ahead (``g6.sources._gate.GOES``).
GOES = frozenset({"allow", "allow_with_notice"})
Gate = Callable[[dict[str, Any]], dict[str, Any]]


class ItemAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passage_id: str
    action: Literal["create", "upsert", "remove"]
    state: Literal["acknowledged", "failed", "unknown"]
    reason: str = ""


class Target(BaseModel):
    """The concrete index the writes go to, resolved and pinned before the first write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alias: str
    concrete_index: str
    mapping_sha256: str


class DeletionConsent(BaseModel):
    """What the deletion dialog gave: made by the engine from the person's answer, never from an argument."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: str
    attribution_sha256: str
    passage_ids: tuple[str, ...]      # the registration's recorded membership: the only ids that may be removed


class Held(Exception):
    """The action gate did not let the write go ahead: nothing was sent; ``dry_run`` says what would have been."""

    def __init__(self, gate: dict[str, Any], dry_run: dict[str, Any]) -> None:
        super().__init__(f"[{HELD}] the action gate decided {gate.get('decision')}")
        self.code, self.gate, self.dry_run = HELD, gate, dry_run


def _mapping_sha(properties: Any) -> str:
    return hashlib.sha256(json.dumps(properties, sort_keys=True).encode("utf-8")).hexdigest()


def _resolve(alias: str, caller: elastic.Caller | None) -> list[str]:
    try:
        return sorted(elastic._call("GET", f"/_alias/{alias}", None, caller))
    except elastic.ElasticError as exc:
        if exc.code == elastic.NOT_FOUND:
            return []
        raise


def _check(target: Target, caller: elastic.Caller | None) -> None:
    resolved = _resolve(target.alias, caller)
    if resolved != [target.concrete_index]:
        raise IndexRefused(TARGET_REFUSED, "the managed alias no longer resolves to the one pinned index; nothing "
                                           "was written")
    held = elastic._call("GET", f"/{target.concrete_index}/_mapping", None, caller)
    found = (held.get(target.concrete_index) or {}).get("mappings") if isinstance(held, dict) else None
    if _mapping_sha(found) != target.mapping_sha256:
        raise IndexRefused(TARGET_REFUSED, "the pinned index's mapping is not G6's managed mapping; nothing was written")


def pin(project: str, *, caller: elastic.Caller | None = None) -> Target:
    """The project's managed index, created with G6's mapping when absent. :class:`IndexRefused` when the alias
    resolves to more than one index, or to one whose mapping differs."""
    alias = elastic.check_index(managed.index_name(project))
    resolved = _resolve(alias, caller)
    if len(resolved) > 1:
        raise IndexRefused(TARGET_REFUSED, "the managed alias resolves to more than one index; G6 writes to exactly one")
    concrete = resolved[0] if resolved else elastic.check_index(alias + "-000001")
    if not resolved:
        elastic._call("PUT", f"/{concrete}", {"mappings": managed.MAPPING, "aliases": {alias: {}}}, caller)
    target = Target(alias=alias, concrete_index=concrete, mapping_sha256=_mapping_sha(managed.MAPPING))
    _check(target, caller)
    return target


def _batches(lines: Sequence[tuple[str, bytes]]) -> list[list[tuple[str, bytes]]]:
    out: list[list[tuple[str, bytes]]] = [[]]
    size = 0
    for ident, payload in lines:
        if out[-1] and (len(out[-1]) >= MAX_BATCH_ITEMS or size + len(payload) > MAX_BATCH_BYTES):
            out.append([])
            size = 0
        out[-1].append((ident, payload))
        size += len(payload)
    return [b for b in out if b]


def _bulk(target: Target, batch: Sequence[tuple[str, bytes]], verb: str,
          caller: elastic.Caller | None) -> dict[str, tuple[str, str]]:
    """One bulk request; each item's state from the reply's own item, ``unknown`` for all when the reply is lost."""
    send = caller or outside.call_service
    headers = {"Content-Type": "application/x-ndjson", "Accept": "application/json"}
    key = elastic.keys.get(elastic.ELASTIC_KEY.name)
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    try:
        reply = send(elastic.ELASTICSEARCH, "POST", f"/{target.concrete_index}/_bulk",
                     body=b"".join(payload for _, payload in batch), headers=headers, timeout=elastic.TIMEOUT,
                     max_bytes=elastic.MAX_REPLY)
        items = json.loads(reply.body)["items"]
        if len(items) != len(batch):
            raise ValueError("the reply does not answer every item")
    except (outside.ServiceError, ValueError, KeyError, TypeError, UnicodeDecodeError, OSError) as exc:
        return {ident: ("unknown", type(exc).__name__) for ident, _ in batch}
    states = {}
    for (ident, _), item in zip(batch, items, strict=True):
        body = item.get(verb) if isinstance(item, dict) else None
        status = body.get("status") if isinstance(body, dict) else None
        same = isinstance(body, dict) and body.get("_id") == ident and body.get("_index") == target.concrete_index
        fine = same and isinstance(status, int) and (200 <= status < 300 or (verb == "delete" and status == 404))
        states[ident] = ("acknowledged", "") if fine else ("failed", f"status {status}")
    return states


class Stopped(Exception):
    """A write that ended before every batch was sent because the source may no longer be used: why, and how many
    records the server had acknowledged by then."""

    def __init__(self, why: str, sent: int) -> None:
        super().__init__(f"stopped: {why}")
        self.why, self.sent = why, sent


def _run(target: Target, lines: Sequence[tuple[str, bytes]], verb: str, action: str, gate: Gate | None,
         described: dict[str, Any], caller: elastic.Caller | None,
         still: Callable[[], str] | None = None) -> list[ItemAck]:
    decision = {"kind": "action_gate", "decision": "block", "message": "no action gate was given"}
    if gate is not None:
        try:
            decision = gate(described)
        except Exception as exc:  # noqa: BLE001 - a gate that cannot decide holds the write
            decision = {"kind": "action_gate", "decision": "block", "message": type(exc).__name__}
    if decision.get("decision") not in GOES:
        raise Held(decision, {"would_send": len(lines), "to": target.concrete_index, "action": action})
    state: dict[str, tuple[str, str]] = {ident: ("unknown", "not sent") for ident, _ in lines}
    for _ in range(MAX_ATTEMPTS):
        pending = [(ident, payload) for ident, payload in lines if state[ident][0] == "unknown"]
        if not pending:
            break
        for batch in _batches(pending):
            why = still() if still is not None else ""   # a stop stops: asked again before every batch
            if why:
                raise Stopped(why, sum(1 for one in state.values() if one[0] == "acknowledged"))
            _check(target, caller)   # the target is checked again before every batch
            state.update(_bulk(target, batch, verb, caller))
    return [ItemAck(passage_id=ident, action=action, state=state[ident][0], reason=state[ident][1])
            for ident, _ in lines]


def _describe(target: Target, action: str, count: int) -> dict[str, Any]:
    return {"action_type": "call_api", "target_backend": "elasticsearch",
            "target_resource": f"{target.concrete_index} (loopback)",
            "description": f"{action} {count} G6 passage records in the local Elasticsearch index G6 manages",
            "action_params": {"op": action, "items": count}, "requested_by": f"knowledge.elastic_managed.{action}",
            "actor_type": "agent", "external_side_effect": False, "blast_radius": "local",
            "reversibility": "reversible" if action != "remove" else "irreversible",
            "safety_domains": ["data_integrity"],
            "verification_contract": {
                "expected_result": "every item acknowledged and found again by id",
                "observation_window": "until the bulk request returns", "monitor": "the bulk reply's own items",
                "leading_indicators": ["each item's status"], "lagging_indicators": ["a read-back by _mget"],
                "verification_time": "before the generation is published",
                "stop_threshold": "an item that is failed or unknown after the retries",
                "rollback_procedure": "the generation is not published; the previous one stays in use"}}


def write(target: Target, records: Sequence[Mapping[str, Any]], *, gate: Gate | None, upsert: bool = True,
          caller: elastic.Caller | None = None, still: Callable[[], str] | None = None) -> list[ItemAck]:
    """Write canonical records (``managed.encode``), each under its passage id; one :class:`ItemAck` per record.
    ``still`` says, before every batch, why the records' source may no longer be used (``""``: it may): the first
    reason ends the write with :class:`Stopped` and no further batch is sent."""
    lines = []
    for record in records:
        ident = record["passage_id"]
        if record.get("schema") != managed.SCHEMA or not isinstance(ident, str) or len(ident) != 64:
            raise IndexRefused(TARGET_REFUSED, "only canonical G6 passage records are written")
        head = {"index" if upsert else "create": {"_id": ident}}
        lines.append((ident, (json.dumps(head) + "\n" + json.dumps(dict(record), sort_keys=True) + "\n").encode("utf-8")))
    verb, action = ("index", "upsert") if upsert else ("create", "create")
    return _run(target, lines, verb, action, gate, _describe(target, action, len(lines)), caller, still)


def remove(target: Target, passage_ids: Sequence[str], *, consent: DeletionConsent, gate: Gate | None,
           caller: elastic.Caller | None = None) -> list[ItemAck]:
    """Remove records by passage id. Every id must be in the consent's recorded membership: a caller cannot choose a
    deletion target."""
    if not isinstance(consent, DeletionConsent) or not consent.attribution_sha256:
        raise IndexRefused(TARGET_REFUSED, "a removal needs the deletion dialog's consent")
    allowed = set(consent.passage_ids)
    if any(pid not in allowed for pid in passage_ids):
        raise IndexRefused(TARGET_REFUSED, "a removal names only passages of the consented registration's membership")
    lines = [(pid, (json.dumps({"delete": {"_id": pid}}) + "\n").encode("utf-8")) for pid in dict.fromkeys(passage_ids)]
    return _run(target, lines, "delete", "remove", gate, _describe(target, "remove", len(lines)), caller)


def read_back(target: Target, passage_ids: Sequence[str], *, caller: elastic.Caller | None = None) -> dict[str, bool]:
    """Whether each passage id is held now, by ``_mget`` on the pinned index, each found record decoded and checked."""
    found = {pid: False for pid in passage_ids}
    wanted = list(found)
    for start in range(0, len(wanted), MAX_BATCH_ITEMS):
        chunk = wanted[start:start + MAX_BATCH_ITEMS]
        try:
            reply = elastic._call("POST", f"/{target.concrete_index}/_mget", {"ids": chunk}, caller)
            docs = reply["docs"]
        except (elastic.ElasticError, KeyError, TypeError):
            continue
        for doc in docs if isinstance(docs, list) else []:
            if isinstance(doc, dict) and doc.get("found") and doc.get("_id") in found:
                try:
                    managed.decode(doc)
                    found[doc["_id"]] = True
                except IndexRefused:
                    found[doc["_id"]] = False
    return found


__all__ = ["GOES", "HELD", "MAX_ATTEMPTS", "MAX_BATCH_BYTES", "MAX_BATCH_ITEMS", "TARGET_REFUSED", "DeletionConsent",
           "Held", "ItemAck", "Stopped", "Target", "pin", "read_back", "remove", "write"]
