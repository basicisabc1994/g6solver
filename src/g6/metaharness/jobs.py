# SPDX-License-Identifier: Apache-2.0
"""Bounded process-local jobs. A server restart loses job handles, never implies success.

A job may carry ``compact``: run in the job once its operation returns, with the result and the job's id, it gives the
reply ``status`` returns by default; ``status(..., detail="full")`` returns the result itself. Should ``compact`` fail,
``status`` returns the result itself."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from threading import Lock
from uuid import uuid4

from g6.core.door import blocked_reply

DETAILS = ("compact", "full")


class Jobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="g6-evaluation")
        self._jobs = {}
        self._compact = {}
        self._provenance = {}
        self._lock = Lock()

    def start(self, operation, *, compact=None):
        capture = cloud.current_capture()
        origin = (capture.client, frozenset(capture.supplied)) if capture is not None else (None, frozenset())
        context = copy_context()
        with self._lock:
            if sum(not job.done() for job in self._jobs.values()) >= 16:
                return blocked_reply("[G6_E_JOB_LIMIT] sixteen jobs are already pending")
            if len(self._jobs) >= 64:
                oldest = next((key for key, job in self._jobs.items() if job.done()), None)
                if oldest:
                    del self._jobs[oldest]
                    self._compact.pop(oldest, None)
                    self._provenance.pop(oldest, None)
            ident = uuid4().hex
            self._jobs[ident] = self._pool.submit(context.run, self._run, operation, compact, ident, origin)
        return cloud.bind_output({"ok": True, "job_id": ident, "status": "pending",
                "completion_state": "qualified-draft",
                "note": "Execution is pending. Poll metaharness_job; this is not an evaluation result."}, ())

    def _run(self, operation, compact, ident, origin):
        refs = shown_refs = (cloud.UNKNOWN,)
        metadata = shown_metadata = {}
        with cloud.collect_sources() as capture:
            capture.client, capture.supplied = origin[0], set(origin[1])
            try:
                result = operation()
                known = cloud.output_sources(result)
                refs = cloud.boundary_sources(carried=(*(known if known is not None else
                    cloud.current_sources() or (cloud.UNKNOWN,)), *capture.sources))
                metadata = cloud.output_metadata(result)
                shown_metadata = metadata
                shown_refs = refs
                if compact:
                    try:
                        shown = compact(result, ident)
                        known = cloud.output_sources(shown)
                        shown_refs = cloud.boundary_sources(carried=(*refs, *(known or ()), *capture.sources))
                        shown_metadata = cloud.output_metadata(shown)
                    except Exception:
                        shown = result
                    with self._lock:
                        self._compact[ident] = shown
                return result
            finally:
                with self._lock:
                    self._provenance[ident] = {"full": refs, "compact": shown_refs,
                        "sources": tuple(capture.sources), "origin": origin, "review_unsent": capture.review_unsent,
                        "metadata": {'full': metadata, 'compact': shown_metadata}}

    def _status_reply(self, ident, result, detail):
        capture = cloud.current_capture()
        if capture is None:
            return result
        with self._lock:
            stored = self._provenance.get(ident, {})
        cloud.record_sources(stored.get("sources", ()))
        public = result.get("status") in ("pending", "running")
        refs = () if public else cloud.boundary_sources(
            carried=(*stored.get(detail, (cloud.UNKNOWN,)), *stored.get('sources', ())))
        cloud.bind_output(result, refs)
        metadata = dict(stored.get('metadata', {}).get(detail, {}))
        if result['status'] == 'failed':
            # This code describes the host's failed future, not its opaque
            # exception text. Withholding that text must retain the stable code.
            metadata['error_code'] = 'G6_E_JOB_FAILED'
        capture.job = {"job_id": ident, "status": result["status"], "digest": digest(result),
                       "review_unsent": stored.get("review_unsent", False),
                       "metadata": metadata}
        return result

    def status(self, ident, detail="compact"):
        if detail not in DETAILS:
            return cloud.bind_output(blocked_reply(
                f"[G6_E_INPUT_VALIDATION] detail must be one of {', '.join(DETAILS)}"), ())
        with self._lock:
            job = self._jobs.get(ident)
        if job is None:
            return cloud.bind_output(blocked_reply("[G6_E_JOB_UNKNOWN] no retained job; the server may have restarted"), ())
        if not job.done():
            return self._status_reply(ident, {"ok": True, "job_id": ident,
                "status": "running" if job.running() else "pending"}, detail)
        try:
            result = job.result()
        except Exception as exc:
            return self._status_reply(ident, {**blocked_reply(f"[G6_E_JOB_FAILED] {type(exc).__name__}: {exc}"),
                    "job_id": ident, "status": "failed"}, detail)
        with self._lock:
            shown = self._compact.get(ident, result) if detail == "compact" else result
        return self._status_reply(ident, {"ok": True, "job_id": ident, "status": "completed", "result": shown}, detail)

    def close(self):
        self._pool.shutdown(wait=True)
