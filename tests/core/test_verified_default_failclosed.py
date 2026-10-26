# SPDX-License-Identifier: Apache-2.0
"""#135 — `verified` must never be what you get by saying nothing.

The defect this pins: the reliability envelope, and 137 sibling defaults across the
component tree, declared ``completion_state = "verified"`` as their DEFAULT. A
producer that named no label received a verified, non-degraded envelope with empty
evidence, and 193 call sites did exactly that. Worse, the promotion law could not
catch it: components build their own output models and never reach the core door,
so ``G6_KERNEL_PROMOTION_LAW=enforce`` demoted the core builder and left the
component untouched.

WHAT THESE TESTS ARE EVIDENCE FOR
  * a DEFAULT envelope is not verified, at the core door and at a real component;
  * that holds with the promotion law OFF, which is the shipped prod default;
  * naming ``verified`` explicitly still works, so the change removed the free
    ride rather than removing the state;
  * no fail-open label default has crept back into the tree (the guard).

WHAT THEY ARE **NOT** EVIDENCE FOR — stated so nobody reads more into a green:
  * NOT that every `verified` still reachable in the product is EARNED. This closes
    the unnamed-default route only. A component that explicitly passes
    ``completion_state="verified"`` on a weak predicate is a different defect class
    (#69/#78/#99/#100) and these tests say nothing about it.
  * NOT that `degraded` is consistent. Component output models are plain
    BaseModels with no reconcile invariant, so they can still report
    ``qualified-draft`` alongside ``degraded=False``. That is a KNOWN RESIDUAL of
    this slice, fixed structurally by routing components through the core envelope.
  * NOT a statement about non-Python emitters (templates, JS, JSON fixtures).
"""
from __future__ import annotations

import ast
import io
import os
import re
from collections.abc import Callable

import pytest

from g6.core.reliability_envelope import (
    ReliabilityEnvelope,
    envelope_fields,
)

# The guard is keyed on the VALUE, not on a field-name allowlist.
#
# WHY: the previous version listed {"completion_state", "reliability_label"} and
# was blind to `reliability_status` and `assurance_strength`, both of which were
# live. Adding those two names would have left a fifth name open -- the same
# "narrowing a hole is not closing it" shape this campaign keeps hitting. Keying
# on the value catches EVERY field name, including ones nobody has invented yet,
# and pushes the burden onto an explicit, structural exemption below.
LABEL_VALUE = "verified"
# CORE_LABELS-03: the guard walks the library itself (``src/g6`` beside this ``tests`` folder), the same layout in
# this repository and in the public export. It used to walk the archive's ``components/`` and ``bases/``, which the
# export does not have, so it never read a line of the library.
LIBRARY_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "g6")
)
LIBRARY_LABEL = "g6"
# Far below the measured count (797 parsed files on 2026-10-23), far above an empty or collapsed walk.
LIBRARY_FILE_FLOOR = 500
# Exact base names that make a class an enum VOCABULARY declaration rather than a
# holder of defaults. Exact, never substring -- see `_is_enum_class`. Both the
# bare and the `enum.`-qualified spellings resolve to the same names here because
# the walker reads `ast.Name.id` or `ast.Attribute.attr`.
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
TEST_MARK = re.compile(
    r"(^|[\\/])(tests?|testing)([\\/]|$)|(^|[\\/])test_[^\\/]*\.py$|_test\.py$|conftest\.py$"
)
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "third_party", "vendor", "deps", "mlruns",
    ".benchmarks", "grounding_cache", "prototype_2025",
}


# --------------------------------------------------------------------------
# 1. the core door
# --------------------------------------------------------------------------

def test_bare_envelope_is_not_verified():
    """An envelope built with no arguments must not claim verification."""
    env = ReliabilityEnvelope()
    assert env.completion_state != "verified"
    assert env.reliability_label != "verified"
    assert env.completion_state == "qualified-draft"


def test_bare_envelope_fields_is_not_verified():
    assert envelope_fields()["completion_state"] != "verified"


def test_bare_envelope_reports_itself_degraded():
    """The core envelope derives `degraded` from the label, so a default
    envelope must not simultaneously claim qualified-draft AND not-degraded."""
    assert ReliabilityEnvelope().degraded is True


def test_holds_whatever_the_old_law_switch_says(monkeypatch):
    """The law used to ship off (G6_KERNEL_PROMOTION_LAW unset). It has no switch
    now (plan A1): an off-word changes nothing, for a bare envelope or a stated one."""
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "off")
    assert envelope_fields()["completion_state"] != "verified"
    assert envelope_fields(reliability_label="verified")["completion_state"] == "qualified-draft"


# --------------------------------------------------------------------------
# 2. the change removed the free ride, not the state (discrimination)
# --------------------------------------------------------------------------

def test_explicitly_named_verified_is_held_without_g6s_recheck():
    """One outcome (plan A1): naming `verified` is a claim, and with no re-check by
    G6 it is held at qualified-draft. The DISCRIMINATION CHECK (that `verified` is
    still reachable, so the tests above are not vacuously green) is
    tests/core/test_door_promotion.py::test_a_registered_g6_rechecker_promotes."""
    env = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert env.reliability_label == "qualified-draft"
    assert env.degraded is True


@pytest.mark.parametrize("label", ["qualified-draft", "blocked-escalated"])
def test_explicit_non_verified_labels_round_trip(label):
    env = ReliabilityEnvelope(completion_state=label, reliability_label=label)
    assert env.reliability_label == label
    assert env.degraded is True


# --------------------------------------------------------------------------
# 3. a real component, end to end -- the path the law could not see
# --------------------------------------------------------------------------

def test_real_component_op_does_not_default_to_verified():
    """The core door alone is not enough: components build their OWN output
    models and never reach it. This asserts at the surface a person sees.

    CORE_LABELS-03: this used to importorskip an archive component the library does not have, so it always skipped.
    It now runs a library component's op through the registry, with no skip."""
    # Fails if llm_router's list_tasks op (or the registry) hands out "verified" for a committed table it never checked.
    from g6.core.registry import get_registry

    payload = get_registry().invoke("llm_router", "list_tasks", {}).unwrap()
    assert payload["completion_state"] in ("qualified-draft", "blocked-escalated"), (
        "a component op with no independent check still claims verification: %r" % (payload["completion_state"],)
    )


# --------------------------------------------------------------------------
# 4. the guard -- stop it growing back
# --------------------------------------------------------------------------

def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` bindings, so a NAME default can be resolved.

    This is what made ``completion_state: str = _COMPLETION_VERIFIED`` invisible
    to the previous guard: it only ever compared against ``ast.Constant``.
    """
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
            if isinstance(stmt.value.value, str):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = stmt.value.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Constant):
            if isinstance(stmt.target, ast.Name) and isinstance(stmt.value.value, str):
                consts[stmt.target.id] = stmt.value.value
    return consts


def _resolved_default(
    node: ast.AST | None,
    consts: dict[str, str],
    name_fallback: "Callable[[str], str | None] | None" = None,
) -> str | None:
    """The static string value of a default expression, or None.

    Covers the four shapes the previous guard missed:
      * ``ast.Name`` resolved against module constants;
      * ``Field(default=...)`` keyword form;
      * ``Field("verified", ...)`` positional form (pydantic's first positional
        argument IS the default);
      * the above nested, e.g. ``Field(default=_COMPLETION_VERIFIED)``.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Attribute):
        # `errors.VERIFIED` and `CompletionState.VERIFIED`. Neither contains an
        # `ast.Name` for the constant, so a Name-only resolver was blind to both --
        # and `CompletionState.VERIFIED` is the NATURAL spelling for anyone
        # "fixing" one of the defaults this branch just closed.
        return name_fallback(node.attr) if name_fallback else None
    if isinstance(node, ast.Name):
        local = consts.get(node.id)
        if local is not None:
            return local
        return name_fallback(node.id) if name_fallback else None
    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname == "Field":
            for kw in node.keywords:
                if kw.arg == "default":
                    return _resolved_default(kw.value, consts, name_fallback)
            if node.args:
                return _resolved_default(node.args[0], consts, name_fallback)
    return None


def _is_enum_class(node: ast.ClassDef) -> bool:
    """Is this class an Enum, i.e. does it DECLARE the vocabulary?

    ``class CompletionState(str, Enum): VERIFIED = "verified"`` is the definition
    of the label, not a default anybody can receive by omission. Two such classes
    exist in the tree (`hyperdistillation.measurement.CompletionState`,
    `invariants.verifier.VerificationState`) and both are legitimate.

    This is a STRUCTURAL exemption, not a name allowlist, so a newly-added enum
    is exempt automatically while a newly-added label FIELD is not.

    🔴 THE MATCH IS EXACT, NOT A SUBSTRING. It used to accept any base whose name
    CONTAINED "Enum", and independent review broke it in one line:

        class NonEnumerableBase: pass
        class Sneaky(NonEnumerableBase):
            completion_state: str = "verified"   # silently exempted

    The docstring at the time claimed this "fails in the safe direction", which
    was only true of enum-recognition FALSE NEGATIVES. The false-POSITIVE
    direction -- an ordinary class silently exempted -- was live and unstated.
    That was a wrong safety claim, which this project treats as a defect in its
    own right even though no such class exists in the tree today (measured:
    disabling the exemption entirely still yields exactly the 2 legitimate enum
    declarations and nothing else).

    Failure direction now: an enum spelled through an idiom not listed here gets
    FLAGGED as an offender -- noisy, never silently skipped.
    """
    for base in node.bases:
        name = getattr(base, "id", None) or getattr(base, "attr", None)
        if name in _ENUM_BASES:
            return True
    return False


def _failopen_label_defaults(
    root: str, tree_label: str = "", counter: list[int] | None = None
) -> list[str]:
    """Every param/field default that hands out `verified` for free.

    Keyed on the VALUE. Any field name qualifies; only Enum member declarations
    are exempt.

    ``counter``, when given, is incremented once per file actually PARSED. The
    coverage control needs a count of files READ, not of directories that exist
    -- see `_scan_all_trees`.
    """
    found: list[str] = []
    prefix = (tree_label + "/") if tree_label else ""

    # PASS 1 -- parse every file once, recording module-level string constants
    # and the candidate sites whose default is an unresolved NAME.
    #
    # 🔴 THERE IS NO `if LABEL_VALUE not in src: continue` SHORT-CIRCUIT ANY MORE.
    # It skipped a file BEFORE PARSING whenever the file only ever spelled the
    # UPPERCASE constant -- e.g. `discovery_ops.py` contains no lowercase
    # "verified" at all. Combined with module-local-only constant resolution,
    # that hid FOUR offenders, three of them live across 38 call sites. The
    # guard was the null-result instrument its own commit message warned about.
    parsed: list[tuple[str, ast.Module, dict[str, str], set[str]]] = []
    global_consts: dict[str, set[str]] = {}
    parse_failures: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = prefix + os.path.relpath(path, root).replace("\\", "/")
            if TEST_MARK.search(rel):
                continue
            try:
                src = io.open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if counter is not None:
                counter[0] += 1
            try:
                tree = ast.parse(src)
            except SyntaxError:
                # RECORDED, not swallowed. A file this guard cannot parse is a
                # file it cannot vouch for, and a silent `continue` here is how a
                # reviewer's probe returned a false clean.
                parse_failures.append(rel)
                continue
            consts = _module_constants(tree)
            for name, value in consts.items():
                global_consts.setdefault(name, set()).add(value)
            # Enum MEMBERS too, so `CompletionState.VERIFIED` resolves. The
            # DECLARATION stays exempt (see `_is_enum_class`); what is caught is a
            # DEFAULT that points at it.
            for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
                if not _is_enum_class(cls):
                    continue
                for stmt in cls.body:
                    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)):
                        global_consts.setdefault(
                            stmt.targets[0].id, set()).add(stmt.value.value)
            # LOCAL NAME -> REAL NAME. A set of local names lost the binding, so
            # `from x import VERIFIED as V` was unresolvable. Review measured it.
            imported: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported[alias.asname or alias.name] = alias.name
            parsed.append((rel, tree, consts, imported))

    if parse_failures:
        raise AssertionError(
            "%d file(s) could not be parsed, so this guard cannot vouch for them: %s"
            % (len(parse_failures), parse_failures[:10])
        )

    # PASS 2 -- resolve, with IMPORTED names now resolvable against every
    # module-level constant seen anywhere in the tree. One hop is enough: all
    # four missed offenders import their constant directly.
    for rel, tree, consts, imported in parsed:
        def _imported_constant(name: str) -> str | None:
            """One hop: an imported NAME resolved against the whole scanned tree.

            Only when EVERY module-level definition of that name agrees, so an
            unrelated same-named constant cannot manufacture a hit.
            """
            # `imported.get(name, name)` so an ATTRIBUTE hop (`mod.CONST`,
            # `EnumClass.MEMBER`) can reach the same map without an import entry.
            real = imported.get(name, name)
            values = global_consts.get(real)
            return LABEL_VALUE if values and values == {LABEL_VALUE} else None

        def _resolve(node: ast.AST | None) -> str | None:
            # NOTE: this deliberately does NOT re-implement the Field(...) or
            # constant handling. It did, and the duplicate masked its own
            # mutation -- breaking `_resolved_default`'s Field branch left every
            # test green because the copy here still worked. Duplicated logic
            # cannot be mutation-tested. One implementation, one hook.
            return _resolved_default(node, consts, _imported_constant)

        # The innermost function around each node, so an expression hit names where it sits. ast.walk is
        # breadth-first, so an inner function overwrites its outer one.
        owner: dict[int, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(fn):
                    owner[id(inner)] = fn.name

        for node in ast.walk(tree):
                # CORE_LABELS-03: the expression forms of a free default, the shapes CORE_LABELS-01 used.
                where = owner.get(id(node), "<module>")
                if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                    if any(_resolve(v) == LABEL_VALUE for v in node.values[1:]):
                        found.append('%s:%d %s: or "verified"' % (rel, node.lineno, where))
                if isinstance(node, ast.Return) and _resolve(node.value) == LABEL_VALUE:
                    found.append('%s:%d %s: return "verified"' % (rel, node.lineno, where))
                if isinstance(node, ast.Call):
                    fallback = None
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) > 1:
                        fallback, callee = node.args[1], ".get"
                    elif isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) > 2:
                        fallback, callee = node.args[2], "getattr"
                    if fallback is not None and _resolve(fallback) == LABEL_VALUE:
                        found.append('%s:%d %s: %s(..., "verified")' % (rel, node.lineno, where, callee))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    pairs = list(zip(a.args[len(a.args) - len(a.defaults):], a.defaults))
                    pairs += [
                        (k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None
                    ]
                    for arg, dflt in pairs:
                        if _resolve(dflt) == LABEL_VALUE:
                            found.append(
                                "%s:%d %s(%s=)" % (rel, dflt.lineno, node.name, arg.arg)
                            )
                if isinstance(node, ast.ClassDef):
                    if _is_enum_class(node):
                        continue
                    for stmt in node.body:
                        target = value = None
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                            target, value = stmt.target.id, stmt.value
                        elif (
                            isinstance(stmt, ast.Assign)
                            and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)
                        ):
                            target, value = stmt.targets[0].id, stmt.value
                        if target is None or value is None:
                            continue
                        if _resolve(value) == LABEL_VALUE:
                            found.append("%s:%d %s.%s" % (rel, stmt.lineno, node.name, target))
    return found


_SCAN_CACHE: dict[str, tuple[list[str], dict[str, int]]] = {}


def _scan_all_trees() -> tuple[list[str], dict[str, int]]:
    """Returns (offenders, {tree: files_actually_parsed}).

    🔴 THE SECOND VALUE IS A FILE COUNT, NOT A LIST OF DIRECTORY NAMES. It used
    to record which trees EXISTED (``os.path.isdir``), and independent review
    killed that: both trees hold exactly one subdirectory (``mvp/``), so adding
    ``"mvp"`` to ``SKIP_DIRS`` made the walk read ZERO files while every test in
    this file stayed green -- ``offenders == []`` and both trees still "scanned".

    That is the same defect as the one the literal-tree-names fix already
    corrected, one layer down: the control took its EXPECTATION from an
    independent source but its EVIDENCE from a predicate a shrunken scan does
    not disturb. A coverage control has to count the thing it is claiming.
    """
    # MEMOIZED. Measured 66 s and ~3.0 GB peak RSS per scan, and TWO tests here
    # each called it -- doubling both on a small self-hosted runner.
    if "v1" in _SCAN_CACHE:
        return _SCAN_CACHE["v1"]
    counter = [0]
    offenders = _failopen_label_defaults(LIBRARY_ROOT, LIBRARY_LABEL, counter)
    scanned = {LIBRARY_LABEL: counter[0]}
    _SCAN_CACHE["v1"] = (offenders, scanned)
    return offenders, scanned


def _allowlist_key(hit: str) -> str:
    """A hit without its line number: ``g6/x.py:12 f: return "verified"`` -> ``g6/x.py f: return "verified"``."""
    return re.sub(r":\d+ ", " ", hit, count=1)


#: Every ``verified`` the walk finds under ``src/g6`` that is NOT a free default, pinned to its file and function,
#: the number of hits there, and why. A new hit anywhere, a changed count, or an entry nothing matches any more
#: fails the guard, so the list cannot quietly grow or go stale. (CORE_LABELS-03, 2026-10-23.)
NOT_A_FREE_DEFAULT: dict[str, tuple[int, str]] = {
    'g6/core/reliability_envelope.py _apply_promotion_law: return "verified"': (
        1, "demote-only gate, reached only when the producer stated verified: keeps or lowers that state, never "
           "raises one"),
    'g6/core/door.py state_of: return "verified"': (
        1, "reads a word a result declares as the state it names, so the door can hold or correct it; a missing or "
           "unknown word is None, never verified"),
    'g6/evidence/certificate.py _state: return "verified"': (
        1, "the issuer's rule: only when every predicate is supported by a pass receipt from an independent, frozen, "
           "controlled check G6 resolved in its own ledger, and every gate is decided"),
    'g6/workforce/contracts.py _usage_state: return "verified"': (
        1, "maps a reply's own declared word to the canonical spelling inside a down-only precedence; a missing or "
           "unknown word is 'none', never verified"),
}


def test_no_failopen_verified_default_anywhere():
    """REGRESSION GUARD: no ``verified`` for free anywhere in the library (``src/g6``).

    KEYED ON THE VALUE: any parameter default, annotated-assignment default,
    plain class assignment, or ``Field(...)`` default that statically resolves to
    the string ``"verified"``, with module-level NAME constants resolved; and the
    expression forms (CORE_LABELS-03): ``x or "verified"``, ``return "verified"``,
    ``.get(key, "verified")`` and ``getattr(obj, name, "verified")``.

    Every ``return "verified"`` is a hit, because the walk cannot tell a fallback
    from a decision. The ones that are a decision (or a down-only relay of a stated
    ``verified``) sit in ``NOT_A_FREE_DEFAULT`` with their reason and count.

    EXEMPT, structurally: members of an ``Enum`` subclass, which DECLARE the
    vocabulary rather than hand out a default.

    WHAT A GREEN STILL DOES **NOT** MEAN -- stated so nobody over-reads it.
      * NOT that every reachable `verified` is EARNED. A component that
        EXPLICITLY passes ``completion_state="verified"`` on a weak predicate is
        a different class entirely and is untouched here.
      * NOT a statement about non-Python emitters (templates, JS, JSON fixtures).
      * NOT a claim about defaults computed at runtime -- a
        ``default_factory=lambda: "verified"``, an ``x if c else "verified"`` or a
        value assembled by string concatenation is outside what this walk sees.
      * Module-level constants resolve, including ``from x import VERIFIED``,
        ``import VERIFIED as V``, ``mod.VERIFIED`` and ``EnumClass.MEMBER``.
        A class-level constant, a dict lookup or an f-string still do not.
      * Only ``src/g6`` is walked: not ``tools/``, not ``tests/``.
      * A FILE THAT FAILS ``ast.parse`` MAKES THIS GUARD RAISE -- it is not
        silently skipped. Noisy beats silent, but know which.
      * The exemption list ``_ENUM_BASES`` is spelled by NAME. An enum reached
        through an alias or a locally-defined subclass is not recognised, and
        will be FLAGGED rather than skipped (noisy, not silent).
    """
    offenders, scanned = _scan_all_trees()
    assert scanned.get(LIBRARY_LABEL, 0) > LIBRARY_FILE_FLOOR, scanned
    free = [hit for hit in offenders if _allowlist_key(hit) not in NOT_A_FREE_DEFAULT]
    assert free == [], (
        "%d fail-open `verified` default(s) in the library. A label must be earned "
        "and stated, never received by omission:\n  %s" % (len(free), "\n  ".join(free))
    )
    counts: dict[str, int] = {}
    for hit in offenders:
        counts[_allowlist_key(hit)] = counts.get(_allowlist_key(hit), 0) + 1
    drift = {key: (counts.get(key, 0), n) for key, (n, _why) in NOT_A_FREE_DEFAULT.items() if counts.get(key, 0) != n}
    assert drift == {}, "allowlisted sites changed (found, pinned): %r" % (drift,)


def test_the_guard_scans_every_tree_it_claims_to():
    """SURVIVAL CONTROL on COVERAGE, not just on detection.

    A guard that silently scanned nothing would return [] and read as a clean
    pass -- this campaign's documented dominant failure mode.

    Two independent failures had to be closed here, both of the same family --
    a control that cannot witness the quantity it names:

    1. TREE NAMES ARE LITERALS. This first read ``set(scanned) ==
       set(SCANNED_TREES)``, and my own mutation run killed it: a mutant that
       shrank ``SCANNED_TREES`` left the test GREEN, because it compared the walk
       against the very constant the mutant edited.
    2. THE EVIDENCE IS A FILE COUNT. Independent review then killed the repair:
       ``scanned`` recorded which trees EXISTED, so adding ``"mvp"`` to
       ``SKIP_DIRS`` -- both trees hold exactly that one subdirectory -- made the
       walk read ZERO files with all 13 tests still green. Naming the trees
       independently was necessary and not sufficient; the count is what makes an
       empty walk fail.

    3. THE TREE WAS THE WRONG ONE (CORE_LABELS-03). It walked the archive's
       ``components/`` and ``bases/``, never ``src/g6``, and in the public export
       neither exists. It now walks the library beside this ``tests`` folder.

    The floor (``LIBRARY_FILE_FLOOR``) sits far below the measured count -- 797
    files parsed under src/g6 on 2026-10-23 -- so ordinary growth or pruning never
    trips it while any collapse of the walk does.
    """
    # Fails if LIBRARY_ROOT points anywhere but the library, or the walk skips its folders.
    _offenders, scanned = _scan_all_trees()
    assert os.path.isfile(os.path.join(LIBRARY_ROOT, "core", "reliability_envelope.py")), LIBRARY_ROOT
    assert scanned.get(LIBRARY_LABEL, 0) > LIBRARY_FILE_FLOOR, (
        "src/g6 walk collapsed: only %d files parsed" % scanned.get(LIBRARY_LABEL, 0)
    )


ALL_SIX_SHAPES = """\
_COMPLETION_VERIFIED = "verified"


class ConstField:
    completion_state: str = "verified"


class NameField:
    completion_state: str = _COMPLETION_VERIFIED


class FieldKw:
    completion_state: str = Field(default="verified")


class NovelName:
    trust_state: str = "verified"


def const_param(*, reliability_label: str = "verified"):
    return reliability_label


def name_param(*, completion_state: str = _COMPLETION_VERIFIED):
    return completion_state
"""

# The SEVENTH shape lives in its own module, because it is the one that needs
# a SECOND file: a constant IMPORTED from elsewhere in the tree. This is the
# shape that hid four live offenders from the previous guard, and it had no
# positive control until independent review found them. A matrix that cannot
# plant the shape that broke you is not covering it.
IMPORTED_CONSTANT_SOURCE = 'VERIFIED = "verified"\n'
IMPORTED_CONSTANT_USE = """\
from tree_constants import VERIFIED


class ImportedField:
    completion_state: str = VERIFIED
"""


@pytest.mark.parametrize("tree", ("components", "bases"))
def test_the_guard_kills_all_six_shapes_in_each_tree(tmp_path, tree):
    """POSITIVE CONTROL: all seven reintroduction shapes are detected.

    A guard that cannot fail is not a guard. If this ever goes green with fewer
    than seven detections, the empty result above means nothing.

    HONEST ABOUT WHAT THE `tree` PARAMETER BUYS, because I overstated it. This
    was described as "6 shapes x 2 trees = the 12-mutant matrix". It is neither:
      * the `tree` parameter only changes the path PREFIX used to label a hit;
        the detection logic is byte-identical, so this is six facts checked
        twice, not twelve independent facts. The real per-tree coverage claim is
        the file-count control above, not this;
      * "the 12-mutant matrix" named an unrelated set -- the production mutants
        M1-M12 in the branch's commit messages.
    The parameter list is also written as LITERALS rather than `SCANNED_TREES`,
    for the same reason the coverage control is: a case list drawn from the
    constant under test shrinks silently when that constant shrinks.

    ``NovelName.trust_state`` is the load-bearing shape: a field name that
    appears nowhere in this repo, so it can only be caught by value-keying. It is
    the one the old name-allowlist guard could never have killed.
    """
    root = tmp_path / tree
    root.mkdir()
    (root / "offender.py").write_text(ALL_SIX_SHAPES, encoding="utf-8")
    (root / "tree_constants.py").write_text(IMPORTED_CONSTANT_SOURCE, encoding="utf-8")
    (root / "importer.py").write_text(IMPORTED_CONSTANT_USE, encoding="utf-8")

    offenders = _failopen_label_defaults(str(root), tree)
    assert len(offenders) == 7, (
        "expected all 7 planted shapes to be detected in %s/, got %d:\n  %s"
        % (tree, len(offenders), "\n  ".join(offenders))
    )
    joined = " ".join(offenders)
    for expected in (
        "ConstField", "NameField", "FieldKw", "NovelName",
        "const_param", "name_param", "ImportedField",
    ):
        assert expected in joined, "shape %r was not detected: %r" % (expected, offenders)


EXPRESSION_SHAPES = '''\
def or_default(state):
    return state or "verified"


def return_fallback(states):
    if not states:
        return "verified"
    return max(states)


def get_default(value):
    return value.get("completion_state", "verified")


def getattr_default(result):
    return getattr(result, "completion_state", "verified")


def honest(value, result, states):
    if not states:
        return "qualified-draft"
    return value.get("completion_state", "qualified-draft") or getattr(result, "state", "qualified-draft")
'''


def test_the_guard_kills_the_expression_shapes(tmp_path):
    """POSITIVE CONTROL for the expression forms CORE_LABELS-01 used, with a
    discrimination case: the same shapes defaulting to ``qualified-draft`` are not hits."""
    # Fails if the BoolOp, Return, .get or getattr branch of _failopen_label_defaults is removed.
    root = tmp_path / LIBRARY_LABEL
    root.mkdir()
    (root / "expressions.py").write_text(EXPRESSION_SHAPES, encoding="utf-8")
    offenders = _failopen_label_defaults(str(root), LIBRARY_LABEL)
    assert sorted(_allowlist_key(hit) for hit in offenders) == [
        'g6/expressions.py get_default: .get(..., "verified")',
        'g6/expressions.py getattr_default: getattr(..., "verified")',
        'g6/expressions.py or_default: or "verified"',
        'g6/expressions.py return_fallback: return "verified"',
    ], offenders


def test_the_enum_exemption_is_not_over_broad(tmp_path):
    """DISCRIMINATION CHECK on the one exemption this guard grants.

    The Enum carve-out must exempt the vocabulary declaration and NOTHING else.
    If it ever swallowed an ordinary field, the guard would go quiet on the exact
    defect it exists to catch -- so plant both in one file and require exactly
    one detection.
    """
    root = tmp_path / "components"
    root.mkdir()
    (root / "mixed.py").write_text(
        "from enum import Enum\n"
        "\n"
        "class CompletionState(str, Enum):\n"          # exempt: real enum
        "    VERIFIED = \"verified\"\n"
        "    QUALIFIED_DRAFT = \"qualified-draft\"\n"
        "\n"
        "class Envelope:\n"                            # caught: base-less class
        "    completion_state: str = \"verified\"\n"
        "\n"
        "class NonEnumerableBase:\n"
        "    pass\n"
        "\n"
        "class Sneaky(NonEnumerableBase):\n"           # caught: name CONTAINS 'Enum'
        "    completion_state: str = \"verified\"\n",
        encoding="utf-8",
    )
    offenders = _failopen_label_defaults(str(root), "components")
    joined = " ".join(offenders)
    assert len(offenders) == 2, (
        "the Enum exemption must cover the enum member and nothing else; "
        "detected: %r" % (offenders,)
    )
    assert "Envelope" in joined
    # THE SUBSTRING EXPLOIT. `NonEnumerableBase` contains "Enum", so the previous
    # substring match exempted `Sneaky` silently. This is the case that was
    # missing: the old discrimination test planted only a REAL enum and a
    # base-less class, so it never touched the path that was actually broken.
    assert "Sneaky" in joined, (
        "a class whose BASE NAME merely contains 'Enum' was silently exempted"
    )
    assert "CompletionState" not in joined
