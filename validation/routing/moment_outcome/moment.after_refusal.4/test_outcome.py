# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.after_refusal.4: top_label(messages) is the label classify gives to the most messages,
and on a tie the alphabetically first of the tied labels, whatever order the messages come in."""


from fixture_paths import attribute

def test_the_label_given_to_most_messages():
    top_label = attribute('classify_m', 'source', 'top_label')
    assert top_label(["Please refund order 1", "A refund please", "When will it be delivered?"]) == "refund"
    assert top_label(["Do you sell gift cards?"]) == "other"


def test_a_tie_goes_to_the_alphabetically_first_label():
    top_label = attribute('classify_m', 'source', 'top_label')
    assert top_label(["Please refund order 1", "When will it be delivered?"]) == "delivery"
    assert top_label(["A refund please", "Do you sell gift cards?"]) == "other"
    assert top_label(["Hello", "A refund please", "When will it be delivered?"]) == "delivery"


def test_the_order_of_the_messages_does_not_settle_a_tie():
    top_label = attribute('classify_m', 'source', 'top_label')
    assert top_label(["When will it be delivered?", "Please refund order 1"]) == "delivery"
    assert top_label(["Please refund order 1", "Hello", "Delivered?", "refund", "hi"]) == "other"
