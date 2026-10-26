# SPDX-License-Identifier: Apache-2.0
"""The registry must work from an installed wheel: no path-walking, no imports until asked."""
from __future__ import annotations

import sys

from g6.core.registry import ComponentRegistry, _NOT_COMPONENTS


def test_infrastructure_packages_are_not_components() -> None:
    names = ComponentRegistry().names()
    assert not set(names) & _NOT_COMPONENTS


def test_listing_names_imports_nothing() -> None:
    before = set(sys.modules)
    ComponentRegistry().names()
    newly = {m for m in set(sys.modules) - before if m.startswith("g6.") and m != "g6.core.registry"}
    assert newly == set()


def test_unknown_component_raises_keyerror() -> None:
    import pytest

    with pytest.raises(KeyError):
        ComponentRegistry().get_module("definitely_not_a_component")
