# SPDX-License-Identifier: Apache-2.0
"""The jobs family of extensions: profession profiles: what each kind of work needs, as data plus checks.

Each sub-package is one extension, registered under its own name (``g6.core.registry``); the family package itself is
not a component. Code the family's extensions share lives in ``_``-prefixed modules, which never register.
"""

__g6_family__ = "jobs"
