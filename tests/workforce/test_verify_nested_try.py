# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The nested-try defect in the weakened-test comparison (Gate W D-W7, founder 2026-10-10).

``mvp.venture.verify._asserts`` decides which asserts a test really makes: one guarded by a catch-all handler is no assert.
It walked a statement directly inside a ``try`` as a PARENT (its children) and never examined the statement itself, so:

* a ``try`` directly inside another ``try``'s body had its own catch-all handler ignored: a test weakened by wrapping its
  assert in ``try: ... except Exception: pass`` inside an outer ``try`` still counted as asserting;
* a ``try`` or an ``assert`` directly in an ``else:`` or ``finally:`` block was never counted: removing it looked like nothing.

The comparison reports evidence, never proof (a clean answer is not a claim that the tests are intact). Each case here is the
weakening, its control (the same shape that is not a weakening), and the answer through the op verify_task uses.
"""
from __future__ import annotations

import pytest
from g6.workforce.verify import weakened_tests

PATH = "tests/test_calc.py"


def _found(before: str, after: str) -> list[str]:
    return weakened_tests({PATH: before}, {PATH: after})


STRAIGHT = "def test_add():\n    assert add(1, 2) == 3\n"
NESTED_SWALLOWED = (
    "def test_add():\n"
    "    try:\n"
    "        try:\n"
    "            assert add(1, 2) == 3\n"
    "        except Exception:\n"
    "            pass\n"
    "    except ValueError:\n"
    "        pass\n"
)
NESTED_NARROW = (
    "def test_add():\n"
    "    try:\n"
    "        try:\n"
    "            assert add(1, 2) == 3\n"
    "        except ValueError:\n"
    "            pass\n"
    "    except KeyError:\n"
    "        pass\n"
)


def test_an_assert_swallowed_by_a_catch_all_inside_an_outer_try_is_a_weakening():
    found = _found(STRAIGHT, NESTED_SWALLOWED)
    assert found and "an assert was removed or changed" in found[0], found


def test_the_same_nesting_with_only_narrow_handlers_still_asserts():
    """NEGATIVE CONTROL: a handler for ValueError or KeyError does not swallow an AssertionError, so the assert stands."""
    assert _found(STRAIGHT, NESTED_NARROW) == []


def test_a_test_that_was_already_swallowed_is_not_reported_for_staying_that_way():
    """NEGATIVE CONTROL: the comparison is about what a change removes, not about a test that never asserted."""
    assert _found(NESTED_SWALLOWED, NESTED_SWALLOWED) == []


def test_a_catch_all_two_levels_down_in_a_body_is_seen():
    deep = ("def test_add():\n    try:\n        try:\n            try:\n                assert add(1, 2) == 3\n"
            "            except BaseException:\n                pass\n        except KeyError:\n            pass\n"
            "    except ValueError:\n        pass\n")
    assert _found(STRAIGHT, deep)


FINALLY_ASSERT = "def test_add():\n    try:\n        result = add(1, 2)\n    finally:\n        assert result == 3\n"
FINALLY_GONE = "def test_add():\n    try:\n        result = add(1, 2)\n    finally:\n        pass\n"
ELSE_ASSERT = "def test_add():\n    try:\n        result = add(1, 2)\n    except KeyError:\n        result = 0\n    else:\n        assert result == 3\n"
ELSE_GONE = "def test_add():\n    try:\n        result = add(1, 2)\n    except KeyError:\n        result = 0\n    else:\n        pass\n"


@pytest.mark.parametrize("before,after", [(FINALLY_ASSERT, FINALLY_GONE), (ELSE_ASSERT, ELSE_GONE)],
                         ids=["finally", "else"])
def test_an_assert_removed_from_a_finally_or_else_block_is_a_weakening(before, after):
    found = _found(before, after)
    assert found and "an assert was removed or changed" in found[0], found


@pytest.mark.parametrize("src", [FINALLY_ASSERT, ELSE_ASSERT], ids=["finally", "else"])
def test_an_unchanged_finally_or_else_assert_is_not_a_finding(src):
    """NEGATIVE CONTROL: counting these asserts must not turn an unchanged test into a finding."""
    assert _found(src, src) == []


def test_an_assert_in_an_else_block_is_not_guarded_by_the_handlers_of_its_own_try():
    """An exception in ``else:`` is not caught by that ``try``'s handlers, so a catch-all there does not swallow it."""
    swallow_around = ("def test_add():\n    try:\n        result = add(1, 2)\n    except Exception:\n        result = 0\n"
                      "    else:\n        assert result == 3\n")
    assert _found(swallow_around, swallow_around) == []
    assert _found(swallow_around, swallow_around.replace("        assert result == 3\n", "        pass\n"))


def test_a_catch_all_around_a_single_assert_in_a_plain_body_is_still_a_weakening():
    """REGRESSION: the case that always worked keeps working."""
    plain = "def test_add():\n    try:\n        assert add(1, 2) == 3\n    except Exception:\n        pass\n"
    assert _found(STRAIGHT, plain)


