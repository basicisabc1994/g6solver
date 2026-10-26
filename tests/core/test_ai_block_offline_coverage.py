# SPDX-License-Identifier: Apache-2.0
"""Offline coverage for mvp.core.ai_block default methods."""
from __future__ import annotations

from g6.core import AIBlock, Result


def test_default_infer_returns_failure() -> None:
    """AIBlock.infer default returns Result.fail with 'not implemented'."""
    blk = AIBlock(name="test")
    res = blk.infer("x")
    assert res.is_fail()
    assert "not implemented" in res.error


def test_default_bias_returns_descriptor() -> None:
    blk = AIBlock(name="test")
    bias = blk.bias
    assert bias["name"] == "test"
    assert bias["type"] == "AIBlock"
    assert bias["strategy"] == "default"
    assert bias["assumptions"] == []
    assert bias["evolvable_params"] == {}
    assert bias["version"] == 0


def test_repr_format() -> None:
    blk = AIBlock(name="abc")
    s = repr(blk)
    assert "AIBlock" in s
    assert "abc" in s


def test_pipeline_propagates_failure() -> None:
    """When left.infer returns Failure, the pipeline returns it without calling right."""

    class _LeftFail(AIBlock):
        def infer(self, data):
            return Result.fail("left-fail")

    class _Right(AIBlock):
        called = False
        def infer(self, data):
            type(self).called = True
            return Result.ok(data)

    left = _LeftFail(name="L")
    right = _Right(name="R")
    pipe = left >> right
    res = pipe.infer("x")
    assert res.is_fail()
    assert "left-fail" in res.error
    assert _Right.called is False
