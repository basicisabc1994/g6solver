# SPDX-License-Identifier: Apache-2.0
"""Held-out checks: every Store operation reads a SKU as the store keeps it (not only the method the traceback named),
an unknown SKU has nothing available, and the shipped tests are left as they are."""
from fixture_paths import attribute, path_for

import hashlib

import pytest


def test_every_operation_takes_any_spelling():
    load = attribute('stock', 'io', 'load')
    store = load([("ab-12", 5)])
    store.reserve("AB-12", 2)
    store.reserve("ab 12", 1)
    assert store.available("AB12") == 2
    store.release("Ab-12", 3)
    assert store.available("ab-12") == 5


def test_add_takes_any_spelling_too():
    Store = attribute('stock', 'source', 'Store')
    store = Store()
    store.add("cd-3", 2)
    store.add("CD 3", 1)
    assert store.available("cd3") == 3


def test_too_many_raises():
    load = attribute('stock', 'io', 'load')
    store = load([("x-1", 1)])
    with pytest.raises(ValueError):
        store.reserve("X1", 2)
    with pytest.raises(ValueError):
        store.release("x 1", 1)


def test_an_unknown_sku():
    load = attribute('stock', 'io', 'load')
    store = load([])
    assert store.available("Q-9") == 0
    with pytest.raises(ValueError):
        store.reserve("Q-9", 1)


def test_the_tests_are_as_shipped():
    text = path_for('stock', "tests").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == '5755d06db7a213cd6d7deae5c4870b9f4517b53ca7d31a9d3ec75e1ba104e235'
