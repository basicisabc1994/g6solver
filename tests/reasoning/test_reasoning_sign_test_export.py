# SPDX-License-Identifier: Apache-2.0
"""``sign_test_p`` is reachable from the package. The function ``compare`` shadows the submodule of the same name on
``g6.reasoning``, so ``g6.reasoning.compare.sign_test_p`` is an attribute of a function and fails; the package exports
the sign test itself."""
from __future__ import annotations

import pytest

import g6.reasoning


def test_sign_test_p_is_exported_by_the_package() -> None:
    assert "sign_test_p" in g6.reasoning.__all__
    assert g6.reasoning.sign_test_p(10, 0) == pytest.approx(1 / 1024)
    assert g6.reasoning.sign_test_p(0, 0) == 1.0


def test_the_export_is_the_function_compare_uses() -> None:
    from g6.reasoning.compare import sign_test_p

    assert g6.reasoning.sign_test_p is sign_test_p and callable(g6.reasoning.compare)
