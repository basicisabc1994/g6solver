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


def public_instructions(value: dict) -> dict:
    """Declare fresh literal program instructions containing no personal data."""
    return cloud.bind_output(value, ())


def host_controls(value: dict, controls: dict, *, sources: Sequence[cloud.Source] | None = None) -> dict:
    """Bind explicitly supplied host controls while retaining the content's origins."""
    body, refs, metadata = cloud.freeze_output(value)
    value = json.loads(body)
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
