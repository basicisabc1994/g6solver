# SPDX-License-Identifier: Apache-2.0
"""RQ's small queue door: operator functions, JSON only, sensitive network access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core import outside
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.errors import unknown_op
from g6.core.maturity import component_maturity
from g6.core.resources import RESOURCE_LIMIT
from g6.core.result import Result
from g6.integrations._boundary import Refusal, json_value, reply
from g6.integrations.adapt_rq._payload import read_job, return_value, validate_recovery
from g6.integrations.adapt_rq.config import FUNCTIONS, REDIS, WORKER, dependencies

OPS = ("enqueue", "enqueue_at", "enqueue_in", "job_status", "cancel_job", "list_workers", "queue_snapshot",
       "list_failed", "requeue_failed")
SENSITIVE_OPS = OPS  # Reading Redis also reaches a network service.


class RQInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str = "help"
    function: str = Field("", max_length=64)
    queue_name: str = Field("default", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    args: list[Any] = Field(default_factory=list, max_length=100)
    kwargs: dict[str, Any] = Field(default_factory=dict, max_length=100)
    job_id: str = Field("", max_length=200)
    job_ids: list[str] = Field(default_factory=list, max_length=100)
    scheduled_at: str = Field("", max_length=50)
    delay_seconds: int = Field(0, ge=0, le=31_536_000)
    limit: int = Field(50, ge=1, le=100)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    task_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("args", "kwargs", mode="before")
    @classmethod
    def _json_only(cls, value: Any) -> Any:
        return json_value(value)

    @model_validator(mode="after")
    def _required(self) -> RQInput:
        json_value([self.args, self.kwargs])
        if self.op.startswith("enqueue") and not self.function:
            raise ValueError("enqueue needs a registered function name")
        if self.op in {"job_status", "cancel_job"} and not self.job_id:
            raise ValueError("this operation needs a job_id")
        if self.op == "requeue_failed" and (not self.job_ids or any(not j or len(j) > 200 for j in self.job_ids)):
            raise ValueError("requeue_failed needs 1 to 100 bounded job IDs")
        if self.op == "enqueue_at":
            when = datetime.fromisoformat(self.scheduled_at.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("scheduled_at needs a timezone")
        return self


@block_contract(purpose="Observe queue reports and submit only operator-registered JSON jobs",
                mitigates_failure_modes=("unseen_job_failure",))
@component_maturity("beta", reason="bounded queue operations; results are queue reports, qualified-draft at best")
class AdaptRQBlock(AIBlock[RQInput, dict, dict]):
    def __init__(self, name: str = "adapt_rq", state: dict | None = None, *, connection: Any = None,
                 functions: dict[str, str] | None = None) -> None:
        super().__init__(name=name, state=state or {})
        # Constructor arguments are trusted operator setup, never fields of RQInput.
        self.functions = dict(FUNCTIONS if functions is None else functions)
        if len(self.functions) > 256 or any(not k or not isinstance(v, str) or len(v) > 300
                                            for k, v in self.functions.items()):
            raise ValueError("invalid operator function registry")
        self.connection = connection

    def infer(self, data: RQInput) -> Result[dict]:
        if data.op in {"ops", "help"}:
            return reply(self.name, data, code="G6_E_RQ_INFO", message="Queue operations; all Redis access is sensitive.",
                         ops=list(OPS), help=list(OPS), sensitive=list(SENSITIVE_OPS))
        if data.op not in OPS:
            return unknown_op(self.name, data.op, OPS)
        try:
            if data.op.startswith("enqueue"):
                if data.function not in self.functions:
                    raise Refusal("G6_E_RQ_FUNCTION_REFUSED", "The operator has not registered that function name.")
            try:
                self.modules = dependencies()
            except (ImportError, OSError, ValueError):
                raise Refusal("G6_E_RQ_NOT_INSTALLED", "Install the rq extra: pip install 'g6[rq] @ git+https://github.com/basicisabc1994/g6solver'. "
                              "The separate rq worker is POSIX-only.") from None
            if data.dry_run:
                return reply(self.name, data, code="G6_E_RQ_DRY_RUN", message="Plan only; no Redis connection or job run.",
                             dry_run=True, proposed_operation=data.op)
            redis, Queue, _, _, _, serializer = self.modules
            connection = self.connection
            owned = connection is None
            if owned:
                connection = redis.Redis(host="127.0.0.1", port=6379, socket_timeout=3, socket_connect_timeout=3)
            try:
                self._require_server(connection)
                queue = Queue(data.queue_name, connection=connection, serializer=serializer)
                fields = self._dispatch(data, queue)
                failed = any(not outcome["requeued"] for outcome in fields.get("outcomes", []))
                return reply(self.name, data, code="G6_E_RQ_PARTIAL_FAILURE" if failed else "G6_E_RQ_REPORT",
                             message="Redis/RQ report only; a job's return value is its claim.", blocked=failed,
                             dry_run=False, **fields)
            finally:
                if owned:
                    connection.close()
        except Refusal as exc:
            return reply(self.name, data, code=exc.code, message=exc.reason, action=exc.action, blocked=True)
        except Exception as exc:  # noqa: BLE001 - never fabricate success or fall back
            return reply(self.name, data, code="G6_E_RQ_BACKEND_FAILED", blocked=True,
                         message=f"RQ did not finish the operation ({type(exc).__name__}); effects may be partial.",
                         action="Inspect the queue before retrying; retries can duplicate effects.")

    @staticmethod
    def _require_server(connection: Any) -> None:
        def ping(argv: list[str], timeout: float) -> tuple[int, str]:
            try:
                return (0, "Redis PING answered") if connection.ping() else (1, "Redis PING failed")
            except Exception:  # noqa: BLE001 - connection details may contain credentials
                return 1, "Redis did not answer PING"
        found = outside.require(REDIS, which=lambda command: "operator-redis-connection", run=ping, environ={})
        if found.is_fail():
            raise Refusal(outside.OUTSIDE_PROGRAM_MISSING, "Redis did not answer; no fallback is used.", REDIS.install)

    def _workers(self, queue: Any) -> list[dict]:
        deadline = monotonic() + 3

        def check_deadline() -> None:
            if monotonic() >= deadline:
                raise Refusal(RESOURCE_LIMIT, "Worker inspection exceeded its three-second deadline.")

        def read(command: Any, *args: Any) -> Any:
            check_deadline()
            value = command(*args)
            check_deadline()
            return value

        connection = queue.connection
        key = f"rq:workers:{queue.name}"
        if read(connection.scard, key) > 100:
            raise Refusal(RESOURCE_LIMIT, "More than 100 queue worker IDs; ask the operator to maintain membership.")
        # A positive count returns distinct members and caps the response even if membership grows after SCARD.
        identifiers = read(connection.srandmember, key, 101)
        if len(identifiers) > 100:
            raise Refusal(RESOURCE_LIMIT, "Queue worker membership grew beyond 100 IDs.")
        workers = []
        for identifier in sorted(identifiers):
            if len(identifier) > 200 or not identifier.startswith(b"rq:worker:"):
                raise Refusal("G6_E_RQ_WORKER_REFUSED", "Invalid worker ID in queue membership.")
            heartbeat, state = read(connection.hmget, identifier, ("last_heartbeat", "state"))
            if heartbeat is None:
                continue  # Stale membership is left for the operator/RQ to maintain.
            if len(heartbeat) > 100 or (state is not None and len(state) > 100):
                raise Refusal(RESOURCE_LIMIT, "Worker report exceeds the field limit.")
            workers.append({"name": identifier.decode("utf-8").removeprefix("rq:worker:"),
                            "state": state.decode("utf-8") if state else None,
                            "last_heartbeat": heartbeat.decode("ascii")})
        return workers

    def _require_worker(self, queue: Any) -> None:
        workers = self._workers(queue)
        now = datetime.now(timezone.utc)
        live = False
        for worker in workers:
            heartbeat = datetime.fromisoformat(worker["last_heartbeat"].replace("Z", "+00:00"))
            live |= 0 <= (now - heartbeat.replace(tzinfo=timezone.utc)).total_seconds() <= 600
        found = outside.require(WORKER, which=lambda command: "operator-worker-heartbeat",
                                run=lambda argv, timeout: (0 if live else 1, "RQ heartbeat check"), environ={})
        if found.is_fail():
            raise Refusal(outside.OUTSIDE_PROGRAM_MISSING, "No recent worker heartbeat for this queue.", WORKER.install)

    def _job(self, queue: Any, job_id: str) -> tuple[Any, dict[str, bytes]]:
        return read_job(queue, job_id, self.modules[3], self.modules[5])

    def _dispatch(self, data: RQInput, queue: Any) -> dict:
        _, _, _, _, FailedJobRegistry, _ = self.modules
        if data.op.startswith("enqueue"):
            self._require_worker(queue)
            target = self.functions[data.function]
            json_value([target, None, data.args, data.kwargs])
            # args/kwargs passed explicitly: caller kwargs cannot become RQ's control options.
            options = {"args": data.args, "kwargs": data.kwargs, "result_ttl": 3600}
            # enqueue_call takes timeout directly; scheduled enqueues parse the job_timeout option.
            if data.op == "enqueue_at":
                when = datetime.fromisoformat(data.scheduled_at.replace("Z", "+00:00"))
                job = queue.enqueue_at(when, target, job_timeout=300, **options)
            elif data.op == "enqueue_in":
                job = queue.enqueue_in(timedelta(seconds=data.delay_seconds), target, job_timeout=300, **options)
            else:
                job = queue.enqueue_call(target, timeout=300, **options)
            return self._status(job)
        if data.op == "job_status":
            return self._status(self._job(queue, data.job_id)[0])
        if data.op == "cancel_job":
            job, _ = self._job(queue, data.job_id)
            job.cancel()
            return self._status(job)
        if data.op == "list_workers":
            workers = self._workers(queue)
            return {"workers": workers[:data.limit],
                    "truncated": len(workers) > data.limit}
        if data.op == "queue_snapshot":
            # Direct cardinalities avoid RQ registry cleanup (a hidden mutation during a read).
            return {"queue_name": queue.name, "pending": queue.connection.llen(queue.key),
                    **{name: queue.connection.zcard(getattr(queue, f"{name}_job_registry").key)
                       for name in ("started", "finished", "failed", "scheduled")}}
        failed = FailedJobRegistry(queue=queue, serializer=self.modules[5])
        if data.op == "list_failed":
            ids = failed.get_job_ids(start=0, end=data.limit, cleanup=False)
            return {"job_ids": ids[:data.limit], "truncated": len(ids) > data.limit,
                    "basis": "failed registry membership; not an explanation of the failure"}
        self._require_worker(queue)
        outcomes = []
        for job_id in dict.fromkeys(data.job_ids):
            try:
                job, stored = self._job(queue, job_id)
                if job.func_name not in self.functions.values():
                    raise Refusal("G6_E_RQ_FUNCTION_REFUSED", "Job function is not currently registered.")
                if job.instance is not None:
                    raise Refusal("G6_E_RQ_FUNCTION_REFUSED", "Bound instances are not registered functions.")
                json_value([list(job.args), job.kwargs])
                validate_recovery(job, stored)
                failed.requeue(job)
                outcomes.append({"job_id": job_id, "requeued": True, "queue_reports_finished": False})
            except Exception as exc:  # noqa: BLE001 - every attempted ID retains its own failure
                outcomes.append({"job_id": job_id, "requeued": False,
                                 "code": exc.code if isinstance(exc, Refusal) else "G6_E_RQ_REQUEUE_FAILED",
                                 "reason": exc.reason if isinstance(exc, Refusal) else type(exc).__name__})
        return {"outcomes": outcomes}

    @staticmethod
    def _status(job: Any) -> dict:
        status = job.get_status(refresh=True)
        status = str(getattr(status, "value", status))
        finished = status == "finished"
        value = return_value(job) if finished else None
        return {"job_id": job.id, "status": status, "queue_reports_finished": finished,
                "return_value_claim": value}
