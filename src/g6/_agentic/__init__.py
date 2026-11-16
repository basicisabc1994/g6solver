# SPDX-License-Identifier: Apache-2.0
"""g6._agentic: the agentic override kernel, v1 (prompt mode only). Not a registry component, so ``g6_invoke`` never
reaches it: from Python it is :func:`run`, over MCP the tool ``g6_agentic_run`` (``g6.mcp.agentic``), which asks the
person in the client's dialog before every override. See :mod:`g6._agentic.kernel`."""
from g6._agentic.kernel import Confirmation, Pending, declined, override, prepare, run

__all__ = ["Confirmation", "Pending", "declined", "override", "prepare", "run"]
