# SPDX-License-Identifier: Apache-2.0
import ast

import pytest

from g6.distill.operators import LEGITIMATE, TRAINING, apply_operator

SUITE = """from subject import answer
def test_answer():
    assert answer(1) == 2
    assert answer(3) == 4
"""


@pytest.mark.parametrize("operator", TRAINING)
def test_training_operators_change_a_planted_suite(operator):
    changed = apply_operator(SUITE, operator)
    assert ast.dump(ast.parse(changed)) != ast.dump(ast.parse(SUITE))
    assert changed.endswith("\n")
    assert "\r" not in changed


@pytest.mark.parametrize("operator", LEGITIMATE)
def test_legitimate_operators_retain_assertions(operator):
    changed = ast.parse(apply_operator(SUITE, operator))
    assert len([node for node in ast.walk(changed) if isinstance(node, ast.Assert)]) == 2
    assert len([node for node in ast.walk(changed) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "answer"]) == 2


def test_operator_names_and_inapplicable_inputs_fail_closed():
    with pytest.raises(ValueError):
        apply_operator(SUITE, "sealed-operator")
    with pytest.raises(ValueError):
        apply_operator("def test_empty():\n    pass\n", TRAINING[0])
    with pytest.raises(ValueError):
        apply_operator("not python!", TRAINING[0])
