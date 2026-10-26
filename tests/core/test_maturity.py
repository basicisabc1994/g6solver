# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from g6.core.maturity import (
    component_capabilities,
    component_maturity,
    get_capabilities,
    get_maturity,
    is_observable,
    maturity_reason,
    observable,
)


def test_decorator_sets_attribute():
    @component_maturity("beta", reason="no telemetry yet")
    class MyBlock:
        pass

    assert get_maturity(MyBlock) == "beta"
    assert maturity_reason(MyBlock) == "no telemetry yet"


def test_decorator_rejects_invalid_level():
    with pytest.raises(ValueError):
        component_maturity("gold")  # type: ignore[arg-type]


def test_unmarked_class_returns_none():
    class Plain:
        pass

    assert get_maturity(Plain) is None
    assert maturity_reason(Plain) == ""


def test_component_capabilities_decorator_sets_tuple():
    @component_capabilities("routing", "audit")
    class CapableBlock:
        pass

    assert get_capabilities(CapableBlock) == ("routing", "audit")


def test_unmarked_class_has_no_capabilities():
    class Plain:
        pass

    assert get_capabilities(Plain) == ()


def test_observable_decorator_marks_class():
    @observable
    class ObservableBlock:
        pass

    assert is_observable(ObservableBlock) is True


def test_unmarked_class_is_not_observable():
    class Plain:
        pass

    assert is_observable(Plain) is False
