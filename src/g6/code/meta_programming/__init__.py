# SPDX-License-Identifier: Apache-2.0
r"""g6.code.meta_programming — rename and extract Python code through its syntax tree and scopes, each edit checked against the tree it should give, comments kept.

An extension in the code family, on the standard library alone. ``parse`` and ``analyze`` read a module's syntax tree
(Python's own parser): whether it parses, its functions, classes, imports and ``__all__``. ``rename`` renames one
binding: Python's scope rules (:mod:`g6.code.meta_programming.scopes`) decide which identifiers mean it, so a
parameter, a shadowing local in another function, an attribute, a keyword argument, a string and a comment that spell
the same name are told apart; only those identifiers' characters change. ``extract_function`` moves whole statements
of a function into a new function with the parameters and returns their names need. Each edit is re-parsed, compiled
(which runs nothing) and compared with the tree it should give, with its changed spans in the evidence, and is
refused rather than returned when a check fails. ``run_tests`` runs your tests on the result in ``g6.core.sandbox``;
it is sensitive.

    from g6.core.registry import get_registry
    out = get_registry().invoke("meta_programming", "rename", {
        "source": "def f(old):\n    return old  # old stays here\n", "old": "old", "new": "count"}).unwrap()
    out["edited"]   # "def f(count):\n    return count  # old stays here\n", a qualified-draft

Not ported from the archive's meta_programming, and why: the vendored code-graph-rag folder and the book PDF in it
(never shipped); rope, which is LGPL-3 (the standard library's scopes and position-based edits keep comments, so no
extra is needed); running caller code on the host (run_code, runtime_profile); the model ops, the Claude CLI and the
direct Ollama calls; context7 over npx; the library catalogue; the fine-tune data generators; verify_logic, which
checked only that no "assert False" appeared; self_heal and orchestrate; black and isort formatting; and every result
it labelled verified.
"""
from g6.code.meta_programming import analyze, edits, extract, runner, scopes
from g6.code.meta_programming.block import SENSITIVE_OPS, MetaProgrammingBlock, MetaProgrammingInput

__g6_component__ = True
__g6_block__ = "MetaProgrammingBlock"
__g6_family__ = "code"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # run_tests runs your code in a bounded child process
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a rename cannot
# see a name reached through a string or from another module, so behaviour there can change).
__g6_mitigates__: tuple[str, ...] = ("fragile_text_edit",)
__g6_introduces__: tuple[str, ...] = ("outside_scope",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "MetaProgrammingBlock", "MetaProgrammingInput", "analyze", "edits", "extract", "runner",
           "scopes"]
