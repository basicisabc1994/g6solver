"""g6.core.errors.unknown_op: one stable refusal for an op a component does not have (FW-05)."""
from __future__ import annotations

from g6.core.errors import UNKNOWN_OP, unknown_op


def test_the_code_is_stable() -> None:
    assert UNKNOWN_OP == "G6_E_UNKNOWN_OP"


def test_a_near_miss_is_named() -> None:
    result = unknown_op("debate", "rnu", ["run", "help"])
    assert result.is_fail()
    assert result.error == "[G6_E_UNKNOWN_OP] debate has no op 'rnu'; did you mean 'run'?"


def test_with_no_near_miss_the_ops_are_listed_in_order() -> None:
    result = unknown_op("debate", "zzz", {"run", "help"})
    assert result.error == "[G6_E_UNKNOWN_OP] debate has no op 'zzz'; its ops are help, run"


def test_a_long_op_list_is_cut_after_eight() -> None:
    ops = [f"op{n:02d}" for n in range(12)]
    result = unknown_op("workforce", "zzz", ops)
    assert result.error.endswith("its ops are op00, op01, op02, op03, op04, op05, op06, op07 and 4 more")


def test_a_component_with_no_ops_says_so() -> None:
    assert unknown_op("empty", "run", []).error == "[G6_E_UNKNOWN_OP] empty has no op 'run'; it has no ops"


def test_the_mcp_door_reads_its_code() -> None:
    # g6_invoke takes a failure's code from a leading "[G6_E_...]"; without the brackets it reports G6_E_INVOKE_FAILED
    from g6.mcp import _CODE

    match = _CODE.match(unknown_op("debate", "rnu", ["run"]).error)
    assert match is not None and match.group(1) == UNKNOWN_OP
