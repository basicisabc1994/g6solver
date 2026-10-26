# SPDX-License-Identifier: Apache-2.0
"""Context tree weighting over bits, with an action-conditional history and a revert for planning.

Kept from the archive's ``ctw.py``: each node holds the Krichevsky-Trofimov estimate of the bits seen in its context,
``P(next = 1) = (ones + 1/2) / (seen + 1)``, and the weighted mixture ``Pw = 1/2 Pkt + 1/2 Pw(child 0) Pw(child 1)``
(a leaf at full depth is its KT estimate), all in log space (Willems, Shtarkov and Tjalkens's context tree weighting).

Changed: a context shorter than the depth is padded with zeros, so every bit is counted along a full-depth path;
:meth:`CTWTree.extend` appends bits to the history as context without counting them (an action is conditioned on,
never predicted: AIXI-04); :meth:`CTWTree.revert` undoes the last counted bit from the nodes it saved, and
:meth:`CTWTree.retract` the last uncounted ones. The deprecated ``predict``, the unused save in ``predict_clean`` and
the unused count rebuild are gone (AIXI-09).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

_LOG_HALF = math.log(0.5)


def _logsumexp(a: float, b: float) -> float:
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    top = max(a, b)
    return top + math.log(math.exp(a - top) + math.exp(b - top))


@dataclass
class CTWNode:
    """One context's counts, its KT log-probability and its weighted log-probability."""

    counts: list[int] = field(default_factory=lambda: [0, 0])
    log_kt: float = 0.0
    log_weighted: float = 0.0
    children: dict[int, CTWNode] = field(default_factory=dict)

    def kt_log_predict(self, symbol: int) -> float:
        zeros, ones = self.counts
        return math.log(((ones if symbol else zeros) + 0.5) / (zeros + ones + 1))

    def kt_update(self, symbol: int) -> None:
        self.log_kt += self.kt_log_predict(symbol)
        self.counts[symbol] += 1

    def compute_weighted(self, leaf: bool) -> None:
        if leaf:
            self.log_weighted = self.log_kt
            return
        children = sum(child.log_weighted for child in self.children.values())
        self.log_weighted = _logsumexp(_LOG_HALF + self.log_kt, _LOG_HALF + children)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"c": list(self.counts), "k": self.log_kt, "w": self.log_weighted}
        if self.children:
            d["n"] = {str(k): v.to_dict() for k, v in self.children.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CTWNode:
        node = cls(counts=[int(d["c"][0]), int(d["c"][1])], log_kt=float(d["k"]), log_weighted=float(d["w"]))
        for k, v in d.get("n", {}).items():
            node.children[int(k)] = cls.from_dict(v)
        return node


@dataclass
class CTWTree:
    """A context tree of ``depth`` over a bit history."""

    depth: int = 16
    root: CTWNode = field(default_factory=CTWNode)
    history: list[int] = field(default_factory=list)
    _undo: list[tuple[list[tuple[CTWNode, list[int], float, float]], int]] = field(default_factory=list)

    def _context(self) -> list[int]:
        """The last ``depth`` bits, most recent first, padded with zeros."""
        recent = self.history[-self.depth:] if self.depth else []
        return list(reversed(recent)) + [0] * (self.depth - len(recent))

    def _path(self) -> list[CTWNode]:
        node, path = self.root, [self.root]
        for bit in self._context():
            node = node.children.setdefault(bit, CTWNode())
            path.append(node)
        return path

    def update(self, bits: list[int]) -> None:
        """Count each bit in its context, then append it to the history (each one can be reverted)."""
        for bit in bits:
            path = self._path()
            self._undo.append(([(n, list(n.counts), n.log_kt, n.log_weighted) for n in path], len(self.history)))
            for depth, node in reversed(list(enumerate(path))):
                node.kt_update(bit)
                node.compute_weighted(leaf=depth == self.depth)
            self.history.append(bit)

    def extend(self, bits: list[int]) -> None:
        """Append bits to the history as context only: they are conditioned on, never counted."""
        self.history.extend(bits)

    def retract(self, count: int) -> None:
        """Remove the last ``count`` bits that :meth:`extend` appended."""
        if count:
            del self.history[-count:]

    def revert(self, count: int = 1) -> None:
        """Undo the last ``count`` counted bits (and any history appended after them)."""
        for _ in range(count):
            if not self._undo:
                return
            saved, length = self._undo.pop()
            for node, counts, log_kt, log_weighted in saved:
                node.counts, node.log_kt, node.log_weighted = counts, log_kt, log_weighted
            del self.history[length:]

    def predict(self, symbol: int) -> float:
        """The mixture's probability that the next bit is ``symbol``, leaving the tree as it was."""
        before = self.root.log_weighted
        self.update([symbol])
        after = self.root.log_weighted
        self.revert()
        return math.exp(after - before)

    def log_probability(self) -> float:
        """The mixture's log-probability of every bit counted so far."""
        return self.root.log_weighted

    def forget_undo(self) -> None:
        """Drop the undo records (real experience is never reverted)."""
        self._undo.clear()

    def node_count(self) -> int:
        count, stack = 0, [self.root]
        while stack:
            node = stack.pop()
            count += 1
            stack.extend(node.children.values())
        return count

    def to_dict(self) -> dict[str, Any]:
        return {"depth": self.depth, "history": list(self.history), "root": self.root.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CTWTree:
        return cls(depth=int(d["depth"]), root=CTWNode.from_dict(d["root"]), history=[int(b) for b in d["history"]])


__all__ = ["CTWNode", "CTWTree"]
