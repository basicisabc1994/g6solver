# SPDX-License-Identifier: Apache-2.0
"""Bounded process-local jobs. A server restart loses job handles, never implies success."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from g6.core.door import blocked_reply


class Jobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="g6-evaluation")
        self._jobs = {}
        self._lock = Lock()

    def start(self, operation):
        with self._lock:
            if sum(not job.done() for job in self._jobs.values()) >= 16:
                return blocked_reply("[G6_E_JOB_LIMIT] sixteen jobs are already pending")
            if len(self._jobs) >= 64:
                oldest = next((key for key, job in self._jobs.items() if job.done()), None)
                if oldest:
                    del self._jobs[oldest]
            ident = uuid4().hex
            self._jobs[ident] = self._pool.submit(operation)
        return {"ok": True, "job_id": ident, "status": "pending", "completion_state": "qualified-draft",
                "note": "Execution is pending. Poll metaharness_job; this is not an evaluation result."}

    def status(self, ident):
        with self._lock:
            job = self._jobs.get(ident)
        if job is None:
            return blocked_reply("[G6_E_JOB_UNKNOWN] no retained job; the server may have restarted")
        if not job.done():
            return {"ok": True, "job_id": ident, "status": "running" if job.running() else "pending"}
        try:
            return {"ok": True, "job_id": ident, "status": "completed", "result": job.result()}
        except Exception as exc:
            return {**blocked_reply(f"[G6_E_JOB_FAILED] {type(exc).__name__}: {exc}"),
                    "job_id": ident, "status": "failed"}

    def close(self):
        self._pool.shutdown(wait=True)
