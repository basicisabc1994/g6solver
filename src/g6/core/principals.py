# SPDX-License-Identifier: Apache-2.0
"""Compare recorded identity fields. A recorded name is not authenticated.

Two names are one principal when they are equal after compatibility normalisation (NFKC), case folding and collapsing
whitespace, which is the base's case-insensitive comparison made a little stricter. Every name is comparable, as at the
base: an absent name compares as empty text. This cannot detect aliases or look-alike letters and cannot authenticate
the person (whether look-alike names should be refused is an open owner question).
"""
import unicodedata


def _identity(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def same_principal(a: object, b: object) -> bool:
    """Whether two recorded identities coincide."""
    return _identity(a) == _identity(b)
