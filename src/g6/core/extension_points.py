# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Declared T3/self-programming extension points for a harness.

WHAT THIS TYPE IS
-----------------
A DECLARATION. A harness states, on its ``BlockContract``, that it is
extensible and describes the intended shape of that extensibility.

Exactly ONE of its three fields drives runtime behaviour, and only
indirectly: whether ``extension_points`` is non-``None`` at all is the
harness's OPT-IN to receiving extensions. A target harness that declared
``None`` yields ``G6_E_EXTENSION_REJECTED_NOT_OPTED_IN``.

EVERY SURFACE THAT SHIPS EXTENSION CODE MUST ASK
------------------------------------------------
``extension_targets_opted_in`` below is the ONLY implementation of that
question. Every surface that attaches extension code calls it: an MCP
server registering an extension's tools, and a web app mounting its routes.

It lives here, once, deliberately. The first version of this gate was written
into the MCP surface alone; the REST surface imported and mounted
``routes_module`` from an extension whose declared target had never opted in,
because it had its own copy of the loop and no copy of the check. A second
implementation of this predicate is the failure mode, not a convenience.

WHAT THE OPT-IN GATE IS NOT
---------------------------
It is a CONSENT control over ATTACHMENT and nothing more. It is not a
security boundary, and it does not confine what extension code may do:

* An extension that declares no targets is not gated at all (see
  ``extension_targets_opted_in``), so hostile code simply declares none.
* The manifest's ``sandbox`` field is NOT enforced on the registration path.
  Measured on this tree (2026-08-11): a signed, trusted extension declaring
  ``"sandbox": "restricted"`` and ``required_components: []`` runs IN-PROCESS
  during registration, and from there resolves and instantiates an opted-OUT
  harness through ``get_registry().get_class(...)``, reads ``os.environ``,
  lists the working directory and writes files outside its own directory.

The control that actually stops hostile code is signature re-verification in
``mvp.core.extension_loader._gate_extension_execution``, which fails closed on
every ``g6ext.*`` import. Opt-in only stops an ALREADY-TRUSTED extension from
being attached to a harness that never declared itself extensible — it is an
HONESTY control over manifests that declare their targets truthfully.

WHAT THIS TYPE IS NOT
---------------------
``validation_gate`` and ``allowed_mutation_surfaces`` are NOT enforced
controls. Measured on this tree (2026-08-11):

* ``mvp.core.validation_gates`` has exactly one functional importer in all of
  ``components/``, ``bases/`` and ``scripts/`` — this module. Every other
  occurrence is a comment or docstring. Nothing anywhere calls a harness's
  declared gate before mutating that harness.
* ``ExtensionPoints.allowed_mutation_surfaces`` has ZERO production readers.
  Every occurrence outside this module is a declaration site; the only reads
  are tests asserting a component declared what it declared.

The real action-time mutation seams — ``patch_server.server_block._deploy``
(writes and commits into any component's source tree),
``evoskill.evoskill_block._install_generated_skill`` (writes new skill files)
— gate on their own controls (review status, a repo-wide path allowlist,
signature verification, a code-safety scanner) and never consult the target's
``ExtensionPoints``. Two obstacles make wiring them here the wrong move rather
than merely unfinished work:

1. ``allowed_mutation_surfaces`` has no shared grammar. Declared values in
   this tree include abstract labels (``"prompts"``), attribute paths
   (``"CsfAuditLog.record"``), globs (``"rules/*.json"``), source paths
   (``"poka_yoke/skill"``) and prose ("LLM_REPAIR_ATTEMPTS
   schema-validation retry semantics"). There is no total function from those
   strings to a proposed mutation. ``patch_server/mutation_policy.py`` reads a
   REPO-WIDE path-glob allowlist that happens to share the name but not the
   schema or the wiring.
2. Four of the five registered gates return ``True`` unconditionally, so
   calling them at a mutation seam would add a control that cannot refuse.
   See ``mvp.core.validation_gates``.

So this field records intent for a reviewer; it does not stop anything. Wiring
an action-time approval belongs to the approval-authority work (issuance
through ``mvp.core.approval_authority``), not to a construction-time hook on a
frozen dataclass — construction is wiring time, and an approval that fires
when a dataclass is built approves nothing.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from g6.core.validation_gates import is_registered as _gate_name_is_registered


@dataclass(frozen=True)
class ExtensionPoints:
    """What a harness DECLARES about its T3/self-programming surface.

    ``validation_gate`` IS A DECLARATION, NOT AN ENFORCED CONTROL — and the
    declaration is published, so this distinction has to be stated where it
    cannot be missed. MEASURED 2026-08-10 across the whole repo:

    * the only production read of ``validation_gate`` is ``__post_init__``
      below, which asks ``validation_gates.is_registered`` whether the name is
      spelled correctly and NEVER RESOLVES THE CALLABLE AT ALL. It used to
      resolve it and throw it away, which read like enforcement;
    * no code path anywhere invokes a resolved gate — every other resolution of
      ``validation_gates.get`` is in tests;
    * FOUR of the five registered gates (``meta_eval_suite``,
      ``unit_tests_only``, ``unit_plus_codex_review``, ``none``) are bound to
      ``_unimplemented_gate`` and return True unconditionally, and the one with
      a real implementation, ``human_approval``, is never called despite being
      correct. ``validation_gates.is_enforced(name)`` is the machine-readable
      form of this bullet, MEASURED at registration rather than asserted here,
      so it cannot rot as gates are added — prefer it over this prose.

    This matters because ``block_contract`` serialises these fields into public
    contract output and ``scripts/gen_harness_pages.py`` publishes them per
    harness — including ``validation_gate: human_approval`` on the CSF page,
    which reads as "a human signs off here" and is not true today. The
    generated pages carry a matching disclosure.

    If you wire real enforcement, remove this note, the generator's disclosure,
    and the pin in
    ``tests/contract/test_extension_point_gates_are_declarative.py`` in the SAME
    change — that test exists to make sure those three move together.
    """

    training_signal_inputs: tuple[str, ...] = ()
    #: A NAME from ``mvp.core.validation_gates``. Validated for spelling only —
    #: see the module docstring. Nothing resolves or invokes this gate.
    validation_gate: str = "none"
    #: Documentation of which surfaces the harness considers mutable. Not read
    #: by any production code path — see the module docstring.
    allowed_mutation_surfaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A spelling check, and only a spelling check. Do not mistake this line
        # for enforcement. It deliberately asks "is this name registered?"
        # rather than resolving the callable and discarding it, which is what
        # this code used to do — resolving a control you never invoke reads
        # like enforcement and is not.
        if not _gate_name_is_registered(self.validation_gate):
            raise ValueError(
                f"G6_E_CONTRACT_UNKNOWN_VALIDATION_GATE: {self.validation_gate}"
            )


#: Registry names that DECLARE ``extension_points`` in source and can
#: nonetheless never be confirmed as opted in at runtime.
#:
#: NOT an environment problem and NOT fixable by provisioning. Measured on this
#: tree (2026-08-11) over all 270 registry components: 27 resolve and have
#: opted in, 206 resolve and have not, 37 do not resolve at all. Of those 37,
#: 36 fail with ``AIBlock subclass not found: 'g6.<name>'`` — the component's
#: package root exports no ``AIBlock`` subclass, which is a structural property
#: of the source tree and is identical in every environment including the
#: shipped containers. Exactly ONE (``security_gateway``) is environment
#: dependent (``SECURITY_GATEWAY_JWT_SECRET`` unset ⇒ cannot instantiate), and
#: it declares no ``extension_points``, so it is not in this list.
#:
#: The 16 below are the intersection: in the registry by name, declaring
#: ``extension_points`` in source, and permanently unresolvable. They read as
#: "did not opt in" forever. ``BlockContractRegistry.is_registered`` does not
#: rescue them either — it resolves through the same package root and returns
#: False for all 16.
#:
#: THE GATE IS NOT LOOSENED TO ACCOMMODATE THEM, and this is the reasoned
#: choice rather than an omission. These names are not reachable as extension
#: targets through ANY registry path: ``get_registry().invoke(name, op, {})``
#: fails for all 16 with the same ``[G6_E_REGISTRY_CLASS_LOAD]``, while the
#: same call against a resolvable component (``solver``) returns an ordinary
#: op-level error. They are library packages, not harnesses — there is no block
#: object for an extension to attach to. Counting a source declaration as
#: consent would weaken a fail-closed consent control in order to admit
#: extensions whose targets they could not invoke anyway.
#:
#: If a component here ever grows a registry-resolvable block, the test
#: ``test_unresolvable_extension_targets_are_listed_and_still_unresolvable``
#: goes red and this list must shrink.
UNRESOLVABLE_EXTENSION_TARGETS: frozenset[str] = frozenset({
    "app_repair",
    "business_ops",
    "coding_agent_gate",
    "dogfood_scheduler",
    "domain_standards",
    "domain_workflow",
    "founder_os",
    "governance",
    "license_manager",
    "llm_extension",
    "poka_yoke",
    "regulated_ai",
    "reliability_ledger",
    "safety_engine",
    "self_improvement",
    "valuable_labour",
})


def harness_opted_in(harness_id: str, *, registry: Any = None) -> bool:
    """True iff ``harness_id`` names a component that OPTED IN to extensions.

    The documented contract (``docs/product_global_specs/remediation_plans/
    00b_foundational_types.md``): "the harness opts in to receiving extensions
    by declaring a non-null ``extension_points``; any harness with ``None``
    rejects extensions targeting it."

    FAILS CLOSED. An unknown name, a class that cannot be resolved, or a class
    with no ``BlockContract`` all read as "did not opt in".

    Resolution goes through ``registry.get_class``, which INSTANTIATES the
    block. That is not merely a caveat — for 16 named components it is a
    permanent verdict; see ``UNRESOLVABLE_EXTENSION_TARGETS`` for the measured
    cause, the full list, and why the gate is not loosened for them.
    """
    from g6.core.contract_decorator import get_block_contract_attr

    if registry is None:
        from g6.core.registry import get_registry

        registry = get_registry()
    try:
        cls = registry.get_class(harness_id)
    except Exception:  # noqa: BLE001 - any resolution error ⇒ fail closed
        return False
    if cls is None:
        return False
    contract = get_block_contract_attr(cls)
    if contract is None:
        return False
    return contract.extension_points is not None


def extension_targets_opted_in(
    required_components: Iterable[str] | None,
    *,
    registry: Any = None,
) -> bool:
    """True iff EVERY harness this extension targets has opted in.

    ``required_components`` is the extension manifest's declared target set.

    All-or-nothing by construction. Both surfaces attach an extension to a
    single object — one MCP server, one FastAPI app — so there is no way to
    grant it its opted-in targets while withholding the others. One opted-out
    target therefore rejects the whole extension.

    EMPTY ``required_components`` PASSES. An extension that names no harness
    has no opt-in to fail: the gate's subject is the TARGETED harness, and
    there is no target. That is a decision about semantics, not a concession to
    tooling — ``scaffold_extension`` also emits ``"required_components": []``,
    but that is not why the empty case passes and it would be a poor reason:
    the scaffold's output cannot load unsigned anyway, and a hostile author
    writes ``[]`` by hand regardless of what the scaffold emits. The
    consequence is stated plainly in this module's header: opt-in is not the
    control that stops hostile code.

    The answer does not depend on which server is asking. Every declared target
    is checked even when the caller is serving one specific harness, so an
    extension declaring ``["agent_codex", "safety_engine"]`` is rejected on the
    codex path and on the main server alike.
    """
    targets = [str(name) for name in (required_components or [])]
    if not targets:
        return True
    return all(harness_opted_in(name, registry=registry) for name in targets)


__all__ = [
    "UNRESOLVABLE_EXTENSION_TARGETS",
    "ExtensionPoints",
    "extension_targets_opted_in",
    "harness_opted_in",
]
