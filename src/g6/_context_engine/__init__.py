# SPDX-License-Identifier: Apache-2.0
"""The Context Engine (design section 12): knowledge search over the folders named in ``G6_KNOWLEDGE_ROOTS``, served
by the ``g6_knowledge_*`` MCP tools (``g6.mcp.knowledge``).

Core code, not a family and not a component (the ``_`` prefix keeps the registry from listing it): it composes the
knowledge family's members (``ctx_elastic``, ``ctx_colbert``, ``knowledge_indexer``, ``knowledge_chunker``,
``knowledge_converter``) and ``g6.sources.ctx_git``, which a family's shared code may not import (conformance D4). The
helpers the members share stay in ``g6.knowledge``'s ``_``-prefixed modules.
"""
