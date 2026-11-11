"""A ToolSession's second observation must retain the launch's identity."""
from g6.core import host_models
from g6.core.llm import LLMError
from g6.harness.completion_loop import ToolSession
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


def test_session_reader_interruption_bars_the_actual_launch(review_host, monkeypatch):
    root, launches = review_host
    session = ToolSession()
    usage.read_stops(session)
    original = host_models._result
    reads = 0
    interrupted = False

    def read(printed):
        nonlocal reads, interrupted
        reads += 1
        # Owned observation and the native reply have succeeded; ToolSession.run
        # now invokes its watched reply after _launch has released the record.
        if len(launches) == 1 and reads == 3:
            interrupted = True
            raise KeyboardInterrupt("transient second-observation failure")
        return original(printed)

    monkeypatch.setattr(host_models, "_result", read)

    class Caller:
        def complete(self, system, user, **kwargs):
            return session.run(user, root, root, 1)

    meter = _Requests()
    caller = _Counted(Caller(), meter)
    errors = []
    for _ in range(2):
        try:
            caller.complete("system", "identical request")
        except LLMError as error:
            errors.append(str(error))
    if interrupted:
        assert errors and "G6_E_LOOP_HOST_UNKNOWN" in errors[0]
    # Removing the redundant read is also a valid repair: then no unknown was
    # observed, and two successful launches are permitted.
    assert len(launches) == (1 if interrupted else 2), (
        "unknown ToolSession inputs were launched again after their record was released"
    )
