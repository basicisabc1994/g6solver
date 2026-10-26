# SPDX-License-Identifier: Apache-2.0
"""RQ regression cases. Server-dependent cases use only fakeredis, never a worker."""
from __future__ import annotations

import json
import zlib

import pytest
from pydantic import ValidationError

from g6.integrations.adapt_rq import AdaptRQBlock, RQInput
from g6.integrations.adapt_rq import block as module


def call(block, op, **values):
    return block.infer(RQInput(op=op, **values)).unwrap()


@pytest.fixture
def queue_block(monkeypatch):
    redis = pytest.importorskip("fakeredis", reason="fakeredis is required; no real Redis is permitted")
    pytest.importorskip("rq", reason="the rq extra is not installed; no network or install in tests")
    from rq import Queue
    from rq.serializers import JSONSerializer

    connection = redis.FakeRedis()
    block = AdaptRQBlock(connection=connection, functions={"size": "builtins.len"})
    monkeypatch.setattr(block, "_require_worker", lambda queue: None)
    return block, Queue("default", connection=connection, serializer=JSONSerializer)


def test_d1_unregistered_function_is_refused_before_backend(monkeypatch):
    block = AdaptRQBlock(functions={"size": "builtins.len"})
    monkeypatch.setattr(module, "dependencies", lambda: pytest.fail("allow-list must precede backend access"))
    result = call(block, "enqueue", function="operator_missing_57", args=[[1]], dry_run=False)
    assert result["code"] == "G6_E_RQ_FUNCTION_REFUSED"
    assert result["completion_state"] == "blocked-escalated"
    # Removal of `if data.function not in self.functions:` makes this test fail (ADAPT_RQ-01, D1).


def test_caller_cannot_supply_import_path_or_approval():
    for extra in ({"func_path": "os.system"}, {"approved": True}, {"functions": {"x": "os.system"}}):
        with pytest.raises(ValidationError):
            RQInput(op="enqueue", function="x", **extra)
    with pytest.raises(ValidationError):
        RQInput(op="enqueue", function="x", args=[object()])
    with pytest.raises(ValidationError):
        RQInput(op="enqueue", function="x", args=[float("nan")])
    # Removal of extra="forbid" or the strict JSON validator makes this test fail.


def test_capability_enqueue_and_status_are_queue_reports(queue_block):
    block, queue = queue_block
    result = call(block, "enqueue", function="size", args=[[1, 2, 3]], dry_run=False)
    job = queue.fetch_job(result["job_id"])
    assert job.func_name == "builtins.len" and list(job.args) == [[1, 2, 3]]
    assert result["completion_state"] == "qualified-draft" and result["queue_reports_finished"] is False
    status = call(block, "job_status", job_id=job.id, dry_run=False)
    assert status["status"] == "queued" and status["queue_reports_finished"] is False
    assert status["return_value_claim"] is None
    assert json.loads(zlib.decompress(queue.connection.hget(job.key, "data"))) == ["builtins.len", None, [[1, 2, 3]], {}]
    # Removal of serializer=JSONSerializer or the fixed qualified-draft envelope fails this test (ADAPT_RQ-02).


@pytest.mark.parametrize("op", module.OPS)
def test_all_redis_operations_are_dry_by_default(queue_block, monkeypatch, op):
    block, queue = queue_block
    monkeypatch.setattr(queue.connection, "execute_command", lambda *a, **kw: pytest.fail("Redis touched in dry run"))
    result = call(block, op, function="size", job_id="job", job_ids=["job"],
                  scheduled_at="2030-01-01T00:00:00Z")
    assert result["code"] == "G6_E_RQ_DRY_RUN" and result["dry_run"] is True
    # Removal of `if data.dry_run: return reply(...)` before connecting makes this test fail.


def test_requeue_reports_each_failure(queue_block):
    block, queue = queue_block
    from rq.job import JobStatus
    from rq.registry import FailedJobRegistry
    failed = FailedJobRegistry(queue=queue)
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    queue.remove(job)
    job.set_status(JobStatus.FAILED)
    failed.add(job, ttl=600)
    result = call(block, "requeue_failed", job_ids=[job.id, "missing-job-91"], dry_run=False)
    assert result["completion_state"] == "blocked-escalated"
    assert result["outcomes"][0]["requeued"] is True
    assert result["outcomes"][1]["requeued"] is False
    assert result["outcomes"][1]["code"].startswith("G6_E_RQ_")
    # Removal of `outcomes.append` in the per-job exception handler makes this test fail (ADAPT_RQ-03).


def test_foreign_function_cannot_be_requeued(queue_block):
    block, queue = queue_block
    from rq.registry import FailedJobRegistry
    job = queue.enqueue("builtins.sum", [1, 2])
    queue.remove(job)
    FailedJobRegistry(queue=queue).add(job, ttl=600)
    result = call(block, "requeue_failed", job_ids=[job.id], dry_run=False)
    assert result["outcomes"][0]["code"] == "G6_E_RQ_FUNCTION_REFUSED"
    assert queue.count == 0
    # Removal of the requeue function allow-list check makes this test fail.


def test_missing_server_is_an_outside_program_refusal(queue_block, monkeypatch):
    block, _ = queue_block
    def missing():
        raise OSError("not listening")
    monkeypatch.setattr(block.connection, "ping", missing)
    result = call(block, "queue_snapshot", dry_run=False)
    assert result["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING"
    assert "redis-server" in result["warning_card"]["suggested_action"]
    # Removal of outside.require's ping check makes this test fail.


@pytest.mark.parametrize("op", module.OPS)
def test_missing_extra_is_honest_for_every_operation(monkeypatch, op):
    def absent():
        raise ImportError("rq unavailable")
    monkeypatch.setattr(module, "dependencies", absent)
    values = dict(function="size", job_id="job", job_ids=["job"], scheduled_at="2030-01-01T00:00:00Z")
    result = call(AdaptRQBlock(functions={"size": "builtins.len"}), op, **values)
    assert result["code"] == "G6_E_RQ_NOT_INSTALLED" and "g6[rq]" in result["reason"]
    # Removal of the dependency refusal makes this test fail.


@pytest.mark.parametrize("op,extra", [("enqueue_at", {"scheduled_at": "2030-01-01T00:00:00Z"}),
                                     ("enqueue_in", {"delay_seconds": 60})])
def test_scheduled_enqueue_uses_json_and_the_registered_name(queue_block, op, extra):
    block, queue = queue_block
    result = call(block, op, function="size", args=[[1]], dry_run=False, **extra)
    assert result["status"] == "scheduled" and result["queue_reports_finished"] is False
    job = queue.fetch_job(result["job_id"])
    assert job.func_name == "builtins.len" and list(job.args) == [[1]]
    assert queue.connection.zscore(queue.scheduled_job_registry.key, job.id) is not None
    # Removal of the scheduled enqueue branches or explicit JSON serializer makes this test fail.


def test_finished_job_result_is_only_a_claim(queue_block):
    from rq.job import JobStatus
    from rq.results import Result

    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    job.set_status(JobStatus.FINISHED)
    Result.create(job, Result.Type.SUCCESSFUL, ttl=3600, return_value={"answer": "unreliable"})
    result = call(block, "job_status", job_id=job.id, dry_run=False)
    assert result["queue_reports_finished"] is True
    assert result["return_value_claim"] == {"answer": "unreliable"}
    assert result["completion_state"] == "qualified-draft"
    assert result["evidence"][0]["job_id"] == job.id
    # Removal of return_value_claim or fixed qualified-draft labelling fails this test (ADAPT_RQ-02).


def test_failed_listing_snapshot_and_cancellation_through_block(queue_block):
    block, queue = queue_block
    job = queue.enqueue("builtins.len", [1])
    assert call(block, "queue_snapshot", dry_run=False)["pending"] == 1
    result = call(block, "cancel_job", job_id=job.id, dry_run=False)
    assert result["status"] == "canceled"
    from rq.registry import FailedJobRegistry
    failed = queue.enqueue("builtins.len", [1])
    queue.remove(failed)
    FailedJobRegistry(queue=queue).add(failed, ttl=600)
    assert failed.id in call(block, "list_failed", dry_run=False)["job_ids"]
    assert call(block, "list_workers", dry_run=False)["workers"] == []
    # Removal of the matching queue operation branch makes this test fail.


def test_operator_registration_is_used_without_executing_the_function(monkeypatch):
    from g6.integrations.adapt_rq import config
    monkeypatch.setitem(config.FUNCTIONS, "count", "old")
    config.register_function("count", len)
    assert AdaptRQBlock().functions["count"] == "builtins.len"
    # Removal of FUNCTIONS[name] assignment makes this test fail.


def test_missing_worker_is_refused_with_posix_json_start_instructions(queue_block, monkeypatch):
    block, queue = queue_block
    monkeypatch.setattr(block, "_require_worker", module.AdaptRQBlock._require_worker.__get__(block))
    result = call(block, "enqueue", function="size", args=[[1]], dry_run=False)
    assert result["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING" and queue.count == 0
    assert "POSIX" in result["warning_card"]["suggested_action"]
    assert "JSONSerializer" in result["warning_card"]["suggested_action"]
    # Removal of the worker heartbeat requirement makes this test fail.


@pytest.mark.parametrize("op", ["enqueue", "enqueue_at", "enqueue_in", "cancel_job", "requeue_failed"])
def test_mcp_mutations_require_operator_permission(queue_block, monkeypatch, op):
    from g6.core.registry import get_registry
    from g6.mcp import invoke_reply

    block, queue = queue_block
    registry = get_registry()
    original = registry.get_class
    monkeypatch.setattr(registry, "get_class", lambda name: (lambda: block) if name == "adapt_rq" else original(name))
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    if op == "requeue_failed":
        from rq.job import JobStatus
        from rq.registry import FailedJobRegistry
        queue.remove(job)
        job.set_status(JobStatus.FAILED)
        FailedJobRegistry(queue=queue).add(job, ttl=600)
    params = json.dumps(dict(function="size", args=[[1]], job_id=job.id, job_ids=[job.id],
                             scheduled_at="2030-01-01T00:00:00Z", dry_run=False))
    execute = queue.connection.execute_command
    with monkeypatch.context() as guard:
        guard.setattr(queue.connection, "execute_command", lambda *a, **kw: pytest.fail("denied MCP call reached Redis"))
        denied = invoke_reply("adapt_rq", op, params, allow_sensitive=frozenset())
    assert not denied["ok"] and "G6_E_SENSITIVE_COMPONENT_DENIED" in denied["error"]
    assert queue.connection.execute_command == execute
    allowed = invoke_reply("adapt_rq", op, params, allow_sensitive=frozenset({f"adapt_rq.{op}"}))
    assert allowed["ok"] and allowed["result"]["code"] == "G6_E_RQ_REPORT", allowed
    if op == "requeue_failed":
        assert allowed["result"]["outcomes"][0]["requeued"] is True and queue.count == 1
    elif op == "cancel_job":
        assert allowed["result"]["status"] == "canceled" and queue.count == 0
    else:
        assert queue.fetch_job(allowed["result"]["job_id"]).func_name == "builtins.len"
    # Removal of the op from `__g6_sensitive_ops__` lets the denied call reach Redis and fails this test.


@pytest.mark.parametrize("field,value", [
    ("success_callback_name", "builtins.print"), ("failure_callback_name", "builtins.print"),
    ("stopped_callback_name", "builtins.print"), ("success_callback_timeout", "1"),
    ("failure_callback_timeout", "1"), ("stopped_callback_timeout", "1"),
    ("webhooks", '[{"url":"https://invalid.example/hook","job_status":"finished"}]'),
    ("retries_left", "2"), ("number_of_retries", "1"), ("retry_intervals", "[10]"),
    ("enqueue_at_front_on_retry", "1"), ("repeats_left", "2"), ("repeat_intervals", "[10]"),
    ("dependency_id", "parent"), ("dependency_ids", '["parent"]'),
    ("allow_dependency_failures", "1"), ("enqueue_at_front", "1"),
    ("timeout", None), ("timeout", "-1"), ("timeout", "0"), ("timeout", "301"),
    ("result_ttl", None), ("result_ttl", "-1"), ("result_ttl", "3601"),
    ("ttl", "-1"), ("failure_ttl", "-1"), ("future_execution_setting", "enabled"),
])
def test_requeue_refuses_unsupported_execution_metadata(queue_block, field, value):
    from rq.job import JobStatus
    from rq.registry import FailedJobRegistry

    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    queue.remove(job)
    job.set_status(JobStatus.FAILED)
    failed = FailedJobRegistry(queue=queue)
    failed.add(job, ttl=600)
    if value is None:
        queue.connection.hdel(job.key, field)
    else:
        queue.connection.hset(job.key, field, value)
    before = queue.connection.hgetall(job.key)
    result = call(block, "requeue_failed", job_ids=[job.id], dry_run=False)
    assert len(result["outcomes"]) == 1
    outcome = result["outcomes"][0]
    assert outcome["job_id"] == job.id and outcome["requeued"] is False
    assert outcome["code"] == "G6_E_RQ_METADATA_REFUSED" and outcome["reason"]
    assert result["completion_state"] == "blocked-escalated"
    assert queue.count == 0 and job.id in failed.get_job_ids(cleanup=False)
    assert queue.connection.hgetall(job.key) == before
    # Removal of `validate_recovery` before `failed.requeue(job)` makes this test fail.


def test_requeue_refuses_dependency_set_even_without_hash_metadata(queue_block):
    from rq.job import JobStatus
    from rq.registry import FailedJobRegistry

    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    queue.remove(job)
    job.set_status(JobStatus.FAILED)
    failed = FailedJobRegistry(queue=queue)
    failed.add(job, ttl=600)
    queue.connection.sadd(job.dependencies_key, "parent")
    result = call(block, "requeue_failed", job_ids=[job.id], dry_run=False)
    assert result["outcomes"][0]["code"] == "G6_E_RQ_METADATA_REFUSED"
    assert queue.count == 0 and job.id in failed.get_job_ids(cleanup=False)
    # Removal of the dependency-set `scard` refusal in `validate_recovery` makes this test fail.


@pytest.mark.parametrize("size", [2, 101])
def test_worker_membership_reads_are_bounded_passive_and_keep_timeouts(queue_block, monkeypatch, size):
    from datetime import datetime, timezone
    from rq import Worker

    block, queue = queue_block
    connection = queue.connection
    key = "rq:workers:default"
    connection.sadd(key, *(f"rq:worker:w{i}" for i in range(size)))
    connection.hset("rq:worker:w0", mapping={"state": "idle", "last_heartbeat": datetime.now(timezone.utc).isoformat()})
    # Global membership is empty, and all other per-queue members are stale.
    assert connection.scard("rq:workers") == 0
    before = dict(connection.connection_pool.connection_kwargs)
    sockets = [(c, c.socket_timeout, c.socket_connect_timeout) for c in connection.connection_pool._available_connections]
    commands = []
    connection_type = connection.connection_pool.connection_class
    send = connection_type.send_command
    def observe(self, *args, **kwargs):
        commands.append(args)
        return send(self, *args, **kwargs)
    monkeypatch.setattr(connection_type, "send_command", observe)
    monkeypatch.setattr(Worker, "__init__", lambda *a, **kw: pytest.fail("worker object constructed"))
    result = call(block, "list_workers", dry_run=False)
    assert all(str(cmd[0]).upper() in {"PING", "SCARD", "SRANDMEMBER", "HMGET"} for cmd in commands), commands
    assert ("SCARD", key) in commands
    assert connection.connection_pool.connection_kwargs == before
    assert all((c.socket_timeout, c.socket_connect_timeout) == (read, connect) for c, read, connect in sockets)
    if size > 100:
        assert result["code"] == "G6_E_RESOURCE_LIMIT"
        assert len(commands) == 2  # PING and the same per-queue cardinality, no enumeration.
    else:
        assert [worker["name"] for worker in result["workers"]] == ["w0"]
        assert result["workers"][0]["state"] == "idle"
        assert result["workers"][0]["last_heartbeat"]
        assert len(commands) <= 5
    assert connection.scard(key) == size
    # Removal of the per-queue cap or replacement of direct reads with `Worker.all` makes this test fail.


def test_worker_reads_share_one_deadline(queue_block, monkeypatch):
    block, queue = queue_block
    queue.connection.sadd("rq:workers:default", "rq:worker:a", "rq:worker:b", "rq:worker:c")
    elapsed = [0.0]
    reads = []
    execute = queue.connection.execute_command
    def slow(*args, **kwargs):
        if args[0] == "HMGET":
            reads.append(args)
            elapsed[0] += 2
        return execute(*args, **kwargs)
    monkeypatch.setattr(module, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(queue.connection, "execute_command", slow)
    result = call(block, "list_workers", dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    assert len(reads) == 2
    # Removal of `if monotonic() >= deadline:` in `check_deadline` makes this test fail.


@pytest.mark.parametrize("field,raw", [("data", b"x" * 65_537), ("exc_info", b"x" * 65_537)],
                         ids=["data", "exception"])
def test_stored_size_is_refused_before_fetch_or_decompression(queue_block, monkeypatch, field, raw):
    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    queue.connection.hset(job.key, field, raw)
    execute = queue.connection.execute_command
    def observe(*args, **kwargs):
        if args[0] in {"HGETALL", "HMGET", "HGET"} and args[1] == job.key:
            pytest.fail("oversize hash values fetched before their lengths were checked")
        return execute(*args, **kwargs)
    monkeypatch.setattr(queue.connection, "execute_command", observe)
    monkeypatch.setattr(zlib, "decompress", lambda *a, **kw: pytest.fail("unbounded decompress"))
    result = call(block, "job_status", job_id=job.id, dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    # Removal of the `hstrlen` cap before `hmget` in `read_hash` makes this test fail.


@pytest.mark.parametrize("field", ["data", "exc_info"])
def test_compressed_job_payload_is_refused_without_full_expansion(queue_block, monkeypatch, field):
    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    queue.connection.hset(job.key, field, zlib.compress(b'"' + b"x" * 200_000 + b'"'))
    original = zlib.decompressobj
    expansions = []
    class BoundedSpy:
        def __init__(self):
            self.inner = original()
        def decompress(self, data, max_length=0):
            assert 0 < max_length <= 65_537
            result = self.inner.decompress(data, max_length)
            expansions.append(len(result))
            return result
        def __getattr__(self, name):
            return getattr(self.inner, name)
    monkeypatch.setattr(zlib, "decompressobj", BoundedSpy)
    monkeypatch.setattr(zlib, "decompress", lambda *a, **kw: pytest.fail("unbounded decompress"))
    result = call(block, "job_status", job_id=job.id, dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    assert expansions and max(expansions) <= 65_537
    # Removal of `decompress(data, MAX_BYTES + 1)` or its overflow refusal makes this test fail.


def test_stream_result_is_refused_before_base64_or_json_decode(queue_block, monkeypatch):
    import base64
    from rq.job import JobStatus

    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    job.set_status(JobStatus.FINISHED)
    queue.connection.xadd(f"rq:results:{job.id}", {"type": "1", "return_value": b"eA==" * 30_000})
    monkeypatch.setattr(base64, "b64decode", lambda *a, **kw: pytest.fail("oversize result decoded"))
    result = call(block, "job_status", job_id=job.id, dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    # Removal of the encoded stream value length cap before `base64.b64decode` makes this test fail.


def test_worker_membership_growth_cannot_cause_unbounded_enumeration(queue_block, monkeypatch):
    block, queue = queue_block
    execute = queue.connection.execute_command
    reads = []
    def grow(*args, **kwargs):
        if args[0] == "SCARD" and args[1] == "rq:workers:default":
            result = execute(*args, **kwargs)
            execute("SADD", "rq:workers:default", *(f"rq:worker:w{i}" for i in range(200)))
            return result
        if args[0] == "SRANDMEMBER":
            reads.append(args)
            assert args[2] == 101
        return execute(*args, **kwargs)
    monkeypatch.setattr(queue.connection, "execute_command", grow)
    result = call(block, "list_workers", dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT" and len(reads) == 1
    # Removal of the `len(identifiers) > 100` check after bounded enumeration makes this test fail.


def test_worker_heartbeat_allows_enqueue_without_worker_objects(queue_block, monkeypatch):
    from datetime import datetime, timezone
    from rq import Worker

    block, queue = queue_block
    queue.connection.sadd("rq:workers:default", "rq:worker:present")
    queue.connection.hset("rq:worker:present", "last_heartbeat", datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(block, "_require_worker", module.AdaptRQBlock._require_worker.__get__(block))
    monkeypatch.setattr(Worker, "__init__", lambda *a, **kw: pytest.fail("worker object constructed"))
    result = call(block, "enqueue", function="size", args=[[1]], dry_run=False)
    assert result["code"] == "G6_E_RQ_REPORT" and queue.count == 1
    # Removal of `live |= ... <= 600` from the direct heartbeat check makes this test fail.


def test_compressed_result_exception_is_bounded_before_expansion(queue_block, monkeypatch):
    import base64
    from rq.job import JobStatus

    block, queue = queue_block
    job = queue.enqueue_call("builtins.len", args=([1],), timeout=300, result_ttl=3600)
    job.set_status(JobStatus.FINISHED)
    queue.connection.xadd(f"rq:results:{job.id}", {
        "type": "2", "exc_string": base64.b64encode(zlib.compress(b"x" * 200_000)),
    })
    original = zlib.decompressobj
    sizes = []
    class BoundedSpy:
        def __init__(self):
            self.inner = original()
        def decompress(self, data, max_length=0):
            assert 0 < max_length <= 65_537
            value = self.inner.decompress(data, max_length)
            sizes.append(len(value))
            return value
        def __getattr__(self, name):
            return getattr(self.inner, name)
    monkeypatch.setattr(zlib, "decompressobj", BoundedSpy)
    monkeypatch.setattr(zlib, "decompress", lambda *a, **kw: pytest.fail("unbounded result decompression"))
    result = call(block, "job_status", job_id=job.id, dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    assert sizes[-1] == 65_537
    # Removal of the `bounded_data` call for stream exception text makes this test fail.
