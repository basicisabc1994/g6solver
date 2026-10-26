# SPDX-License-Identifier: Apache-2.0
"""Shared bounded build/check plumbing; this module never registers.

Only trusted Python integrations register builders and check scripts. Request data
cannot replace a checker. The sandbox is a resource bound, not an OS boundary.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from g6.code import _roots
from g6.code._types import CodeRefused, answer, refuse
from g6.core import sandbox

MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 8_000_000
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def plain_path(name: str) -> str:
    """Portable relative POSIX spelling, without Windows aliases or streams."""
    parts = name.split("/")
    if (len(name) > 240 or len(parts) > 12 or any(
        not _NAME.fullmatch(p) or p in {".", ".."} or p.endswith(".")
        or p.split(".")[0].upper() in _RESERVED for p in parts
    )):
        raise CodeRefused("G6_E_BUILD_PATH", "file names must be plain relative paths without aliases")
    return name


def _validate_names(files: Mapping[str, Any]) -> None:
    if not files or len(files) > MAX_FILES:
        raise CodeRefused("G6_E_BUILD_SIZE", f"supply 1 to {MAX_FILES} files")
    names = set()
    for name in files:
        plain_path(name)
        folded = name.casefold()
        if folded in names:
            raise CodeRefused("G6_E_BUILD_PATH", "case-insensitive duplicate file name")
        names.add(folded)
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise CodeRefused("G6_E_BUILD_PATH", "a file is also a parent directory")


@dataclass(frozen=True)
class Provenance:
    kind: Literal["template", "model", "caller"]
    caller: str = ""
    prompt: str = ""
    template: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"template", "model", "caller"}:
            raise ValueError("unknown provenance")
        if self.kind == "model" and (not self.caller.strip() or not self.prompt.strip()):
            raise ValueError("model provenance needs the caller and prompt named")
        if sum(len(v) for v in (self.caller, self.prompt, self.template)) > 100_000:
            raise ValueError("provenance exceeds its bound")


@dataclass(frozen=True)
class Artefact:
    files: Mapping[str, bytes]
    provenance: Provenance

    def __post_init__(self) -> None:
        _validate_names(self.files)
        total, copied = 0, {}
        for name, content in self.files.items():
            if not isinstance(content, bytes):
                raise CodeRefused("G6_E_BUILD_INPUT", "file contents must be bytes")
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise CodeRefused("G6_E_BUILD_SIZE", "artefact exceeds file or total byte bound")
            copied[name] = content
        object.__setattr__(self, "files", MappingProxyType(copied))

    @classmethod
    def from_text(cls, files: Mapping[str, str]) -> Artefact:
        # Check every name/count before allocating any encoded payload, even for dry runs.
        _validate_names(files)
        encoded, total = {}, 0
        for name, content in files.items():
            if not isinstance(content, str):
                raise CodeRefused("G6_E_BUILD_INPUT", "file contents must be text")
            data = content.encode("utf-8")
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise CodeRefused("G6_E_BUILD_SIZE", "artefact exceeds file or total byte bound")
            encoded[name] = data
        # Provenance submitted through a block is a caller claim, never model/check evidence.
        return cls(encoded, Provenance("caller"))

    def describe(self) -> dict[str, Any]:
        return {"files": {p: b.decode("utf-8") for p, b in self.files.items()},
                "provenance": asdict(self.provenance), "bytes": sum(map(len, self.files.values()))}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool | None
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    code: str = "G6_E_BUILD_CHECK_FAILED"

    def __post_init__(self) -> None:
        if not self.name or (self.passed is not None and type(self.passed) is not bool):
            raise ValueError("check needs a name and a boolean result, or None")
        if self.passed is None and not self.reason:
            raise ValueError("a check not run needs a reason")


@dataclass(frozen=True)
class Check:
    name: str
    source: str
    code: str = "G6_E_BUILD_CHECK_FAILED"
    timeout_s: float = 30.0
    mem_mb: int = 1024
    oracle_expected: tuple[Any, ...] | None = None


class BuildRegistry:
    """An instance-local extension point for trusted Python integrations."""
    def __init__(self) -> None:
        self.builders: dict[str, Callable[..., Artefact]] = {}
        self.checkers: dict[str, Callable[..., list[Check]]] = {}

    def register(self, kind: str, builder: Callable[..., Artefact],
                 checker: Callable[..., list[Check]]) -> None:
        if kind in self.builders:
            raise ValueError(f"backend already registered: {kind}")
        self.builders[kind], self.checkers[kind] = builder, checker

    def build(self, kind: str, spec: Any, **kwargs: Any) -> Artefact:
        if kind not in self.builders:
            raise CodeRefused("G6_E_BUILD_BACKEND", f"unknown backend: {kind}")
        return self.builders[kind](spec, **kwargs)

    def checks(self, kind: str, **kwargs: Any) -> list[Check]:
        if kind not in self.checkers:
            raise CodeRefused("G6_E_BUILD_BACKEND", f"unknown backend: {kind}")
        return self.checkers[kind](**kwargs)


# This trusted prelude runs only inside core.sandbox. No extractall, links or absolute paths.
_PRELUDE = """
import json
import os
import pathlib
import tempfile
import zipfile
from g6.code._types import CHECKER_NOT_INSTALLED, CodeRefused as CheckerUnavailable
# The token is in memory before project imports; remove its only on-disk source.
pathlib.Path(__file__).unlink()
initial_directory = pathlib.Path.cwd()
report = pathlib.Path.cwd() / "_g6_check.json"
archive = pathlib.Path.cwd() / "artefact.zip"
class NotRun(Exception):
    pass
evidence = {}
"""


def run_checks(artefact: Artefact, checks: list[Check]) -> list[CheckResult]:
    """Pack once; each check gets a fresh sandbox and its own extracted directory.

    Caller code is never imported here. Optional checker dependencies are detected
    inside the child interpreter that actually uses them.
    """
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in artefact.files.items():
            archive.writestr(name, content)
    results = []
    for check in checks:
        token = secrets.token_hex(32)
        source = "\n".join("            " + line for line in check.source.splitlines())
        script = "_g6_token = " + repr(token) + "\n" + _PRELUDE + """
try:
    with tempfile.TemporaryDirectory(prefix="g6_code_", dir=".") as directory:
        root = pathlib.Path(directory).resolve()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = root / member.filename
                if not target.resolve().is_relative_to(root) or member.is_dir():
                    raise ValueError("invalid archive member")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(member))
        try:
""" + source + """
        finally:
            os.chdir(initial_directory)
    result = {"passed": True, "evidence": evidence, "reason": "", "code": ""}
except NotRun as exc:
    result = {"passed": None, "evidence": evidence, "reason": str(exc), "code": ""}
except CheckerUnavailable as exc:
    result = {"passed": False, "evidence": evidence, "reason": exc.reason, "code": exc.code}
except BaseException as exc:
    result = {"passed": False, "evidence": evidence, "reason": type(exc).__name__ + ": " + str(exc)[:1000], "code": ""}
report.write_text(json.dumps({**result, "token": _g6_token, "completed": True}), encoding="utf-8")
"""
        try:
            ran = sandbox.run_script(script, {"artefact.zip": packed.getvalue()}, timeout_s=check.timeout_s,
                                     mem_mb=check.mem_mb, max_output_bytes=128_000)
            evidence = {"sandbox_status": ran.status, "limits": ran.limits,
                        "stdout": ran.stdout[-4000:], "stderr": ran.stderr[-4000:]}
            if ran.status != "ok" or "_g6_check.json" not in ran.outputs:
                results.append(CheckResult(check.name, False, evidence, "sandbox failed or returned no check report",
                                           check.code))
                continue
            data = json.loads(ran.outputs["_g6_check.json"])
            if (not isinstance(data, dict)
                    or set(data) != {"passed", "evidence", "reason", "code", "token", "completed"}
                    or data["token"] != token or data["completed"] is not True
                    or not isinstance(data["evidence"], dict) or not isinstance(data["reason"], str)
                    or (data["code"] != "" and not re.fullmatch(r"G6_E_[A-Z0-9_]+", data["code"]))):
                raise ValueError("malformed check report")
            if check.oracle_expected is not None:
                actual = data["evidence"].get("actual")
                observed = json.dumps(actual, sort_keys=True, allow_nan=False)
                expected = json.dumps(list(check.oracle_expected), sort_keys=True, allow_nan=False)
                if data["passed"] is not True or not isinstance(actual, list) or observed != expected:
                    data["passed"] = False
                    data["reason"] = "caller examples failed or no values were returned"
                data["evidence"]["oracle_comparison"] = "G6 parent compared returned values to caller expectations"
            results.append(CheckResult(check.name, data["passed"], {**data["evidence"], **evidence},
                                       data["reason"], data.get("code") or check.code))
        except Exception as exc:  # a broken checker cannot silently pass
            results.append(CheckResult(check.name, False, {}, f"checker error: {type(exc).__name__}", check.code))
    return results


def checked_reply(component: str, op: str, checks: list[CheckResult], **fields: Any) -> dict[str, Any]:
    ran = [asdict(c) for c in checks if c.passed is not None]
    skipped = [asdict(c) for c in checks if c.passed is None]
    failed = next((c for c in checks if c.passed is False), None)
    message = ("Checks run: " + (", ".join(c["name"] for c in ran) or "none")
               + ". Checks not run: " + (", ".join(c["name"] + " (" + c["reason"] + ")" for c in skipped) or "none") + ".")
    kwargs = dict(action="Review the evidence and supply independent oracles for the remaining blind spots.",
                  evidence=[{"kind": "check", **asdict(c)} for c in checks],
                  checks_run=ran, checks_not_run=skipped, **fields)
    if failed:
        return refuse(component, op, failed.code, failed.name + " failed: " + failed.reason + ". " + message, **kwargs)
    return answer(component, op, "G6_E_BUILD_DRAFT", message, **kwargs)



def write_files(artefact: Artefact, dest: str, roots: _roots.Roots, *, dry_run: bool,
                component: str) -> dict[str, Any]:
    if dry_run:
        return {"written": [], "dry_run": True}
    target = _roots.destination(dest, roots)
    written = []
    try:
        target.mkdir(exist_ok=True)
        for name, data in artefact.files.items():
            output = target / name
            # Recheck every parent and file immediately before writing; refuse links.
            for parent in [target, *output.relative_to(target).parents]:
                candidate = parent if parent.is_absolute() else target / parent
                if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                    raise CodeRefused("G6_E_BUILD_PATH", "destination contains a link")
            if not _roots.under(output.resolve(), (target.resolve(),)):
                raise CodeRefused("G6_E_BUILD_PATH", "destination leaves its root")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as handle:
                handle.write(data)
            if output.read_bytes() != data:
                raise OSError("written bytes differ")
            written.append(name)
    except OSError as exc:
        raise CodeRefused("G6_E_BUILD_WRITE", f"write failed: {type(exc).__name__}", written=written) from None
    return {"written": written, "dry_run": False}
