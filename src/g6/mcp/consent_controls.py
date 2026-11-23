# SPDX-License-Identifier: Apache-2.0
"""Explicit host control declarations for replies whose content may be withheld.

These helpers are host-code contracts, never tool arguments or source grants.
Callers must supply controls from a fresh host envelope, a freshly minted handle,
or the client's literal handle argument. A field in arbitrary result data is not
authority to disclose it. Unannotated content remains UNKNOWN.
"""
from __future__ import annotations

from collections.abc import Sequence
import json

from g6.core import cloud_consent as cloud
from g6.core.door import blocked_reply as _raw_blocked_reply


def public_instructions(value: dict) -> dict:
    """Declare fresh literal program instructions containing no personal data."""
    return cloud.bind_output(value, ())


def blocked_reply(error: str, *, request_id: str = "", public: bool = False) -> dict:
    """The door's fresh refusal, with its minted code carried as a host control.

    Diagnostics keep their origins. Only a callsite that knows its text is fixed
    program instructions may additionally declare it public.
    """
    value = _raw_blocked_reply(error, request_id=request_id)
    if public:
        public_instructions(value)
    return refused_send(value, code=value['warning_card']['code'])


def blocked(code: str, *args, public: bool = False, **kwargs) -> dict:
    """The meta-harness's fresh refusal with its producer-supplied code."""
    from g6.metaharness.api import _blocked

    value = _blocked(code, *args, **kwargs)
    if public:
        public_instructions(value)
    return refused_send(value, code=code)


def producer_reply(value):
    """Carry a fresh host producer's refusal envelope across an MCP adapter.

    Use only immediately around a known host operation, never on arbitrary
    component payloads or dictionaries supplied by a caller. No content becomes
    public: the producer's bound origins and metadata are retained.
    """
    from g6.invariants import ERROR_CODE_PATTERN

    if isinstance(value, dict) and value.get('completion_state') == 'blocked-escalated':
        card = value.get('warning_card')
        code = card.get('code') if isinstance(card, dict) else None
        if isinstance(code, str) and ERROR_CODE_PATTERN.fullmatch(code):
            _, refs, metadata = cloud.freeze_output(value)
            controls = {key: value[key] for key in ('request_id', 'run_id') if key in value}
            # Preserve producer subclasses and activity custody; a JSON copy
            # would erase their process-local ownership before the job freezes it.
            return cloud.bind_output(value, refs if refs is not None else cloud.current_sources() or (cloud.UNKNOWN,),
                                     metadata={**metadata, **controls, 'error_code': code})
    return value


def host_controls(value: dict, controls: dict, *, sources: Sequence[cloud.Source] | None = None) -> dict:
    """Bind explicitly supplied host controls while retaining the content's origins."""
    body, refs, metadata = cloud.freeze_output(value)
    try:
        value = json.loads(body)
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    if sources is not None:
        refs = cloud.boundary_sources(sources, carried=refs if refs is not None else (), inherit=False)
    if refs is None:
        refs = cloud.current_sources() or (cloud.UNKNOWN,)
    metadata = {**metadata, **controls}
    return cloud.bind_output(value, refs, metadata=metadata)


def refused_send(value: dict, *, code: str) -> dict:
    """Preserve a fresh host refusal factory's constant code and minted envelope ids.

    The caller supplies its own code constant; diagnostics and result text are
    never parsed for a code or promoted into shareable metadata.
    """
    controls = {key: value[key] for key in ('request_id', 'run_id') if key in value}
    controls['error_code'] = code
    if value.get('code') == code:
        controls['code'] = code
    return host_controls(value, controls)
