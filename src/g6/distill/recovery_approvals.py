# SPDX-License-Identifier: Apache-2.0
"""Recovery approvals minted by the client-dialog adapter, consumed once by the gate.

The MCP adapter (``g6.mcp.distill``) registers each approval it issues after an accepted client dialog; the gate
consumes it here, so the protected evaluator never imports the MCP surface. Only an approval registered by the
adapter, unchanged since issue, scoped to this binding and still within its 60-second life can be consumed, once.
Same-user shell access is outside this boundary.
"""
from __future__ import annotations

from dataclasses import asdict

from g6.core.ledger import digest

_ISSUED: dict = {}


def register(record, authority) -> None:
    """Called by the dialog adapter only, after the person accepted the exact binding."""
    _ISSUED[record.approval_id] = (authority, digest(asdict(record)))


def forget(approval_id) -> None:
    _ISSUED.pop(approval_id, None)


def consume(approval_id, binding, *, authority=None) -> bool:
    issued = _ISSUED.pop(approval_id, None) if isinstance(approval_id, str) else None
    if issued is None:
        return False
    issuer, original = issued
    if authority is not None and issuer is not authority:
        return False
    authority = issuer
    record = authority.resolve(approval_id)
    scope = "distill:reconfirm:" + digest(binding)
    if (record is None or record.scope != scope or record.run_id != binding["interval_id"]
            or not record.single_use or record.expires_at is None
            or not 0 < record.expires_at - record.created_at <= 60.
            or digest(asdict(record)) != original):
        return False
    return authority.check(approval_id, scope=scope, run_id=binding["interval_id"], consume=True, require_human=True)
