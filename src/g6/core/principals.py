# SPDX-License-Identifier: Apache-2.0
"""Compare recorded identity fields. A recorded name is not authenticated.

The standard library has no Unicode TR39 skeleton. After compatibility
normalisation only printable ASCII identities can establish separation.
Ambiguous or absent identities refuse the comparison instead of proving two
different people. This cannot detect aliases or authenticate the person.
"""
import unicodedata


def _identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("a recorded principal identity is required; names are not authenticated")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = " ".join(normalized.split())
    if not normalized or not normalized.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in normalized):
        raise ValueError("principal identity is empty or ambiguous; names are recorded, not authenticated")
    return normalized


def same_principal(a: object, b: object) -> bool:
    """Whether two recorded identities coincide; refuse ambiguous separation."""
    return _identity(a) == _identity(b)
