# SPDX-License-Identifier: Apache-2.0
"""Strategy artefacts: a heuristic's identity is the bytes of what is applied (0.3 design, 14.2 "bind the definition").

A strategy is one instruction for how to search or decompose, with the trigger that decides when it applies, the
template it is placed in, the application mapping, the implementation version and its configuration. Its
``content_digest`` is the SHA-256 of all of them in one canonical byte form. A name is not part of it: two names over
the same fields are one strategy, and one name over a changed body, trigger, template, mapping or configuration is
another strategy, which starts unmeasured.

A strategy proposes how to work. It is never evidence, and nothing here measures one: an artefact carries no worth,
strength or confidence. Worth comes only from the paired evaluator's receipts.

``StrategyStore`` keeps artefacts as content-addressed files, written once; ``resolve`` re-hashes the bytes it reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, fields
from pathlib import Path

from g6.learning.addendum import HEADER
from g6.learning.seeds import _SEEDS

#: The application mapping: one instruction, placed by ``g6.learning.addendum`` as the task's hint block, nothing else
#: changed.
MAPPING = "addendum.single/1"
IMPLEMENTATION = "g6.learning.strategy/1"
SCOPES = ("search", "decomposition")
#: The label the hint line carries (``g6.learning``'s failure code for an unranked hint).
LABEL = "unranked_heuristics"
#: The hint block as ``g6.learning.addendum`` renders one instruction, pinned as text: the header, then the line.
TEMPLATE = HEADER + "\n- [" + LABEL + "] {instruction}"
MAX_INSTRUCTION = 2000
#: Seeds that say how to split or read a problem's structure; the others say how to search.
_DECOMPOSITION = frozenset({"decompose_problem", "domain_detector", "math_structure_parser", "reasoning_type_identifier",
                            "code_structure_parser", "code_strategy_identifier", "tool_context_parser",
                            "tool_chain_identifier"})
BYTES = "G6_E_STRATEGY_BYTES"
UNKNOWN = "G6_E_STRATEGY_UNKNOWN"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEY_VALUE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*:.+\Z", re.S)


class StrategyError(ValueError):
    """A strategy artefact or store refusal, with its stable code."""

    def __init__(self, code: str, said: str) -> None:
        super().__init__(f"[{code}] {said}")
        self.code = code


def _trigger_ok(trigger: str) -> bool:
    """``g6.learning.addendum``'s trigger grammar: ``always``, ``key:value``, or ``condition_keys:<JSON list of
    names>``."""
    if trigger == "always":
        return True
    if trigger.startswith("condition_keys:"):
        try:
            keys = json.loads(trigger.removeprefix("condition_keys:"))
        except ValueError:
            return False
        return isinstance(keys, list) and all(isinstance(key, str) and key for key in keys)
    return bool(_KEY_VALUE.match(trigger))


@dataclass(frozen=True)
class StrategyArtefact:
    """One immutable strategy. Every field is identity; there is no name, time, author or measured value."""

    instruction: str                                 # 1 to 2,000 characters
    trigger: str                                     # learning.addendum's trigger grammar; "always" allowed
    scope: str                                       # "search" | "decomposition"
    template: str = TEMPLATE                         # the hint block as rendered, pinned as text
    mapping: str = MAPPING
    implementation: str = IMPLEMENTATION
    config: tuple[tuple[str, str], ...] = ()         # sorted by key; for example (("max_chars", "6000"),)

    def __post_init__(self) -> None:
        for name in ("instruction", "trigger", "scope", "template", "mapping", "implementation"):
            if not isinstance(getattr(self, name), str):
                raise StrategyError(BYTES, f"a strategy's {name} is text")
        if not self.instruction.strip() or len(self.instruction) > MAX_INSTRUCTION:
            raise StrategyError(BYTES, f"a strategy's instruction has 1 to {MAX_INSTRUCTION} characters")
        if "\r" in self.instruction or any(not line.strip() for line in self.instruction.split("\n")):
            # the first blank line of a prompt ends the hint block: an instruction cannot hold one
            raise StrategyError(BYTES, "a strategy's instruction has no blank line and no carriage return")
        if not _trigger_ok(self.trigger):
            raise StrategyError(BYTES, "a strategy's trigger is always, key:value or condition_keys:[names]")
        if self.scope not in SCOPES:
            raise StrategyError(BYTES, f"a strategy's scope is one of {', '.join(SCOPES)}")
        if self.template.count("{instruction}") != 1 or not self.mapping or not self.implementation:
            raise StrategyError(BYTES, "a strategy names its mapping and implementation, and its template places the "
                                       "instruction once")
        pairs = self.config
        if not isinstance(pairs, tuple) or not all(isinstance(pair, tuple) and len(pair) == 2
                                                   and all(isinstance(part, str) for part in pair) for pair in pairs):
            raise StrategyError(BYTES, "a strategy's configuration is a tuple of (key, value) text pairs")
        keys = [key for key, _ in pairs]
        if keys != sorted(set(keys)):
            raise StrategyError(BYTES, "a strategy's configuration is sorted by key, each key once")

    def to_bytes(self) -> bytes:
        """The one byte form of this strategy: canonical JSON of every field, ASCII-escaped, so two texts that differ
        in any character, space or code point differ here. Nothing is normalised."""
        body = {field.name: getattr(self, field.name) for field in fields(self)}
        body["config"] = [list(pair) for pair in self.config]
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_bytes(cls, data: bytes) -> "StrategyArtefact":
        """The artefact whose byte form is exactly ``data``; ``StrategyError`` for anything else."""
        try:
            body = json.loads(data.decode("ascii"))
            found = cls(**{**body, "config": tuple(tuple(pair) for pair in body["config"])})
        except StrategyError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise StrategyError(BYTES, f"not a strategy artefact: {type(exc).__name__}") from None
        if found.to_bytes() != data:
            raise StrategyError(BYTES, "not a strategy artefact in its canonical byte form")
        return found


#: The digest that stands for "no instruction" in a baseline arm. No artefact has it: an artefact's bytes are a JSON
#: object, and this is the digest of a text that is not one.
NO_STRATEGY = hashlib.sha256(b"g6.learning.strategy/1: no strategy").hexdigest()


def seed_artefacts() -> dict[str, StrategyArtefact]:
    """The 16 first-party seed instructions (``g6.learning.seeds``) as strategy artefacts, by seed name: the first
    strategy candidates. Each is unmeasured."""
    return {name: StrategyArtefact(instruction=text, trigger="condition_keys:" + json.dumps(list(keys)),
                                   scope="decomposition" if name in _DECOMPOSITION else "search",
                                   config=(("max_chars", "6000"),))
            for name, keys, text in _SEEDS}


class StrategyStore:
    """Content-addressed strategy files in one folder: ``<digest>.json`` holds exactly the artefact's bytes. A file is
    written once and never replaced."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not _HEX.match(digest):
            raise StrategyError(UNKNOWN, "a strategy is named by the 64-digit SHA-256 of its bytes")
        return self.root / f"{digest}.json"

    def put(self, artefact: StrategyArtefact) -> str:
        """Keep ``artefact`` and answer its digest. Bytes already kept are left as they are."""
        data, digest = artefact.to_bytes(), artefact.content_digest
        path = self._path(digest)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            self.resolve(digest)             # the file kept under this digest must still be these bytes
        return digest

    def resolve(self, digest: str) -> StrategyArtefact:
        """The artefact kept under ``digest``, its bytes hashed again: ``G6_E_STRATEGY_UNKNOWN`` when none is kept,
        ``G6_E_STRATEGY_BYTES`` when the file's bytes are not the ones the digest names."""
        path = self._path(digest)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise StrategyError(UNKNOWN, f"no strategy {digest} is kept here") from None
        if hashlib.sha256(data).hexdigest() != digest:
            raise StrategyError(BYTES, f"the bytes kept as strategy {digest} are not the bytes that digest names")
        return StrategyArtefact.from_bytes(data)

    def digests(self) -> tuple[str, ...]:
        """Every digest a file is kept under, sorted. Nothing is hashed here; ``resolve`` checks a file's bytes."""
        if not self.root.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self.root.glob("*.json") if _HEX.match(path.stem)))


__all__ = ["BYTES", "IMPLEMENTATION", "LABEL", "MAPPING", "MAX_INSTRUCTION", "NO_STRATEGY", "SCOPES", "TEMPLATE",
           "UNKNOWN", "StrategyArtefact", "StrategyError", "StrategyStore", "seed_artefacts"]
