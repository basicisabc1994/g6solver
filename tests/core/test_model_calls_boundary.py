# SPDX-License-Identifier: Apache-2.0
"""What a ``counting`` observer is given at a request's boundary (0.3.0 issuance window: review b13 finding 2; review
refu2 finding 1).

Two gaps in ``g6.core.model_calls``, as an admission check behind ``counting(before_call=...)`` met them:

* A thread a caller's backend started itself had none of the request's context, so what it noted passed no observer
  and was sent whatever the observer would have said. Pinned: a ``threading.Thread`` started while an observer is open
  runs in a copy of the starting context, so its notes pass the observer and count in the scope; one started with no
  observer open starts as it always did; one that was already running is not reached, which is the limit left.
* A note said only that a request was going out, so a failure could be read back to its send only by what a frame's
  names held when it came out, which a sender can have changed by then. Pinned: each observed note has a record of the
  send's boundary (``noting``), and a failure is read back to its own send by it (``outcome_of``), re-raised or not.

It also pins what the ``counting`` docstring now says: what an observer raises reaches the caller that noted the
request, and that request is not counted. Offline: nothing here asks a model.
"""
from __future__ import annotations

import contextvars
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from g6.core import llm
from g6.core import model_calls as mc

_INHERITS = bool(getattr(sys.flags, "thread_inherit_context", False))


class _NotAdmitted(RuntimeError):
    pass


def _joined(thread: threading.Thread) -> None:
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive()


# -- an observer may raise -----------------------------------------------------------------------------------------------

def test_what_an_observer_raises_reaches_the_noting_caller_and_the_request_is_not_counted() -> None:
    def admit() -> None:
        raise _NotAdmitted("nothing is reserved")

    before = mc.model_calls()
    with mc.counting(before_call=admit) as mine:
        with pytest.raises(_NotAdmitted):
            mc.note_model_call()
        assert mine() == 0
    assert mc.model_calls() == before
    assert "may raise" in mc.counting.__doc__ and "not call a model or raise" not in mc.counting.__doc__


# -- a thread started inside an observed scope ---------------------------------------------------------------------------

def test_a_thread_started_where_an_observer_is_open_passes_it_and_counts_in_the_scope() -> None:
    seen: list[int] = []
    with mc.counting(before_call=lambda: seen.append(threading.get_ident())) as mine:
        _joined(threading.Thread(target=mc.note_model_call))
        assert mine() == 1 and len(seen) == 1 and seen[0] != threading.get_ident()


def test_a_pool_a_backend_makes_inside_the_scope_cannot_note_past_what_the_observer_admits() -> None:
    sent: list[int] = []

    def admit() -> None:
        if len(sent) >= 2:
            raise _NotAdmitted("two were reserved")

    def backend() -> None:
        for number in range(3):
            mc.note_model_call()       # noted before it is sent, as every built-in caller does
            sent.append(number)

    with mc.counting(before_call=admit) as mine:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(_NotAdmitted):
                pool.submit(backend).result(timeout=20)
        assert sent == [0, 1] and mine() == 2


def test_a_thread_of_a_class_with_a_run_of_its_own_is_carried_too() -> None:
    class Sends(threading.Thread):
        def run(self) -> None:
            mc.note_model_call()

    seen: list[str] = []
    with mc.counting(before_call=lambda: seen.append("admitted")) as mine:
        thread = Sends()
        _joined(thread)
        assert mine() == 1 and seen == ["admitted"]
    assert "run" not in vars(thread), "the thread kept a name the carrying gave it"


def test_the_started_thread_has_a_copy_of_the_context_and_changes_nothing_in_its_starter() -> None:
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("issuance_marker", default="unset")
    read: list[str] = []

    def work() -> None:
        read.append(marker.get())
        marker.set("changed by the thread")

    marker.set("set by the request")
    with mc.counting(before_call=lambda: None):
        _joined(threading.Thread(target=work))
    assert read == ["set by the request"] and marker.get() == "set by the request"


def test_a_thread_started_with_no_observer_open_starts_as_it_always_did() -> None:
    with mc.counting(before_call=lambda: None):
        pass                           # an observer has been open in this process, and is no longer
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("issuance_plain_marker", default="unset")
    read: list[str] = []
    marker.set("set by the request")
    with mc.counting() as mine:        # a count with no observer carries nothing
        _joined(threading.Thread(target=lambda: (read.append(marker.get()), mc.note_model_call())))
        assert mine() == (1 if _INHERITS else 0)
    assert read == ["set by the request" if _INHERITS else "unset"]


def test_a_thread_that_was_already_running_is_not_reached() -> None:
    """The limit left: only a thread started while the observer is open is carried."""
    work: queue.Queue = queue.Queue()
    done = threading.Event()

    def serve() -> None:
        work.get(timeout=20)()
        done.set()

    worker = threading.Thread(target=serve)
    worker.start()
    seen: list[str] = []
    before = mc.model_calls()
    with mc.counting(before_call=lambda: seen.append("admitted")) as mine:
        work.put(mc.note_model_call)
        assert done.wait(20)
        assert mine() == 0 and seen == []
    worker.join(timeout=20)
    assert mc.model_calls() - before == 1     # still counted for the process


def test_a_carried_thread_that_notes_once_the_scope_closed_still_passes_its_observer() -> None:
    """As a context a request handed over does: the observer decides what a late note meets."""
    go, seen = threading.Event(), []

    def late() -> None:
        assert go.wait(20)
        mc.note_model_call()

    with mc.counting(before_call=lambda: seen.append("admitted")) as mine:
        thread = threading.Thread(target=late)
        thread.start()
    go.set()
    thread.join(timeout=20)
    assert seen == ["admitted"] and mine() == 1


def test_a_frame_let_go_as_a_scope_closes_may_finalise_what_notes_a_call_or_closes_a_scope() -> None:
    """A frame that goes can run finalisers of what its names held. One that notes a call, or closes a scope of its own,
    needs this module's lock, so no frame is let go while the lock is held. Run on a thread of its own, so that a close
    that never comes back fails this test and does not hang it."""
    finalised: list[str] = []

    class NotesAsItGoes:
        def __del__(self) -> None:
            with mc.counting(before_call=lambda: None):
                mc.note_model_call()
            finalised.append("noted and closed")

    def sender(held: NotesAsItGoes) -> None:     # ``held`` lives as long as this call's frame, which the note's record keeps
        mc.note_model_call()

    def request() -> None:
        with mc.counting(before_call=lambda: None):
            sender(NotesAsItGoes())
            assert finalised == []
        closed.set()

    closed = threading.Event()
    threading.Thread(target=request, daemon=True).start()
    assert closed.wait(20), "closing the scope let a frame go under its own lock, and a finaliser waited on that lock"
    assert finalised == ["noted and closed"]


# -- the record of a note, and the send a failure is the outcome of ------------------------------------------------------

def test_an_observer_reads_the_record_of_the_note_it_is_run_for() -> None:
    notes: list[mc.Note] = []

    def sender() -> None:
        mc.note_model_call()

    def helper() -> None:
        mc.note_model_call(boundary=1)

    def through_a_helper() -> None:
        helper()

    def built_in() -> None:
        llm._start_model_call()        # the built-in callers' own helper names its caller as the boundary

    assert mc.noting() is None
    with mc.counting(before_call=lambda: notes.append(mc.noting())):
        sender()
        through_a_helper()
        built_in()
        assert [note.frame.f_code for note in notes] == [sender.__code__, through_a_helper.__code__, built_in.__code__]
    assert mc.noting() is None


def test_no_record_is_made_where_no_observer_is_open() -> None:
    with mc.counting() as mine:
        mc.note_model_call()
        assert mc.noting() is None and mine() == 1


def test_a_record_holds_its_frame_only_while_the_outermost_observed_scope_is_open() -> None:
    """A frame keeps the stack it ran under alive: it is held while a failure of the request could still come out."""
    notes: list[mc.Note] = []
    go = threading.Event()

    def late() -> None:
        assert go.wait(20)
        mc.note_model_call()

    with mc.counting(before_call=lambda: None):                       # the request's own scope, outermost
        with mc.counting(before_call=lambda: notes.append(mc.noting())):   # a scope nested in it
            mc.note_model_call()
            thread = threading.Thread(target=late)
            thread.start()
        assert notes[0].frame is not None, "the frame went with a scope nested in the request's own"
    assert notes[0].frame is None
    go.set()                           # a thread that kept its context notes once every scope has closed
    thread.join(timeout=20)
    assert len(notes) == 2 and notes[1].frame is None


def test_an_error_re_raised_after_other_sends_is_read_back_to_its_own_send() -> None:
    """Review refu2 finding 1, at the seam: the sender's own frame names the last request by the time the first one's
    error comes out, and the record of each note still says which send the error left through."""
    notes: list[mc.Note] = []
    raised: list[Exception] = []

    def transport(pair: tuple[str, str]) -> str:
        mc.note_model_call()
        if pair[1] == "refused":
            raised.append(RuntimeError("refused by policy"))
            raise raised[-1]
        return "answer"

    def batch(system: str) -> None:
        errors = []
        for user in ("refused", "answered"):
            try:
                transport((system, user))
            except RuntimeError as error:
                errors.append(error)
        raise errors[0]                # the original error, as it was raised, after a later send answered

    with mc.counting(before_call=lambda: notes.append(mc.noting())):
        with pytest.raises(RuntimeError) as caught:
            batch("system")
        assert caught.value is raised[0] and len(notes) == 2
        assert mc.outcome_of(caught.value, notes) is notes[0]
        wrapped = ValueError("the batch failed")
        wrapped.__cause__ = caught.value
        assert mc.outcome_of(wrapped, notes) is notes[0]
    assert mc.outcome_of(caught.value, notes) is None      # the scope has closed: its records hold no frame


def test_an_error_that_left_through_no_boundary_or_a_shared_one_names_no_send() -> None:
    notes: list[mc.Note] = []

    def twice_from_one_call() -> None:
        mc.note_model_call()
        mc.note_model_call()
        raise RuntimeError("one of the two failed")

    with mc.counting(before_call=lambda: notes.append(mc.noting())):
        with pytest.raises(RuntimeError) as shared:
            twice_from_one_call()
        assert len(notes) == 2 and notes[0].frame is notes[1].frame is not None
        assert mc.outcome_of(shared.value, notes) is None
        assert mc.outcome_of(shared.value, notes[:1]) is notes[0]      # one record at that boundary names its send
        assert mc.outcome_of(RuntimeError("raised nowhere"), notes) is None
        assert mc.outcome_of(shared.value, []) is None
