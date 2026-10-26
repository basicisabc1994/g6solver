# SPDX-License-Identifier: Apache-2.0
"""MDL scoring (framework section 6): the two-part code.

Raw code length of a trace: each evaluated config costs full precision —
sum over params of bits(param). Under an abstraction, a config whose cell is
already known costs only log2(n_cells) bits (name the cell) plus a residual
in-cell precision term; the mechanism itself costs spec-length bits.

This is a proxy for Kolmogorov structure, honest about being one: its role
in (BLP) is *relative* comparison between candidate libraries on the same
trace, for which a consistent proxy suffices.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .abstractions import Abstraction
from .domain import ConcreteDomain

BITS_FLOAT = 32.0
BITS_INT = 16.0


def _bits_per_config(domain: ConcreteDomain) -> float:
    total = 0.0
    for spec in domain.params.values():
        if spec.kind == "categorical":
            total += math.log2(max(2, len(spec.choices or ())))
        elif spec.kind == "int":
            total += BITS_INT
        else:
            total += BITS_FLOAT
    return total


@dataclass
class MDLReport:
    raw_bits: float
    coded_bits: float
    mechanism_bits: float

    @property
    def compression(self) -> float:
        return self.raw_bits / max(1e-9, self.coded_bits + self.mechanism_bits)

    @property
    def net_bits_saved(self) -> float:
        return self.raw_bits - self.coded_bits - self.mechanism_bits

    def to_dict(self) -> dict[str, Any]:
        return {"raw_bits": round(self.raw_bits, 1),
                "coded_bits": round(self.coded_bits, 1),
                "mechanism_bits": round(self.mechanism_bits, 1),
                "compression": round(self.compression, 4),
                "net_bits_saved": round(self.net_bits_saved, 1)}


def score(abstraction: Abstraction, domain: ConcreteDomain,
          warmup: int = 3) -> MDLReport:
    per = _bits_per_config(domain)
    n = len(domain.records)
    raw = per * n

    coded = per * min(warmup, n)  # warmup configs at full price
    from .abstractions import from_spec
    fitted = from_spec(abstraction.spec())
    residual_frac = 0.25  # in-cell residual precision share
    for i in range(warmup, n):
        fitted.fit(domain, upto=i)
        rec = domain.records[i]
        if fitted.covers(rec.config):
            coded += math.log2(max(2, fitted.n_cells())) + residual_frac * per
        else:
            coded += per
    return MDLReport(raw_bits=raw, coded_bits=coded,
                     mechanism_bits=abstraction.mdl_bits())
