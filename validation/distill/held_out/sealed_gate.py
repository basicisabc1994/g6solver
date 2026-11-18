# SPDX-License-Identifier: Apache-2.0
"""Opaque operator custody. Only protected evaluator entry points may consume it.

There is deliberately no content-returning reader or arbitrary callback API.
These checks protect the application channel; they cannot isolate hostile Python
running as the same OS user. Mission containers must not mount sealed custody.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .case_gate import CaseRecord, Record, SHA256
from .oracle import OraclePolicy, OracleResult, label
from .registration_gate import read_registration, strict_json

__all__ = ["SealedRef", "pin", "verify", "install", "evaluate_sealed"]
MAX_BYTES = 65536
ROOT = Path(__file__).resolve().parents[3]
_EVALUATORS = {
    "g6.distill.acceptance_gate": ROOT / "src/g6/distill/acceptance_gate.py",
    "distill.held_out.study_gate": ROOT / "validation/distill/held_out/study_gate.py",
}


class SealedRef(Record):
    sha256: str = Field(pattern=SHA256)


def _bytes(path: Path) -> bytes:
    # An internal reader may only be called by this module, and never exposes
    # its return through a public function. Reject links in every path component.
    caller = inspect.currentframe().f_back
    if caller.f_globals is not globals():
        raise PermissionError("G6_E_DISTILL_SEALED_ACCESS: no content reader")
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked sealed input refused")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("sealed input exceeds byte cap")
    return data


def pin(path: Path) -> SealedRef:
    """Read opaque bytes once; return only their digest, without parsing."""
    return SealedRef(sha256=hashlib.sha256(_bytes(path)).hexdigest())


def verify(path: Path, reference: SealedRef) -> None:
    """Recheck bytes against an independently kept reference; no content return."""
    if pin(path) != reference:
        raise ValueError("sealed operator digest differs from frozen digest")


def install(source: Path, *, home: Path) -> SealedRef:
    """Keep a supplied opaque file under home/distill/held_out/objects/<digest>.json."""
    data = _bytes(source)
    reference = SealedRef(sha256=hashlib.sha256(data).hexdigest())
    folder = Path(home).absolute() / "distill/held_out/objects"
    for part in (folder, *folder.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked custody refused")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{reference.sha256}.json"
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        verify(path, reference)
    return reference


def _require_evaluator() -> None:
    # Skip this helper and evaluate_sealed, and bind both module identity and
    # filename. A caller-supplied role string cannot grant access.
    frame = inspect.currentframe().f_back.f_back
    name = frame.f_globals.get("__name__")
    module = sys.modules.get(name)
    expected = _EVALUATORS.get(name)
    if (expected is None or module is None or frame.f_globals is not vars(module)
            or Path(frame.f_code.co_filename).resolve() != expected):
        raise PermissionError("G6_E_DISTILL_SEALED_ACCESS: protected acceptance or study path required")


class _Rewrite(Record):
    name: str = Field(min_length=1, max_length=80)
    before: str = Field(min_length=1, max_length=8192)
    after: str = Field(min_length=1, max_length=8192)
    occurrence: int = Field(default=-1, ge=-1, le=4096)

    @model_validator(mode="after")
    def syntax(self):
        for text in (self.before, self.after):
            try:
                tree = ast.parse(text)
            except (ValueError, SyntaxError) as exc:
                raise ValueError("unparseable sealed pattern") from exc
            if len(tree.body) != 1:
                raise ValueError("sealed pattern must contain exactly one statement or expression")
        return self


class _Payload(Record):
    version: Literal[1]
    author_family: Literal["claude", "codex", "non_model"]
    training_family: Literal["claude", "codex"]
    authorship_sha256: str = Field(pattern=SHA256)
    operators: tuple[_Rewrite, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def independent(self):
        if self.author_family == self.training_family:
            raise ValueError("sealed operators must have independent family provenance")
        if len({op.name for op in self.operators}) != len(self.operators):
            raise ValueError("duplicate sealed operator")
        return self


def evaluate_sealed(path: Path, reference: SealedRef, case: CaseRecord, *, policy: OraclePolicy,
                    workspace: Path, registration: tuple[Path, str]) -> tuple[OracleResult, ...]:
    """Evaluator-only consumption; the sole output is oracle decisions and digests.

The manifest binds the base case plus the frozen deterministic operator set.
Operator bytes, transformed tests and facts never leave this evaluator channel.
The future acceptance/study slices own the two named entry modules.
"""
    _require_evaluator()  # must precede even opening a file or examining a case
    frozen = read_registration(registration[0], expected_digest=registration[1])
    if frozen.operator_digests["held_out"] != reference.sha256:
        raise ValueError("sealed operator is not the registered digest")
    case = CaseRecord.model_validate(case.model_dump(mode="json"))
    ref = next((c for c in frozen.cases if c.case_id == case.case_id), None)
    if ref is None or ref.split == "tuning":
        raise ValueError("sealed evaluation requires a registered non-training case")
    if policy != frozen.policy:
        raise ValueError("oracle policy differs from registration")
    # Verify the same bytes that are parsed; no reopen after the digest check.
    data = _bytes(path)
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError("sealed operator digest differs from frozen digest")
    payload = _Payload.model_validate(strict_json(data))
    results = []
    for operator in payload.operators:
        tree = ast.parse(case.before_tests)
        pattern = ast.parse(operator.before).body[0]
        replacement = ast.parse(operator.after).body[0]
        # Expressions match expressions; statements match statements. This is a
        # generic AST substitution interpreter, not a public catalogue of the
        # independent family's weakening transformations.
        if isinstance(pattern, ast.Expr) and isinstance(replacement, ast.Expr):
            pattern, replacement = pattern.value, replacement.value
        if isinstance(pattern, ast.stmt) != isinstance(replacement, ast.stmt):
            raise ValueError("sealed rewrite changes AST category")
        matches = [n for n in ast.walk(tree) if ast.dump(n) == ast.dump(pattern)]
        if not matches or operator.occurrence >= len(matches):
            raise ValueError("sealed operator is inapplicable")
        target = matches[operator.occurrence]
        for parent in ast.walk(tree):
            for field, value in ast.iter_fields(parent):
                if value is target:
                    setattr(parent, field, copy.deepcopy(replacement))
                elif isinstance(value, list):
                    setattr(parent, field, [copy.deepcopy(replacement) if n is target else n for n in value])
        after = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
        generated = CaseRecord.model_validate(case.model_dump(mode="json") | {"after_tests": after})
        results.append(label(generated, policy=policy, workspace=workspace))
    return tuple(results)
