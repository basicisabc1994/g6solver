# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.after_refusal.2: a message about a parcel or a courier is a delivery, whatever its case
and in the plural too, and the other labels stay as they were."""
from fixture_paths import attribute

import pytest


@pytest.mark.parametrize("message", ["Where is my parcel?", "The courier never came", "PARCEL tracking number please",
                                     "Two parcels are missing", "Is the Courier coming today?"])
def test_parcels_and_couriers_are_deliveries(message):
    classify = attribute('classify_m', 'source', 'classify')
    assert classify(message) == "delivery"


@pytest.mark.parametrize("message,label", [("Please refund order 1182", "refund"),
                                           ("When will it be delivered?", "delivery"),
                                           ("Do you sell gift cards?", "other"),
                                           ("Can I change my address for the invoice?", "other")])
def test_the_other_labels_stay(message, label):
    classify = attribute('classify_m', 'source', 'classify')
    assert classify(message) == label
