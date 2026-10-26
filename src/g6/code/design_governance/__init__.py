# SPDX-License-Identifier: Apache-2.0
"""g6.code.design_governance — check a codebase's structure for import cycles, layer breaches and pass-through wrappers, and which cycles a change introduced.

An extension in the code family. Every op reads a file map the caller gives (path to source) for a package it names,
parses it with ``ast`` and runs none of it; nothing walks a disk. ``import_graph`` lists the imports between the
package's modules, grouped by their first ``depth`` dotted parts; ``cycles`` finds the import cycles (Tarjan's strongly
connected components, iterative), each with one cycle through it and the imports that close it; ``layer_violations``
holds the imports to a contract of layers (highest first) and forbidden pairs; ``pass_through`` lists functions that
only forward their parameters; ``diff_cycles(before, after)`` says which cycles a change introduced or removed. A check
that found nothing to check is refused rather than read as clean (no import between the package's modules, a contract
that matches no module), and a file that does not parse is listed and keeps a result from being clean.

    from g6.core.registry import get_registry
    files = {"shop/__init__.py": "", "shop/cart.py": "from shop import price", "shop/price.py": "from shop import cart"}
    out = get_registry().invoke("design_governance", "cycles", {"files": files, "package": "shop"}).unwrap()
    out["cycles"][0]["cycle"]   # ["shop.cart", "shop.price", "shop.cart"]

Not ported from the archive's design_governance, and why: the layout it was fixed to (only imports within the archive's own
package counted, so any other codebase had no edges and passed), the recursive Tarjan that raised the interpreter's recursion
limit for the whole process, the design graph, the lifecycle, the twenty and more design policies, waivers, the
complexity scorecard of scores the caller supplies, and the release gate.
"""
from g6.code.design_governance import graph
from g6.code.design_governance.block import (SENSITIVE_OPS, Contract, DesignGovernanceBlock, DesignGovernanceInput,
                                             Forbid)

__g6_component__ = True
__g6_block__ = "DesignGovernanceBlock"
__g6_family__ = "code"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # none: every op reads the file map it is given
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against; its own use causes none it can name (a clean
# structural result says nothing of complexity inside a function, which the skill states as a blind spot).
__g6_mitigates__: tuple[str, ...] = ("unmanaged_complexity",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "Contract", "DesignGovernanceBlock", "DesignGovernanceInput", "Forbid", "graph"]
