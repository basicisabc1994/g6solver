# SPDX-License-Identifier: Apache-2.0
"""Who repairs each refusal G6 writes, declared where the refusal is written (plan revision 8, the repair loop).

Every ``G6_E_*`` code G6 writes into a reply is declared once, for the component or layer that writes it:

* a component declares its codes as ``__g6_refusals__`` in its own package's ``__init__.py``: a dict literal, code ->
  ``fix_by``, or ``(fix_by, words)``, or ``(fix_by, words, setup)``;
* a component whose package a certificate binds (``BOUND_COMPONENTS``) is declared here instead, in ``OVERLAY``;
* a code written outside every component (G6's MCP layer, core, the harness, a family's shared module) is declared
  here in ``LAYERS``, beside the top folders (layers) that write it: ``(fix_by, where[, words[, setup]])``.

``fix_by`` is one of:

* ``input``: the agent fixes its own call; the refusal names the field;
* ``person``: only the person can (a consent, an allowance, an approval, their own work or files, an install,
  isolation, anything outward);
* ``tool:<tool or component.op>``: G6's own next step, which acts on nothing outside G6;
* ``retry``: a passing condition; the same call later, a bounded number of times;
* ``none``: not repairable here; tell the person.

``words`` are G6's words for the step; a class's own (``WORDS``) when none are given, or a shared phrase by its name
(``PHRASES``). ``setup`` is a step only the person takes where G6 runs (``g6 doctor``).

A census test (``tests/core/test_refusals.py``) holds every code G6 writes into a reply (not merely compares) to
exactly one declaration for the component or layer that writes it, so a new code without one fails CI. At run time a
code no declaration names resolves to ``person``. :func:`package_declarations` reads the packages statically (``ast``;
no component is imported); the corpus (``g6.skills.corpus``) carries what it read, so ``g6_select`` and the MCP
repair block read only the corpus and this module.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLASSES = ("input", "person", "retry", "none")
#: Each class's own words, used where a declaration gives none.
WORDS = {
    "input": "The input did not fit: the refusal names the field. Fix that field in your own call and make it again; "
             "for an op, g6_describe lists its fields. Never send the same call unchanged.",
    "person": "Only the person can resolve this: tell them what the reply says, and stop. Never work around it.",
    "retry": "A passing condition: make the same call once more, later; if it is refused again, tell the person.",
    "none": "G6 cannot repair this here: tell the person what the reply says. Never work around it.",
    "tool": "Make G6's own next step named here.",
}
_ASK = "Ask the person; offer no other way to agree, and never answer for them."
#: Shared words a declaration names instead of writing them out.
PHRASES = {
    "wait": "A gate decision, an approval, a confirmation or an authority that only a person gives: tell the person "
            "what the reply says. Never rework the act or its input until the gate stops refusing it.",
    "ask": "The person declined or cancelled, gave no name, or the client has no dialog. " + _ASK,
    "no_model": "No model is configured for this op. Do the step yourself, then ask for the op that checks it.",
}
#: The one person class whose dialog the agent opens itself: the person decides in it. No other refusal makes a
#: call to it.
DIALOG = {"G6_E_CLOUD_CONSENT_REQUIRED": "g6_cloud_grant"}
#: Components whose package a certificate binds: their declarations live in ``OVERLAY``, not in the package.
BOUND_COMPONENTS = ("action_gating", "action_logic", "adapt_automl", "adapt_bayesian", "adapt_physical_ai",
                    "adapt_sklearn", "csf", "duty_of_care", "epistemic_integrity", "evidence", "guards", "invariants")
UNDECLARED = ("G6 has no declaration for this refusal, so it is not known to be one you can fix: tell the person "
              "what it says. Never work around it.")

# Open suffixes cannot be enumerated. Each writer declares the whole family and its repair class.
FAMILIES: dict[tuple[str, str], tuple[str, str]] = {
    ("algorithm_design/autonomous.py", "G6_E_ALGO_{}"): ("person", "component:algorithm_design"),
    ("agents/agent_nanoclaw/mailbox.py", "G6_E_MAILBOX_{}"): ("person", "layer:agents"),
}


@dataclass(frozen=True)
class Refusal:
    """One declaration: ``owner`` is ``component:<name>`` or ``layer:<names>``, "" when nothing declares the code."""

    code: str
    fix_by: str
    words: str
    setup: str = ""
    owner: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.owner)

    @property
    def kind(self) -> str:
        """The class: ``tool`` for a ``tool:<name>`` declaration."""
        return "tool" if self.fix_by.startswith("tool:") else self.fix_by


def _valid(fix_by: Any) -> bool:
    return isinstance(fix_by, str) and (fix_by in CLASSES or (fix_by.startswith("tool:") and len(fix_by) > 5))


def parse(code: str, value: Any, owner: str) -> Refusal:
    """A declaration's value as a :class:`Refusal`; ``ValueError`` naming the code when it is not one."""
    parts = (value,) if isinstance(value, str) else tuple(value) if isinstance(value, (tuple, list)) else ()
    if not 1 <= len(parts) <= 3 or not _valid(parts[0]) or not all(isinstance(p, str) for p in parts):
        raise ValueError(f"{code}: a declaration is fix_by, (fix_by, words) or (fix_by, words, setup)")
    fix_by = parts[0]
    words = parts[1] if len(parts) > 1 and parts[1] else WORDS["tool" if fix_by.startswith("tool:") else fix_by]
    return Refusal(code, fix_by, PHRASES.get(words, words), parts[2] if len(parts) > 2 else "", owner)


def layer(code: str) -> Refusal:
    row = LAYERS[code]
    return parse(code, (row[0], *row[2:]), "layer:" + row[1])


def read_package(path: Path) -> dict[str, Any] | None:
    """The ``__g6_refusals__`` dict literal in a package's ``__init__.py``, read with ``ast`` (nothing imported);
    ``None`` when it declares none. ``ValueError`` naming the code for a code given twice or a value that is no
    declaration."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
            else []
        if any(isinstance(t, ast.Name) and t.id == "__g6_refusals__" for t in targets):
            if not isinstance(node.value, ast.Dict):
                raise ValueError(f"{path.name}: __g6_refusals__ is a dict literal")
            keys = [ast.literal_eval(k) for k in node.value.keys]
            twice = sorted({k for k in keys if keys.count(k) > 1})
            if twice:
                raise ValueError(f"{', '.join(twice)} declared twice")
            out = ast.literal_eval(node.value)
            for code, value in out.items():
                parse(code, value, "check")
            return out
    return None


def _package_paths() -> dict[str, Path]:
    """Each built-in component's package ``__init__.py``, from the registry's module names (no import)."""
    from g6.core.registry import get_registry

    root = Path(__file__).resolve().parents[1]                   # the g6 package
    out = {}
    for name, ref in get_registry().refs.items():
        parts = ref.module.split(":", 1)[0].split(".")
        path = root.joinpath(*parts[1:], "__init__.py")
        if ref.source == "builtin" and parts[0] == "g6" and path.is_file():
            out[name] = path
    return out


def package_declarations() -> dict[str, dict[str, Refusal]]:
    """Every built-in component's ``__g6_refusals__``, read statically from its package."""
    out = {}
    for name, path in sorted(_package_paths().items()):
        found = read_package(path)
        if found is not None:
            out[name] = {code: parse(code, value, f"component:{name}") for code, value in sorted(found.items())}
    return out


def component_declarations() -> dict[str, dict[str, Refusal]]:
    """Every component's declarations: its package's, or ``OVERLAY``'s for a bound package."""
    out = package_declarations()
    for name, codes in OVERLAY.items():
        out[name] = {code: parse(code, value, f"component:{name}") for code, value in sorted(codes.items())}
    return dict(sorted(out.items()))


def as_data(declared: dict[str, dict[str, Refusal]]) -> dict[str, dict[str, list[str]]]:
    """Declarations as the corpus keeps them: component -> code -> [fix_by, words, setup]."""
    return {name: {code: [r.fix_by, r.words, r.setup] for code, r in codes.items()} for name, codes in declared.items()}


def resolve(code: str, component: str = "", declared: dict[str, dict[str, list[str]]] | None = None) -> Refusal:
    """Who repairs ``code``, from the declarations alone. With a ``component`` named by the reply, its own
    declaration, else a layer's; with none named, a layer's, else the components' when every one that declares the
    code agrees. A code nothing declares for that writer is ``person``: G6 does not know it to be fixable."""
    if declared is None:
        from g6.skills import corpus

        declared = corpus.load()["refusals"]
    own = declared.get(component, {}) if component else {}
    if code in own:
        return Refusal(code, *own[code], owner=f"component:{component}")
    if code in LAYERS:
        return layer(code)
    if not component:
        rows = {name: codes[code] for name, codes in sorted(declared.items()) if code in codes}
        if rows and len({row[0] for row in rows.values()}) == 1:
            return Refusal(code, *next(iter(rows.values())), owner="component:" + " ".join(rows))
    for (_, pattern), (fix_by, owner) in FAMILIES.items():
        prefix, suffix = pattern.split("{}")
        middle = code[len(prefix):len(code) - len(suffix) if suffix else None]
        if code.startswith(prefix) and code.endswith(suffix) and middle and all(
                c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in middle) and (
                not component or owner == f"component:{component}"):
            return parse(code, fix_by, owner)
    return Refusal(code, "person", UNDECLARED)


# -- declarations for bound components and for layers ----------------------------------------------------------
OVERLAY: dict[str, dict[str, Any]] = {
    "action_gating": {
        "G6_E_ACTION_GATING_ACTION_INVALID": "input",
        "G6_E_ACTION_GATING_ACTION_REQUIRED": "input",
        "G6_E_ACTION_GATING_BLOCKED": ("person", "wait"),
        "G6_E_ACTION_UNPROFILED": "none",
        "G6_E_AUDIT_CHAIN_BROKEN": "person",
        "G6_E_AUDIT_CHAIN_INTACT": "none",
        "G6_E_AUDIT_REQUIRED": "person",
        "G6_E_AUDIT_WRITE_FAILED": "person",
        "G6_E_AUTHORITY_UNVERIFIED": ("person", "wait"),
        "G6_E_CONFIRMATION_REQUIRED": ("person", "wait"),
        "G6_E_CONTROL_NOT_EVALUATED": "none",
        "G6_E_CSF_BOUND_EXCEEDED": "none",
        "G6_E_CSF_CHECK_FAILED": "none",
        "G6_E_CSF_MODEL_BOUND": "none",
        "G6_E_CSF_MODEL_REQUIRED": "person",
        "G6_E_DEGRADED_ASSURANCE_BLOCKS_EXECUTION": "none",
        "G6_E_DURABLE_AUDIT_REQUIRED": "person",
        "G6_E_DURABLE_POLICY_AUDIT_UNAVAILABLE": "person",
        "G6_E_DURABLE_POLICY_DENIED": "person",
        "G6_E_FACTUAL_PRECONDITION_UNSUPPORTED": "person",
        "G6_E_GATE_OK": "none",
        "G6_E_GATE_TOKEN_EXPIRED": "person",
        "G6_E_GATE_TOKEN_INVALID": "person",
        "G6_E_GATE_TOKEN_REQUIRED": "person",
        "G6_E_GATE_TOKEN_SCOPE": "person",
        "G6_E_GLOBAL_POLICY_REQUIRED": "person",
        "G6_E_GLOBAL_SAFETY_BLOCKED": "person",
        "G6_E_HARM_JUDGE": "none",
        "G6_E_HARM_JUDGE_FAILED": "none",
        "G6_E_KEY_FILE_APPROVED": "none",
        "G6_E_MITIGATION_REQUIRED": "person",
        "G6_E_OPERATION_BUDGET": "person",
        "G6_E_OPERATION_BUDGET_EXHAUSTED": "person",
        "G6_E_OPERATION_DRY_RUN": "none",
        "G6_E_OPERATION_INPUT_INVALID": "input",
        "G6_E_OPERATION_LEDGER_FAILED": "none",
        "G6_E_OPERATION_NOT_PERMITTED": "none",
        "G6_E_OPERATION_POLICY_INVALID": "person",
        "G6_E_OPERATION_POLICY_MISSING": "person",
        "G6_E_OPERATION_REFUSED": "none",
        "G6_E_OPERATION_RESERVATION_MISSING": "none",
        "G6_E_OPERATION_RESERVED": "none",
        "G6_E_OPERATION_UNKNOWN": "person",
        "G6_E_QA_FAILED": "none",
        "G6_E_RESERVED_ARGUMENT": "none",
        "G6_E_RESIDUAL_RISK_HIGH": "none",
        "G6_E_REVIEW_CREATION_FAILED": "none",
        "G6_E_RISK_UNKNOWN": "person",
        "G6_E_SAFEGUARDS_AUDIT_FAILED": "person",
        "G6_E_SAFEGUARDS_EVALUATED": "none",
        "G6_E_SAFEGUARDS_FAILED": "none",
        "G6_E_SAFEGUARDS_INACTIVE": "none",
        "G6_E_SCENARIO_REQUIRED": "person",
        "G6_E_SCOPE_DECISION_MISSING": "person",
        "G6_E_SCOPE_EVIDENCE_REFUTED": "person",
        "G6_E_SCOPE_EXPIRED": "person",
        "G6_E_SCOPE_FORBIDDEN_ACTION": "person",
        "G6_E_SCOPE_INVALID": "person",
        "G6_E_SCOPE_NOTHING_OBSERVED": "person",
        "G6_E_SCOPE_OUTSIDE": "person",
        "G6_E_SCOPE_RECORD_DRAFT": "none",
        "G6_E_SCOPE_RECORD_REFUSED": "person",
        "G6_E_SCOPE_RISK_CEILING": "person",
        "G6_E_SCOPE_WITHIN": "none",
        "G6_E_SECRET_READ": "none",
        "G6_E_SECRET_WRITE": "none",
        "G6_E_WEAK_SIGNAL_ACKNOWLEDGED": "none",
    },
    "action_logic": {
        "G6_E_ACTION_COLLISION": "none",
        "G6_E_ACTION_LOGIC_FIELD_REQUIRED": "input",
        "G6_E_ACTION_LOGIC_INVALID_FIELD": "input",
        "G6_E_BALLISTIC_ACTION_BLOCKED": "person",
        "G6_E_CRISIS_MONITORING_DEGRADED": "person",
        "G6_E_CRISIS_MONITORING_UNKNOWN": "person",
        "G6_E_DISCONFIRMING_EVIDENCE_PENDING": "person",
        "G6_E_FALSIFIED_HYPOTHESIS_REUSED": "none",
        "G6_E_NO_FUTURE_HORIZON": "none",
        "G6_E_THRESHOLD_ALREADY_BREACHED": "none",
        "G6_E_UNADDRESSED_WEAK_SIGNAL": "none",
    },
    "adapt_bayesian": {
        "G6_E_ML_ACTION_GATE_BLOCKED": "person",
    },
    "csf": {
        "G6_E_CSF_BOUND_EXCEEDED": "none",
        "G6_E_CSF_ENCODING_MISMATCH": "none",
        "G6_E_CSF_EPSILON_INVALID": "input",
        "G6_E_CSF_GIT_FAILED": "person",
        "G6_E_CSF_GIT_OUTSIDE_ROOT": "person",
        "G6_E_CSF_GIT_RESET_DISABLED": "person",
        "G6_E_CSF_GIT_UNAVAILABLE": "person",
        "G6_E_CSF_GIT_UNSAFE": "person",
        "G6_E_CSF_INPUT_INVALID": "input",
        "G6_E_CSF_LTL_PARSE_ERROR": "input",
        "G6_E_CSF_MODEL_BOUND": "none",
        "G6_E_CSF_MODEL_INVALID": "input",
        "G6_E_CSF_POLICY_AUTHORITY": "person",
        "G6_E_CSF_POLICY_INPUT": "input",
        "G6_E_CSF_PROPERTY_NOT_ENCODABLE": "none",
        "G6_E_CSF_PROPERTY_VIOLATED": "none",
        "G6_E_CSF_ROLLBACK_INFEASIBLE": "none",
        "G6_E_CSF_SOLVER_DECISION": "none",
        "G6_E_CSF_STATIC_CLASSIFICATION": "none",
        "G6_E_CSF_TRACE_FAILED": "none",
        "G6_E_CSF_TRACE_INPUT": "input",
        "G6_E_CSF_TRACE_SUPPORT": "none",
    },
    "epistemic_integrity": {
        "G6_E_CERTAINTY_WEAK_EVIDENCE": "none",
        "G6_E_EPISTEMIC_INTEGRITY_EVIDENCE_INVALID": "input",
        "G6_E_EPISTEMIC_INTEGRITY_MESSAGE_REQUIRED": "input",
        "G6_E_MARKER_CHECK_FAILED": "none",
        "G6_E_MARKER_INPUT": "input",
    },
    "evidence": {
        "G6_E_EVIDENCE_BINDING_ARTEFACT": "none",
        "G6_E_EVIDENCE_BINDING_CHECK": "none",
        "G6_E_EVIDENCE_BINDING_PREDICATE": "none",
        "G6_E_EVIDENCE_BINDING_STANDARD": "none",
        "G6_E_EVIDENCE_BINDING_UNREADABLE": "none",
        "G6_E_EVIDENCE_CONFLICTING_RECORD": "none",
        "G6_E_EVIDENCE_KIND_NOT_PROMOTED": "none",
        "G6_E_EVIDENCE_KIND_UNMAPPED": "none",
        "G6_E_EVIDENCE_NO_ARTEFACT": "none",
        "G6_E_EVIDENCE_NO_CHECK": "none",
        "G6_E_EVIDENCE_NO_ENVIRONMENT": "none",
        "G6_E_EVIDENCE_NO_INDEPENDENCE": "none",
        "G6_E_EVIDENCE_NO_MODEL": ("tool:g6_select", "no_model"),
        "G6_E_EVIDENCE_NO_NEGATIVE_CONTROL": "none",
        "G6_E_EVIDENCE_NO_ORDER": "none",
        "G6_E_EVIDENCE_NO_RESULT": "none",
        "G6_E_EVIDENCE_NO_SCOPE": "person",
        "G6_E_EVIDENCE_NO_STANDARD": "none",
        "G6_E_EVIDENCE_OTHER_ARTEFACT": "none",
        "G6_E_EVIDENCE_OTHER_ORDER": "none",
        "G6_E_EVIDENCE_OTHER_PRODUCER": "none",
        "G6_E_EVIDENCE_OTHER_STANDARD": "none",
        "G6_E_EVIDENCE_RUN_NOT_BELIEVED": "none",
    },
    "guards": {
        "G6_E_PERSONAL_DATA_FINDING_LIMIT": "person",
        "G6_E_PERSONAL_DATA_TOO_LARGE": "person",
    },
}
LAYERS: dict[str, tuple[str, ...]] = {
    "G6_E_AGENT_CHANGE_FLAGGED": ("none", "bundles"),
    "G6_E_BLOCKED": ("none", "bundles core mcp skills",
        "Read the warning card: it says why and what can be done. Tell the person."),
    "G6_E_BOOKKEEPING_PROBLEM": ("none", "bundles"),
    "G6_E_BUILD_BACKEND": ("none", "code"),
    "G6_E_BUILD_CHECK_FAILED": ("none", "code"),
    "G6_E_BUILD_DRAFT": ("none", "code"),
    "G6_E_BUILD_INPUT": ("input", "code"),
    "G6_E_BUILD_PATH": ("none", "code"),
    "G6_E_BUILD_SIZE": ("none", "code"),
    "G6_E_BUILD_WRITE": ("none", "code"),
    "G6_E_BUNDLE_FAILED": ("none", "bundles"),
    "G6_E_CERTIFICATE_CRLF": ("none", "core"),
    "G6_E_CLOUD_CALLER_UNSUPPORTED": ("person", "core"),
    "G6_E_CLOUD_CONSENT": ("tool:g6_cloud_grants", "mcp", "Read which sources are shared with which providers."),
    "G6_E_CLOUD_CONSENT_INPUT": ("person", "mcp",
        "The grant named a source or provider G6 does not take: ask the person which source and provider they mean, as "
        "the refusal lists them. Never grant for the person."),
    "G6_E_CLOUD_CONSENT_REQUIRED": ("person", "core harness mcp",
        "The reply was withheld until the person shares the source with your provider. Call g6_cloud_grant: the person "
        "answers in the client's dialog. Never grant for the person; if they decline, go on with local work."),
    "G6_E_CLOUD_CONSENT_STATE": ("none", "core",
        "G6 could not read its sharing state, so nothing was shared. Tell the person."),
    "G6_E_CODE_CHECKER_NOT_INSTALLED": ("person", "code"),
    "G6_E_CODE_CONFIG_INVALID": ("person", "code"),
    "G6_E_CODE_NO_ROOT": ("none", "code"),
    "G6_E_CODE_OUTSIDE_ROOT": ("person", "code"),
    "G6_E_CODE_PATH_INVALID": ("input", "code"),
    "G6_E_COMPLIANCE_GAP": ("none", "bundles"),
    "G6_E_COMPOSITION_EMPTY": ("input", "core"),
    "G6_E_CONTRACT_UNKNOWN_FAILURE_MODE": ("none", "core"),
    "G6_E_CONTRACT_UNKNOWN_VALIDATION_GATE": ("none", "core"),
    "G6_E_DEFECT_NOT_REPRODUCED": ("none", "harness",
        "The loop stopped on a defect the reviewer named and no test could show. Show the reply's report to the person "
        "as it is."),
    "G6_E_DOCTOR_REPORT_DRAFT": ("none", "cli"),
    "G6_E_DOCTOR_REPORT_DRY_RUN": ("none", "cli"),
    "G6_E_DOCTOR_REPORT_NO_TRACEBACK": ("none", "cli"),
    "G6_E_DOCTOR_REPORT_WRITE_FAILED": ("none", "cli"),
    "G6_E_DOCTOR_REPORT_WRITTEN": ("none", "cli"),
    "G6_E_DUTY_OF_CARE_CHECK_FAILED": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_CRISIS": ("person", "jobs"),
    "G6_E_DUTY_OF_CARE_DIRECTORY": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_DIRECTORY_STALE": ("person", "jobs"),
    "G6_E_DUTY_OF_CARE_DIRECTORY_UNAVAILABLE": ("person", "jobs"),
    "G6_E_DUTY_OF_CARE_INSTRUMENT_LIST": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_INSTRUMENT_UNAVAILABLE": ("person", "jobs"),
    "G6_E_DUTY_OF_CARE_LICENCE_UNSETTLED": ("person", "jobs"),
    "G6_E_DUTY_OF_CARE_MISSING_INPUT": ("input", "jobs"),
    "G6_E_DUTY_OF_CARE_NO_LISTED_WORDS": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_OUT_OF_RANGE": ("input", "jobs"),
    "G6_E_DUTY_OF_CARE_REGION_NOT_COVERED": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_RESPONSE_REFUSED": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_SCORE_COMPUTED": ("none", "jobs"),
    "G6_E_DUTY_OF_CARE_UNKNOWN_INSTRUMENT": ("input", "jobs"),
    "G6_E_EXECUTION_ARTIFACT_UNAVAILABLE": ("none", "harness",
        "The copy G6 runs the tests in held something it cannot run with (a cache or an input it does not take). Show "
        "the reply's report to the person as it is."),
    "G6_E_FOCUS_NOT_FOUND": ("none", "bundles"),
    "G6_E_GIT_COMMAND_FAILED": ("none", "mcp", "git failed on the repository: tell the person what the refusal says."),
    "G6_E_GIT_REPO_DIRTY": ("person", "mcp",
        "The repository has changes that are not committed, and a mission starts on a clean one: ask the person "
        "whether to commit or set aside their changes. Never commit, stash or discard them yourself."),
    "G6_E_GIT_SUBMODULE_REFUSED": ("person", "core",
        "The repository holds a submodule that was set up, which G6 does not run tests in: tell the person."),
    "G6_E_HARNESS_ANALYTICS": ("none", "harness"),
    "G6_E_HARNESS_ASSERTION": ("none", "harness"),
    "G6_E_HARNESS_BROKEN_CHECK": ("none", "harness"),
    "G6_E_HARNESS_CASES": ("none", "harness"),
    "G6_E_HARNESS_CERTIFICATE_REFUSED": ("none", "harness"),
    "G6_E_HARNESS_CHECK_FAILED": ("none", "harness"),
    "G6_E_HARNESS_CRITERIA": ("none", "harness"),
    "G6_E_HARNESS_EXECUTOR_FAILED": ("none", "harness",
        "The executor failed while it ran: tell the person what the reply says."),
    "G6_E_HARNESS_GAPS": ("none", "harness"),
    "G6_E_HARNESS_NO_EXECUTOR": ("none", "harness"),
    "G6_E_HARNESS_OVERRIDE": ("none", "harness"),
    "G6_E_HARNESS_REMEMBERED_FAILURE": ("none", "harness"),
    "G6_E_HARNESS_REPORT": ("none", "harness"),
    "G6_E_HARNESS_RUN": ("none", "harness"),
    "G6_E_HARNESS_STANDARD": ("none", "harness"),
    "G6_E_HARNESS_STEPS": ("none", "harness"),
    "G6_E_HARNESS_SUITE": ("none", "harness"),
    "G6_E_HTTP_FAILED": ("none", "core"),
    "G6_E_HTTP_MISMATCH": ("none", "core"),
    "G6_E_HTTP_REFUSED": ("none", "core"),
    "G6_E_HTTP_ROBOTS": ("none", "core"),
    "G6_E_HTTP_STATUS": ("none", "core"),
    "G6_E_HTTP_TOO_LARGE": ("person", "core"),
    "G6_E_HUMAN_APPROVAL_REQUIRED": ("person", "mcp",
        "Only the person can approve this. Ask the person; offer no other way to agree, and never answer for them."),
    "G6_E_INPUT_VALIDATION": ("input", "agents bundles core harness mcp skills",
        "The input did not fit: the refusal names the field. For an op, read g6_describe; for a tool, its description. "
        "Fix the field and call again."),
    "G6_E_INVOKE_FAILED": ("none", "core",
        "The op failed while it ran, which does not say the input was wrong: tell the person what the refusal says."),
    "G6_E_ISOLATION_UNAVAILABLE": ("person", "bundles core",
        "No operating-system boundary can confine the run here. Tell the person: g6 doctor says what this computer "
        "needs. Never allow an unconfined run yourself.",
        "g6 doctor"),
    "G6_E_KEY_MISSING": ("person", "core"),
    "G6_E_KNOWLEDGE": ("tool:g6_knowledge_sources", "mcp",
        "Read which sources are indexed and shared, then search again."),
    "G6_E_KNOWLEDGE_BACKEND_NOT_ACCEPTED": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_DELETION_PENDING": ("person", "_context_engine"),
    "G6_E_KNOWLEDGE_DESIGNATION": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_EXTRACTION_NOT_SENT": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_FAILED": ("none", "mcp"),
    "G6_E_KNOWLEDGE_FUSION_BUDGET": ("person", "_context_engine"),
    "G6_E_KNOWLEDGE_FUSION_CANDIDATES": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_GRAPH": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_INDEX": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_INDEX_BACKEND": ("none", "knowledge"),
    "G6_E_KNOWLEDGE_INDEX_FAILED": ("none", "_context_engine knowledge"),
    "G6_E_KNOWLEDGE_INDEX_QUERY": ("none", "knowledge"),
    "G6_E_KNOWLEDGE_INDEX_READ_ONLY": ("none", "knowledge"),
    "G6_E_KNOWLEDGE_INDEX_TIMEOUT": ("person", "knowledge"),
    "G6_E_KNOWLEDGE_INDEX_UNAVAILABLE": ("person", "knowledge"),
    "G6_E_KNOWLEDGE_INPUT": ("input", "_context_engine"),
    "G6_E_KNOWLEDGE_JOB": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_NOT_PUBLISHED": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_OVER_BUDGET": ("person", "knowledge"),
    "G6_E_KNOWLEDGE_PACK": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_PREVIEW_CHANGED": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_PREVIEW_USED": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_REFRESH": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_SCOPE": ("person", "_context_engine"),
    "G6_E_KNOWLEDGE_SOURCE": ("none", "_context_engine mcp"),
    "G6_E_KNOWLEDGE_STATE": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_SUPERSEDED": ("none", "_context_engine"),
    "G6_E_KNOWLEDGE_TOP_K": ("none", "knowledge"),
    "G6_E_KNOWLEDGE_UNSUPPORTED": ("person", "_context_engine"),
    "G6_E_LIBRARY_CATALOGUE": ("none", "workspace"),
    "G6_E_LIBRARY_CORRUPT": ("none", "workspace"),
    "G6_E_LIBRARY_EXTRA_VALUE": ("person", "workspace"),
    "G6_E_LIBRARY_INPUT_INVALID": ("input", "workspace"),
    "G6_E_LIBRARY_INSTALL_PARTIAL": ("person", "workspace"),
    "G6_E_LIBRARY_KIND_MISMATCH": ("none", "workspace"),
    "G6_E_LIBRARY_LISTED": ("none", "workspace"),
    "G6_E_LIBRARY_MISSING_VALUE": ("input", "workspace"),
    "G6_E_LIBRARY_NAME_TAKEN": ("input", "workspace"),
    "G6_E_LIBRARY_NOT_FOUND": ("none", "workspace"),
    "G6_E_LIBRARY_READ": ("none", "workspace"),
    "G6_E_LIBRARY_RENDERED": ("none", "workspace"),
    "G6_E_LIBRARY_RETIRED": ("none", "workspace"),
    "G6_E_LIBRARY_STORED": ("none", "workspace"),
    "G6_E_LIBRARY_UNKNOWN_VERSION": ("input", "workspace"),
    "G6_E_LLM_BAD_MODEL": ("person", "core"),
    "G6_E_LLM_DISABLED": ("tool:g6_select", "agents core",
        "No model is configured for G6. Do the step yourself, then ask for the op that checks it."),
    "G6_E_LLM_HOST_FAILED": ("person", "core"),
    "G6_E_LLM_NO_IMAGES": ("person", "core"),
    "G6_E_LLM_RATE_LIMITED": ("retry", "core"),
    "G6_E_LLM_REDIRECT_REFUSED": ("person", "core"),
    "G6_E_LLM_REPLY_TOO_LARGE": ("person", "core"),
    "G6_E_LLM_TIMEOUT": ("person", "core"),
    "G6_E_LOOP_ACT_FAILED": ("none", "mcp"),
    "G6_E_LOOP_BAD_SETTINGS": ("person", "mcp"),
    "G6_E_LOOP_BUSY": ("tool:metaharness_job", "harness",
        "Another step of this session is running: wait for its result, then call again."),
    "G6_E_LOOP_CHILD_ENV": ("person", "mcp"),
    "G6_E_LOOP_DEMOTED": ("none", "mcp"),
    "G6_E_LOOP_ENROLLED": ("none", "mcp"),
    "G6_E_LOOP_FROZEN_CHANGED": ("none", "mcp"),
    "G6_E_LOOP_IMPORT_PATH": ("none", "mcp"),
    "G6_E_LOOP_NOT_A_FOLDER": ("none", "mcp"),
    "G6_E_LOOP_NOT_ENROLLED": ("person", "mcp",
        "No domain is enrolled: only the person enrols one, with g6_improve_enrol, in the client's dialog."),
    "G6_E_LOOP_NOT_PAUSED": ("person", "mcp"),
    "G6_E_LOOP_PAUSED": ("person", "mcp",
        "The loop is paused: the person reads why and may lift it with g6_improve_resume, in the client's dialog."),
    "G6_E_LOOP_RECORDS_UNREADABLE": ("none", "mcp"),
    "G6_E_LOOP_RESUMED": ("none", "mcp"),
    "G6_E_LOOP_RUNNER_ALIVE": ("none", "mcp"),
    "G6_E_LOOP_STARTED": ("none", "mcp"),
    "G6_E_LOOP_START_FAILED": ("none", "mcp"),
    "G6_E_LOOP_START_STOPPED": ("person", "mcp"),
    "G6_E_LOOP_STATUS": ("none", "mcp"),
    "G6_E_LOOP_STOP_ASKED": ("none", "mcp"),
    "G6_E_LOOP_STOP_FAILED": ("none", "mcp"),
    "G6_E_LOOP_STOP_NOTE_FAILED": ("none", "mcp"),
    "G6_E_LOOP_STOP_RUNNER_UNREADABLE": ("none", "mcp"),
    "G6_E_LOOP_TERMS_CHANGED": ("person", "mcp",
        "What the person read is no longer what would start, so nothing started: the person reads the terms as they "
        "stand now. Ask the person; offer no other way to agree, and never answer for them."),
    "G6_E_LOOP_WAITING_ON_PERSON": ("person", "harness",
        "G6's review loop waits for the person's answer to the question that was asked. Ask the person; offer no other "
        "way to agree, and never answer for them."),
    "G6_E_MEDIA_BACKEND_FAILED": ("none", "media"),
    "G6_E_MEDIA_BACKEND_UNAVAILABLE": ("person", "media"),
    "G6_E_MEDIA_BAD_NAME": ("input", "media"),
    "G6_E_MEDIA_BLANK": ("none", "media"),
    "G6_E_MEDIA_CLIPPED": ("none", "media"),
    "G6_E_MEDIA_DESCRIBED": ("none", "media"),
    "G6_E_MEDIA_DURATION_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_EXISTS": ("none", "media"),
    "G6_E_MEDIA_EXTRA_MISSING": ("person", "media"),
    "G6_E_MEDIA_FORMAT_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_INPUT_INVALID": ("input", "media"),
    "G6_E_MEDIA_INSPECTED": ("none", "media"),
    "G6_E_MEDIA_LOUDNESS_MISSED": ("none", "media"),
    "G6_E_MEDIA_MEASURED": ("none", "media"),
    "G6_E_MEDIA_MESH_READ": ("none", "media"),
    "G6_E_MEDIA_NO_OUTPUT": ("none", "media"),
    "G6_E_MEDIA_PAID_NOT_ALLOWED": ("person", "media"),
    "G6_E_MEDIA_PARSED": ("none", "media"),
    "G6_E_MEDIA_PENDING": ("person", "media"),
    "G6_E_MEDIA_PITCH_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_PROBED": ("none", "media"),
    "G6_E_MEDIA_READBACK_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_READ_BACK": ("none", "media"),
    "G6_E_MEDIA_REPORTED": ("none", "media"),
    "G6_E_MEDIA_SESSION": ("none", "media"),
    "G6_E_MEDIA_SILENT": ("none", "media"),
    "G6_E_MEDIA_SIZE_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_STREAM_MISMATCH": ("none", "media"),
    "G6_E_MEDIA_TEXT_ONLY": ("none", "media"),
    "G6_E_MEDIA_TIME_LIMIT": ("person", "media"),
    "G6_E_MEDIA_TRANSCRIBED": ("none", "media"),
    "G6_E_MEDIA_TRANSCRIPT_DOUBTFUL": ("none", "media"),
    "G6_E_MEDIA_UNDECODABLE": ("none", "media"),
    "G6_E_META_SCHEMA": ("input", "mcp",
        "The workflow's specification or the profile you passed did not fit: fix the field the refusal names in the "
        "call you made, then make it again."),
    "G6_E_MISSION_NO_CHANGE": ("tool:g6_mission_submit", "mcp",
        "Change the one permitted file in the working tree, then submit again."),
    "G6_E_MISSION_OUT_OF_SCOPE": ("tool:g6_mission_submit", "mcp",
        "Change only the permitted file, never a test, then submit again."),
    "G6_E_MISSION_REFUSED": ("none", "mcp", "The mission refused this step: tell the person what it says."),
    "G6_E_ML_BACKEND_NOT_INSTALLED": ("person", "ml"),
    "G6_E_ML_BELOW_BASELINE": ("none", "ml"),
    "G6_E_ML_CHECK_PROBLEM": ("none", "bundles"),
    "G6_E_ML_DEADLINE": ("person", "ml"),
    "G6_E_ML_DESCRIBED": ("none", "ml"),
    "G6_E_ML_DUPLICATES_ACROSS_SPLIT": ("none", "ml"),
    "G6_E_ML_FIT_FAILED": ("none", "ml"),
    "G6_E_ML_INPUT_INVALID": ("input", "ml"),
    "G6_E_ML_INPUT_OVERFLOW": ("person", "ml"),
    "G6_E_ML_LEAKAGE": ("none", "ml"),
    "G6_E_ML_MEASURED": ("none", "ml"),
    "G6_E_ML_MODEL_MISFIT": ("person", "ml"),
    "G6_E_ML_NON_FINITE_LOSS": ("none", "ml"),
    "G6_E_ML_NON_FINITE_OUTPUT": ("none", "ml"),
    "G6_E_ML_NON_FINITE_WEIGHTS": ("person", "ml"),
    "G6_E_ML_NOT_CONVERGED": ("none", "ml"),
    "G6_E_ML_OP_FAILED": ("none", "ml"),
    "G6_E_ML_POSTERIOR": ("none", "ml"),
    "G6_E_ML_PREDICTED": ("none", "ml"),
    "G6_E_ML_PRIOR_SENSITIVE": ("person", "ml"),
    "G6_E_ML_PROFILED": ("none", "ml"),
    "G6_E_ML_SEED_MISSING": ("input", "ml"),
    "G6_E_ML_TOO_FEW_HELD_OUT": ("none", "ml"),
    "G6_E_ML_TOO_LARGE": ("person", "ml"),
    "G6_E_ML_UNKNOWN_ESTIMATOR": ("input", "ml"),
    "G6_E_ML_UNTRUSTED_CODE": ("person", "ml"),
    "G6_E_MODEL_REFUSED": ("person", "core"),
    "G6_E_MOTION_CANNOT_ASSESS": ("none", "embodied"),
    "G6_E_MOTION_COLLISION": ("none", "embodied"),
    "G6_E_MOTION_ESTIMATE": ("none", "embodied"),
    "G6_E_MOTION_GOAL_MISSED": ("none", "embodied"),
    "G6_E_MOTION_GRASP_CRUSH": ("none", "embodied"),
    "G6_E_MOTION_GRASP_SLIP": ("none", "embodied"),
    "G6_E_MOTION_GRIPPER_FORCE_LIMIT": ("person", "embodied"),
    "G6_E_MOTION_INPUT_INVALID": ("input", "embodied"),
    "G6_E_MOTION_JOINT_LIMIT": ("person", "embodied"),
    "G6_E_MOTION_NO_PATH": ("none", "embodied"),
    "G6_E_MOTION_NO_VIOLATION_FOUND": ("none", "embodied"),
    "G6_E_MOTION_OUT_OF_WORKSPACE": ("none", "embodied"),
    "G6_E_MOTION_TIMING": ("none", "embodied"),
    "G6_E_MOTION_VELOCITY_LIMIT": ("person", "embodied"),
    "G6_E_NOT_ASSESSED": ("none", "bundles"),
    "G6_E_NO_BLOCK": ("tool:g6_select", "core",
        "That component is a library with no block, so g6_invoke cannot run it: pick a runnable call for the task, or "
        "use it from Python as g6_select's library form says."),
    "G6_E_NO_CHANGE": ("none", "bundles"),
    "G6_E_NO_CLEAN_REVIEW": ("none", "harness",
        "The loop stopped with no clean review. Show the reply's report to the person as it is."),
    "G6_E_NO_MODEL": ("tool:g6_select", "core", "no_model"),
    "G6_E_NO_PYTHON_CHANGED": ("tool:g6_review_until_clean", "harness",
        "No Python file changed against the base, so nothing was tested and no loop began: make the change, or name "
        "the right base, then call again."),
    "G6_E_NO_TESTS": ("none", "bundles"),
    "G6_E_OBSERVATION_UNAVAILABLE": ("person", "harness"),
    "G6_E_OPERATOR_APPROVAL_MISMATCH": ("person", "operator"),
    "G6_E_OPERATOR_APPROVAL_REQUIRED": ("person", "operator", "wait"),
    "G6_E_OPERATOR_CALLER_CHECKS": ("none", "operator"),
    "G6_E_OPERATOR_CHECKS_PASSED": ("none", "operator"),
    "G6_E_OPERATOR_CHECK_FAILED": ("none", "operator"),
    "G6_E_OPERATOR_CHECK_UNKNOWN": ("none", "operator"),
    "G6_E_OPERATOR_DRAFT_BLOCKED": ("person", "operator"),
    "G6_E_OPERATOR_ESTIMATE": ("none", "operator"),
    "G6_E_OPERATOR_EXECUTOR_FAILED": ("none", "operator"),
    "G6_E_OPERATOR_GATE_ERROR": ("person", "operator"),
    "G6_E_OPERATOR_GUARD_CANDIDATE": ("none", "operator"),
    "G6_E_OPERATOR_NOT_MEASURED": ("none", "operator"),
    "G6_E_OPERATOR_NO_CHECK": ("none", "operator"),
    "G6_E_OPERATOR_NO_MODEL": ("tool:g6_select", "operator", "no_model"),
    "G6_E_OPERATOR_PROPOSAL": ("none", "operator"),
    "G6_E_OPERATOR_RANK": ("none", "operator"),
    "G6_E_OPERATOR_SELF_REPORTED": ("none", "operator"),
    "G6_E_OPERATOR_STOP": ("none", "operator"),
    "G6_E_OPERATOR_TASK": ("none", "operator"),
    "G6_E_OUTSIDE_PROGRAM_MISSING": ("person", "core media"),
    "G6_E_OUTSIDE_SERVICE_FAILED": ("person", "core"),
    "G6_E_OUTSIDE_SERVICE_NOT_LOCAL": ("person", "core"),
    "G6_E_OUTSIDE_SERVICE_STATUS": ("person", "core"),
    "G6_E_OUTSIDE_SERVICE_TIMEOUT": ("person", "core"),
    "G6_E_OUTSIDE_SERVICE_TOO_LARGE": ("person", "core"),
    "G6_E_PATCH_REVIEW": ("tool:g6_select", "core harness",
        "The code every review result carries: the outcome beside it (test_integrity_compromised, defect_detected, "
        "insufficient_evidence or supported_within_scope) says what to do. Pass that outcome back as last_outcome."),
    "G6_E_PATCH_REVIEW_INTERRUPTED": ("tool:g6_review_patch", "mcp", "The review was interrupted: start it again."),
    "G6_E_PATCH_STALE": ("tool:g6_review_patch", "harness",
        "The working tree changed since the digest you passed: review it again as it stands now, without the old "
        "expected_artifact_digest."),
    "G6_E_PROMOTION_LAW": ("none", "core", "The result is held at qualified-draft: report it so, never as verified."),
    "G6_E_QUALIFIED_DRAFT": ("none", "bundles core mcp",
        "A proposal or draft: report it with its warning card; nothing in it is checked."),
    "G6_E_QUESTION_ANSWER_REFUSED": ("none", "harness"),
    "G6_E_QUESTION_BUDGET_SPENT": ("person", "mcp"),
    "G6_E_QUESTION_CONFIRMED": ("tool:g6_review_patch", "mcp",
        "The person's answer is enforced on each later review of the task: review it."),
    "G6_E_QUESTION_HELD": ("none", "mcp"),
    "G6_E_QUESTION_OUTCOMES_IDENTICAL": ("none", "harness"),
    "G6_E_QUESTION_OUTCOMES_INDEPENDENT": ("none", "harness"),
    "G6_E_QUESTION_OUTCOMES_OVERLAP": ("none", "harness"),
    "G6_E_QUESTION_PROBE_UNAVAILABLE": ("person", "harness"),
    "G6_E_QUESTION_REVIEW_NOT_BOUND": ("tool:g6_review_patch", "mcp",
        "The review job is not of this repository, task and working tree: run a review of it first."),
    "G6_E_QUESTION_STANDARD_SUPERSEDED": ("none", "harness"),
    "G6_E_QUESTION_STORE_UNREADABLE": ("none", "harness"),
    "G6_E_QUESTION_UNCONFIRMED": ("none", "mcp"),
    "G6_E_REPLY_WITHHELD": ("none", "core"),
    "G6_E_RESOURCE_LIMIT": ("person", "core",
        "The run was over a resource limit: tell the person; a narrower input may fit."),
    "G6_E_REVIEW_UNTIL_CLEAN": ("none", "mcp", "Show the reply's report to the person as it is."),
    "G6_E_SCENARIO_UNAVAILABLE": ("tool:g6_confirm_behaviour", "harness",
        "Give the question a concrete candidate with two competing outcomes."),
    "G6_E_SCREEN_RAW_FAILED": ("none", "core",
        "The component's own input screen failed: tell the person; it is G6's fault."),
    "G6_E_SENSITIVE_COMPONENT_DENIED": ("person", "agents core mcp",
        "This op is sensitive. The person decides whether to allow it themselves. Tell the person and stop."),
    "G6_E_SOURCE_INSTRUCTIONS": ("none", "bundles"),
    "G6_E_STOPPED": ("none", "core operator", "The review stopped: Show the reply's report to the person as it is."),
    "G6_E_STRUCTURED_CALL_FAILED": ("none", "core"),
    "G6_E_STRUCTURED_INPUT": ("none", "core"),
    "G6_E_STRUCTURED_RETRIES_EXHAUSTED": ("none", "core"),
    "G6_E_STRUCTURED_VALIDATOR_FAILED": ("none", "core"),
    "G6_E_UNKNOWN_COMPONENT": ("tool:g6_find", "core", "No component has that name: find one by the task."),
    "G6_E_UNKNOWN_OP": ("input", "core"),
    "G6_E_UNSAFE_OUTPUT": ("none", "core"),
    "G6_E_WORKFLOW_NOT_ADMITTED": ("person", "mcp",
        "G6's workflow record is off or this call is not one it keeps: tell the person, who may set "
        "G6_WORKFLOW_RECORD=on."),
    "G6_E_WORKFLOW_RECORD": ("tool:g6_workflow_export", "mcp", "Look at what G6 recorded, in a file the person opens."),
    "G6_E_WORKFLOW_RECORD_OFF": ("none", "harness"),
    "G6_E_WORKFLOW_RECORD_UNREADABLE": ("none", "harness"),
    "G6_E_WORKSPACE_ARCHIVE_REFUSED": ("none", "workspace"),
    "G6_E_WORKSPACE_BACKED_UP": ("none", "workspace"),
    "G6_E_WORKSPACE_BUSY": ("retry", "workspace"),
    "G6_E_WORKSPACE_DRY_RUN": ("none", "workspace"),
    "G6_E_WORKSPACE_INPUT_INVALID": ("input", "workspace"),
    "G6_E_WORKSPACE_LISTED": ("none", "workspace"),
    "G6_E_WORKSPACE_LOCKED": ("retry", "workspace"),
    "G6_E_WORKSPACE_LOCK_RELEASED": ("none", "workspace"),
    "G6_E_WORKSPACE_LOCK_TAKEN": ("none", "workspace"),
    "G6_E_WORKSPACE_NOT_A_FILE": ("none", "workspace"),
    "G6_E_WORKSPACE_NOT_A_WORKSPACE": ("none", "workspace"),
    "G6_E_WORKSPACE_NOT_FOUND": ("none", "workspace"),
    "G6_E_WORKSPACE_NOT_TEXT": ("none", "workspace"),
    "G6_E_WORKSPACE_OP_FAILED": ("none", "workspace"),
    "G6_E_WORKSPACE_OUTSIDE_ROOT": ("person", "workspace"),
    "G6_E_WORKSPACE_READ": ("none", "workspace"),
    "G6_E_WORKSPACE_REMOVED": ("none", "workspace"),
    "G6_E_WORKSPACE_RESERVED": ("none", "workspace"),
    "G6_E_WORKSPACE_RESOLVED": ("none", "workspace"),
    "G6_E_WORKSPACE_RESTORED": ("none", "workspace"),
    "G6_E_WORKSPACE_STALE_WRITE": ("person", "workspace"),
    "G6_E_WORKSPACE_WRITTEN": ("none", "workspace"),
    "G6_E_AGENT_BAD_EVENT": ("person", "agents"),
    "G6_E_AGENT_CALL_REQUIRED": ("person", "agents"),
    "G6_E_AGENT_CALL_REUSED": ("person", "agents"),
    "G6_E_AGENT_CIRCUIT_OPEN": ("person", "agents"),
    "G6_E_AGENT_CLEANUP_UNCONFIRMED": ("person", "agents"),
    "G6_E_AGENT_DEADLINE": ("person", "agents"),
    "G6_E_AGENT_DECIDED": ("person", "agents"),
    "G6_E_AGENT_DRY_RUN": ("person", "agents"),
    "G6_E_AGENT_EXIT_FAILED": ("person", "agents"),
    "G6_E_AGENT_FAILED": ("person", "agents"),
    "G6_E_AGENT_GATE_BLOCKED": ("person", "agents"),
    "G6_E_AGENT_GATE_ERROR": ("person", "agents"),
    "G6_E_AGENT_NOT_A_RUNTIME": ("person", "agents"),
    "G6_E_AGENT_NOT_VERSIONED": ("person", "agents"),
    "G6_E_AGENT_NO_OUTCOME": ("person", "agents"),
    "G6_E_AGENT_OUTPUT_TOO_LARGE": ("person", "agents"),
    "G6_E_AGENT_OUTSIDE_WORKSPACE": ("person", "agents"),
    "G6_E_AGENT_PATHS_REQUIRED": ("person", "agents"),
    "G6_E_AGENT_PERMISSION": ("person", "agents"),
    "G6_E_AGENT_POLICY_BLOCKED": ("person", "agents"),
    "G6_E_AGENT_SELF_CALL": ("person", "agents"),
    "G6_E_AGENT_SELF_REPORTED": ("person", "agents"),
    "G6_E_AGENT_START_FAILED": ("person", "agents"),
    "G6_E_AGENT_STEP_LIMIT": ("person", "agents"),
    "G6_E_AGENT_TASK_CHECKED": ("person", "agents"),
    "G6_E_AGENT_TASK_REQUIRED": ("person", "agents"),
    "G6_E_AGENT_TOOL_ALLOWED": ("person", "agents"),
    "G6_E_AGENT_TOOL_FAILED": ("person", "agents"),
    "G6_E_AGENT_TOOL_LIMIT": ("person", "agents"),
    "G6_E_AGENT_TOOL_UNDECLARED": ("person", "agents"),
    "G6_E_AGENT_UNGATED_TOOL": ("person", "agents"),
    "G6_E_AGENT_WORKSPACE": ("person", "agents"),
    "G6_E_COMPOSITION_CONTRACT_CHANGED": ("person", "core"),
    "G6_E_COMPOSITION_EVIDENCE_MISMATCH": ("person", "core"),
    "G6_E_COMPOSITION_INVALID_MAPPING": ("input", "core"),
    "G6_E_COMPOSITION_NO_CONTRACT": ("person", "core"),
    "G6_E_COMPOSITION_OUTPUT_MISSING": ("person", "core"),
    "G6_E_COMPOSITION_PRECONDITION_FAILED": ("person", "core"),
    "G6_E_COMPOSITION_STOP_CONDITION": ("person", "core"),
    "G6_E_COMPOSITION_UNMET_INPUT": ("input", "core"),
    "G6_E_EVIDENCE_BINDING_UNREADABLE": ("person", "research"),
}
