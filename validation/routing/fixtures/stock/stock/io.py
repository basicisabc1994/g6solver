# SPDX-License-Identifier: Apache-2.0
from stock.skus import normalise
from stock.store import Store


def load(rows):
    """A store holding ``rows`` of ``(sku, count)``, each SKU as the store keeps it."""
    store = Store()
    for sku, count in rows:
        store.add(normalise(sku), count)
    return store
