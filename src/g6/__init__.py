# SPDX-License-Identifier: Apache-2.0
"""G6 — an open-source harness-engineering library for AI agents.

Every sub-package is usable on its own (it depends only on `g6.core`).
`g6.harness` composes them end to end. `g6.invoke` runs one op of a component and checks what it says.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__version__ = "0.2.0"


def invoke(component: str, op: str, params: Any = None, *, allow: Iterable[str] | str = ()) -> dict[str, Any]:
    """Run ``op`` of ``component`` on ``params`` and check what it says, exactly as G6's MCP server (``g6_invoke``) and
    ``g6 invoke`` do.

    The reply is the library envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``,
    ``run_id``) beside ``ok`` and ``result`` (or ``error`` when there is no result). A component's own ``verified``
    stands only when G6's own code re-checks the result and reproduces it; otherwise it is held at ``qualified-draft``.
    ``ok`` is false whenever the reply is ``blocked-escalated``.

    A sensitive op (it runs a program on your machine, reaches the network or changes it) runs only when you allow it:
    name it, or its component, in ``allow`` (``"lean"`` or ``("lean.check_proof",)``), or list it in
    ``G6_MCP_SENSITIVE`` where you start G6. ``allow`` grants that one call and nothing after it.

    ``g6.core.registry.get_registry().invoke`` is the raw call underneath: it returns a component's own answer,
    unchecked.
    """
    from g6.core.door import SENSITIVE_ENV, allowed_sensitive, invoke_checked

    names = [allow] if isinstance(allow, str) else list(allow)
    granted = frozenset(part.strip() for name in names for part in str(name).split(",") if part.strip())
    return invoke_checked(component, op, {} if params is None else params, allow=allowed_sensitive() | granted,
                          how_to_allow=f"Allow it in your own code with g6.invoke(..., allow=('{component}.{op}',)), "
                                       f"or list {component}.{op} (or {component}) in {SENSITIVE_ENV} where you start "
                                       "G6.")
