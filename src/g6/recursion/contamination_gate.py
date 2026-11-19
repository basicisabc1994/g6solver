# SPDX-License-Identifier: Apache-2.0
"""Evaluator-only detectors for accidental copies under a fixed set of text views.

ACCESS CONTROL is the binding barrier: the learner and proposer never hold the
evaluator capability. These screens and canaries are DETECTORS, covering copies
under NFKC/casefold/escape fixed points with default-ignorables removed or spaced,
plus a whitespace-free view for short identity and canaries. The serialiser scope
is JSON (json.dumps with either ensure_ascii setting), Python literals (repr,
ascii and str of containers), URL percent-encoding (urllib.parse.quote and
quote_plus), and HTML/XML entities (html.escape, numeric and named character
references), including compositions of these families. Base64, compression,
ciphers and custom encodings are outside this scope as deliberate encoding.
Adjacent fragments carry both empty and space boundaries in a token graph;
matching tracks at most 13 tokens, without enumerating whole-case alternatives
(linear in fields, so ordinary cases with many meta fields are screened, not refused).
Fixed views are computed per field and on the two whole-case joins (all empty,
all space). STATED LIMIT: an escape, percent or combining sequence cut by a field
boundary is recomposed only when every boundary of the case is of one kind; under
mixed boundaries it is not, since such a copy is constructed rather than accidental.
Traversal is bounded to depth 32, 16384 nodes and 1048576 raw string characters;
crossing a bound refuses the screen, never skips it.
"""
from __future__ import annotations

import html
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import mean, stdev

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


@dataclass(frozen=True, repr=False)
class ScreenReport:
    """Evaluator-private findings; public serialization carries no slice outcomes."""

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
        return {}


@dataclass
class _Traversal:
    nodes: int = 16384
    characters: int = 1048576
    limited: bool = False
    invalid: bool = False


def _walk(value, state, *, depth=0, is_key=False):
    """One leaf traversal for validation and the durable incident prepass."""
    state.nodes -= 1
    if depth > 32 or state.nodes < 0:
        state.limited = True
        return
    if isinstance(value, str):
        remaining = max(0, state.characters)
        state.characters -= len(value)
        state.limited |= state.characters < 0
        yield value[:remaining], is_key
    elif isinstance(value, CanaryProbe):
        rates = (getattr(value, "completion_rate", None), getattr(value, "chance_rate", None))
        if all(_number(v) and v <= 1 for v in rates):
            yield CanaryProbe(*rates), is_key
        else:
            state.invalid = True
    elif isinstance(value, SuiteCase):
        yield from _walk((value.case_id, value.input, value.expected, value.meta), state,
                         depth=depth + 1, is_key=is_key)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if state.nodes < 0:
                break
            yield from _walk(key, state, depth=depth + 1, is_key=True)
            yield from _walk(item, state, depth=depth + 1, is_key=is_key)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            if state.nodes < 0:
                break
            yield from _walk(item, state, depth=depth + 1, is_key=is_key)
    elif value is not None and type(value) not in (bool, int, float):
        state.invalid = True


def _strings(value: object, *, strict=True, keys=True):
    state = _Traversal()
    for leaf, is_key in _walk(value, state):
        if isinstance(leaf, str):
            if keys or not is_key:
                yield leaf
        else:
            state.invalid = True
    if state.limited:
        raise ContaminationError("G6_E_RSI_SCREEN_LIMIT")
    if strict and state.invalid:
        raise ContaminationError("G6_E_RSI_SCREEN_INVALID")


def case_strings(case: SuiteCase) -> tuple[str, ...]:
    return tuple(_strings(case))


# Unicode Default_Ignorable_Code_Point, including its reserved ranges.
_IGNORABLE = re.compile(
    "[\u00ad\u034f\u061c\u115f-\u1160\u17b4-\u17b5\u180b-\u180f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f"
    "\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a\U000e0000-\U000e0fff]"
)


_LITERAL_ESCAPE = re.compile(
    r"\\[\\'\"abfnrtv/]|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}"
    r"|\\[0-7]{1,3}|\\N\{[^{}]*\}"
)
_PERCENT_RUN = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _literal_text(text: str) -> tuple[str, bool]:
    """Decode each literal token independently, retaining invalid sequences."""
    def decode(match):
        token = match.group()
        if token == r"\/":
            return "/"
        if token[1] in "01234567":
            return chr(int(token[1:], 8))
        try:
            return token.encode("ascii").decode("unicode_escape")
        except UnicodeError:
            return token

    decoded = _LITERAL_ESCAPE.sub(decode, text)
    # JSON represents astral scalars as adjacent UTF-16 surrogates.
    decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")
    return decoded, "\\" not in decoded


def _percent_text(text: str) -> str:
    """Decode UTF-8 segments in each byte run, retaining the original invalid bytes."""
    def decode(match):
        token = match.group()
        decoded = bytes.fromhex(token.replace("%", " ")).decode("utf-8", "surrogateescape")
        parts, offset = [], 0
        for char in decoded:
            if 0xDC80 <= ord(char) <= 0xDCFF:
                parts.append(token[offset:offset + 3])
                offset += 3
            else:
                parts.append(char)
                offset += 3 * len(char.encode("utf-8"))
        return "".join(parts)

    return _PERCENT_RUN.sub(decode, text.replace("+", " "))


def _canonical(text: str, replacement: str, *, compact=False) -> str:
    while True:
        normalized = unicodedata.normalize("NFKC", text)
        decoded, literal_valid = _literal_text(normalized)
        decoded = _percent_text(decoded)
        decoded = html.unescape(decoded)
        result = ("" if compact else " ").join(_IGNORABLE.sub(replacement, decoded).split())
        # Finish decoding and boundary changes before folding case-sensitive escapes.
        if result == text:
            if literal_valid:
                result = result.casefold()
            else:
                # Keep undecoded escape markers intact while folding surrounding text.
                head, *tails = result.split("\\")
                result = "\\".join((head.casefold(), *(s[:1] + s[1:].casefold() for s in tails)))
        if result == text:
            return result
        text = result


def canonical_views(text: str) -> tuple[str, str, str]:
    """The fixed detector views; the compact view is for identity and canaries."""
    removed, spaced = _canonical(text, ""), _canonical(text, " ")
    compact = _canonical(removed, "", compact=True)
    assert _canonical(removed, "") == removed and _canonical(spaced, " ") == spaced
    assert _canonical(compact, "", compact=True) == compact and not any(c.isspace() for c in compact)
    return removed, spaced, compact


def canonical_text(text: str) -> str:
    """The default-ignorable removal view, retained for callers of repair 1."""
    return canonical_views(text)[0]


def _fragment_texts(fragments):
    # Each field, then the two whole-case joins; linear in the number of fields.
    for fragment in fragments:
        yield from canonical_views(fragment)
    for separator in ("", " "):
        yield from canonical_views(separator.join(fragments))


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
        fields = tuple(_strings(case, strict=strict))
        values = tuple(_strings(case, strict=strict, keys=False))
        for fragments in (fields, values):
            found.extend(_fragment_texts(fragments))
    for output in model_outputs:
        if isinstance(output, str):
            found.extend(canonical_views(output))
        elif strict:
            raise ContaminationError("G6_E_RSI_SCREEN_INVALID")
    return tuple(found)


def incident_scan(*sources):
    """Read raw supported containers before validation; return partial evidence on limits.

    Each source is bounded to depth 32, 16384 nodes and 1 MiB of strings. Top-level
    cases and outputs remain separate. Unsupported leaves cannot hide siblings.
    """
    texts, probes = [], []
    limited = False
    for source in sources:
        state = _Traversal()
        groups = (source if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray))
                  else (source,))
        for group in groups:
            if state.nodes < 0:
                break
            leaves = tuple(_walk(group, state))
            probes.extend(leaf for leaf, _ in leaves if isinstance(leaf, CanaryProbe))
            fragments = tuple((leaf, key) for leaf, key in leaves if isinstance(leaf, str))
            for fields in (tuple(s for s, _ in fragments), tuple(s for s, key in fragments if not key)):
                try:
                    texts.extend(_fragment_texts(fields))
                except ContaminationError:
                    limited = True
        limited |= state.limited
    return tuple(texts), tuple(probes), limited


def canary_matches(canaries: Sequence[str], texts: Sequence[str]) -> tuple[int, ...]:
    """Return marker positions only; text comes from the shared canonical scanner."""
    compact = tuple(canonical_views(text)[2] for text in texts)
    return tuple(i for i, marker in enumerate(canaries)
                 if any(canonical_views(marker)[2] in text for text in compact))


def _token_graph(fragments):
    atoms, joins = [], set()
    for fragment in fragments:
        tokens = re.findall(r"\w+|[^\w\s]", fragment)
        if atoms and tokens and re.fullmatch(r"\w+", atoms[-1]) and re.fullmatch(r"\w+", tokens[0]):
            joins.add(len(atoms))
        atoms.extend(tokens)
    graph = []
    for start, atom in enumerate(atoms):
        edges = [(start + 1, atom)]
        end = start + 1
        while end in joins:
            atom += atoms[end]
            end += 1
            edges.append((end, atom))
        graph.append(edges)
    return graph


def _graphs(case):
    for keys in (True, False):
        fields = tuple(_strings(case, keys=keys))
        for view in (0, 1):
            yield _token_graph([canonical_views(s)[view] for s in fields])
            for separator in ("", " "):
                yield _token_graph([canonical_views(separator.join(fields))[view]])


def _shared_13(left, right):
    """Longest common token path on two DAGs; cap state at the detector's 13 tokens.

    A merged word edge spans consecutive fragment atoms. There are at most
    quadratically many such edges, even when every fragment is one character.
    """
    positions = {}
    for j, edges in enumerate(right):
        for end, token in edges:
            positions.setdefault(token, []).append((j, end))
    lengths = {}
    for i, edges in enumerate(left):
        for end, token in edges:
            for j, other_end in positions.get(token, ()):
                count = lengths.get((i, j), 0) + 1
                if count >= 13:
                    return True
                pair = (end, other_end)
                lengths[pair] = max(lengths.get(pair, 0), count)
    return False


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
    graphs = [graph for case in training for graph in _graphs(case)]
    for output in model_outputs:
        graphs.extend(_graphs(SuiteCase("", output, "")))
    inputs = {view for c in training for view in canonical_views(c.input)}
    excluded, identities, overlaps, members = set(), 0, 0, 0
    for index, case in enumerate(held_out):
        identity = (bool(set(canonical_views(case.input)) & inputs)
                    or any(code == NOT_HELD_OUT for code, _ in Suite(tuple(training), (case,)).problems()))
        overlap = any(_shared_13(held_graph, train_graph) for held_graph in _graphs(case) for train_graph in graphs)
        member = membership[case.case_id].flagged
        identities += identity
        overlaps += overlap
        members += member
        if identity or overlap or member:
            excluded.add(index)
    return ScreenReport(len(held_out), tuple(sorted(excluded)), identities, overlaps, members, 0, 0)
