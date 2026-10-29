# SPDX-License-Identifier: Apache-2.0
"""G6 self-maintenance: Theory of Constraints, budgeted agent proposals, held-out safety checks.

Exposes read-only planning through the existing G6 MCP registry. Writes and
agent invocations require a separately authorised host and disposable checkout.
"""
from g6.self_maintenance.block import SelfMaintenanceBlock, MaintenanceInput

__g6_component__ = True
__g6_block__ = "SelfMaintenanceBlock"
__g6_maturity__ = "experimental"
__g6_requires__ = ("agent_codex", "agent_claude", "workforce")
__g6_sensitive_ops__: tuple[str, ...] = ()
__all__ = ["SelfMaintenanceBlock", "MaintenanceInput"]
