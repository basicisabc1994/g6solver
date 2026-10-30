# SPDX-License-Identifier: Apache-2.0
import threading
from g6.metaharness.jobs import Jobs


def test_job_returns_result_without_restarting_the_operation():
    jobs = Jobs()
    ready = threading.Event()
    calls = []
    def work():
        calls.append(1)
        ready.set()
        return {"completion_state": "qualified-draft", "answer": 42}
    started = jobs.start(work)
    assert started["job_id"]
    assert ready.wait(5)
    jobs.close()
    got = jobs.status(started["job_id"])
    assert got["status"] == "completed" and got["result"]["answer"] == 42
    assert jobs.status(started["job_id"]) == got and calls == [1]


def test_job_exception_and_missing_job_are_explicit_failures():
    jobs = Jobs()
    def fail():
        raise RuntimeError("deliberate test failure")
    started = jobs.start(fail)
    jobs.close()
    assert jobs.status(started["job_id"])["status"] == "failed"
    assert jobs.status("unknown")["ok"] is False
