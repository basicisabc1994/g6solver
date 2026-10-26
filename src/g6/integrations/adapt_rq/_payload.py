# SPDX-License-Identifier: Apache-2.0
"""Bounded reads of RQ JSON records; never use RQ's unrestricted restore path."""
from __future__ import annotations

import base64
import json
import zlib
from datetime import datetime
from typing import Any

from g6.core.resources import RESOURCE_LIMIT
from g6.integrations._boundary import Refusal, json_value

MAX_BYTES = 65_536
MAX_HASH_BYTES = 262_144
# RQ's passive job fields. Execution settings outside this set are refused on recovery.
PASSIVE_FIELDS = frozenset({
    "data", "origin", "status", "timeout", "result_ttl", "created_at", "enqueued_at", "started_at", "ended_at",
    "last_heartbeat", "worker_name", "description", "meta", "exc_info", "result",
})
EMPTY_FIELDS = frozenset({"success_callback_name", "failure_callback_name", "stopped_callback_name", "group_id"})


def bounded_data(data: bytes) -> bytes:
    """RQ stores zlib data, with a legacy uncompressed fallback. Bound both forms."""
    if len(data) > MAX_BYTES:
        raise Refusal(RESOURCE_LIMIT, "Stored job field exceeds 64 KiB.")
    decoder = zlib.decompressobj()
    try:
        expanded = decoder.decompress(data, MAX_BYTES + 1)
    except zlib.error:
        # Only the legacy, already bounded, uncompressed representation is left.
        return data
    if len(expanded) > MAX_BYTES or decoder.unconsumed_tail:
        raise Refusal(RESOURCE_LIMIT, "Expanded job field exceeds 64 KiB.")
    if not decoder.eof or decoder.unused_data:
        raise Refusal("G6_E_RQ_PAYLOAD_REFUSED", "Compressed job field is incomplete or has trailing data.")
    return expanded


def read_hash(connection: Any, key: str) -> dict[str, bytes]:
    """Check lengths in Redis before fetching values; recheck the received snapshot for races."""
    if connection.hlen(key) > 64:
        raise Refusal(RESOURCE_LIMIT, "Job hash exceeds 64 fields.")
    names = connection.hkeys(key)
    if len(names) > 64 or any(len(name) > 100 for name in names):
        raise Refusal(RESOURCE_LIMIT, "Job hash field names exceed the limit.")
    if not names:
        raise Refusal("G6_E_RQ_JOB_MISSING", "No such job is stored.")
    total = 0
    for name in names:
        length = connection.hstrlen(key, name)
        total += length
        if length > MAX_BYTES or total > MAX_HASH_BYTES:
            raise Refusal(RESOURCE_LIMIT, "Stored job hash exceeds the byte limit.")
    values = connection.hmget(key, names)
    if any(value is None for value in values):
        raise Refusal("G6_E_RQ_PAYLOAD_REFUSED", "Job hash changed while it was read.")
    if any(len(value) > MAX_BYTES for value in values) or sum(map(len, values)) > MAX_HASH_BYTES:
        raise Refusal(RESOURCE_LIMIT, "Received job hash exceeds the byte limit.")
    return {name.decode("utf-8"): value for name, value in zip(names, values, strict=True)}


def read_job(queue: Any, job_id: str, job_class: Any, serializer: Any) -> tuple[Any, dict[str, bytes]]:
    job = job_class(job_id, connection=queue.connection, serializer=serializer)
    raw = read_hash(queue.connection, job.key)
    job.origin = raw.get("origin", b"").decode("utf-8")
    if job.origin != queue.name:
        raise Refusal("G6_E_RQ_QUEUE_MISMATCH", "This job belongs to a different queue.")
    data = bounded_data(raw["data"])
    payload = json_value(json.loads(data))
    if (not isinstance(payload, list) or len(payload) != 4 or not isinstance(payload[0], str)
            or not isinstance(payload[2], list) or not isinstance(payload[3], dict)):
        raise Refusal("G6_E_RQ_PAYLOAD_REFUSED", "Stored job data is not an RQ JSON call.")
    job.data = data
    job._status = raw.get("status", b"created").decode("utf-8")
    # Reconstruct only passive fields. Recovery validates the raw execution settings before using this object.
    for name in ("created_at", "enqueued_at", "started_at", "ended_at", "last_heartbeat"):
        if raw.get(name):
            setattr(job, name, datetime.fromisoformat(raw[name].decode("ascii").replace("Z", "+00:00")))
    for name in ("description", "worker_name"):
        if raw.get(name):
            setattr(job, name, raw[name].decode("utf-8"))
    if raw.get("meta"):
        job.meta = json_value(json.loads(raw["meta"]))
    if raw.get("result"):
        job._result = json_value(json.loads(raw["result"]))
    if raw.get("exc_info"):
        job._exc_info = bounded_data(raw["exc_info"]).decode("utf-8")
    return job, raw


def validate_recovery(job: Any, raw: dict[str, bytes]) -> None:
    """Only the execution policy emitted by this adapter may be put back on the queue."""
    unsupported = set(raw) - PASSIVE_FIELDS - EMPTY_FIELDS
    unsupported.update(name for name in EMPTY_FIELDS if raw.get(name))
    if unsupported or job.connection.scard(job.dependencies_key):
        raise Refusal("G6_E_RQ_METADATA_REFUSED", "Job carries unsupported execution metadata or dependencies.")
    try:
        timeout, retention = int(raw["timeout"]), int(raw["result_ttl"])
        if not 1 <= timeout <= 300 or not 0 <= retention <= 3600:
            raise ValueError("outside bounds")
    except (KeyError, ValueError):
        raise Refusal("G6_E_RQ_METADATA_REFUSED", "Recovery needs a 1-300 second timeout and 0-3600 second result retention.") from None
    job.timeout, job.result_ttl = timeout, retention


def return_value(job: Any) -> Any:
    """Read one RQ result stream entry without Result.restore's unrestricted decoders."""
    entries = job.connection.xrevrange(f"rq:results:{job.id}", "+", "-", count=1)
    if not entries:
        return None
    fields = entries[0][1]
    # RQ 2.x stores the return value as base64 JSON, and exception text as base64 zlib.
    encoded_cap = 4 * ((MAX_BYTES + 2) // 3)
    if (len(fields) > 16 or any(len(key) > 100 or len(value) > encoded_cap for key, value in fields.items())
            or sum(map(len, fields.values())) > MAX_HASH_BYTES):
        raise Refusal(RESOURCE_LIMIT, "Stored result exceeds the byte limit.")
    if fields.get(b"exc_string"):
        bounded_data(base64.b64decode(fields[b"exc_string"], validate=True))
    if fields.get(b"type") != b"1" or b"return_value" not in fields:
        return None
    data = base64.b64decode(fields[b"return_value"], validate=True)
    if len(data) > MAX_BYTES:
        raise Refusal(RESOURCE_LIMIT, "Decoded result exceeds 64 KiB.")
    return json_value(json.loads(data))
