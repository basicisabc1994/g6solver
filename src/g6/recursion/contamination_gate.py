# SPDX-License-Identifier: Apache-2.0
"""Evaluator-only contamination screens; exported records contain counts only."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from statistics import mean, stdev
from urllib.parse import unquote

from g6.metaharness.schema import NOT_HELD_OUT, Suite, SuiteCase


class ContaminationError(ValueError):
    """A content-free screening refusal."""


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class MembershipProbe:
    """Per-token losses from the base and adapter on matched statements and controls."""

    base_loss: float
    adapter_loss: float
    control_base_losses: tuple[float, ...]
    control_adapter_losses: tuple[float, ...]

    def __post_init__(self):
        if (type(self.control_base_losses) is not tuple or type(self.control_adapter_losses) is not tuple
                or len(self.control_base_losses) < 2
                or len(self.control_base_losses) != len(self.control_adapter_losses)
                or not all(_number(v) for v in (self.base_loss, self.adapter_loss,
                                                *self.control_base_losses, *self.control_adapter_losses))):
            raise ContaminationError("G6_E_RSI_MEMBERSHIP_INVALID")

    @property
    def flagged(self) -> bool:
        drops = tuple(a - b for a, b in zip(self.control_base_losses, self.control_adapter_losses))
        return self.base_loss - self.adapter_loss > mean(drops) + 2 * stdev(drops)


@dataclass(frozen=True)
class CanaryProbe:
    completion_rate: float
    chance_rate: float

    def __post_init__(self):
        if not all(_number(v) and v <= 1 for v in (self.completion_rate, self.chance_rate)):
            raise ContaminationError("G6_E_RSI_CANARY_PROBE_INVALID")

    @property
    def flagged(self) -> bool:
        return self.completion_rate > self.chance_rate


@dataclass(frozen=True)
class ScreenReport:
    total: int
    excluded_indices: tuple[int, ...] = field(repr=False)
    identity_excluded: int
    ngram_excluded: int
    membership_excluded: int
    canary_hits: int
    canary_probe_flags: int

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_indices)

    @property
    def voided(self) -> bool:
        return bool(self.canary_hits or self.canary_probe_flags or self.membership_excluded)

    def to_record(self) -> dict:
        record = asdict(self)
        record.pop("excluded_indices")
        return record | {"excluded_count": self.excluded_count, "voided": self.voided}


def _strings(value: object, *, strict=True, keys=True):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if keys:
                yield from _strings(key, strict=strict, keys=keys)
            yield from _strings(item, strict=strict, keys=keys)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _strings(item, strict=strict, keys=keys)
    elif strict and value is not None and type(value) not in (bool, int, float):
        raise ContaminationError("G6_E_RSI_SCREEN_INVALID")


def case_strings(case: SuiteCase) -> tuple[str, ...]:
    return tuple(_strings((case.case_id, case.input, case.expected, case.meta)))


# Unicode Default_Ignorable_Code_Point, including its reserved ranges.
_IGNORABLE = re.compile(
    "[\u00ad\u034f\u061c\u115f-\u1160\u17b4-\u17b5\u180b-\u180f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f"
    "\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a\U000e0000-\U000e0fff]"
)
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_SURROGATE_ESCAPE = re.compile(r"\\u(d[89ab][0-9a-f]{2})\\u(d[cdef][0-9a-f]{2})", re.IGNORECASE)


def canonical_text(text: str) -> str:
    """Use the same text equivalence for identities, overlap and canary incidents."""
    while True:
        normalized = _IGNORABLE.sub("", unicodedata.normalize("NFKC", text).casefold())
        paired = _SURROGATE_ESCAPE.sub(
            lambda m: chr(0x10000 + ((int(m[1], 16) - 0xD800) << 10) + int(m[2], 16) - 0xDC00), normalized)
        decoded = unquote(_ESCAPE.sub(lambda m: chr(int(m[1], 16)), paired))
        if decoded == normalized:
            return " ".join(normalized.split())
        text = decoded


def scan_texts(training, model_outputs=(), *, strict=True) -> tuple[str, ...]:
    """Scan fields and ordered joins per case; never join independent cases or outputs."""
    if not isinstance(training, Sequence) or isinstance(training, (str, bytes)):
        if strict:
            raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
        training = ()
    if not isinstance(model_outputs, Sequence) or isinstance(model_outputs, (str, bytes)):
        if strict:
            raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
        model_outputs = (model_outputs,)
    found = []
    for case in training:
        if not isinstance(case, SuiteCase):
            if strict:
                raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
            continue
        value = (case.case_id, case.input, case.expected, case.meta)
        fields = tuple(_strings(value, strict=strict))
        values = tuple(_strings(value, strict=strict, keys=False))
        found.extend(canonical_text(s) for s in fields)
        for fragments in (fields, values):
            found.extend(canonical_text(separator.join(fragments)) for separator in ("", " "))
    for output in model_outputs:
        if isinstance(output, str):
            found.append(canonical_text(output))
        elif strict:
            raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
    return tuple(found)


def canary_matches(canaries: Sequence[str], texts: Sequence[str]) -> tuple[int, ...]:
    """Return marker positions only; text comes from the shared canonical scanner."""
    return tuple(i for i, marker in enumerate(canaries)
                 if any(canonical_text(marker) in text for text in texts))


def _grams(texts: Sequence[str]) -> set[tuple[str, ...]]:
    found = set()
    for text in texts:
        tokens = re.findall(r"\w+|[^\w\s]", canonical_text(text))
        found.update(tuple(tokens[i:i + 13]) for i in range(len(tokens) - 12))
    return found


def screen(training: Sequence[SuiteCase], held_out: Sequence[SuiteCase], *, canaries: Sequence[str],
           membership: Mapping[str, MembershipProbe], model_outputs: Sequence[str] = (),
           canary_probes: Sequence[CanaryProbe] = ()) -> ScreenReport:
    """Check every item; missing membership readings are a refusal, never a clean result."""
    if (not held_out or any(not isinstance(c, SuiteCase) for c in (*training, *held_out))
            or len({c.case_id for c in held_out}) != len(held_out)
            or not canaries or any(type(c) is not str or not re.fullmatch(r"[0-9a-f]{32}", c) for c in canaries)
            or any(type(s) is not str for s in model_outputs)
            or any(not isinstance(p, CanaryProbe) for p in canary_probes)):
        raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
    training_texts = scan_texts(training)
    hits = len(canary_matches(tuple(set(canaries)), (*training_texts, *scan_texts((), model_outputs))))
    probe_flags = sum(p.flagged for p in canary_probes)
    # A known leak takes precedence over absent statistical readings.
    if hits or probe_flags:
        return ScreenReport(len(held_out), (), 0, 0, 0, hits, probe_flags)
    if (not isinstance(membership, Mapping) or set(membership) != {c.case_id for c in held_out}
            or any(not isinstance(p, MembershipProbe) for p in membership.values())):
        raise ContaminationError("G6_E_RSI_MEMBERSHIP_REQUIRED")
    grams = _grams(training_texts)
    inputs = {canonical_text(c.input) for c in training}
    excluded, identities, overlaps, members = set(), 0, 0, 0
    for index, case in enumerate(held_out):
        identity = (canonical_text(case.input) in inputs
                    or any(code == NOT_HELD_OUT for code, _ in Suite(tuple(training), (case,)).problems()))
        overlap = bool(_grams(scan_texts((case,))) & grams)
        member = membership[case.case_id].flagged
        identities += identity
        overlaps += overlap
        members += member
        if identity or overlap or member:
            excluded.add(index)
    return ScreenReport(len(held_out), tuple(sorted(excluded)), identities, overlaps, members, 0, 0)
