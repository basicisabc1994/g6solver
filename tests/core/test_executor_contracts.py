# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral wire contracts carry observations, never acceptance authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from g6.core import executor_contracts as ec
from pydantic import ValidationError

SHA = "a" * 64
OTHER = "b" * 64
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def task(**changes):
    data = {"request_id": "req-1", "owner_id": "alice", "mission_id": "mission-1", "run_id": "run-1",
                "task_id": "patch", "attempt": 1, "idempotency_key": "run-1", "adapter_id": "local-tool-loop",
                "adapter_version": "1", "executor_id": "local-tool-loop", "executor_version": "1",
                "workspace_id": "work-1", "baseline_sha256": SHA, "standard_sha256": SHA, "premortem_sha256": SHA,
                "instructions": "Fix the accepted objective; do not change the frozen tests.",
                "model": ec.ModelIdentity(provider="ollama", model="gpt-oss:20b"),
                "permissions": ec.AgentPermissions(read_files=("policy.py", "tests/test_policy.py"),
                                                write_files=("policy.py",), tools=("read_file", "write_file", "finish")),
                "limits": ec.ExecutionLimits(max_model_calls=3, max_tool_calls=8, max_output_tokens=100,
                                          max_output_bytes=5000, max_runtime_seconds=30,
                                          cost_ceiling=ec.Money(amount_minor_units=0, currency="AUD")),
                "deadline": NOW + timedelta(seconds=30)}
    return ec.AgentTask(**(data | changes))


def capabilities(**changes):
    data = {"adapter_id": "local-tool-loop", "adapter_version": "1", "executor_id": "local-tool-loop",
                "executor_version": "1", "tools": ("read_file", "write_file", "finish"),
                "effects": ("workspace_read", "workspace_write", "local_inference"),
                "isolation": "trusted_same_user", "environment": "controlled-local",
                "models": (ec.ModelIdentity(provider="ollama", model="gpt-oss:20b"),),
                "status": True, "artifacts": True, "cancellation": "unsupported", "resume": False,
                "idempotency": False, "max_output_bytes": 10000, "max_runtime_seconds": 60}
    return ec.AgentCapabilities(**(data | changes))


def handle(t):
    return ec.AgentRunHandle.from_task(t, backend_id="observed-run-1")


def test_task_round_trip_has_stable_binding_without_authority_fields():
    t = task()
    assert ec.AgentTask.model_validate_json(t.model_dump_json()) == t
    assert t.sha256 == ec.AgentTask.model_validate_json(t.model_dump_json()).sha256
    assert t.sha256 != task(instructions="different work").sha256
    assert t.sha256 != task(limits=t.limits.model_copy(update={"max_model_calls": 2})).sha256
    assert handle(t).matches(t)
    assert not handle(t).matches(task(owner_id="bob"))
    assert not handle(t).matches(task(standard_sha256=OTHER))
    assert not handle(t).matches(task(workspace_id="other-workspace"))
    with pytest.raises(ValidationError):
        t.instructions = "weaken the standard"


@pytest.mark.parametrize("version", [True, "1", 0, 2])
def test_unknown_or_coerced_contract_versions_refused(version):
    with pytest.raises(ValidationError):
        task(contract_version=version)


@pytest.mark.parametrize("field", ["completion_state", "verified", "independent", "approval", "evaluator"])
def test_executor_cannot_add_acceptance_or_approval_fields(field):
    with pytest.raises(ValidationError):
        task(**{field: True})
    with pytest.raises(ValidationError):
        ec.AgentStatus(handle=handle(task()), state="finished", dispatch="sent", observed_at=NOW,
                       source="observed", usage=ec.ExecutorUsage(), **{field: True})


@pytest.mark.parametrize("path", [".", "./", "../other.py", "/other.py", "C:/other.py", "file:stream", "a\\b.py"])
def test_wire_file_paths_are_canonical_relative_files(path):
    with pytest.raises(ValidationError):
        ec.AgentPermissions(read_files=(path,), write_files=(), tools=())


def test_duplicates_and_writes_without_read_scope_refused():
    with pytest.raises(ValidationError):
        ec.AgentPermissions(read_files=("a.py", "a.py"), tools=())
    with pytest.raises(ValidationError):
        ec.AgentPermissions(read_files=("a.py",), write_files=("b.py",), tools=())


@pytest.mark.parametrize("amount", [-1, True, "2", float("inf"), float("nan")])
def test_money_and_resource_counts_are_finite_nonnegative_integers(amount):
    with pytest.raises(ValidationError):
        ec.Money(amount_minor_units=amount, currency="AUD")
    with pytest.raises(ValidationError):
        ec.ExecutorUsage(output_tokens=amount)


def test_unknown_usage_is_not_zero_or_a_reservation():
    u = ec.ExecutorUsage(input_tokens=2)
    assert u.output_tokens is None and u.cost is None
    assert not u.tokens_complete
    assert not ec.ExecutorUsage(input_tokens=0, output_tokens=0).tokens_complete
    assert ec.ExecutorUsage(input_tokens=0, output_tokens=0, token_scope="complete").tokens_complete
    with pytest.raises(ValidationError):
        ec.ExecutorUsage(reserved_tokens=50)


def test_capability_intersection_refuses_gaps_without_self_qualification():
    t = task()
    assert ec.agent_incompatibilities(t, capabilities()) == ()
    assert "tools" in ec.agent_incompatibilities(t, capabilities(tools=("read_file",)))
    assert "model" in ec.agent_incompatibilities(t, capabilities(models=()))
    assert "adapter" in ec.agent_incompatibilities(t, capabilities(adapter_version="2"))
    assert "output_bytes" in ec.agent_incompatibilities(t, capabilities(max_output_bytes=5))
    network_task = task(permissions=t.permissions.model_copy(update={"network": ("approved.example",)}))
    assert "network" in ec.agent_incompatibilities(network_task, capabilities())
    # Passing the declaration comparison is not measured qualification or permission.
    assert capabilities().qualification_refs == ()


def test_manifest_matches_task_and_actual_bytes_not_provider_brand():
    t = task()
    manifest = ec.ArtifactManifest(handle=handle(t), baseline_sha256=t.baseline_sha256,
                                   files=(ec.FileArtifact.from_text("policy.py", "repaired\n"),))
    assert manifest.matches(t, {"policy.py": "repaired\n"})
    assert not manifest.matches(t, {"policy.py": "changed since execution\n"})
    assert not manifest.matches(t, {"policy.py": "repaired\n", "other.py": "extra"})
    assert not manifest.matches(task(standard_sha256=OTHER), {"policy.py": "repaired\n"})
    with pytest.raises(ValidationError):
        ec.ArtifactManifest(handle=handle(t), baseline_sha256=SHA, files=manifest.files * 2)


def test_cancel_request_cannot_claim_termination_or_rollback():
    receipt = ec.CancellationReceipt(handle=handle(task()), requested_at=NOW,
                                     acknowledged=False, process_stopped=False, effects="unknown")
    assert receipt.effects == "unknown"
    with pytest.raises(ValidationError):
        ec.CancellationReceipt(handle=handle(task()), requested_at=NOW,
                               acknowledged=False, process_stopped=True, effects="unknown")
    with pytest.raises(ValidationError):
        ec.CancellationReceipt(handle=handle(task()), requested_at=NOW,
                               acknowledged=True, process_stopped=True, effects="rolled_back")


def test_inference_contract_cannot_silently_replace_model_or_certify():
    identity = ec.ModelIdentity(provider="local-provider", model="chosen-model")
    request = ec.InferenceRequest(request_id="r", mission_id="m", run_id="run",
                                  standard_sha256=SHA, model=identity, input_json='[{"role":"user","content":"hello"}]',
                                  required_capabilities=("text",), max_output_tokens=20,
                                  max_input_tokens=100, cost_ceiling=ec.Money(amount_minor_units=0, currency="AUD"),
                                  timeout_seconds=10, deadline=NOW + timedelta(seconds=10))
    receipt = ec.InferenceReceipt(request_sha256=request.sha256, requested=identity, observed=identity,
                                  dispatch="sent", state="finished", output_json='{"text":"hello"}',
                                  observed_at=NOW, finished_at=NOW, usage=ec.ExecutorUsage())
    assert receipt.matches(request)
    assert not receipt.model_copy(update={"observed": ec.ModelIdentity(provider="other", model="chosen-model")}).matches(request)
    with pytest.raises(ValidationError):
        ec.InferenceReceipt(**(receipt.model_dump() | {"verified": True}))
    with pytest.raises(ValidationError):
        ec.ModelCapabilities(adapter_id="provider", adapter_version="1", provider_version="1",
                             models=(identity,), features=("text",), context_tokens=100, output_tokens=20,
                             execution="local", cancellation="confirmed")


@pytest.mark.parametrize("changes", [
    {"dispatch": "not_sent"}, {"finished_at": None},
    {"started_at": NOW + timedelta(seconds=1)},
    {"finished_at": NOW + timedelta(seconds=1)},
    {"state": "running"},
])
def test_lifecycle_contradictions_refused(changes):
    status = {"handle": handle(task()), "state": "finished", "dispatch": "sent", "observed_at": NOW,
                  "finished_at": NOW, "source": "observed", "usage": ec.ExecutorUsage()}
    with pytest.raises(ValidationError):
        ec.AgentStatus(**(status | changes))
    receipt = {k: v for k, v in status.items() if k not in ("handle", "source")}
    receipt.update(request_sha256=SHA, requested=task().model, output_json='{"text":"draft"}')
    with pytest.raises(ValidationError):
        ec.InferenceReceipt(**(receipt | changes))


def test_finished_inference_requires_output_and_cancel_observations_are_separate():
    with pytest.raises(ValidationError):
        ec.InferenceReceipt(request_sha256=SHA, requested=task().model, dispatch="sent", state="finished",
                            observed_at=NOW, finished_at=NOW, usage=ec.ExecutorUsage())
    data = {"handle": handle(task()), "requested_at": NOW, "acknowledged": False, "process_stopped": True,
                "effects": "unknown", "observed_at": NOW, "receipt_refs": ("observed-process-exit",)}
    # OS termination need not have been acknowledged by the executor.
    assert ec.CancellationReceipt(**data).process_stopped
    for change in ({"observed_at": None}, {"receipt_refs": ()},
                   {"observed_at": NOW - timedelta(seconds=1)}):
        with pytest.raises(ValidationError):
            ec.CancellationReceipt(**(data | change))


@pytest.mark.parametrize("field,changes", [
    ("effects", {"effects": ()}), ("isolation", {"isolation": "other"}),
    ("status", {"status": False}), ("artifacts", {"artifacts": False}),
    ("cancellation", {"cancellation": "unsupported"}), ("resume", {"resume": False}),
    ("idempotency", {"idempotency": False}),
])
def test_all_mandatory_capabilities_can_be_required_and_refused(field, changes):
    requirements = ec.AgentRequirements(effects=("workspace_read",), isolation=("trusted_same_user",),
                                        cancellation="process_termination", resume=True, idempotency=True)
    t = task(requirements=requirements)
    good = capabilities(cancellation="process_termination", resume=True, idempotency=True)
    assert ec.agent_incompatibilities(t, good) == ()
    assert field in ec.agent_incompatibilities(t, capabilities(**(good.model_dump() | changes)))


def test_usage_lower_bounds_and_complete_totals_are_distinct():
    assert not ec.ExecutorUsage(input_tokens=1, output_tokens=2).tokens_complete
    with pytest.raises(ValidationError):
        ec.ExecutorUsage(input_tokens=1, token_scope="complete")
    with pytest.raises(ValidationError):
        ec.ExecutorUsage(cost_scope="complete")
    assert ec.ExecutorUsage().cost_scope == "unknown"


@pytest.mark.parametrize("field", ["input_json", "settings_json"])
def test_inference_has_one_attempt_explicit_ceiling_and_utf8_payloads(field):
    data = {"request_id": "r", "mission_id": "m", "run_id": "run", "standard_sha256": SHA, "model": task().model,
                "input_json": "{}", "required_capabilities": ("text",), "max_output_tokens": 20, "max_input_tokens": 100,
                "timeout_seconds": 10, "deadline": NOW, "cost_ceiling": ec.Money(amount_minor_units=0, currency="AUD")}
    request = ec.InferenceRequest(**data)
    assert request.max_attempts == 1
    for change in ({"max_attempts": 2}, {"max_attempts": True}, {"settings_json": "[]"},
                   {field: '{"text":"\\ud800"}'}, {"cost_ceiling": None}):
        with pytest.raises(ValidationError):
            ec.InferenceRequest(**(data | change))
    assert ec.InferenceRequest.model_validate_json(request.model_dump_json()).sha256 == request.sha256
