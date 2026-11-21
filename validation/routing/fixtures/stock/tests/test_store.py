# SPDX-License-Identifier: Apache-2.0
from stock.io import load


def test_load_keeps_skus_as_the_store_does():
    assert load([("ab 12", 1)]).available("AB12") == 1


def test_reserve_a_sku_as_the_order_writes_it():
    store = load([("ab-12", 5)])
    store.reserve("AB-12", 2)
    assert store.available("AB-12") == 3
