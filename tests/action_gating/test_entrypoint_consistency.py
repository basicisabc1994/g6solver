# SPDX-License-Identifier: Apache-2.0
"""AG-012 (audit 002): the same action gets the same decision and the same audit through every entrypoint.

Entrypoints: the library call, the block, the registry, the MCP ``g6_invoke`` reply, the CLI, the
native-op evaluation and the gate-token ledger. Each is given the action a native op builds from
``(backend, op, args)``, so the comparison is like for like.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from g6.action_gating import ActionGatingBlock, ActionGatingInput, evaluate_action
from g6.action_gating.audit_store import ActionGateAuditStore
from g6.action_gating.native_ops import evaluate_backend_op, issue_gate_token
from g6.action_gating.token_store import ActionGatingStore
from g6.cli import main as cli
from g6.core.registry import get_registry
from g6.core.storage import state_path
from g6.mcp import invoke_reply

# A low-risk local write, an external payment and a critical permission change.
CASES = [
    ("local", "write_file", {"path": "notes.txt", "content": "hello"}),
    ("payments", "payment", {"target_resource": "invoice-7", "amount": 40}),
    ("iam", "permission_escalation", {"target_resource": "admin-role"}),
]
_FIELDS = ("decision", "completion_state", "reason_codes", "audit_id")


def _through_every_entrypoint(backend: str, op: str, args: dict, capsys: pytest.CaptureFixture[str]) -> dict:
    action = {**args, "action_type": op, "target_backend": backend, "action_params": dict(args)}
    params = {"action": action}
    evaluations = {
        "library": evaluate_action(action).to_response_dict(),
        "block": ActionGatingBlock().infer(ActionGatingInput(op="evaluate", action=action)).unwrap(),
        "registry": get_registry().invoke("action_gating", "evaluate", params).unwrap(),
        "mcp": (mcp := invoke_reply("action_gating", "evaluate", json.dumps(params)))["result"],
        "native_op": evaluate_backend_op(backend, op, args),
    }
    capsys.readouterr()
    # `g6 invoke` prints the result and exits 1 when the door's reply is not ok (a blocked-escalated decision)
    assert mcp["ok"] is (mcp["completion_state"] != "blocked-escalated")
    assert cli(["invoke", "action_gating", "evaluate", json.dumps(params)]) == (0 if mcp["ok"] else 1)
    evaluations["cli"] = json.loads(capsys.readouterr().out)
    found = {name: {k: ev["gate_decision"][k] for k in _FIELDS} for name, ev in evaluations.items()}
    ledger = ActionGatingStore(str(Path(tempfile.mkdtemp()) / "gate.db"))
    durable = ledger.load_gate_authorization(issue_gate_token(action, store=ledger).token)
    found["gate_token"] = {k: durable[k] for k in _FIELDS}
    return found


@pytest.mark.parametrize(("backend", "op", "args"), CASES, ids=[c[1] for c in CASES])
def test_every_entrypoint_gives_the_same_decision(backend: str, op: str, args: dict, capsys) -> None:
    found = _through_every_entrypoint(backend, op, args, capsys)
    decisions = {name: (f["decision"], f["completion_state"], tuple(f["reason_codes"])) for name, f in found.items()}
    assert len(set(decisions.values())) == 1, decisions


# write_file is low risk, so no audit record is required for it.
@pytest.mark.parametrize(("backend", "op", "args"), CASES[1:], ids=[c[1] for c in CASES[1:]])
def test_every_entrypoint_commits_its_audit_record_to_the_durable_trail(
    backend: str, op: str, args: dict, capsys
) -> None:
    found = _through_every_entrypoint(backend, op, args, capsys)
    trail = ActionGateAuditStore(str(state_path("action_gating_audit.db")))
    try:
        missing = [name for name, f in found.items() if trail.get_by_audit_id(f["audit_id"]) is None]
    finally:
        trail.close()
    assert missing == []
