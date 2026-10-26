# SPDX-License-Identifier: Apache-2.0

"""Global failure-mode registry for harness contracts.

Besides the seeded self-programming and context codes, each extension family and the core harness keep their codes in
``data/failure_modes/<family>.json`` (FW-03): each code with a plain title, and the packages planned to guard it. Every
component declares, in its package ``__init__.py``, the codes it guards against (``__g6_mitigates__``) and the ones its
own use can cause (``__g6_introduces__``), as literal tuples, so they are read without importing it.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NewType

FailureModeCode = NewType("FailureModeCode", str)

#: A code: a lower-case letter, then lower-case letters, digits and underscores (every seeded code already is one).
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
#: One file per family, and one for the core harness: its codes and the packages planned to guard them.
DATA = Path(__file__).resolve().parent / "data" / "failure_modes"
#: The two declarations every component makes in its package ``__init__.py``.
DECLARATIONS = ("__g6_mitigates__", "__g6_introduces__")
_ENTRY_KEYS = frozenset({"code", "title", "description", "severity", "requires_human_review", "aliases", "seed"})


@dataclass(frozen=True)
class FailureModeRecord:
    code: FailureModeCode
    description: str = ""
    signals: tuple[str, ...] = ()
    detection: str = ""
    prevention: str = ""
    recovery: str = ""
    severity: str = "medium"
    requires_human_review: bool = False
    source: str = "seed"
    aliases: tuple[str, ...] = ()
    title: str = ""   # plain words for the page and for ``g6_find``; empty for a seeded code no file has titled


_SELF_PROGRAMMING_CODES = (
    "visual_check_failed",
    "audio_check_failed",
    "design_quality_failed",
    "broke_existing_functionality",
    "dependency_breakage",
    "attempted_too_much_in_one_patch",
    "context_loss",
    "excessive_token_use",
    "hallucinated_failure",
    "hallucinated_success",
    "interagent_coordination_failure",
    "end_to_end_failure",
    "cannot_self_repair",
    "safety_policy_failure",
    "risk_management_failure",
    "long_context_degradation",
    "test_suite_missing_or_weak",
    "docs_not_updated",
    "source_visualisation_stale",
    "license_or_copyright_risk",
    "ungrounded_exemplar_use",
    "unsafe_autonomous_action",
    "rollback_missing",
    "hidden_state_or_workspace_conflict",
)

_CONTEXT_FAILURE_MODES = (
    "authority_conflict",
    "stale_context_used",
    "repository_dumping",
    "missing_user_constraint",
    "context_budget_overflow",
    "irrelevant_exemplar",
    "ungrounded_exemplar_use",
    "prompt_altitude_mismatch",
    "component_metadata_missing",
    "stale_test_result_used",
    "stale_file_handle",
    "compaction_lost_decision",
    "compaction_added_unsourced_claim",
    "subagent_context_leakage",
    "subagent_result_overmerge",
    "provenance_missing",
    "tool_context_missing",
    "output_contract_missing",
    "memory_poisoning",
    "overbroad_retrieval",
    "under_retrieval",
    "long_context_degradation",
    "context_loss",
)

_CONTEXT_ALIASES = {
    "ungrounded_exemplar_use": ("irrelevant_exemplar_unsourced",),
    "long_context_degradation": ("context_window_degradation",),
    "context_loss": ("missing_context",),
}


class FailureModeRegistry:
    """Singleton-style registry keyed by runtime-safe string codes."""

    _records: dict[str, FailureModeRecord] = {}
    _planned: dict[str, tuple[str, ...]] = {}
    _seeded = False

    @classmethod
    def seed_defaults(cls) -> None:
        if cls._seeded:
            return
        for code in _SELF_PROGRAMMING_CODES:
            cls.register(
                FailureModeRecord(
                    code=FailureModeCode(code),
                    description=code.replace("_", " "),
                    signals=(code,),
                    detection="declared_failure_mode_signal",
                    prevention="apply harness-specific guardrails and verification",
                    recovery="escalate, repair, rerun targeted verification",
                    severity="high" if code in {"unsafe_autonomous_action", "license_or_copyright_risk"} else "medium",
                    requires_human_review=code in {"unsafe_autonomous_action", "license_or_copyright_risk"},
                    source="SELF_PROGRAMMING.md",
                )
            )
        for code in _CONTEXT_FAILURE_MODES:
            cls.register(
                FailureModeRecord(
                    code=FailureModeCode(code),
                    description=code.replace("_", " "),
                    signals=(code, "context_engineering"),
                    detection="context_packet_scorecard_and_eval_suite",
                    prevention="recommended policy (guidance, not an enforced control): authority hierarchy, provenance, freshness, budget, and bounded retrieval",
                    recovery="rebuild context packet from authoritative sources, compact, and rerun affected verification",
                    severity="high" if code in {
                        "authority_conflict",
                        "missing_user_constraint",
                        "memory_poisoning",
                        "subagent_context_leakage",
                    } else "medium",
                    requires_human_review=code in {"memory_poisoning", "authority_conflict"},
                    source="CONTEXT.md",
                    aliases=_CONTEXT_ALIASES.get(code, ()),
                )
            )
        cls._seeded = True
        cls._load_families()

    @classmethod
    def _load_families(cls) -> None:
        """Register each family file's codes and planned guards. A malformed entry, a code registered twice or a
        package planned in two files raises: the files ship with the library, and a test reads every one."""
        for path in sorted(DATA.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            source = f"failure_modes/{path.name}"
            for entry in data["codes"]:
                code = entry.get("code", "")
                if not CODE.fullmatch(code) or not entry.get("title") or set(entry) - _ENTRY_KEYS:
                    raise ValueError(f"{source}: malformed failure-mode entry {code!r}")
                existing = cls._records.get(code)
                if entry.get("seed"):
                    if existing is None or existing.source.startswith("failure_modes/") or existing.title:
                        raise ValueError(f"{source}: {code} is not a seeded code, or another file titled it")
                    record = replace(existing, title=entry["title"],
                                     aliases=existing.aliases + tuple(entry.get("aliases", ())))
                elif existing is not None:
                    raise ValueError(f"{source}: {code} is already registered by {existing.source}")
                else:
                    record = FailureModeRecord(
                        code=FailureModeCode(code), title=entry["title"],
                        description=entry.get("description", entry["title"]),
                        severity=entry.get("severity", "medium"),
                        requires_human_review=bool(entry.get("requires_human_review", False)),
                        source=source, aliases=tuple(entry.get("aliases", ())))
                taken = [a for a in record.aliases if a in cls._records and str(cls._records[a].code) != code]
                if taken:
                    raise ValueError(f"{source}: alias {taken[0]} of {code} is another code")
                cls.register(record)
            for package, codes in data["planned"].items():
                if package in cls._planned:
                    raise ValueError(f"{source}: {package} is planned in two files")
                cls._planned[package] = tuple(codes)

    @classmethod
    def register(cls, record: FailureModeRecord) -> None:
        code = str(record.code).strip()
        if not code:
            raise ValueError("failure mode code is required")
        cls._records[code] = record
        for alias in record.aliases:
            if alias.strip():
                cls._records[alias.strip()] = record

    @classmethod
    def get(cls, code: str | FailureModeCode) -> FailureModeRecord:
        cls.seed_defaults()
        key = str(code)
        if key not in cls._records:
            raise KeyError(f"unknown failure mode code: {key}")
        return cls._records[key]

    @classmethod
    def list_all(cls) -> list[FailureModeRecord]:
        cls.seed_defaults()
        seen: set[str] = set()
        records: list[FailureModeRecord] = []
        for record in cls._records.values():
            canonical = str(record.code)
            if canonical in seen:
                continue
            seen.add(canonical)
            records.append(record)
        return sorted(records, key=lambda item: str(item.code))

    @classmethod
    def is_registered(cls, code: str | FailureModeCode) -> bool:
        cls.seed_defaults()
        return str(code) in cls._records

    @classmethod
    def title(cls, code: str | FailureModeCode) -> str:
        """A code's plain title, or its description when no family file has titled it."""
        record = cls.get(code)
        return record.title or record.description

    @classmethod
    def planned(cls) -> dict[str, tuple[str, ...]]:
        """Each package planned to guard failure modes -> those codes. Planned is not guarding: a package guards a mode
        only once it is a registered component that declares the mode itself."""
        cls.seed_defaults()
        return dict(cls._planned)


def declared(package: str) -> dict[str, tuple[str, ...]]:
    """A package's failure-mode declarations, read from its ``__init__.py`` source without importing it: each of
    ``DECLARATIONS`` assigned a literal tuple of strings at module level. ``{}`` if the source cannot be read."""
    try:
        spec = importlib.util.find_spec(package)
        if spec is None or not (spec.origin or "").endswith("__init__.py"):
            return {}
        with open(spec.origin, encoding="utf-8") as f:
            return declarations_in(f.read())
    except (ImportError, OSError, ValueError):
        return {}


def declarations_in(source: str) -> dict[str, tuple[str, ...]]:
    """The failure-mode declarations in a package ``__init__.py``'s source text, which is parsed and never run (the
    rule :func:`declared` applies, for a package that is read from a folder rather than installed). ``{}`` if the text
    does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value = node.target.id, node.value
        else:
            continue
        if name not in DECLARATIONS:
            continue
        try:
            codes = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            found.pop(name, None)
            continue
        if isinstance(codes, tuple) and all(isinstance(c, str) for c in codes):
            found[name] = codes
    return found


def declaration_problems(package: str) -> list[str]:
    """What is wrong with a component's failure-mode declarations (FW-03), or ``[]``: both must be literal tuples in
    its ``__init__.py``; it guards against at least one mode; every code is registered under its own name, not an
    alias; and no code is both guarded against and caused."""
    return problems_in(declared(package), package)


def problems_in(found: dict[str, tuple[str, ...]], package: str) -> list[str]:
    """What :func:`declaration_problems` finds wrong with ``found`` (the declarations as :func:`declarations_in`
    reads them), each problem naming ``package``."""
    problems = [f"{package}: no literal {name} in its __init__.py" for name in DECLARATIONS if name not in found]
    mitigates, introduces = found.get(DECLARATIONS[0], ()), found.get(DECLARATIONS[1], ())
    if DECLARATIONS[0] in found and not mitigates:
        problems.append(f"{package}: mitigates no failure mode")
    for code in dict.fromkeys((*mitigates, *introduces)):
        if not FailureModeRegistry.is_registered(code):
            problems.append(f"{package}: {code!r} is not a registered failure mode")
        elif str(FailureModeRegistry.get(code).code) != code:
            problems.append(f"{package}: {code!r} is an alias of {str(FailureModeRegistry.get(code).code)!r}")
    problems += [f"{package}: {code!r} is both mitigated and introduced" for code in sorted(set(mitigates) & set(introduces))]
    return problems


FailureModeRegistry.seed_defaults()


__all__ = ["CODE", "DATA", "DECLARATIONS", "FailureModeCode", "FailureModeRecord", "FailureModeRegistry",
           "declaration_problems", "declarations_in", "declared", "problems_in"]
