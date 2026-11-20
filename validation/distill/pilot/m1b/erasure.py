"""Bounded, indexed direct-assertion erasures; no source is executed."""
from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    variant_id: str
    after_tests: str
    kind: str = "indexed_erasure"


class ErasureVariants(list[Variant]):
    """A construction list with a reason code, including when empty."""

    def __init__(self, variants: Iterable[Variant] = (), *, reason: str = "ok"):
        super().__init__(variants)
        self.reason = reason


def indexed_erasures(before_tests: str, after_tests: str) -> list[Variant]:
    """Erase each direct assert in changed top-level tests, up to eight total.

    Ids use the function name and its one-based direct-assertion ordinal.
    AST comparisons ignore locations and formatting. Duplicate test names are
    refused because the function identity and therefore the ids are ambiguous.
    The returned list has a ``reason`` code; parse/AST failures return no variants.
    """
    try:
        before, after = ast.parse(before_tests), ast.parse(after_tests)
    except (SyntaxError, ValueError):
        return ErasureVariants(reason="unparseable_tests")
    groups = [
        [node for node in tree.body
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")]
        for tree in (before, after)
    ]
    if any(len({node.name for node in group}) != len(group) for group in groups):
        return ErasureVariants(reason="ambiguous_test_function")
    previous = {node.name: ast.dump(node) for node in groups[0]}
    variants = []
    for function in groups[1]:
        if previous.get(function.name) == ast.dump(function):
            continue
        ordinal = 0
        for index, statement in enumerate(function.body):
            if not isinstance(statement, ast.Assert):
                continue
            ordinal += 1
            function.body[index] = ast.Pass()
            try:
                source = ast.unparse(ast.fix_missing_locations(after)) + "\n"
                compile(source, "<indexed-erasure>", "exec")  # syntax only
            except SyntaxError:
                return ErasureVariants(reason="unparseable_tests")
            finally:
                function.body[index] = statement
            variants.append(Variant(f"indexed_erasure:{function.name}:{ordinal}", source))
            if len(variants) == 8:
                return ErasureVariants(variants)
    return ErasureVariants(variants, reason="ok" if variants else "no_eligible_assert")
