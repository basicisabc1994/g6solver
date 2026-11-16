# SPDX-License-Identifier: Apache-2.0
"""What ``g6_find``, ``g6_describe`` and ``g6_components`` may name (the owner's decision 1, 2026-11-16; plan
revision 8, A+E).

G6's built-in catalogue is G6's own text and public, so those three tools declare no source for it
(``g6.core.consent_declarations``). An installed entry-point extension's name and text are the person's: a reply over
MCP names one only where the person shared ``EXTENSION_SOURCE`` with the client's provider, and then carries that
source, so admission checks the grant again as it sends and records the disclosure. Outside an MCP call (Python, the
command line) nothing is sent and every component is named.
"""
from __future__ import annotations

from g6.core import cloud_consent as policy
from g6.core import registry

#: The source an installed extension's own text stays under.
EXTENSION_SOURCE = "component_catalogue"


def extension(name: str) -> bool:
    """Whether ``name`` is an installed entry-point extension, not one of G6's built-in components."""
    ref = registry.get_registry().refs.get(name)
    return ref is not None and ref.source == "extension"


def carry(name: str) -> None:
    """The reply now holds the extension's text: it goes out only where the person shared ``EXTENSION_SOURCE``."""
    policy.record_sources((policy.Source.text(EXTENSION_SOURCE, "extension:" + name, name),))


def shown_components() -> list[str]:
    """Every built-in component, and an extension only outside an MCP call or where the person shared
    ``EXTENSION_SOURCE`` with this client's provider (the reply then carries that source)."""
    capture = policy.current_capture()
    provider = capture.client[0] if capture is not None and capture.client else "cloud:unknown"
    shown = []
    for name in registry.get_registry().names():
        if extension(name) and capture is not None:
            if not policy.is_granted(EXTENSION_SOURCE, provider):
                continue
            carry(name)
        shown.append(name)
    return shown


__all__ = ["EXTENSION_SOURCE", "carry", "extension", "shown_components"]
