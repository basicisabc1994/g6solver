# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.before_done.4: counts(messages) gives, for every label, how many messages classify gives
it, with a label no message has present at zero."""

LABELS = ("refund", "delivery", "other")


def test_no_messages_gives_every_label_zero():
    from classify import counts
    assert dict(counts([])) == {"refund": 0, "delivery": 0, "other": 0}


def test_each_label_is_counted():
    from classify import counts
    found = counts(["Please refund order 7", "When will it be delivered?", "Do you sell gift cards?", "a refund"])
    assert dict(found) == {"refund": 2, "delivery": 1, "other": 1}


def test_a_label_no_message_has_is_there_at_zero():
    from classify import counts
    assert dict(counts(["refund please", "a refund"])) == {"refund": 2, "delivery": 0, "other": 0}


def test_the_counts_are_classifys_labels():
    from classify import classify, counts
    messages = ["REFUND NOW please", "Delivered late", "hello", "Where was it delivered?", "refund"]
    expected = dict.fromkeys(LABELS, 0)
    for message in messages:
        expected[classify(message)] += 1
    assert dict(counts(messages)) == expected
