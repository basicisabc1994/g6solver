# SPDX-License-Identifier: Apache-2.0
"""The integrations family of extensions: talk to outside systems such as databases, queues and model routers.

Each sub-package is one extension, registered under its own name (``g6.core.registry``); the family package itself is
not a component. Code the family's extensions share lives in ``_``-prefixed modules, which never register.
"""

__g6_family__ = "integrations"
