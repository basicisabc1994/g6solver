# SPDX-License-Identifier: Apache-2.0
"""The archive ran these tests with the intake clarify gate off (its tests/conftest.py set
``G6_PIPELINE_CLARIFICATION=0`` for every test); kept here so they read the same intake."""
import pytest


@pytest.fixture(autouse=True)
def _clarification_gate_off(monkeypatch):
    monkeypatch.setenv("G6_PIPELINE_CLARIFICATION", "0")
