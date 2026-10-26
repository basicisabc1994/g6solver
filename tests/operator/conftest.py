# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a throwaway operator store per test, one owner's scope, a venture and its queue."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from g6.operator import ActionQueue, OperatorStore

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)   # month "2026-10": the queue's clock and every tick's ``now``


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def store(tmp_path):
    s = OperatorStore(tmp_path / "operator.db")
    yield s
    s.close()


@pytest.fixture
def scope(store):
    return store.for_owner("dana")


@pytest.fixture
def venture(scope):
    return scope.create_venture("newsletter", caps={"model": 1.0, "cash": 500.0})["venture_id"]


@pytest.fixture
def queue(scope):
    return ActionQueue(scope, clock=lambda: NOW)


@pytest.fixture
def plan():
    """A complete verification contract, so the action gate sends an outward draft to a person instead of blocking."""
    return {"expected_result": "the buyer replies", "observation_window": "7 days", "leading_indicators": ["reply"],
            "lagging_indicators": ["payment"], "monitor": "the inbox", "verification_time": "day 7",
            "stop_threshold": "no reply", "rollback_procedure": "send a correction", "model_update_rule": "re-rank"}
