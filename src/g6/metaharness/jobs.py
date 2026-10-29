# SPDX-License-Identifier: Apache-2.0
"""Bounded process-local jobs. A server restart loses job handles, never implies success.

A job may carry ``compact``: run in the job once its operation returns, with the result and the job's id, it gives the
reply ``status`` returns by default; ``status(..., detail="full")`` returns the result itself. Should ``compact`` fail,
``status`` returns the result itself."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from g6.core.door import blocked_reply

DETAILS = ("compact", "full")


class Jobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="g6-evaluation")
        self._jobs = {}
        self._compact = {}
        self._lock = Lock()

    def start(self, operation, *, compact=None):
        with self._lock:
            if sum(not job.done() for job in self._jobs.values()) >= 16:
                return blocked_reply("[G6_E_JOB_LIMIT] sixteen jobs are already pending")
            if len(self._jobs) >= 64:
                oldest = next((key for key, job in self._jobs.items() if job.done()), None)
                if oldest:
                    del self._jobs[oldest]
                    self._compact.pop(oldest, None)
            ident = uuid4().hex
            self._jobs[ident] = self._pool.submit(self._run, operation, compact, ident) if compact else \
                self._pool.submit(operation)
        return {"ok": True, "job_id": ident, "status": "pending", "completion_state": "qualified-draft",
                "note": "Execution is pending. Poll metaharness_job; this is not an evaluation result."}

    def _run(self, operation, compact, ident):
        result = operation()
        try:
            shown = compact(result, ident)
        except Exception:   # a finished result is never lost to its compact form: status then returns it whole
            return result
        with self._lock:
            self._compact[ident] = shown
        return result

    def status(self, ident, detail="compact"):
        if detail not in DETAILS:
            return blocked_reply(f"[G6_E_INPUT_VALIDATION] detail must be one of {', '.join(DETAILS)}, not {detail!r}")
        with self._lock:
            job = self._jobs.get(ident)
        if job is None:
            return blocked_reply("[G6_E_JOB_UNKNOWN] no retained job; the server may have restarted")
        if not job.done():
            return {"ok": True, "job_id": ident, "status": "running" if job.running() else "pending"}
        try:
            result = job.result()
        except Exception as exc:
            return {**blocked_reply(f"[G6_E_JOB_FAILED] {type(exc).__name__}: {exc}"),
                    "job_id": ident, "status": "failed"}
        with self._lock:
            shown = self._compact.get(ident, result) if detail == "compact" else result
        return {"ok": True, "job_id": ident, "status": "completed", "result": shown}

    def close(self):
        self._pool.shutdown(wait=True)
