# SPDX-License-Identifier: Apache-2.0
"""The agents family of extensions: run other agent runtimes behind one interface, with permissions, limits and the action gate.

Each sub-package is one extension, registered under its own name (``g6.core.registry``); the family package itself is
not a component. Code the family's extensions share lives in ``_``-prefixed modules, which never register.
"""

__g6_family__ = "agents"
