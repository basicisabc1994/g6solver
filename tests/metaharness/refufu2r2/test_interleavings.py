"""Focused existing concurrency and settlement checks, with no runners."""
import threading

from tests.metaharness import test_send_record as records
from tests.metaharness import test_provider_stops as stops


def test_parallel_nested_reservations():
    records.test_two_threads_of_nested_calls_take_no_more_than_was_reserved()


def test_parallel_sends_of_one_call():
    records.test_unwatched_identical_sends_of_one_call_on_two_threads_go_out_and_are_each_charged()


def test_durability_is_visible_before_a_waiting_admission(review_dir):
    stops.test_ref1_3_no_thread_sees_a_refusal_before_it_is_on_the_disk(review_dir)


def test_stops_reach_every_nested_meter():
    records.test_a_stop_on_a_send_nested_two_deep_ends_every_tick_around_it("refusal")
    records.test_a_stop_on_a_send_nested_two_deep_ends_every_tick_around_it("limit")


def test_limit_rows_precede_settlement(review_dir, monkeypatch):
    records.test_ref4_3_every_limit_is_on_the_record_in_the_order_met_before_settlement(review_dir, monkeypatch)


def test_no_review_threads_remain():
    # All probes above join the threads they start.
    assert not [thread for thread in threading.enumerate() if thread is not threading.main_thread()]
