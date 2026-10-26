# SPDX-License-Identifier: Apache-2.0
"""Which components wait for their G6 certificate before G6 lists them as in the library (the vetting protocol,
handoff §4; DECISIONS #37 and #39).

A component in a vetted area declares it in its ``__init__.py``, as a literal read from source without importing the
package::

    __g6_vetting__ = {
        "kinds": ("physical_safety",),                                  # the areas it works in (``KINDS``)
        "register": "g6/embodied/_motion/data/source_register.json",    # its committed source register
        "independent_pass": "g6/embodied/_motion/data/rule_review.json",  # a second check of each rule, or None
        "signed_review": None,                                          # a qualified person's signed record, or None
    }

Paths are relative to the folder holding the top-level package. Listing needs a
current certificate with no refuted predicate, every register rule's cases and
second route passing, and every check having caught a G6-seeded defect. Its
independent pass is read from that certificate. No kind needs a qualified
person's signature to be listed (owner, 2026-10-23, DECISIONS #39): a detached
signed review over the certificate's content digest can still be recorded, and
``summary`` says whether it verifies, but nothing waits for one. A retrofit is
listed while its issuer state stays qualified-draft. Full re-decision and
candidate execution belong to the kit and tool; core reads records, hashes bytes and verifies SSH signatures.
Nothing here raises a result's label.
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DECLARATION = "__g6_vetting__"
KINDS = frozenset({"clinical", "mental_health", "physical_safety", "finance", "political", "legal_regulatory",
                   "scientific_statistical"})
KEYS = ("kinds", "register", "independent_pass", "signed_review")
#: Built, but its certificate is not current yet (the value is kept for the records and replies that carry it).
AWAITING = "built, awaiting review"
#: Listed: its certificate is current, with its checks and an independent reader's pass. It says nothing of a person.
REVIEWED = "reviewed"
#: What every vetted component's skill and its line on the site say (owner, 2026-10-23: the review is dropped, the
#: honesty stays).
NO_QUALIFIED_REVIEW = ("No qualified person has reviewed this component; its rules are checked against their sources "
                       "by G6's certificate and an independent reader.")
_INVALID = object()


def _source(package: str) -> tuple[ast.Module, Path] | None:
    """The package's ``__init__.py`` parsed, and the folder holding its top-level package; ``None`` if unreadable."""
    try:
        spec = importlib.util.find_spec(package)
        if spec is None or not (spec.origin or "").endswith("__init__.py"):
            return None
        origin = Path(spec.origin)
        tree = ast.parse(origin.read_text(encoding="utf-8"))
    except (ImportError, OSError, SyntaxError, ValueError):
        return None
    return tree, origin.parents[len(package.split("."))]


def declared(package: str) -> Any:
    """The package's ``__g6_vetting__`` literal; ``None`` when it declares none (or its source cannot be read)."""
    found = _source(package)
    if found is None:
        return None
    value: Any = None
    for node in found[0].body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, expr = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, expr = node.target.id, node.value
        else:
            continue
        if name == DECLARATION:
            try:
                value = ast.literal_eval(expr)
            except ValueError:
                value = _INVALID
    return value


def _path_problem(package: str, key: str, value: Any, root: Path | None, optional: bool) -> str | None:
    if value is None:
        return None if optional else f"{package}: {DECLARATION}[{key!r}] is missing"
    if not isinstance(value, str) or not value:
        return f"{package}: {DECLARATION}[{key!r}] must be a path or None"
    if root is None:
        return f"{package}: {DECLARATION}[{key!r}] cannot be checked: the package's source cannot be read"
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        return f"{package}: {DECLARATION}[{key!r}] points outside the package's folder: {value!r}"
    if not path.is_file():
        return f"{package}: {DECLARATION}[{key!r}] does not exist: {value!r}"
    return None


def problems(package: str, required: Iterable[str] = ()) -> list[str]:
    """What is wrong with a package's vetting declaration, or ``[]``. ``required`` are the kinds its area needs (from
    the extension tracker); a package that needs some and declares none is a problem."""
    need = sorted(set(required))
    value = declared(package)
    if value is None:
        return [f"{package}: its area needs review ({', '.join(need)}) but it declares no {DECLARATION}"] if need else []
    if value is _INVALID:
        return [f"{package}: {DECLARATION} is not a literal"]
    if not isinstance(value, dict) or set(value) != set(KEYS):
        return [f"{package}: {DECLARATION} must be a dict with exactly the keys {', '.join(KEYS)}"]
    out = []
    kinds = value["kinds"]
    if not isinstance(kinds, tuple) or not kinds or not all(isinstance(k, str) for k in kinds):
        out.append(f"{package}: {DECLARATION}['kinds'] has no kinds (a non-empty tuple of strings)")
        kinds = ()
    out += [f"{package}: {DECLARATION}['kinds'] has an unknown kind: {k!r}" for k in kinds if k not in KINDS]
    missing = [k for k in need if k not in kinds]
    if missing:
        out.append(f"{package}: {DECLARATION}['kinds'] does not declare {', '.join(missing)}, which its area needs")
    found = _source(package)
    root = found[1] if found else None
    for key, optional in (("register", False), ("independent_pass", True), ("signed_review", True)):
        problem = _path_problem(package, key, value[key], root, optional)
        if problem:
            out.append(problem)
    return out


def status(package: str, required: Iterable[str] = (), *, today: dt.date | None = None) -> str | None:
    """A current certificate with complete checks and controls; no signature is needed (DECISIONS #39)."""
    from g6.core import component_certificate as records

    required = tuple(required)
    value = declared(package)
    if value is None and not required:
        return None
    if problems(package, required):
        return AWAITING
    view, body, findings = records.inspect(package, today=today)
    if findings or body is None or not view["current"]:
        return AWAITING
    return REVIEWED


def awaiting_review(package: str, required: Iterable[str] = ()) -> bool:
    """Whether the package must not be listed as in the library yet."""
    return status(package, required) == AWAITING


def certificate(package: str, *, today: dt.date | None = None) -> dict[str, Any] | None:
    """The certificate field exposed by describe; None outside vetted areas."""
    from g6.core import component_certificate as records

    return records.inspect(package, today=today)[0] if declared(package) is not None else None


def summary(package: str) -> dict[str, Any] | None:
    """Review status; the independent pass is read from the current certificate."""
    from g6.core import component_certificate as records

    value = declared(package)
    if value is None:
        return None
    ok = isinstance(value, dict) and set(value) == set(KEYS)
    view, body, findings = records.inspect(package)
    independent = bool(body and not findings and body.get("independent_pass", {}).get("code_matches") is True)
    signed = False
    if ok and view["sha256"]:
        try:
            root, folder, declaration = records.layout(package)
            signed = records.signed_review(root, declaration, certificate_path=folder / "certificate/certificate.json")
        except (OSError, ValueError, TypeError):
            pass
    return {"kinds": list(value["kinds"]) if ok and isinstance(value["kinds"], tuple) else [],
            "status": status(package), "independent_pass": independent,
            "signed_review": signed, "problems": problems(package)}

__all__ = ["AWAITING", "DECLARATION", "KEYS", "KINDS", "NO_QUALIFIED_REVIEW", "REVIEWED", "awaiting_review",
           "certificate", "declared", "problems", "status", "summary"]
