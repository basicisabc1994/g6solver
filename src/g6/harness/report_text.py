# SPDX-License-Identifier: Apache-2.0
"""Typed Markdown: only G6 templates carry formatting; inserted values are inert."""

from __future__ import annotations

from string import Formatter
from typing import LiteralString

from g6.core.git_name import shown


class _Part(str):
    def __add__(self, other: object) -> G6Text:
        return join_report((self, other))

    def __radd__(self, other: object) -> G6Text:
        return join_report((other, self))


class InertCode(_Part):
    """An encoded caller value; still data, and still handled by _inert at the join."""


class G6Text(_Part):
    """G6's own text or a composition already passed through the guarded join.

    Construction is reserved for source constants and templates. Input records
    are stripped of this marker at the report boundary.
    """

    def __new__(cls, text: LiteralString):
        return super().__new__(cls, text)

    def join(self, values) -> G6Text:
        parts = []
        for value in values:
            if parts:
                parts.append(self)
            parts.append(value)
        return join_report(parts)

    def format(self, *args, **kwargs) -> G6Text:
        parts = []
        automatic = 0
        formatter = Formatter()
        for literal, field, spec, conversion in formatter.parse(self):
            parts.append(G6Text(literal))
            if field is None:
                continue
            if not field:
                field = str(automatic)
                automatic += 1
            value, _ = formatter.get_field(field, args, kwargs)
            if conversion:
                value = formatter.convert_field(value, conversion)
            parts.append(format(value, spec) if spec else value)
        return join_report(parts)


def text(template: LiteralString, *values: object) -> G6Text:
    """Interpolate only through the report's join, never Python's string format."""
    return G6Text(template).format(*values)


def join_report(values) -> G6Text:
    """The only join boundary: untyped text always passes through ``_inert``."""
    from g6.harness.patch_report import _inert

    return G6Text("".join(str(value) if isinstance(value, G6Text) else _inert(value) for value in values))


def caller_values(value):
    """A Python caller cannot grant its record's strings G6's template type."""
    if isinstance(value, str):
        return str(shown(value))
    if isinstance(value, dict):
        return {caller_values(key): caller_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [caller_values(item) for item in value]
    return value
