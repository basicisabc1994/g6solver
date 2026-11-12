# SPDX-License-Identifier: Apache-2.0
"""The synchronous outcome adapter's exception language, shared with the isolated bootstrap."""


def observable_exception(kind, *, resolved=False):
    """Every exception type is observable when raised by the call, including BaseException subclasses.

    Before resolution, a qualified name denotes such a type; resolution must still check the actual type.
    There is no special exception family reserved for G6 errors inside the called function.
    A resolved object is judged by its ancestry as the built-in type's own descriptor reads it, so no instance or
    subclass check, attribute lookup or other hook of the object, its class or its metaclass runs.
    """
    if not resolved and isinstance(kind, str):
        return bool(kind) and all(part.isidentifier() for part in kind.split('.'))
    ancestry = type.__dict__['__mro__'].__get__
    return (any(base is type for base in ancestry(type(kind)))
            and any(base is BaseException for base in ancestry(kind)))
