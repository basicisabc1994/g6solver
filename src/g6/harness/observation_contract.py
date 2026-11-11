# SPDX-License-Identifier: Apache-2.0
"""The synchronous outcome adapter's exception language, shared with the isolated bootstrap."""


def observable_exception(kind, *, resolved=False):
    """Every exception type is observable when raised by the call, including BaseException subclasses.

    Before resolution, a qualified name denotes such a type; resolution must still check the actual type.
    There is no special exception family reserved for G6 errors inside the called function.
    """
    if not resolved and isinstance(kind, str):
        return bool(kind) and all(part.isidentifier() for part in kind.split('.'))
    return isinstance(kind, type) and issubclass(kind, BaseException)
