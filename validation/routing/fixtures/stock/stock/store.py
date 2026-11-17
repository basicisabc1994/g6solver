# SPDX-License-Identifier: Apache-2.0
class Store:
    """Stock on hand and reserved, by SKU."""

    def __init__(self):
        self._on_hand = {}
        self._reserved = {}

    def add(self, sku, count):
        self._on_hand[sku] = self._on_hand.get(sku, 0) + count

    def available(self, sku):
        """What can still be reserved: on hand less reserved."""
        return self._on_hand.get(sku, 0) - self._reserved.get(sku, 0)

    def reserve(self, sku, count):
        """Reserve ``count``; ValueError when fewer are available."""
        if count > self._on_hand[sku] - self._reserved.get(sku, 0):
            raise ValueError("not enough stock")
        self._reserved[sku] = self._reserved.get(sku, 0) + count

    def release(self, sku, count):
        """Give back ``count`` reserved; ValueError when fewer are reserved."""
        if count > self._reserved.get(sku, 0):
            raise ValueError("not that many reserved")
        self._reserved[sku] -= count
