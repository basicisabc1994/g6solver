# SPDX-License-Identifier: Apache-2.0
"""Closed toy worlds for teaching: no file, network or other outward effects."""
from __future__ import annotations

import random


class CoinFlip:
    """Guess the coin (heads 1 with probability bias); reward 1 for a right guess. Observation: the coin."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 1, 1, 0

    def __init__(self, seed: int, bias: float = 0.9) -> None:
        self.rng, self.bias = random.Random(f"coin:{seed}"), bias

    def step(self, action: int) -> tuple[int, int]:
        coin = 1 if self.rng.random() < self.bias else 0
        return coin, int(action == coin)


class TigerDoor:
    """Listen (-1, a noisy hint), or open a door: +10 for the gold, -100 for the tiger, then the tiger moves."""

    num_actions, observation_bits, reward_bits, reward_min = 3, 1, 7, -100

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(f"tiger:{seed}")
        self.tiger = self.rng.randrange(2)

    def step(self, action: int) -> tuple[int, int]:
        if action == 0:
            hint = self.tiger if self.rng.random() < 0.85 else 1 - self.tiger
            return hint, -1
        reward = -100 if action - 1 == self.tiger else 10
        self.tiger = self.rng.randrange(2)
        return 0, reward


class SimpleMaze1D:
    """Cells 0 to 3, start at 0, actions left and right; reaching cell 3 pays 1 and returns to 0. Observation: the
    cell."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 2, 1, 0

    def __init__(self, seed: int) -> None:
        self.cell = 0

    def step(self, action: int) -> tuple[int, int]:
        self.cell = max(0, self.cell - 1) if action == 0 else self.cell + 1
        if self.cell == 3:
            self.cell = 0
            return 0, 1
        return self.cell, 0


