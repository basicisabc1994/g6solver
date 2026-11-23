import threading

from g6.core import cloud_consent as cloud
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_mcp_report_does_not_say_unsent_after_a_late_send(disk_run, monkeypatch):
    repo, base, session = disk_run
    release = threading.Event()
    received, workers, errors = [], [], []

    def worker():
        try:
            assert release.wait(10)
            FakeLLM(lambda *args: received.append(args) or "reply").complete("system", "late request")
        except BaseException as exc:
            errors.append(exc)

    def scout(*args):
        thread = threading.Thread(target=worker)
        workers.append(thread)
        thread.start()  # carries the run's actual reservation observer
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: another request was refused"))

    real_advance = loop.advance

    def advance(*args, **kwargs):
        result = real_advance(*args, **kwargs)
        # Deterministic schedule: the step releases its lock; its late worker
        # sends before the MCP surface loads and returns that step's result.
        release.set()
        for thread in workers:
            thread.join(10)
            assert not thread.is_alive()
        return result

    monkeypatch.setattr(loop, "_scout", scout)
    monkeypatch.setattr(loop, "advance", advance)
    result = review_loop.step(str(repo), "double returns twice its argument", "disk-review2", base)
    assert not errors, errors
    assert len(received) == result["requests_sent"] == loop._load(session)["attempted"] == 1
    assert "not sent" not in result["report"].lower(), result
