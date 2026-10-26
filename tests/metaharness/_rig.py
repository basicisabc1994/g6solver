# SPDX-License-Identifier: Apache-2.0
"""A seeded arithmetic stub model and workflow builder for the meta-harness tests.

The stub answers ``a+b`` correctly with probability ``base_acc``, or ``careful_acc`` when the system prompt contains
``CAREFUL``; a wrong answer is a different number. Each call draws from a stream keyed by the seed, the prompt, the task
and the call count, so two profiles that behave the same still disagree on some cases, as real models do.
"""
from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from g6.harness import Check
from g6.metaharness import Model, Suite, SuiteCase, Workflow


@dataclass
class Stub:
    seed: int
    base_acc: float = 0.5
    careful_acc: float = 0.85
    calls: int = 0
    log: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        self.log.append((system, user))
        m = re.search(r"(\d+)\+(\d+)", user)
        correct = int(m.group(1)) + int(m.group(2)) if m else 0
        acc = self.careful_acc if "CAREFUL" in system else self.base_acc
        key = f"{self.seed}|{system}|{user}|{self.calls}".encode()
        r = random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))
        return str(correct) if r.random() < acc else str(correct + 1 + r.randrange(9))


class Clock:
    """Advances a fixed step per reading, so wall clock depends only on how often it is read."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 0.001
        return self.t


def cases(prefix: str, n: int, offset: int) -> tuple[SuiteCase, ...]:
    out = []
    for i in range(n):
        a, b = 10 + offset + i, 3 + (i * 7) % 50
        out.append(SuiteCase(f"{prefix}{i}", f"What is {a}+{b}?", str(a + b)))
    return tuple(out)


NUMBER = Check("the answer is a whole number", lambda out: out.strip().isdigit(), check_id="number",
               known_bad=("twelve", ""), known_good="12")


def oracle(out: str, expected: str) -> bool:
    return out.strip() == expected


@dataclass
class Fixed:
    """Always the same reply (or ``careful`` when the system prompt contains CAREFUL)."""

    reply: str
    careful: str | None = None
    calls: int = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        return self.careful if self.careful is not None and "CAREFUL" in system else self.reply


def build(seed: int = 1, *, n_dev: int = 10, n_held: int = 40, base_acc: float = 0.5, careful_acc: float = 0.85,
          checks: tuple[Check, ...] = (NUMBER,), oracle_fn: Callable = oracle, stub=None, **kw) -> tuple[Workflow, Stub]:
    stub = stub if stub is not None else Stub(seed, base_acc, careful_acc)
    suite = Suite(dev=cases("d", n_dev, 0), held_out=cases("h", n_held, 500),
                  provenance={"dev": "caller", "held_out": "independent"})
    wf = Workflow(name="arith", task="Compute this. {input} Reply with the number only.", checks=checks,
                  oracle=oracle_fn, suite=suite, models={"local": Model(stub, max_calls_per_run=8)}, **kw)
    return wf, stub

