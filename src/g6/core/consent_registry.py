# SPDX-License-Identifier: Apache-2.0
"""Consent admission for models handed through the certificate-bound door."""
from __future__ import annotations

from typing import Any

from g6.core.consent_callers import guard


class GuardedRegistry:
    """Put every supplied model through the shared consent guard before dispatch.

    The guard checks each actual model call, so a retained caller cannot outlive
    a revoked source/provider grant. Destination policy keeps local models local.
    All unbound door surfaces pass this adapter, including custom registries;
    other registry operations and the door's evidence checks are delegated.

    The Python API ``g6.invoke`` lives in certificate-bound ``g6/__init__.py``;
    that remaining path is closed at the certificate issuance window.
    """

    def __init__(self, registry: Any = None) -> None:
        if registry is None:
            from g6.core.registry import get_registry

            registry = get_registry()
        self._registry = registry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get('llm') is not None:
            kwargs['llm'] = guard(kwargs['llm'])
        return self._registry.invoke(*args, **kwargs)
