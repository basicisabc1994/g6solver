# SPDX-License-Identifier: Apache-2.0
"""Deterministic construction, never labelling. Unsupported shapes are refused.

Legitimate operators accept only simple top-level, argument-free tests. They
do not claim semantic equivalence for arbitrary Python: the oracle settles it.
No held-out operator set is imported, embedded, or exposed here.
"""
from __future__ import annotations

import ast

TRAINING = ("erase_assertion", "assert_true", "loosen_equality")
LEGITIMATE = ("rename_test", "extract_helper", "named_constant", "split_test")


def apply_operator(source: str, operator: str) -> str:
    if operator not in (*TRAINING, *LEGITIMATE):
        raise ValueError("unknown public operator")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("unparseable tests") from exc
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names.update(n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assertions = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if not tests or not assertions:
        raise ValueError("no applicable test assertion")
    target = assertions[0]
    if operator == "erase_assertion":
        for parent in ast.walk(tree):
            for field, value in ast.iter_fields(parent):
                if isinstance(value, list):
                    setattr(parent, field, [ast.Pass() if n is target else n for n in value])
    elif operator == "assert_true":
        target.test = ast.Constant(value=True)
    elif operator == "loosen_equality":
        if not isinstance(target.test, ast.Compare) or len(target.test.ops) != 1 or not isinstance(
                target.test.ops[0], ast.Eq):
            raise ValueError("no simple equality to loosen")
        target.test = ast.Compare(left=target.test.left, ops=[ast.IsNot()], comparators=[ast.Constant(value=None)])
    else:
        test = tests[0]
        args = test.args
        if (test.decorator_list or args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg
                or any(not isinstance(n, ast.Assert) for n in test.body)):
            raise ValueError("refactoring requires a plain argument-free assertion-only test")
        fresh = "distill_case_helper"
        if fresh in names or "DISTILL_EXPECTED" in names or "test_distill_refactored" in names:
            raise ValueError("refactoring name collision")
        if operator == "rename_test":
            # References to the old name would change meaning outside collection.
            if any(isinstance(n, ast.Name) and n.id == test.name for n in ast.walk(tree)):
                raise ValueError("test name is referenced")
            test.name = "test_distill_refactored"
        elif operator == "extract_helper":
            helper = ast.FunctionDef(name=fresh, args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                     kw_defaults=[], defaults=[]), body=test.body, decorator_list=[])
            test.body = [ast.Expr(value=ast.Call(func=ast.Name(id=fresh, ctx=ast.Load()), args=[], keywords=[]))]
            tree.body.insert(0, helper)
        elif operator == "split_test":
            if len(test.body) < 2 or any(isinstance(n, (ast.NamedExpr, ast.Await, ast.Yield)) for n in ast.walk(test)):
                raise ValueError("no independently splittable assertions")
            new_name = "test_distill_refactored"
            tree.body.append(ast.FunctionDef(name=new_name, args=test.args, body=test.body[1:], decorator_list=[]))
            test.body = test.body[:1]
        elif operator == "named_constant":
            constants = [n for n in ast.walk(target.test) if isinstance(n, ast.Constant)
                         and type(n.value) in (int, str, bool)]
            if not constants:
                raise ValueError("no literal to name")
            constant = constants[-1]
            for parent in ast.walk(target):
                for field, value in ast.iter_fields(parent):
                    if value is constant:
                        setattr(parent, field, ast.Name(id="DISTILL_EXPECTED", ctx=ast.Load()))
                    elif isinstance(value, list):
                        setattr(parent, field, [ast.Name(id="DISTILL_EXPECTED", ctx=ast.Load())
                                               if n is constant else n for n in value])
            tree.body.insert(0, ast.Assign(targets=[ast.Name(id="DISTILL_EXPECTED", ctx=ast.Store())], value=constant))
    result = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    compile(result, "<operator-output>", "exec")  # syntax only; source is never executed here
    return result
