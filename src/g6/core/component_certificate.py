# SPDX-License-Identifier: Apache-2.0
"""Component certificate records. No component imports or candidate execution.

The execution adapter lives in workforce.component_certificate. This module reads
committed records for listing and result attribution; the kit re-decides and reruns
them. Digests detect changes, not malicious replacement of all repository records.
"""
from __future__ import annotations

import ast
import calendar
import copy
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from g6.core import component_process, outside

SSH_KEYGEN = outside.OutsideProgram(
    name="OpenSSH ssh-keygen", purpose="verify a qualified reviewer's certificate signature",
    install="Install the OpenSSH client tools and put ssh-keygen on PATH.",
    licence="BSD", command="ssh-keygen", version_args=("-Y", "find-principals"))
__g6_outside__ = (SSH_KEYGEN,)
ALLOWED_SIGNERS = Path(__file__).parent / "data/reviewers/allowed_signers"
HARNESS_ROOT = Path(__file__).parents[2]
HARNESS_PATHS = ("g6/core/component_certificate.py", "g6/workforce/component_certificate.py",
                 "g6/workforce/_certificate_runner.py", "g6/workforce/_certificate_formal.py")

SCHEMA = "g6.component_certificate.v2"
#: An artifact file holds CRLF line endings: refused before any hash is printed, compared or issued (``lf_only``).
CRLF = "G6_E_CERTIFICATE_CRLF"
OUTPUTS = ("certificate.json", "CERTIFICATE.md", "certificate.json.sig")
FIELDS = ("id", "issuing_body", "title", "edition", "published", "jurisdiction", "url",
          "licence", "retrieved", "sha256", "clause", "review_months")
#: The sources whose full bytes the issuing tool hashed at issue (id and hash). Without the bytes, a later check
#: recomputes only the clause hash and trusts this unsigned record.
BYTES_AT_ISSUE = "source_bytes_required_at_issuance"
#: A rule's tier: what its certificate checks. computes: a register rule a result's number rests on (a printed worked
#: example, a second route, a falsifier); property: a register rule no printed value G6's functions can be run on checks
#: (a second route and invariants, and the reader's attestation of why); guard: a rule of G6's own that can only
#: refuse, escalate, lower a label or add a note (a firing, a holding and a recall case, a seeded removal of the guard,
#: seeded boundary changes, and the reader's word that its code has only that effect).
TIERS = ("computes", "property", "guard")
GUARD_EFFECTS = ("refuse", "escalate", "lower_label", "note")
#: The checks each tier's predicate rests on (a formal proof adds "formal" to a computes rule; a property rule with
#: known cases, never printed ones, adds "known": :func:`check_kinds`).
CHECK_KINDS = {"computes": ("known", "second_route"), "property": ("second_route",), "guard": ("known",)}


def check_kinds(tier: str, cased: bool) -> tuple[str, ...]:
    """The checks a rule of ``tier`` rests on; ``cased``: whether cases.json holds known cases for it."""
    return ("known", "second_route") if tier == "property" and cased else CHECK_KINDS[tier]
#: A guard's citation when no source sets its threshold: G6 chose it, and the certificate says so.
OWN_THRESHOLD = "g6"
#: The reader's finding for a clause that supports a rule only by its context: a named gap, never a pass. The issuer
#: records it as an undecided gate, ``context_only:<rule>``, which holds the state at qualified-draft at most.
CONTEXT_ONLY = "by context only"
CONTEXT_GATE = "context_only"
#: A register rule that applies a threshold its source does not set (``Rule(own_choice=True)``): G6's own choice,
#: recorded as the undecided gate ``own_choice:<rule>``, which holds the state at qualified-draft at most.
CHOICE_GATE = "own_choice"
#: A guard with no threshold G6 can move (no comparison or number in its function: a phrase or category check), so only
#: its caught removal shows its cases test it: the undecided gate ``no_threshold:<rule>``, qualified-draft at most.
THRESHOLD_GATE = "no_threshold"
_IMPLEMENTS = re.compile(r"(g6(?:/[A-Za-z_][A-Za-z0-9_]*)+\.py):([A-Za-z_][A-Za-z0-9_]*)")


def harness_files() -> dict[str, bytes]:
    """Read the frozen harness bytes without importing evidence or workforce."""
    return {name: inside(HARNESS_ROOT, name).read_bytes() for name in HARNESS_PATHS}


def canonical(value: Any) -> bytes:
    """Sorted UTF-8 JSON, LF, no binary floating-point numbers in a published record."""
    def check(item):
        if isinstance(item, float):
            raise ValueError("record numbers need declared decimal precision")
        if isinstance(item, dict):
            for v in item.values():
                check(v)
        elif isinstance(item, (tuple, list)):
            for v in item:
                check(v)
    check(value)
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: Mapping[str, Any]) -> str:
    """Same unkeyed canonical digest convention as the issuer."""
    return sha(json.dumps({k: v for k, v in value.items() if k != "digest"},
                          sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def content(body: Mapping[str, Any]) -> dict:
    """The certificate without what a reissue after new history changes (an export, CERTIFICATES.md): the history
    positions, and the two digests computed over them. A positive control is kept as whether it ran. This is what a
    qualified reviewer signs, so a reissue that only renumbers positions keeps the signature."""
    view = copy.deepcopy(dict(body))
    view.pop("positions", None)
    view.pop("digest", None)
    issued = view["issuer"]
    issued.pop("digest", None)
    issued.pop("artifact_seq", None)
    for check in issued["checks"]:
        check.pop("frozen_seq")
        check["positive"] = check.pop("positive_seq") is not None
        for control in check["controls"]:
            control.pop("seq")
    for receipt in issued["receipts"]:
        receipt.pop("seq")
    return view


def content_sha256(body: Mapping[str, Any]) -> str:
    return sha(canonical(content(body)))


def read_json(path: Path) -> dict:
    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                raise ValueError(f"duplicate JSON key: {key}")
            found[key] = value
        return found
    def invalid(value):
        raise ValueError(f"non-finite JSON number: {value}")
    value = json.loads(path.read_bytes(), parse_float=str, parse_constant=invalid, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: an object is required")
    return value


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("a relative POSIX path is required")
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path is outside the package root")
    if path.is_symlink():
        raise ValueError("symlinks are not certificate inputs")
    return path


def layout(package: str, *, allow_missing_review: bool = False) -> tuple[Path, Path, dict]:
    from g6.core import vetting

    found, declaration = vetting._source(package), vetting.declared(package)
    problems = vetting.problems(package)
    if allow_missing_review and isinstance(declaration, dict):
        missing = (f"{package}: __g6_vetting__['independent_pass'] does not exist: "
                   f"{declaration.get('independent_pass')!r}")
        problems = [p for p in problems if p != missing]
    if found is None or not isinstance(declaration, dict) or problems:
        raise ValueError("a readable vetting declaration is required")
    root = found[1].resolve()
    return root, inside(root, package.replace(".", "/")), declaration


def _tiered(name: str, rule: dict) -> None:
    """A rule's kind fixes what its tier, effect and implementation may be."""
    kind, tier, effect = rule["kind"], rule["tier"], rule["effect"]
    if kind == "caller":
        if tier or rule["implements"]:
            raise ValueError(f"{name}: a caller rule has no tier and no implementation of G6's own")
        return
    if kind == "register":
        if tier not in ("computes", "property") or effect:
            raise ValueError(f"{name}: a register rule's tier is computes or property, with no effect")
    elif effect not in GUARD_EFFECTS:
        raise ValueError(f"{name}: not a guard: it changes the result (an own rule's effect is one of "
                         f"{', '.join(GUARD_EFFECTS)})")
    elif tier != "guard":
        raise ValueError(f"{name}: an own rule's tier is guard")
    if not isinstance(rule["source"], str) or not rule["source"]:
        raise ValueError(f"{name}: a {tier} rule cites one source")
    if not isinstance(rule["implements"], str) or not _IMPLEMENTS.fullmatch(rule["implements"]):
        raise ValueError(f"{name}: implements must name the implementation as path:function")


def rules_from(path: Path) -> dict[str, dict]:
    """Read the RULES/Rule table as syntax, without importing its code: kind, source, effect, tier, implements."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    expressions = [node.value for node in tree.body
                   if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RULES"
                                                           for t in node.targets))
                   or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                       and node.target.id == "RULES")]
    if len(expressions) != 1 or not isinstance(expressions[0], ast.Dict):
        raise ValueError("RULES must be a literal table")
    result = {}
    for key, expr in zip(expressions[0].keys, expressions[0].values):
        name = ast.literal_eval(key)
        if not isinstance(name, str) or not name or name in result:
            raise ValueError("rule names must be unique non-empty strings")
        if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name) or expr.func.id != "Rule":
            raise ValueError("RULES entries must be literal Rule(...) declarations")
        fields = ("rule", "kind", "source", "effect")
        if len(expr.args) > len(fields):
            raise ValueError("too many Rule arguments")
        values = dict(zip(fields, (ast.literal_eval(v) for v in expr.args)))
        for kw in expr.keywords:
            if kw.arg not in (*fields, "tier", "implements", "own_choice") or kw.arg in values:
                raise ValueError("invalid Rule keyword")
            values[kw.arg] = ast.literal_eval(kw.value)
        for field in ("effect", "tier", "implements"):
            values.setdefault(field, "")
        values.setdefault("own_choice", False)
        if type(values["own_choice"]) is not bool or (values["own_choice"] and values.get("kind") != "register"):
            raise ValueError(f"{name}: own_choice is True or False, and True only on a register rule whose source "
                             "sets no threshold it applies (G6's own choice)")
        if not {"rule", "kind", "source"} <= values.keys() or values["kind"] not in ("register", "own", "caller"):
            raise ValueError("rule kind/source missing")
        if isinstance(values["source"], tuple):
            values["source"] = list(values["source"])
        _tiered(name, values)
        result[name] = values
    if not result:
        raise ValueError("empty rule table")
    return result


def implementation(rule: Mapping[str, Any]) -> tuple[str, str]:
    """A tiered rule's implementing file and top-level function."""
    file, _, function = rule["implements"].rpartition(":")
    return file, function


def rule_path(root: Path, declaration: dict) -> Path:
    register = inside(root, declaration["register"])
    return register.parent.parent / ("_sources.py" if register.parent.name == "_data" else "sources.py")


def covered(root: Path, folder: Path, declaration: dict, rules: Mapping[str, dict]) -> dict[str, dict]:
    """The rules this component's certificate covers: every rule with a tier, except one implemented inside another
    member of the family that declares the same register, whose own certificate covers it. A rule implemented in a
    member that does not share the register belongs to no certificate, and is refused."""
    from g6.core import vetting

    family = folder.parent.relative_to(root).as_posix() + "/"
    out = {}
    for name, rule in rules.items():
        if not rule["tier"]:
            continue
        file = implementation(rule)[0]
        rest = file[len(family):] if file.startswith(family) else ""
        member = rest.split("/", 1)[0] if "/" in rest else ""
        if member and not member.startswith("_") and member != folder.name:
            other = vetting.declared((family + member).replace("/", "."))
            if not isinstance(other, dict) or other.get("register") != declaration["register"]:
                raise ValueError(f"{name}: implemented in {member}, which does not share this component's register")
            continue
        out[name] = rule
    return out


def _set_files(root: Path, cert: Path) -> dict[str, bytes]:
    """A certificate folder's inputs (everything but its generated outputs and its scope)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(cert.rglob("*"))
            if p.is_file() and p.name not in (*OUTPUTS, "scope.json")
            and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(cert).parts)}


def certificate_dir(root: Path, folder: Path) -> tuple[Path, dict | None]:
    """Where a component's certificate inputs live, and what it inherits. A component may share another member's
    whole input set (cases, checks, second route, falsifier, adapter) by naming that folder and the SHA-256 of its
    inputs in ``checks.json``'s ``inherits``; its ``scope.json`` stays its own, and the rules it covers are still
    decided by its own artifact (:func:`covered`). Any change to the inherited inputs is refused until it is
    recorded."""
    own = folder / "certificate"
    config = read_json(own / "checks.json")
    inherited = config.get("inherits")
    if inherited is None:
        return own, None
    if set(config) - {"SPDX-License-Identifier", "inherits"}:
        raise ValueError("a checks.json that inherits holds nothing else")
    if not isinstance(inherited, dict) or set(inherited) != {"path", "sha256"}:
        raise ValueError("inherits names the certificate folder's path and the SHA-256 of its inputs")
    path = inherited["path"]
    if not isinstance(path, str) or not path.endswith("/certificate"):
        raise ValueError("inherits names a certificate folder")
    other = inside(root, path)
    if not other.is_dir() or other.resolve() == own.resolve():
        raise ValueError("inherits names another component's certificate folder")
    if read_json(other / "checks.json").get("inherits") is not None:
        raise ValueError("an inherited certificate set cannot itself inherit")
    if inherited["sha256"] != sha(artifact_bytes(_set_files(root, other))):
        raise ValueError("inherited certificate inputs changed: record their new SHA-256 in inherits")
    return other, dict(inherited)


def inherited_sha256(root: Path, path: str) -> str:
    """The SHA-256 an ``inherits`` entry records for the certificate folder at ``path``."""
    return sha(artifact_bytes(_set_files(root, inside(root, path))))


def input_bases(root: Path, folder: Path, declaration: dict) -> list[tuple[Path, bool]]:
    """Each folder holding inputs, and whether only its ``_``-prefixed entries count: the component's folder; the
    folder its register belongs to (a family-level ``_data`` register owns shared helpers, not sibling members); and
    the family root's ``_``-prefixed shared code and data, which a member may import wherever its register lives."""
    register = inside(root, declaration["register"])
    return [(folder, False), (register.parent.parent, register.parent.name == "_data"), (folder.parent, True)]


def _exact(root: Path, relative: str) -> bool:
    """Whether ``relative`` names a file under ``root`` with exactly these names (a case-insensitive file system
    would otherwise find grasp.py as Grasp.py)."""
    here = root
    for part in relative.split("/"):
        if not here.is_dir() or part not in {p.name for p in here.iterdir()}:
            return False
        here = here / part
    return here.is_file()


def _module_path(root: Path, dotted: str) -> str | None:
    base = dotted.replace(".", "/")
    for candidate in (base + ".py", base + "/__init__.py"):
        if _exact(root, candidate):
            return candidate
    return None


def _initialisers(root: Path, relative: str) -> set[str]:
    """The initialiser of every package above a module: Python runs each one before the module."""
    parts = relative.split("/")
    found = {"/".join(parts[:i]) + "/__init__.py" for i in range(1, len(parts))}
    return {path for path in found if path != relative and _exact(root, path)}


def _imports(root: Path, relative: str, data: bytes) -> set[str]:
    """The g6 module files one module imports, anywhere in it (inside functions too), absolute or relative."""
    parts = relative.removesuffix(".py").split("/")
    package = parts[:-1]
    names = []
    for node in ast.walk(ast.parse(data.decode("utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package[:len(package) - node.level + 1], *([node.module] if node.module else [])])
            names += [base, *(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")]
    return {path for name in names if name.split(".")[0] == "g6" and (path := _module_path(root, name))}


def imported(root: Path, files: Mapping[str, bytes], roots: Iterable[str]) -> dict[str, bytes]:
    """Every g6 module the ``roots`` import, statically and transitively within g6, with the initialiser of every
    package on the way and what it imports (Python runs it on import), that is not already an input: code a certified
    function runs, and all the g6 code its certificate's child may import. The harness's own files (bound by
    ``harness_sha256``) are not followed."""
    found: dict[str, bytes] = {}
    queue, seen = list(roots), set()
    while queue:
        relative = queue.pop()
        if relative in seen:
            continue
        seen.add(relative)
        data = files[relative] if relative in files else found[relative]
        for target in sorted(_imports(root, relative, data) | _initialisers(root, relative)):
            if target in HARNESS_PATHS:
                continue
            if target not in files and target not in found:
                found[target] = inside(root, target).read_bytes()
            queue.append(target)
    return dict(sorted(found.items()))


def input_files(root: Path, folder: Path, declaration: dict, *, include_review: bool = True) -> dict[str, bytes]:
    """Exact bytes, with paths relative to src; include shared implementation beside the register and the family's,
    an inherited certificate set, and every g6 module the implementing functions and the adapter import.

    Generated output and the detached signed review are excluded to avoid a digest
    cycle. Source, all data files, and all certificate inputs are included. No .env
    or hidden files are opened. Adding/removing an input changes the manifest.
    """
    paths = set()
    signed = inside(root, declaration["signed_review"]) if declaration.get("signed_review") else None
    for base, shared_only in input_bases(root, folder, declaration):
        for path in base.rglob("*"):
            rel = path.relative_to(base)
            if shared_only and not rel.parts[0].startswith("_"):
                continue
            if (not include_review and declaration.get("independent_pass")
                    and path == inside(root, declaration["independent_pass"])):
                continue
            if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                continue
            if path.is_file() and (path.suffix == ".py"
                                   or {"data", "_data", "certificate"}.intersection(rel.parts)):
                if path == signed or (path.parent == folder / "certificate" and path.name in OUTPUTS):
                    continue
                inside(root, path.relative_to(root).as_posix())
                paths.add(path)
    for key in ("register", "independent_pass"):
        if declaration.get(key) and (include_review or key != "independent_pass"):
            path = inside(root, declaration[key])
            if key == "register" or path.is_file():  # a missing review is refused where it is read, never here
                paths.add(path)
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(paths)}
    cert, inherited = certificate_dir(root, folder)
    if inherited:
        files.update(_set_files(root, cert))
    rules = covered(root, folder, declaration, rules_from(rule_path(root, declaration)))
    roots = {implementation(rule)[0] for rule in rules.values()}
    adapter = read_json(cert / "checks.json").get("adapter")
    roots.update([adapter] if isinstance(adapter, str) else [])
    missing = sorted(r for r in roots if r not in files and not (root / r).is_file())
    if missing:
        raise ValueError(f"implementation is outside the artifact: {', '.join(missing)}")
    for relative in sorted(roots - set(files)):
        files[relative] = inside(root, relative).read_bytes()
    files.update(imported(root, files, sorted(roots)))
    return dict(sorted(files.items()))


def imported_code(root: Path, folder: Path, declaration: dict, files: Mapping[str, bytes]) -> list[str]:
    """The inputs outside the component's own bases: implementing modules and the g6 modules they import."""
    bases = [(base.relative_to(root).as_posix() + "/", shared) for base, shared in input_bases(root, folder, declaration)]

    def based(name: str) -> bool:
        for prefix, shared in bases:
            if name.startswith(prefix) and (not shared or name[len(prefix):].startswith("_")):
                return True
        return False
    return sorted(n for n in files if "/certificate/" not in n and n not in (declaration.get("register"),
                                                                               declaration.get("independent_pass"))
                  and not based(n))


def reviewed(package: str) -> dict:
    """Exact review targets, available before the independent pass or source bytes.

    The candidate adapter is among them: it converts inputs and projects the target's
    output, so it can decide an observation. ``None`` records that there is none.
    """
    root, folder, declaration = layout(package, allow_missing_review=True)
    files = input_files(root, folder, declaration, include_review=False)
    lf_only(code_files(files, declaration))   # before any hash is printed, compared or issued (load reaches this too)
    entries = read_json(inside(root, declaration["register"]))["entries"]
    cert, _ = certificate_dir(root, folder)
    config = read_json(cert / "checks.json")
    adapter = config.get("adapter")
    if adapter and adapter not in files:
        raise ValueError("candidate adapter must be a certificate input")
    return {
        "artifact_sha256": sha(artifact_bytes(code_files(files, declaration))),
        "rules_sha256": sha(rule_path(root, declaration).read_bytes()),
        "sources_sha256": {entry["id"]: sha(canonical(entry)) for entry in entries},
        "cases_sha256": sha((cert / "cases.json").read_bytes()),
        "adapter_sha256": sha(files[adapter]) if adapter else None,
        # The exception classes a guard's adapter may read as the guard firing: they decide observations too.
        "refusals": sorted(config.get("refusals", [])),
    }


def source_checks(entries: list[dict], held: Mapping[str, bytes], issued: dict | None = None) -> list[dict]:
    """Rehash clauses and any supplied bytes; offline replay needs issuance observations.

    Offline, a page- or file-hashed source's clause hash sits in the same register
    entry as its clause, so it catches a typo or a one-sided edit only; the source's
    bytes are trusted to the issuance record, which is not signed.
    """
    checked = []
    for entry in entries:
        name = entry["id"]
        scope = entry.get("hash_scope", "retrieved_bytes")
        clause = sha(entry["clause"].encode("utf-8"))
        if scope == "clause_utf8":
            if clause != entry["sha256"]:
                raise ValueError(f"source hash differs: {name}")
        elif scope in ("corpus_file", "retrieved_bytes"):
            if clause != entry.get("clause_sha256"):
                raise ValueError(f"source clause hash differs: {name}")
            observation = {"id": name, "sha256": entry["sha256"]}
            if name not in held:
                if issued is None or observation not in issued.get(BYTES_AT_ISSUE, []):
                    raise ValueError(f"source hash cannot be recomputed offline without issuance bytes record: {name}")
            checked.append(observation)
        else:
            raise ValueError("unknown source hash scope")
        if name in held and sha(held[name]) != entry["sha256"]:
            raise ValueError(f"source hash differs: {name}")
    return sorted(checked, key=lambda row: row["id"])


def code_files(files: Mapping[str, bytes], declaration: dict) -> dict[str, bytes]:
    """Code/data artifact, excluding the checks and independent review of that artifact."""
    return {name: data for name, data in files.items()
            if "/certificate/" not in name and name != declaration.get("independent_pass")}

def lf_only(files: Mapping[str, bytes]) -> None:
    """Refuse (:data:`CRLF`) artifact files that hold ``\\r\\n``. The source is LF (``.gitattributes``), so a checkout
    that kept CRLF bytes gives an artifact hash no LF checkout, reader or CI reproduces; nothing is normalised here. A
    file git treats as binary (a NUL byte in its first 8000 bytes) is not a text file and is left as it is."""
    for name, data in sorted(files.items()):
        if b"\r\n" in data and b"\0" not in data[:8000]:
            raise ValueError(f"[{CRLF}] {name} holds CRLF line endings, so the artifact's hash would differ from every "
                             "LF checkout's; check the source out again with LF line endings (.gitattributes keeps "
                             "src/g6 LF) and run this again")


def artifact_bytes(files: Mapping[str, bytes]) -> bytes:
    """A canonical manifest commits to every exact input byte, name and length."""
    return canonical({name: {"sha256": sha(value), "bytes": len(value)} for name, value in sorted(files.items())})


def due(entry: dict) -> dt.date:
    day = dt.date.fromisoformat(entry["retrieved"])
    months = entry["review_months"]
    if type(months) is not int or not 1 <= months <= 1200:
        raise ValueError("invalid source review window")
    count = day.year * 12 + day.month - 1 + months
    year, month = count // 12, count % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def gated(issued: Mapping[str, Any], kind: str) -> list[str]:
    """The rules the issuer record names in its gates of one kind (``context_only``)."""
    return sorted(g.split(":", 1)[1] for g in issued["gates"] if g.split(":", 1)[0] == kind)


def state_line(issued: Mapping[str, Any], rules: Mapping[str, Mapping[str, Any]]) -> str:
    """The scope's first line, from the issuer's own record and the covered ``rules``: the state, why it is no better,
    each rule a reader read by context only (a named gap that holds the state at qualified-draft), and each property
    rule, which no printed value checks. Every result's evidence carries it."""
    state, gaps = issued["state"], [why for _, why in issued["gaps"]]
    if any("frozen_after_artifact" in why for why in gaps):
        line = (f"Issuer state {state}: these checks were frozen after the code they check existed (a retrofit), so "
                "by the issuer's freeze rule they can reach qualified-draft at most.")
    elif state == "verified":
        line = ("Issuer state verified, for the recorded bytes, cases and seeded inputs only; it says nothing beyond "
                "them.")
    else:
        line = f"Issuer state {state}: {len(gaps)} gap(s) named in the record keep it there."
    context = gated(issued, CONTEXT_GATE)
    if context:
        line += (" A named gap holds it at qualified-draft at most: the independent reader attested that a clause "
                 f"supports its rule only by its context (read by context only: {', '.join(context)}).")
    chosen = gated(issued, CHOICE_GATE)
    if chosen:
        line += (" A named gap holds it at qualified-draft at most: a threshold these rules apply is G6's own choice, "
                 f"which no source sets: {', '.join(chosen)}.")
    unmoved = gated(issued, THRESHOLD_GATE)
    if unmoved:
        line += (" A named gap holds it at qualified-draft at most: these guards have no threshold G6 can move (no "
                 f"comparison or number in their code), so only removing each one tests its cases: {', '.join(unmoved)}.")
    unprinted =sorted(name for name, rule in rules.items() if rule["tier"] == "property")
    if unprinted:
        line += (" A second route and invariants check these rules, and no value their source prints, by the "
                 f"independent reader's attestation (property-checked, no printed value: {', '.join(unprinted)}).")
    return line


def review_present(root: Path, declaration: dict) -> bool:
    """Presence of review metadata; this is never signature verification or approval."""
    try:
        value = read_json(inside(root, declaration["signed_review"]))
        return all(isinstance(value.get(k), str) and value[k].strip()
                   for k in ("reviewer", "qualification"))
    except (OSError, ValueError, KeyError, TypeError):
        return False

def signed_review(root: Path, declaration: dict, *, certificate_path: Path) -> bool:
    """Verify the certificate's content digest (without history positions, :func:`content`) against the owner's
    committed qualified-reviewer keys."""
    try:
        value = read_json(inside(root, declaration["signed_review"]))
        message = canonical(content(read_json(certificate_path)))
        if (value.get("content_sha256") != sha(message)
                or not all(isinstance(value.get(k), str) and value[k].strip()
                           for k in ("reviewer", "qualification"))):
            return False
        signature = certificate_path.with_suffix(".json.sig")
        if not signature.is_file() or signature.is_symlink():
            return False
        # A read-only SSH signature capability probe; missing/unrecognised signers fail closed.
        program = replace(SSH_KEYGEN, version_args=(
            "-Y", "find-principals", "-f", str(ALLOWED_SIGNERS), "-s", str(signature)))
        required = outside.require(program)
        if required.is_fail():
            return False
        with component_process.window(10):
            done = component_process.run_contained(
                [required.value.where, "-Y", "verify", "-f", str(ALLOWED_SIGNERS),
                 "-I", value["reviewer"], "-n", "g6-certificate", "-s", str(signature)],
                env=outside.clean_environment(), input_bytes=message)
        return done.returncode == 0
    except (OSError, ValueError, KeyError, TypeError, component_process.ExecutionInterrupted):
        return False


def inspect(package: str, *, today: dt.date | None = None) -> tuple[dict, dict | None, list[str]]:
    """Read current bytes and listing prerequisites. Full re-decision belongs to the kit."""
    day = today or dt.date.today()
    view = {"path": package.replace(".", "/") + "/certificate/certificate.json",
            "state": None, "sha256": None, "current": False, "not_checked": []}
    body = None
    problems = []
    try:
        root, folder, declaration = layout(package)
        path = folder / "certificate/certificate.json"
        if not path.is_file():
            return view, None, ["missing certificate"]
        raw = path.read_bytes()
        view["sha256"] = sha(raw)
        body = read_json(path)
        issued = body["issuer"]
        view.update(state=issued["state"], not_checked=body["not_checked"])
        if body.get("schema") != SCHEMA or body.get("component") != package:
            problems.append("certificate schema/component differs")
        if body.get("digest") != digest(body) or issued.get("digest") != digest(issued):
            problems.append("certificate digest differs")
        if raw != canonical(body):
            problems.append("certificate bytes are not canonical")
        files = input_files(root, folder, declaration)
        if issued["artifact_sha"] != sha(artifact_bytes(code_files(files, declaration))):
            problems.append("certificate artifact bytes changed")
        if body["inputs_sha256"] != sha(artifact_bytes(files)):
            problems.append("certificate input bytes changed")
        if body.get("harness_sha256") != {name: sha(data) for name, data in harness_files().items()}:
            problems.append("certificate harness bytes changed")
        rules = rules_from(rule_path(root, declaration))
        wanted = covered(root, folder, declaration, rules)
        if (not any(r["kind"] == "register" for r in wanted.values()) or body["rules"] != rules
                or {p["predicate"] for p in issued["predicates"]} != set(wanted)):
            problems.append("certificate rule coverage differs")
        scope = read_json(folder / "certificate/scope.json")["not_checked"]
        if (not isinstance(scope, list) or not scope or not all(isinstance(s, str) and s.strip() for s in scope)
                or body["not_checked"] != [state_line(issued, wanted), *scope]):
            problems.append("certificate scope is empty or changed")
        entries = read_json(inside(root, declaration["register"]))["entries"]
        if any(e["id"] == OWN_THRESHOLD for e in entries):
            problems.append(f'a register entry is named "{OWN_THRESHOLD}", as G6\'s own threshold is')
        sources = {r["source"] for r in wanted.values() if r["source"] != OWN_THRESHOLD}
        cited = sorted((e for e in entries if e["id"] in sources), key=lambda e: e["id"])
        if cited != body["register"] or any(day > due(e) for e in cited) or body["stale_sources"]:
            problems.append("certificate sources are stale or changed")
        if source_checks(entries, {}, body) != body.get(BYTES_AT_ISSUE, []):
            problems.append("source issuance observations differ")
        independent = body["independent_pass"]
        review_path = inside(root, declaration["independent_pass"])
        if independent["sha256"] != sha(review_path.read_bytes()):
            problems.append("independent pass changed")
        if (set(independent["coverage"]) != set(wanted) or independent["code_matches"] is not True
                or not independent.get("reviewer") or independent["reviewer"] == issued["producer"]):
            problems.append("independent pass incomplete")
        if issued["violations"] or any(p["refute"] or p["caveats"] for p in issued["predicates"]):
            problems.append("certificate has a refutation or unresolved checks")
        cert = certificate_dir(root, folder)[0]
        cased = {c.get("rule") for c in read_json(cert / "cases.json")["cases"]}
        expected_checks = {f"{r}:{kind}" for r, rule in wanted.items() for kind in check_kinds(rule["tier"], r in cased)}
        config = read_json(cert / "checks.json")
        expected_checks.update(f"{r}:formal" for r in config.get("proofs", {}))
        checks = issued["checks"]
        if len(checks) != len(expected_checks) or {c["check_id"] for c in checks} != expected_checks:
            problems.append("certificate check coverage differs")
        receipts = issued["receipts"]
        if (len(receipts) != len(expected_checks) or {r["check_id"] for r in receipts} != expected_checks
                or any(r["result"] != "pass" or r["artifact_sha"] != issued["artifact_sha"] for r in receipts)):
            problems.append("not every rule passed its checks")
        for check in checks:
            backed = any(c["killed"] is True and c["generated_by"] == "g6"
                         and c["check_sha"] == check["check_sha"] and c["predicate"] in check["predicates"]
                         and check["frozen_seq"] <= c["seq"] < r["seq"]
                         for c in check["controls"] for r in receipts if r["check_id"] == check["check_id"]
                         and r["check_sha"] == check["check_sha"])
            if not backed:
                problems.append(f"{check['check_id']}: no seeded defect caught")
        # A retrofit may be listed as reviewed while its issuer state remains qualified-draft.
        if issued["state"] not in ("verified", "qualified-draft"):
            problems.append("certificate state is not listable")
        view["current"] = not problems
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, AttributeError, OverflowError):
        problems.append("certificate unreadable or incomplete")
    return view, body, problems


def add_evidence(result: MutableMapping[str, Any], package: str, rules_applied: Sequence[str], *,
                 today: dt.date | None = None) -> MutableMapping[str, Any]:
    """Append attribution; never change a result's completion state."""
    view, body, problems = inspect(package, today=today)
    if problems or body is None:
        raise ValueError("cannot cite a non-current certificate: " + "; ".join(problems))
    if isinstance(rules_applied, str) or not set(rules_applied) <= set(body["rules"]):
        raise ValueError("rules_applied names an undeclared rule")
    callers = {name for name, rule in body["rules"].items() if rule["kind"] == "caller"}
    uncovered = sorted(set(rules_applied) - set(body["independent_pass"]["coverage"]) - callers)
    if uncovered:
        raise ValueError(f"this certificate does not cover {', '.join(uncovered)}: cite the certificate that does")
    evidence = result.setdefault("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("result evidence must be a list")
    evidence.append({"kind": "certificate", "component": package, "certificate_sha256": view["sha256"],
                     "rules_applied": list(rules_applied), "not_checked": list(view["not_checked"])})
    return result
