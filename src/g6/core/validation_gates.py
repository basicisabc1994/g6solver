# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Named validation gate registry for extension points.

READ THIS BEFORE RELYING ON A NAME FROM THIS REGISTRY.

This is a registry of NAMES, most of which have no implementation. Of the five
registered gates, FOUR (``meta_eval_suite``, ``unit_tests_only``,
``unit_plus_codex_review``, ``none``) are unimplemented placeholders that
return ``True`` unconditionally — they are fail-OPEN, and a caller that treats
a ``True`` from one of them as "validated" has validated nothing. Only
``human_approval`` is a real control.

Ask ``is_enforced(name)`` before letting a gate decide anything; it is the
machine-readable form of the paragraph above, so this claim cannot silently
rot as gates are added. It is derived by MEASUREMENT — ``register`` probes
every callable with a request that authorises nothing and records whether it
refused — not by comparing against a known placeholder object, which would
classify any newly written always-true stub as a real control.

No PRODUCTION CALL SITE in ``components/``, ``bases/`` or ``scripts/``
invokes a gate from this registry. One invocation does happen, and the
earlier wording -- "nothing invokes any gate" -- was false because of it:
``register`` probes each callable once, at import time, with the null
request (see ``_null_request_verdict``). That probe IS the measurement
``is_enforced`` rests on; it is confined to this module, and it is why a
gate must not have side effects on an unauthorised request. The single
functional importer is
``mvp.core.extension_points``, which uses ``is_registered`` to spell-check a
declared name. See that module's docstring for why a harness's declared
``validation_gate`` is a declaration rather than an enforced control.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

ValidationGate = Callable[..., Any]

_GATES: dict[str, ValidationGate] = {}

#: Names whose callable was MEASURED refusing the null request at registration
#: time. A name absent from this set cannot refuse anything. Maintained by
#: ``register`` so it cannot drift from ``_GATES``.
_ENFORCED: set[str] = set()


def _unimplemented_gate(*_args: Any, **_kwargs: Any) -> bool:
    """NOT A GATE. Returns True unconditionally — fail-OPEN.

    A placeholder for a named validation strategy that was specified but never
    built. It is registered so that a contract declaring the name validates,
    not because the name does anything. Was called ``_noop_gate``; "noop"
    understated it, because a check that always passes is worse than absent —
    it reads as enforcement in a log and in a reviewer's head.
    """
    return True


def _human_approval_gate(
    *_args: Any,
    approval_id: Any = None,
    scope: str = "",
    run_id: str | None = None,
    consume: bool = True,
    **_kwargs: Any,
) -> bool:
    """Resolve a human approval against the server-side authority. FAIL CLOSED.

    This gate used to be ``_noop_gate`` — it returned True unconditionally, so a
    registry entry literally named ``human_approval`` authorized everything. It
    now requires an ``approval_id`` that the CONTROL PLANE issued for THIS scope;
    a caller can only present an identifier, never assert the decision. An absent,
    fabricated, expired, revoked, consumed or wrong-scope id yields False.

    ⚠ BEFORE YOU WIRE THIS UP, READ THIS. ``scope`` is a PARAMETER, so whoever
    invokes the gate chooses what the approval is checked against. Today that is
    harmless only because NOTHING INVOKES THIS GATE — see
    ``ExtensionPoints``, which resolves gate names to validate spelling and
    discards the callable. The moment a real call site exists, that call site
    must DERIVE the scope server-side from the work actually being done, never
    accept it from the caller and never echo it back out of the request.

    Otherwise this reproduces F-3 exactly: a caller holding a legitimate approval
    for scope A presents it while declaring scope A, and performs work B. The
    authority answers the question it was asked, and the question was the
    caller's to choose. ``account_holder_approval`` fixed the same shape by
    deriving ``job:<domain>:<operation>`` itself; do likewise here.
    """
    from g6.core.approval_authority import get_approval_authority

    if not scope:
        # A human approval that cannot say WHAT was approved authorizes nothing.
        return False
    try:
        authority = get_approval_authority()
    except Exception:
        # Building the authority opens a database, which can fail. A gate that
        # RAISES is not a gate -- the caller sees an error, not a decision. An
        # unavailable authority means we cannot show a human approved this, and
        # "cannot show" must read as "not approved".
        logger.exception("approval authority unavailable; refusing human gate")
        return False
    return authority.check(
        approval_id, scope=scope, run_id=run_id, consume=consume,
    )


#: The three answers a gate can give to the null request. UNMEASURED is not
#: a shade of REFUSED: it means the probe could not hand the gate the
#: request at all, so nothing about this gate has been established.
_AUTHORISED = "authorised"
_REFUSED = "refused"
_UNMEASURED = "unmeasured"

#: The null request, in the two shapes a gate might accept it.
_NULL_REQUEST_SHAPES: tuple[dict[str, Any], ...] = (
    {},
    {"approval_id": None, "scope": "", "run_id": None, "consume": False},
)


def _can_deliver(fn: ValidationGate, kwargs: dict[str, Any]) -> bool:
    """True iff the null request IN THIS SHAPE can actually reach ``fn``.

    Python raises ``TypeError`` for a signature-binding failure BEFORE the
    body runs, and a gate body may raise ``TypeError`` of its own. From the
    call site the two are indistinguishable, so binding is checked against
    the signature separately instead of being inferred from the exception.

    A callable with no introspectable signature counts as UNDELIVERABLE
    rather than as refusing. That ambiguity must not be resolved in favour
    of the strong claim: ``is_enforced`` True asserts that this gate can
    refuse, and an assertion nobody measured is the exact thing this module
    exists to prevent.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    try:
        signature.bind(**kwargs)
    except TypeError:
        return False
    return True


def _null_request_verdict(fn: ValidationGate) -> str:
    """How ``fn`` answers a request that authorises NOTHING.

    The null request carries no approval identifier and no scope: it cannot
    name a thing that was approved, so no honest gate may pass it. A callable
    that returns truthy anyway cannot refuse ANY input and is therefore
    fail-open, whatever it is called and whichever object it is.

    A raise counts as REFUSED. A gate that raises is still not a usable gate
    (``_human_approval_gate``'s docstring says so) but it did not hand out a
    pass, and enforcement status must not be granted for the wrong reason or
    withheld for it either.

    ROUND 2 (BLOCKING-1). This was a two-valued predicate and it CONFLATED
    "the gate refused the null request" with "the probe could not hand the
    null request to this gate at all". A ``TypeError`` raised while BINDING
    the arguments landed in the same ``except`` as a refusal, so an ordinary
    always-true stub -- ``def cost_review_gate(request): return True`` -- was
    recorded as ENFORCED, never having been called at all. Review measured
    four such signatures reading as real controls. That is the precise
    inversion this module's docstring promises cannot happen, arriving
    through the very door built to prevent it, and it failed towards the
    STRONG claim -- worse than the identity check it replaced, which at
    least failed towards the weak one.

    Unmeasured is now its own verdict and never earns the claim.

    Probing at registration means enforcement is MEASURED rather than
    asserted. Identity comparison against ``_unimplemented_gate`` was the
    earlier strategy and it only recognised one specific object: measured on
    this tree, ``def todo_gate(*a, **k): return True`` under a new name,
    ``functools.partial(_unimplemented_gate)`` and
    ``lambda *a, **k: _unimplemented_gate(*a, **k)`` ALL reported as enforcing
    under it. They do not here.
    """
    for kwargs in _NULL_REQUEST_SHAPES:
        if not _can_deliver(fn, kwargs):
            continue
        try:
            return _AUTHORISED if bool(fn(**kwargs)) else _REFUSED
        except Exception:  # noqa: BLE001 - refusing by raising is refusing
            return _REFUSED
    return _UNMEASURED


def register(name: str, fn: ValidationGate) -> ValidationGate:
    """Register ``fn`` under ``name``, MEASURING whether it can refuse.

    ``fn`` is probed once with the null request (see
    ``_null_request_verdict``). ONLY A MEASURED REFUSAL is recorded as
    enforced. A callable that passes the null request has proved it cannot
    refuse; a callable the probe could not CALL has proved nothing at all.
    Both leave the name out of the enforced set, and the second case is
    logged, because a gate nobody can probe is a gate nobody can trust.

    There is no override and no way to assert enforcement a gate does not
    have. ``is_enforced`` exists so that future code can trust it, so the
    next always-true stub someone adds must not be able to read as a real
    control -- whatever its signature.

    Re-registering an existing name RE-MEASURES it. A name downgraded from a
    real gate to a placeholder is discarded from the enforced set rather than
    left stale.

    The probe runs the callable. That is acceptable here because the null
    request is precisely the input that must be refused, and a gate with side
    effects on an unauthorised request is already broken; it is stated so the
    cost is visible rather than discovered.
    """
    normalised = name.strip()
    if not normalised:
        raise ValueError("validation gate name is required")
    _GATES[normalised] = fn
    verdict = _null_request_verdict(fn)
    if verdict == _REFUSED:
        _ENFORCED.add(normalised)
        return fn
    _ENFORCED.discard(normalised)
    if verdict == _UNMEASURED:
        logger.warning(
            "validation gate %r accepts neither shape of the null request, "
            "so it could NOT be probed and is not recorded as enforced. "
            "is_enforced(%r) stays False until the gate accepts a request "
            "carrying no approval id and no scope -- which is the request "
            "it must be able to refuse.",
            normalised,
            normalised,
        )
    return fn


def get(name: str) -> ValidationGate:
    """Return the callable registered under ``name``.

    A returned callable is NOT evidence of a control: four of the five
    registered names return True unconditionally. Check ``is_enforced(name)``
    to find out whether the thing you just fetched can refuse.
    """
    normalised = name.strip()
    if normalised not in _GATES:
        raise KeyError(f"unknown validation gate: {name}")
    return _GATES[normalised]


def is_registered(name: str) -> bool:
    """True iff ``name`` is a known gate name. A spelling check only.

    Says nothing about whether the gate enforces anything — use
    ``is_enforced``. Exists so callers that only need to validate a declared
    name (``mvp.core.extension_points``) do not have to resolve a callable
    they will never invoke.
    """
    return name.strip() in _GATES


def is_enforced(name: str) -> bool:
    """True iff ``name`` maps to a gate that can actually REFUSE.

    False for an unknown name, for every unimplemented placeholder, for
    anything else measured passing the null request at registration time,
    AND for any callable the probe could not deliver the null request to at
    all. Any future code that gates an action on a declared validation gate
    must consult this first and refuse to proceed on False, otherwise it
    inherits a check that always passes.

    What this guarantees precisely: True means the callable WAS CALLED with
    a request carrying no approval id and no scope, and did not authorise
    it. It does not mean the gate is correct, complete, or invoked anywhere:
    no production call site invokes a declared gate at all.

    False is therefore two different things -- measured fail-open, and not
    measurable -- deliberately collapsed, because a caller must treat both
    the same way. The log says which: an unprobeable gate warns at
    registration.
    """
    return name.strip() in _ENFORCED


def list_names() -> list[str]:
    """All registered names, enforcing and placeholder alike."""
    return sorted(_GATES)


def list_enforced() -> list[str]:
    """Only the names backed by a real, refusing implementation."""
    return sorted(_ENFORCED)


# UNIMPLEMENTED placeholders — every one returns True unconditionally. They are
# registered so that contracts naming them pass the spelling check in
# ``ExtensionPoints``; none of them validates anything. If one of these ever
# becomes load-bearing it must be implemented BEFORE it is relied on, exactly
# as ``human_approval`` was.
for _name in (
    "meta_eval_suite",
    "unit_tests_only",
    "unit_plus_codex_review",
    "none",
):
    register(_name, _unimplemented_gate)

# ``human_approval`` is AUTHORITY, not a placeholder: it is the gate that says a
# human signed off. It resolves against the server-side approval authority and
# fails closed. It is the ONLY enforcing entry in this registry — and it has no
# invocation anywhere outside tests, so today it enforces nothing in practice
# either. Wiring it to a real action-time seam is approval-issuance work, not a
# change to this module.
register("human_approval", _human_approval_gate)


__all__ = [
    "ValidationGate",
    "get",
    "is_enforced",
    "is_registered",
    "list_enforced",
    "list_names",
    "register",
]
