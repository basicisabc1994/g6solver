# SPDX-License-Identifier: Apache-2.0
"""Bounded process-local jobs. A server restart loses job handles, never implies success.

A job may carry ``compact``: run in the job once its operation returns, with the result and the job's id, it gives the
reply ``status`` returns by default; ``status(..., detail="full")`` returns the result itself. Should ``compact`` fail,
``status`` returns the result itself."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import json
from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from threading import Lock
from uuid import uuid4

from g6.core.door import blocked_reply

DETAILS = ("compact", "full")


@dataclass(frozen=True)
class _FrozenResult:
    body: str
    body_sha256: str

    @classmethod
    def of(cls, value):
        body = json.dumps(value, separators=(',', ':'), allow_nan=False)
        return cls(body, cloud.sha256(body))

    @classmethod
    def completed(cls, ident, result_body):
        body = json.dumps({"ok": True, "job_id": ident, "status": "completed"},
                          separators=(',', ':'))[:-1] + ',"result":' + result_body + '}'
        return cls(body, cloud.sha256(body))

    def read(self):
        if cloud.sha256(self.body) != self.body_sha256:
            raise ValueError('retained result digest mismatch')
        return json.loads(self.body)


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
                body, known, metadata = cloud.freeze_output(result)
                refs = cloud.boundary_sources(carried=(*(known if known is not None else
                    cloud.current_sources() or (cloud.UNKNOWN,)), *capture.sources))
                metadata = shown_metadata = _FrozenResult.of(metadata)
                shown_refs = refs
                # Freeze before a compactor can mutate the producer's object.
                full = _FrozenResult.completed(ident, body)
                if compact:
                    try:
                        shown = compact(result, ident)
                        body, known, shown_metadata = cloud.freeze_output(shown)
                        shown_refs = cloud.boundary_sources(carried=(*refs, *(known or ()), *capture.sources))
                        shown_metadata = _FrozenResult.of(shown_metadata)
                        shown = _FrozenResult.completed(ident, body)
                    except Exception:
                        shown = full
                        shown_refs, shown_metadata = refs, metadata
                    with self._lock:
                        self._compact[ident] = shown
                return full
            except Exception as exc:
                # Exception objects can mutate too. Polling never formats them.
                refs = shown_refs = cloud.boundary_sources(carried=(cloud.UNKNOWN, *capture.sources))
                metadata = shown_metadata = _FrozenResult.of({'error_code': 'G6_E_JOB_FAILED'})
                try:
                    message = f"[G6_E_JOB_FAILED] {type(exc).__name__}: {exc}"
                except Exception:
                    message = "[G6_E_JOB_FAILED] job failed; exception details unavailable"
                return _FrozenResult.of({**blocked_reply(message), "job_id": ident, "status": "failed"})
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
        try:
            metadata = stored['metadata'][detail].read()
        except (KeyError, AttributeError, TypeError, ValueError):
            metadata = {}
            if not public:
                cloud.bind_output(result, (cloud.UNKNOWN,))
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
        except Exception:
            reply = {**blocked_reply('[G6_E_JOB_FAILED] no completed result snapshot is available'),
                     'job_id': ident, 'status': 'failed'}
            cloud.bind_output(reply, (cloud.UNKNOWN,))
            return self._status_reply(ident, reply, detail)
        with self._lock:
            shown = self._compact.get(ident, result) if detail == "compact" else result
        try:
            reply = shown.read()
        except (AttributeError, TypeError, ValueError):
            reply = {**blocked_reply(cloud.WITHHELD), "job_id": ident, "status": "failed"}
            cloud.bind_output(reply, (cloud.UNKNOWN,))
        return self._status_reply(ident, reply, detail)

    def close(self):
        self._pool.shutdown(wait=True)
